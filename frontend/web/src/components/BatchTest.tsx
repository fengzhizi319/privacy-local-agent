/**
 * 批量测试视图：一键回归验证。
 *
 * 选择一个分类（或全部），顺序调用其下所有接口，
 * 汇总展示通过率与逐条结果，单个失败不中断整个批次。
 */
import { useMemo, useState } from 'react';
import type { EndpointSample, BatchResponse } from '@/types/api';
import { batchRequest } from '@/api/client';
import { orderCategories } from '@/lib/categories';
import { Icon } from '@/components/icons';

interface BatchTestProps {
  samples: EndpointSample[];
  /** 从结果跳转到单个端点测试 */
  onSelectSample: (sample: EndpointSample) => void;
}

const ALL = '__all__';

/**
 * 批量测试视图：选择一个分类（或全部），一键顺序调用其下所有接口，
 * 汇总展示成功 / 失败与耗时，便于快速回归验证。
 */
export default function BatchTest({ samples, onSelectSample }: BatchTestProps) {
  const [category, setCategory] = useState<string>(ALL);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BatchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const categories = useMemo(
    () => orderCategories([...new Set(samples.map((s) => s.category))]),
    [samples],
  );

  /** path+method → sample 的映射，用于结果展示 label 与跳转。 */
  const sampleMap = useMemo(() => {
    const map = new Map<string, EndpointSample>();
    for (const s of samples) map.set(`${s.method} ${s.path}`, s);
    return map;
  }, [samples]);

  const targets = useMemo(
    () => (category === ALL ? samples : samples.filter((s) => s.category === category)),
    [samples, category],
  );

  const handleRun = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const res = await batchRequest(
        targets.map((s) => ({ method: s.method, path: s.path, body: s.body ?? null })),
      );
      setResult(res);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const passRate = result && result.total > 0 ? Math.round((result.passed / result.total) * 100) : 0;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl px-8 py-10">
        {/* 标题区 */}
        <div className="mb-6">
          <h1 className="flex items-center gap-2 text-2xl font-bold text-gray-900">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
              <Icon name="play" className="h-4 w-4" />
            </span>
            批量测试
          </h1>
          <p className="mt-1.5 text-sm text-gray-500">
            一键顺序调用所选分类下的全部接口，快速回归验证。单个失败不会中断整个批次。
          </p>
        </div>

        {/* 控制区 */}
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
          <label className="flex items-center gap-2 text-sm text-gray-600">
            测试范围
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm text-gray-700 transition-colors focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100"
            >
              <option value={ALL}>全部分类（{samples.length} 个接口）</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}（{samples.filter((s) => s.category === c).length}）
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={handleRun}
            disabled={running || targets.length === 0}
            className={[
              'inline-flex h-9 items-center gap-2 rounded-lg px-4 text-sm font-medium text-white shadow-sm transition-colors',
              running || targets.length === 0
                ? 'cursor-not-allowed bg-indigo-400'
                : 'bg-indigo-600 hover:bg-indigo-700',
            ].join(' ')}
          >
            {running ? (
              <>
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-indigo-200 border-t-white" />
                测试中…
              </>
            ) : (
              <>
                <Icon name="play" className="h-3.5 w-3.5" />
                开始测试（{targets.length}）
              </>
            )}
          </button>
        </div>

        {/* 错误提示 */}
        {error && (
          <div className="mt-4 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
            <Icon name="alert" className="h-4 w-4 shrink-0" />
            {error}
          </div>
        )}

        {/* 结果汇总 */}
        {result && (
          <div className="mt-6">
            <div className="flex items-center gap-4 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
              <div className="flex items-center gap-2">
                <span
                  className={[
                    'flex h-10 w-10 items-center justify-center rounded-full text-sm font-bold',
                    result.failed === 0 ? 'bg-emerald-50 text-emerald-600' : 'bg-amber-50 text-amber-600',
                  ].join(' ')}
                >
                  {passRate}%
                </span>
                <div className="text-sm">
                  <div className="font-semibold text-gray-800">
                    {result.failed === 0 ? '全部通过' : `${result.failed} 个失败`}
                  </div>
                  <div className="text-xs text-gray-400">
                    共 {result.total} · 通过 {result.passed} · 失败 {result.failed}
                  </div>
                </div>
              </div>
            </div>

            {/* 结果明细表 */}
            <div className="mt-4 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50/60 text-left text-xs uppercase tracking-wide text-gray-400">
                    <th className="px-4 py-2.5 font-semibold">状态</th>
                    <th className="px-4 py-2.5 font-semibold">接口</th>
                    <th className="px-4 py-2.5 text-right font-semibold">耗时</th>
                    <th className="px-4 py-2.5 font-semibold">信息</th>
                  </tr>
                </thead>
                <tbody>
                  {result.results.map((r, i) => {
                    const sample = sampleMap.get(`${r.method} ${r.path}`);
                    const ok = r.status >= 200 && r.status < 300;
                    return (
                      <tr key={`${r.method}-${r.path}-${i}`} className="border-b border-gray-50 last:border-0">
                        <td className="px-4 py-2.5">
                          <span
                            className={[
                              'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold',
                              ok ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700',
                            ].join(' ')}
                          >
                            <span className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-emerald-500' : 'bg-red-500'}`} />
                            {r.status}
                          </span>
                        </td>
                        <td className="px-4 py-2.5">
                          <button
                            onClick={() => sample && onSelectSample(sample)}
                            className="group text-left"
                            title="跳转到该端点"
                          >
                            <div className="flex items-center gap-2">
                              <span
                                className={`w-10 shrink-0 rounded px-1 py-0.5 text-center text-[10px] font-bold ${
                                  r.method === 'GET' ? 'bg-emerald-50 text-emerald-600' : 'bg-sky-50 text-sky-600'
                                }`}
                              >
                                {r.method}
                              </span>
                              <span className="font-mono text-xs text-gray-700 group-hover:text-indigo-600">
                                {r.path}
                              </span>
                            </div>
                            {sample && <div className="mt-0.5 pl-12 text-xs text-gray-400">{sample.label}</div>}
                          </button>
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono text-xs text-gray-500">
                          {r.duration_ms} ms
                        </td>
                        <td className="max-w-[240px] truncate px-4 py-2.5 text-xs text-gray-400" title={r.error ?? ''}>
                          {ok ? '—' : r.error}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* 空状态 */}
        {!result && !error && !running && (
          <div className="mt-10 flex flex-col items-center gap-3 text-gray-300">
            <Icon name="zap" className="h-12 w-12" strokeWidth={1.5} />
            <p className="text-sm text-gray-400">选择范围后点击"开始测试"</p>
          </div>
        )}
      </div>
    </div>
  );
}
