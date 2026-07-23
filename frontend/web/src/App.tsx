/**
 * 应用根组件：负责全局状态与三栏布局编排。
 *
 * 布局结构：顶部 Header + 左侧 Sidebar + 右侧主区域。
 * 主区域通过 ``View`` 判别联合在三种视图间切换：
 *   - overview：接口总览（卡片式）；
 *   - endpoint：单端点测试（请求/响应分栏）；
 *   - batch：批量测试。
 *
 * 数据流：启动时并行拉取 samples 与 health；切换后端时重新拉取。
 */
import { useEffect, useState, useCallback } from 'react';
import type { EndpointSample, ConsoleHealth } from '@/types/api';
import { fetchSamples, fetchHealth, setBaseUrl } from '@/api/client';
import Header from '@/components/Header';
import Sidebar from '@/components/Sidebar';
import Overview from '@/components/Overview';
import EndpointView from '@/components/EndpointView';
import BatchTest from '@/components/BatchTest';
import FileTest from '@/components/FileTest';
import LbTest from '@/components/LbTest';
import { type BackendOption, DEFAULT_BACKEND } from '@/components/BackendSelector';
import { Icon } from '@/components/icons';

/** 主区域视图：总览 / 单端点测试 / 批量测试 / 文件处理 / 负载均衡。 */
type View =
  | { type: 'overview' }
  | { type: 'endpoint'; sample: EndpointSample }
  | { type: 'batch' }
  | { type: 'filetest' }
  | { type: 'lbtest' };

export default function App() {
  /** 全部端点示例（来自 /api/samples） */
  const [samples, setSamples] = useState<EndpointSample[]>([]);
  /** 当前主区域视图 */
  const [view, setView] = useState<View>({ type: 'overview' });
  /** 后端健康状态（用于 Header 状态灯与 cURL 基址推断） */
  const [health, setHealth] = useState<ConsoleHealth | null>(null);
  /** 加载中 / 错误状态 */
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** 当前选中的后端（Python REST / Go gRPC） */
  const [backend, setBackend] = useState<BackendOption>(DEFAULT_BACKEND);

  /** 并行拉取示例与健状态；失败时记录错误并重置视图。 */
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

  // 后端切换时：更新 API 基址并重新拉取数据。
  useEffect(() => {
    setBaseUrl(backend.value);
    load();
  }, [backend, load]);

  /** 当前选中的端点示例（仅 endpoint 视图非空）。 */
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
              onFileTest={() => setView({ type: 'filetest' })}
              fileTestActive={view.type === 'filetest'}
              onLbTest={() => setView({ type: 'lbtest' })}
              lbTestActive={view.type === 'lbtest'}
            />
            <main className="flex-1 overflow-hidden">
              {/* 根据 view 类型渲染对应视图；EndpointView 用 key 强制在
                  切换端点时重建组件，避免上一个端点的状态残留。 */}
              {view.type === 'endpoint' ? (
                <EndpointView
                  key={`${view.sample.method}-${view.sample.path}`}
                  sample={view.sample}
                  onBack={goOverview}
                  agentUrl={health?.agent_url}
                />
              ) : view.type === 'batch' ? (
                <BatchTest samples={samples} onSelectSample={openEndpoint} />
              ) : view.type === 'filetest' ? (
                <FileTest />
              ) : view.type === 'lbtest' ? (
                <LbTest agentUrl={health?.agent_url} />
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
