import { useEffect, useState, useCallback } from 'react';
import type { EndpointSample, ConsoleHealth } from '@/types/api';
import { fetchSamples, fetchHealth, setBaseUrl } from '@/api/client';
import Header from '@/components/Header';
import Sidebar from '@/components/Sidebar';
import Overview from '@/components/Overview';
import EndpointView from '@/components/EndpointView';
import BatchTest from '@/components/BatchTest';
import { type BackendOption, DEFAULT_BACKEND } from '@/components/BackendSelector';
import { Icon } from '@/components/icons';

/** 主区域视图：总览 / 单端点测试 / 批量测试。 */
type View =
  | { type: 'overview' }
  | { type: 'endpoint'; sample: EndpointSample }
  | { type: 'batch' };

export default function App() {
  const [samples, setSamples] = useState<EndpointSample[]>([]);
  const [view, setView] = useState<View>({ type: 'overview' });
  const [health, setHealth] = useState<ConsoleHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [backend, setBackend] = useState<BackendOption>(DEFAULT_BACKEND);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [samplesData, healthData] = await Promise.all([fetchSamples(), fetchHealth()]);
      setSamples(samplesData);
      setHealth(healthData);
      // 加载完成后回到总览页，避免残留上一个后端的选择状态
      setView({ type: 'overview' });
    } catch (e) {
      setError((e as Error).message);
      setHealth(null);
      setSamples([]);
      setView({ type: 'overview' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setBaseUrl(backend.value);
    load();
  }, [backend, load]);

  const selected = view.type === 'endpoint' ? view.sample : null;
  const goOverview = () => setView({ type: 'overview' });
  const openEndpoint = (sample: EndpointSample) => setView({ type: 'endpoint', sample });

  return (
    <div className="flex h-screen flex-col bg-gray-50">
      <Header
        backend={backend}
        onBackendChange={setBackend}
        health={health}
        loading={loading}
        onHome={goOverview}
      />

      <div className="flex flex-1 overflow-hidden">
        {loading ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 text-gray-400">
            <span className="h-8 w-8 animate-spin rounded-full border-2 border-gray-200 border-t-indigo-500" />
            <p className="text-sm">加载接口列表…</p>
          </div>
        ) : error ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6">
            <span className="flex h-12 w-12 items-center justify-center rounded-full bg-red-50 text-red-500">
              <Icon name="alert" className="h-6 w-6" />
            </span>
            <div className="text-center">
              <p className="text-sm font-medium text-gray-800">无法连接后端 {backend.label}</p>
              <p className="mt-1 max-w-md break-words text-xs text-gray-500">{error}</p>
            </div>
            <button
              onClick={load}
              className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-indigo-700"
            >
              <Icon name="refresh" className="h-4 w-4" />
              重试
            </button>
          </div>
        ) : (
          <>
            <Sidebar
              samples={samples}
              selected={selected}
              onSelect={openEndpoint}
              onHome={goOverview}
              onBatch={() => setView({ type: 'batch' })}
              batchActive={view.type === 'batch'}
            />
            <main className="flex-1 overflow-hidden">
              {view.type === 'endpoint' ? (
                <EndpointView
                  key={`${view.sample.method}-${view.sample.path}`}
                  sample={view.sample}
                  onBack={goOverview}
                  agentUrl={health?.agent_url}
                />
              ) : view.type === 'batch' ? (
                <BatchTest samples={samples} onSelectSample={openEndpoint} />
              ) : (
                <Overview samples={samples} onSelect={openEndpoint} />
              )}
            </main>
          </>
        )}
      </div>
    </div>
  );
}
