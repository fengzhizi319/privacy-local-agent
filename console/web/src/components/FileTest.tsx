/**
 * 数据文件隐私处理视图。
 *
 * 用户上传 CSV/JSON 文件，选择操作类型（脱敏 / K-匿名 / 分类），
 * 按操作动态填写参数，提交后经后端转发到 agent 处理。
 * 右侧上方展示原始响应（复用 ResponsePanel），下方以“原始数据 / 处理结果”
 * 双表并排呈现，并对发生变更的单元格高亮，便于直观对比处理前后的差异。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { FileOperation, UploadResponse } from '@/types/api';
import { uploadFile } from '@/api/client';
import ResponsePanel from '@/components/ResponsePanel';
import { Icon } from '@/components/icons';
import { createSampleFile, downloadSampleFile, type SampleFormat } from '@/utils/sampleFile';
import { parseDataFile, type ParsedRecords } from '@/utils/fileParse';

/** 操作选项的中文标签与说明。 */
const OPERATIONS: { value: FileOperation; label: string; hint: string }[] = [
  { value: 'mask_dataframe', label: '数据脱敏', hint: '对指定列做掩码脱敏' },
  { value: 'k_anonymize', label: 'K-匿名', hint: '对准标识符列做 K-匿名泛化' },
  { value: 'classify_table', label: '数据分类', hint: '对整表做敏感等级分类' },
];

/** 把逗号分隔的输入拆分为去空的列名数组。 */
function splitCols(text: string): string[] {
  return text
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** 表格预览的最大行数，避免大文件渲染过多 DOM。 */
const MAX_PREVIEW_ROWS = 50;

/** 客户端上传大小上限（与后端 CONSOLE_MAX_UPLOAD_BYTES 默认值保持一致，10MB）。 */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

/** 支持的文件扩展名。 */
export const ACCEPTED_EXTS = ['.csv', '.json'];

/**
 * 客户端预校验文件类型与大小，返回错误提示；合法时返回 null。
 *
 * 在上传前提前拦截不合规文件，避免无效的大文件 / 错误格式
 * 消耗网络与后端资源（与后端 413/400 校验互为双保险）。
 */
export function validateFile(f: File): string | null {
  const lower = f.name.toLowerCase();
  if (!ACCEPTED_EXTS.some((ext) => lower.endsWith(ext))) {
    return '仅支持 .csv 与 .json 文件';
  }
  if (f.size > MAX_UPLOAD_BYTES) {
    return `文件过大（${(f.size / 1024 / 1024).toFixed(1)} MB），上限 ${MAX_UPLOAD_BYTES / 1024 / 1024} MB`;
  }
  return null;
}

/**
 * 通用记录表格：按 schema 列序渲染记录数组。
 *
 * 传入 ``baseline``（原始记录）时，会逐行逐列对比，
 * 将“处理后与原始值不同”的单元格高亮为琥珀色，
 * 从而直观呈现脱敏 / K-匿名等操作带来的变化。
 */
function DataTable({
  records,
  schema,
  baseline = null,
}: {
  records: Record<string, any>[];
  /** 列名顺序；省略时从记录中推导。 */
  schema?: string[];
  /** 对比基准（原始记录），用于高亮变更单元格。 */
  baseline?: Record<string, any>[] | null;
}) {
  const cols = useMemo(() => {
    if (schema && schema.length > 0) return schema;
    const set = new Set<string>();
    records.forEach((r) => {
      if (r && typeof r === 'object') Object.keys(r).forEach((k) => set.add(k));
    });
    return Array.from(set);
  }, [records, schema]);

  if (records.length === 0 || cols.length === 0) return null;
  const preview = records.slice(0, MAX_PREVIEW_ROWS);

  return (
    <table className="w-full border-collapse text-xs">
      <thead>
        <tr>
          {cols.map((c) => (
            <th
              key={c}
              className="sticky top-0 z-10 border border-gray-200 bg-gray-50 px-2 py-1 text-left font-semibold text-gray-600"
            >
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {preview.map((row, i) => (
          <tr key={i} className="hover:bg-indigo-50/40">
            {cols.map((c) => {
              const val = row?.[c] ?? '';
              // 与原始记录同一行同一列对比，值不同则高亮。
              const baseRow = baseline?.[i];
              const changed =
                !!baseline && baseRow !== undefined && String(baseRow[c] ?? '') !== String(val);
              return (
                <td
                  key={c}
                  className={
                    changed
                      ? 'border border-amber-200 bg-amber-100 px-2 py-1 font-medium text-amber-900'
                      : 'border border-gray-100 px-2 py-1 text-gray-700'
                  }
                >
                  {String(val)}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function FileTest() {
  const [file, setFile] = useState<File | null>(null);
  const [operation, setOperation] = useState<FileOperation>('mask_dataframe');
  // 各操作的参数输入
  const [columns, setColumns] = useState('email, phone');
  const [context, setContext] = useState('');
  const [qiCols, setQiCols] = useState('age, zip, gender');
  const [k, setK] = useState(2);

  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<UploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  /** 原始文件解析结果（用于“原始数据”预览与差异对比）。 */
  const [original, setOriginal] = useState<ParsedRecords | null>(null);
  /** 原始文件解析失败的提示（不影响上传，仅预览不可用）。 */
  const [parseError, setParseError] = useState<string | null>(null);

  const opMeta = useMemo(() => OPERATIONS.find((o) => o.value === operation)!, [operation]);

  /**
   * 文件变化时在浏览器端解析为 records + schema，供“原始数据”预览。
   *
   * 该解析仅用于界面展示，与上传后后端的解析相互独立；
   * 解析失败只提示预览不可用，不阻止上传。
   */
  useEffect(() => {
    let cancelled = false;
    if (!file) {
      setOriginal(null);
      setParseError(null);
      return;
    }
    parseDataFile(file)
      .then((parsed) => {
        if (!cancelled) {
          setOriginal(parsed);
          setParseError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setOriginal(null);
          setParseError((e as Error).message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [file]);

  /** 处理结果中的记录数组（仅当 result 为非空数组时，脱敏 / K-匿名场景）。 */
  const resultRecords = useMemo(() => {
    const r = response?.data?.result;
    return Array.isArray(r) && r.length > 0 ? (r as Record<string, any>[]) : null;
  }, [response]);

  /**
   * 结果表的列序：以原始 schema 为准（保证两表列序一致、可并排对比），
   * 再追加结果中多出的列；原始解析不可用时退化为从结果推导。
   */
  const resultCols = useMemo(() => {
    if (!resultRecords) return [];
    const base = original?.schema ?? [];
    const extra = new Set<string>();
    resultRecords.forEach((r) => {
      if (r && typeof r === 'object') {
        Object.keys(r).forEach((k) => {
          if (!base.includes(k)) extra.add(k);
        });
      }
    });
    return [...base, ...Array.from(extra)];
  }, [resultRecords, original]);

  /** 根据当前操作组装 params 对象。 */
  function buildParams(): Record<string, unknown> {
    switch (operation) {
      case 'mask_dataframe':
        return { columns: splitCols(columns), context };
      case 'k_anonymize':
        return { qi_cols: splitCols(qiCols), k };
      case 'classify_table':
        return {};
      default:
        return {};
    }
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null;
    setResponse(null);
    setError(null);
    // 客户端预校验类型 / 大小；不合规时拒绝并清空选择。
    if (f) {
      const problem = validateFile(f);
      if (problem) {
        setError(problem);
        setFile(null);
        e.target.value = '';
        return;
      }
    }
    setFile(f);
  };

  /**
   * 一键填充预生成的示例文件。
   *
   * 在内存中构造与磁盘文件等价的 ``File`` 对象并填入上传控件，
   * 用户无需手工准备测试数据即可直接点击“上传并处理”。
   */
  const handleUseSample = (format: SampleFormat) => {
    setFile(createSampleFile(format));
    setResponse(null);
    setError(null);
  };

  const handleSubmit = async () => {
    if (!file) {
      setError('请先选择 CSV 或 JSON 文件');
      return;
    }
    setLoading(true);
    setError(null);
    setResponse(null);
    try {
      const resp = await uploadFile(file, operation, buildParams());
      setResponse(resp);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const inputCls =
    'w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm text-gray-700 placeholder-gray-400 transition-colors focus:border-indigo-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-100';

  return (
    <div className="flex h-full">
      {/* 左侧：配置表单 */}
      <div className="flex w-[380px] shrink-0 flex-col gap-4 overflow-y-auto border-r border-gray-200 bg-white p-5">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold text-gray-800">
            <span className="flex h-6 w-6 items-center justify-center rounded bg-indigo-50 text-indigo-600">
              <Icon name="upload" className="h-3.5 w-3.5" />
            </span>
            数据文件隐私处理
          </h2>
          <p className="mt-1 text-xs text-gray-500">上传 CSV/JSON 文件，选择脱敏 / K-匿名 / 分类操作。</p>
        </div>

        {/* 文件选择 */}
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">数据文件</label>
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.json"
            onChange={handleFileChange}
            className="block w-full text-sm text-gray-600 file:mr-3 file:rounded-lg file:border-0 file:bg-indigo-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-indigo-600 hover:file:bg-indigo-100"
          />
          {file && (
            <p className="mt-1 text-xs text-gray-400">
              已选择：{file.name}（{(file.size / 1024).toFixed(1)} KB）
            </p>
          )}
          {/* 示例文件：免手工准备数据，一键填充或下载 */}
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-gray-400">示例文件：</span>
            {(['csv', 'json'] as SampleFormat[]).map((fmt) => (
              <span key={fmt} className="inline-flex items-center overflow-hidden rounded-md border border-indigo-200">
                <button
                  onClick={() => handleUseSample(fmt)}
                  className="px-2 py-0.5 text-xs font-medium text-indigo-600 transition-colors hover:bg-indigo-50"
                  title={`填充 ${fmt.toUpperCase()} 示例文件并直接用于处理`}
                >
                  {fmt.toUpperCase()}
                </button>
                <button
                  onClick={() => downloadSampleFile(fmt)}
                  className="border-l border-indigo-200 px-1.5 py-0.5 text-indigo-400 transition-colors hover:bg-indigo-50 hover:text-indigo-600"
                  title={`下载 ${fmt.toUpperCase()} 示例文件到本地`}
                >
                  <Icon name="download" className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>
        </div>

        {/* 操作选择 */}
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">操作类型</label>
          <select
            value={operation}
            onChange={(e) => setOperation(e.target.value as FileOperation)}
            className={inputCls}
          >
            {OPERATIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-gray-400">{opMeta.hint}</p>
        </div>

        {/* 动态参数 */}
        {operation === 'mask_dataframe' && (
          <>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600">脱敏列（逗号分隔）</label>
              <input value={columns} onChange={(e) => setColumns(e.target.value)} className={inputCls} placeholder="email, phone" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600">上下文（可选）</label>
              <input value={context} onChange={(e) => setContext(e.target.value)} className={inputCls} placeholder="如：医疗场景" />
            </div>
          </>
        )}

        {operation === 'k_anonymize' && (
          <>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600">准标识符列 QI（逗号分隔）</label>
              <input value={qiCols} onChange={(e) => setQiCols(e.target.value)} className={inputCls} placeholder="age, zip, gender" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600">K 值</label>
              <input
                type="number"
                min={2}
                value={k}
                onChange={(e) => setK(Math.max(2, Number(e.target.value) || 2))}
                className={inputCls}
              />
            </div>
          </>
        )}

        {operation === 'classify_table' && (
          <p className="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-500">
            分类操作无需额外参数，将自动推断表结构并给出敏感等级。
          </p>
        )}

        <button
          onClick={handleSubmit}
          disabled={loading}
          className="inline-flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {loading ? (
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
          ) : (
            <Icon name="send" className="h-4 w-4" />
          )}
          {loading ? '处理中…' : '上传并处理'}
        </button>
      </div>

      {/* 右侧：上方原始响应，下方“原始数据 / 处理结果”并排对比 */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* 上方：原始响应 JSON（含后端 / 协议徽章与错误展示） */}
        <div className="h-[34%] min-h-[150px] shrink-0 overflow-hidden border-b border-gray-200">
          <ResponsePanel response={response} error={error} duration={null} path="upload" />
        </div>

        {/* 下方：处理前后对比 */}
        <div className="flex flex-1 overflow-hidden">
          {/* 原始数据 */}
          <section className="flex w-1/2 flex-col overflow-hidden border-r border-gray-200">
            <header className="flex shrink-0 items-center justify-between border-b border-gray-100 bg-gray-50/70 px-4 py-2">
              <span className="text-xs font-semibold text-gray-600">原始数据</span>
              {original && (
                <span className="text-[11px] text-gray-400">
                  前 {Math.min(original.records.length, MAX_PREVIEW_ROWS)} 行 / 共 {original.records.length} 行
                </span>
              )}
            </header>
            <div className="flex-1 overflow-auto p-2">
              {original ? (
                <DataTable records={original.records} schema={original.schema} />
              ) : (
                <div className="flex h-full items-center justify-center px-6 text-center text-xs text-gray-400">
                  {parseError ? `预览不可用：${parseError}` : '选择或填充示例文件后，在此预览原始数据'}
                </div>
              )}
            </div>
          </section>

          {/* 处理结果 */}
          <section className="flex w-1/2 flex-col overflow-hidden">
            <header className="flex shrink-0 items-center justify-between gap-2 border-b border-gray-100 bg-gray-50/70 px-4 py-2">
              <span className="text-xs font-semibold text-gray-600">处理结果</span>
              <span className="flex items-center gap-2 text-[11px] text-gray-400">
                {resultRecords && (
                  <span>
                    前 {Math.min(resultRecords.length, MAX_PREVIEW_ROWS)} 行 / 共 {resultRecords.length} 行
                  </span>
                )}
                {/* 差异图例：琥珀色 = 相比原始数据发生变更 */}
                <span className="inline-flex items-center gap-1">
                  <span className="inline-block h-2.5 w-2.5 rounded-sm border border-amber-300 bg-amber-100" />
                  已变更
                </span>
              </span>
            </header>
            <div className="flex-1 overflow-auto p-2">
              {resultRecords ? (
                <DataTable records={resultRecords} schema={resultCols} baseline={original?.records ?? null} />
              ) : (
                <div className="flex h-full items-center justify-center px-6 text-center text-xs text-gray-400">
                  {response
                    ? operation === 'classify_table'
                      ? '分类结果为非表格结构，请查看上方原始响应 JSON'
                      : '本次响应未返回记录数组，请查看上方原始响应 JSON'
                    : '处理完成后在此查看结果，变更单元格将高亮显示'}
                </div>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
