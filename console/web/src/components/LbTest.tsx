/**
 * 负载均衡测试视图。
 *
 * 用户配置多个 agent 后端地址（name + url，默认用 health.agent_url 预填一行），
 * 设置请求数与分发策略，运行后由后端按策略分发探测请求，
 * 以表格 + 简易条形可视化展示各节点的命中数、成功率与平均延迟。
 */
import { useEffect, useState } from 'react';
import type { LbBackend, LbStrategy, LbTestResponse } from '@/types/api';
import { lbTest } from '@/api/client';
import { Icon } from '@/components/icons';

/** 策略选项的中文标签。 */
const STRATEGIES: { value: LbStrategy; label: string }[] = [
  { value: 'round_robin', label: '轮询 (round_robin)' },
  { value: 'random', label: '随机 (random)' },
  { value: 'least_connections', label: '最少连接 (least_connections)' },
];

interface LbTestProps {
  /** agent REST 地址，用于预填第一个后端节点 */
  agentUrl?: string;
}

export default function LbTest({ agentUrl }: LbTestProps) {
  const [backends, setBackends] = useState<LbBackend[]>([
    { name: 'agent-1', url: agentUrl || 'http://127.0.0.1:8079' },
  ]);
  const [numRequests, setNumRequests] = useState(20);
  const [strategy, setStrategy] = useState<LbStrategy>('round_robin');

  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<LbTestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // agentUrl 变化（切换后端）时，若第一行仍为默认值则同步更新。
  useEffect(() => {
    if (agentUrl) {
      setBackends((prev) => {
        if (prev.length === 1 && (prev[0].url === 'http://127.0.0.1:8079' || prev[0].url === '')) {
          return [{ ...prev[0], url: agentUrl }];
        }
        return prev;
      });
    }
  }, [agentUrl]);

  const updateBackend = (idx: number, patch: Partial<LbBackend>) => {
    setBackends((prev) => prev.map((b, i) => (i === idx ? { ...b, ...patch } : b)));
  };
  const addBackend = () => {
    setBackends((prev) => [...prev, { name: `agent-${prev.length + 1}`, url: agentUrl || 'http://127.0.0.1:8079' }]);
  };
  const removeBackend = (idx: number) => {
    setBackends((prev) => (prev.length > 1 ? prev.filter((_, i) => i !== idx) : prev));
  };

  const handleRun = async () => {
    const valid = backends.filter((b) => b.url.trim());
    if (valid.length === 0) {
      setError('请至少填写一个后端地址');
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const resp = await lbTest({
        backends: valid,
        num_requests: numRequests,
        strategy,
      });
      setResult(resp);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const inputCls =
    'rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm text-gray-700 placeholder-gray-400 transition-colors focus:border-indigo-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-100';

  // 条形可视化的最大命中数基准。
  const maxCount = result ? Math.max(1, ...result.distribution.map((d) => d.count)) : 1;

  return (
    <div className="flex h-full">
      {/* 左侧：配置 */}
      <div className="flex w-[380px] shrink-0 flex-col gap-4 overflow-y-auto border-r border-gray-200 bg-white p-5">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold text-gray-800">
            <span className="flex h-6 w-6 items-center justify-center rounded bg-indigo-50 text-indigo-600">
              <Icon name="scale" className="h-3.5 w-3.5" />
            </span>
            负载均衡测试
          </h2>
          <p className="mt-1 text-xs text-gray-500">配置多个后端地址，按策略分发探测请求并对比各节点表现。</p>
        </div>

        {/* 后端列表 */}
        <div>
          <div className="mb-1 flex items-center justify-between">
            <label className="text-xs font-medium text-gray-600">后端节点</label>
            <button
              onClick={addBackend}
              className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs text-indigo-600 transition-colors hover:bg-indigo-50"
            >
              <Icon name="copy" className="h-3 w-3" />
              添加节点
            </button>
          </div>
          <div className="space-y-2">
            {backends.map((b, idx) => (
              <div key={idx} className="flex items-center gap-2">
                <input
                  value={b.name}
                  onChange={(e) => updateBackend(idx, { name: e.target.value })}
                  className={`${inputCls} w-24 shrink-0`}
                  placeholder="名称"
                />
                <input
                  value={b.url}
                  onChange={(e) => updateBackend(idx, { url: e.target.value })}
                  className={`${inputCls} flex-1`}
                  placeholder="http://127.0.0.1:8079"
                />
                <button
                  onClick={() => removeBackend(idx)}
                  disabled={backends.length <= 1}
                  className="shrink-0 rounded-md p-1.5 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-500 disabled:cursor-not-allowed disabled:opacity-40"
                  title="删除节点"
                >
                  <Icon name="trash" className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* 请求数 */}
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">探测请求数</label>
          <input
            type="number"
            min={1}
            max={1000}
            value={numRequests}
            onChange={(e) => setNumRequests(Math.min(1000, Math.max(1, Number(e.target.value) || 1)))}
            className={`${inputCls} w-full`}
          />
        </div>

        {/* 策略 */}
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">分发策略</label>
          <select
            value={strategy}
            onChange={(e) => setStrategy(e.target.value as LbStrategy)}
            className={`${inputCls} w-full`}
          >
            {STRATEGIES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>

        <button
          onClick={handleRun}
          disabled={loading}
          className="inline-flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {loading ? (
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
          ) : (
            <Icon name="play" className="h-4 w-4" />
          )}
          {loading ? '测试中…' : '运行测试'}
        </button>
      </div>

      {/* 右侧：结果 */}
      <div className="flex-1 overflow-y-auto p-5">
        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-600">
            <Icon name="alert" className="h-4 w-4 shrink-0" />
            {error}
          </div>
        )}

        {!result && !error && (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-gray-300">
            <Icon name="scale" className="h-10 w-10" strokeWidth={1.5} />
            <p className="text-sm text-gray-400">运行测试后在此查看各节点分发结果</p>
          </div>
        )}

        {result && (
          <div className="space-y-5">
            {/* 汇总卡片 */}
            <div className="grid grid-cols-4 gap-3">
              <SummaryCard label="总请求" value={result.total} tone="text-gray-800" />
              <SummaryCard label="成功" value={result.success} tone="text-emerald-600" />
              <SummaryCard label="失败" value={result.failed} tone="text-red-500" />
              <SummaryCard label="总耗时" value={`${result.duration_ms.toFixed(1)} ms`} tone="text-indigo-600" />
            </div>

            {/* 分发结果表 */}
            <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50 text-left text-xs text-gray-500">
                    <th className="px-4 py-2 font-medium">节点</th>
                    <th className="px-4 py-2 font-medium">命中分布</th>
                    <th className="px-4 py-2 text-right font-medium">命中数</th>
                    <th className="px-4 py-2 text-right font-medium">成功率</th>
                    <th className="px-4 py-2 text-right font-medium">平均延迟</th>
                    <th className="px-4 py-2 text-right font-medium">最小/最大延迟</th>
                  </tr>
                </thead>
                <tbody>
                  {result.distribution.map((d, i) => {
                    const rate = d.count > 0 ? (d.success / d.count) * 100 : 0;
                    return (
                      <tr key={i} className="border-b border-gray-50 last:border-0 hover:bg-indigo-50/30">
                        <td className="px-4 py-2.5">
                          <div className="font-medium text-gray-800">{d.name}</div>
                          <div className="text-xs text-gray-400">{d.url}</div>
                        </td>
                        <td className="px-4 py-2.5">
                          <div className="h-2.5 w-full overflow-hidden rounded-full bg-gray-100">
                            <div
                              className="h-full rounded-full bg-indigo-500"
                              style={{ width: `${(d.count / maxCount) * 100}%` }}
                            />
                          </div>
                        </td>
                        <td className="px-4 py-2.5 text-right font-medium text-gray-700">{d.count}</td>
                        <td className="px-4 py-2.5 text-right">
                          <span className={rate === 100 ? 'text-emerald-600' : rate > 0 ? 'text-amber-600' : 'text-red-500'}>
                            {rate.toFixed(0)}%
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-right text-gray-700">{d.avg_latency_ms.toFixed(2)} ms</td>
                        <td className="px-4 py-2.5 text-right text-xs text-gray-500">
                          {d.min_latency_ms.toFixed(2)} / {d.max_latency_ms.toFixed(2)} ms
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** 汇总小卡片。 */
function SummaryCard({ label, value, tone }: { label: string; value: number | string; tone: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
      <div className="text-xs text-gray-400">{label}</div>
      <div className={`mt-1 text-xl font-semibold ${tone}`}>{value}</div>
    </div>
  );
}
