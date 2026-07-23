/**
 * 数据文件隐私处理视图。
 *
 * 用户上传 CSV/JSON 文件，选择操作类型（脱敏 / K-匿名 / 分类），
 * 按操作动态填写参数，提交后经后端转发到 agent 处理，
 * 右侧展示处理结果（复用 ResponsePanel）并对记录数组做表格预览。
 */
import { useMemo, useRef, useState } from 'react';
import type { FileOperation, UploadResponse } from '@/types/api';
import { uploadFile } from '@/api/client';
import ResponsePanel from '@/components/ResponsePanel';
import { Icon } from '@/components/icons';

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

/** 结果表格预览：仅当 result 为记录数组时渲染。 */
function ResultTable({ result }: { result: any }) {
  if (!Array.isArray(result) || result.length === 0) return null;
  const cols = Array.from(
    result.reduce<Set<string>>((acc, row) => {
      if (row && typeof row === 'object') Object.keys(row).forEach((k) => acc.add(k));
      return acc;
    }, new Set()),
  );
  if (cols.length === 0) return null;
  const preview = result.slice(0, 50);
  return (
    <div className="border-t border-gray-100">
      <div className="px-4 py-2 text-xs font-medium text-gray-500">
        结果预览（前 {preview.length} 行 / 共 {result.length} 行）
      </div>
      <div className="max-h-64 overflow-auto px-4 pb-4">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr>
              {cols.map((c) => (
                <th
                  key={c}
                  className="sticky top-0 border border-gray-200 bg-gray-50 px-2 py-1 text-left font-semibold text-gray-600"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.map((row, i) => (
              <tr key={i} className="hover:bg-indigo-50/40">
                {cols.map((c) => (
                  <td key={c} className="border border-gray-100 px-2 py-1 text-gray-700">
                    {row?.[c] ?? ''}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
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

  const opMeta = useMemo(() => OPERATIONS.find((o) => o.value === operation)!, [operation]);

  /** 根据当前操作组装 params 对象。 */
  function buildParams(): Record<string, any> {
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
    setFile(f);
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

      {/* 右侧：结果展示 */}
      <div className="flex flex-1 flex-col overflow-hidden">
        <div className="flex-1 overflow-hidden">
          <ResponsePanel response={response} error={error} duration={null} path="upload" />
        </div>
        {response && <ResultTable result={response.data?.result} />}
      </div>
    </div>
  );
}
