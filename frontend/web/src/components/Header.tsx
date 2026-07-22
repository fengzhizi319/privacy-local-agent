import type { ConsoleHealth } from '@/types/api';
import type { BackendOption } from '@/components/BackendSelector';
import BackendSelector from '@/components/BackendSelector';
import { Icon } from '@/components/icons';

interface HeaderProps {
  backend: BackendOption;
  onBackendChange: (option: BackendOption) => void;
  health: ConsoleHealth | null;
  loading: boolean;
  /** 点击 logo 返回总览页 */
  onHome?: () => void;
}

/** 健康状态徽章：绿色圆点表示正常，红色表示不可达。 */
function HealthPill({ health, loading }: { health: ConsoleHealth | null; loading: boolean }) {
  if (loading && !health) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-500">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-gray-400" />
        检测中…
      </span>
    );
  }
  if (!health) return null;

  const ok = !health.error;
  return (
    <span
      className={[
        'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium',
        ok ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700',
      ].join(' ')}
      title={health.agent_url}
    >
      <span className={['h-1.5 w-1.5 rounded-full', ok ? 'bg-emerald-500' : 'bg-red-500'].join(' ')} />
      Agent {ok ? '正常' : '不可达'}
    </span>
  );
}

export default function Header({ backend, onBackendChange, health, loading, onHome }: HeaderProps) {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-gray-200 bg-white px-4">
      <button
        onClick={onHome}
        className="group flex items-center gap-3 rounded-lg px-1 py-1 text-left transition-colors hover:bg-gray-50"
        title="返回总览"
      >
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-white shadow-sm transition-colors group-hover:bg-indigo-700">
          <Icon name="shield" className="h-5 w-5" />
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold text-gray-900">Privacy Test Console</div>
          <div className="text-[11px] text-gray-400">privacy-local-agent</div>
        </div>
      </button>

      <div className="flex items-center gap-3">
        <HealthPill health={health} loading={loading} />
        <BackendSelector value={backend} onChange={onBackendChange} />
      </div>
    </header>
  );
}
