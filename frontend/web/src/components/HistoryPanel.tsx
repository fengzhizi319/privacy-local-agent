import type { HistoryEntry } from '@/types/api';
import { formatRelativeTime } from '@/lib/history';
import { Icon } from '@/components/icons';

interface HistoryPanelProps {
  /** 已按当前端点过滤的历史记录 */
  entries: HistoryEntry[];
  onRestore: (body: string) => void;
  onDelete: (id: string) => void;
  onClear: () => void;
  onClose: () => void;
}

/** 状态码徽章配色。 */
function statusBadge(status: number): string {
  if (status === 0) return 'bg-gray-100 text-gray-500';
  if (status >= 200 && status < 300) return 'bg-emerald-50 text-emerald-600';
  return 'bg-red-50 text-red-600';
}

/**
 * 请求历史面板：以右侧滑出层展示当前端点的历史请求，
 * 点击条目可快速回填请求体，支持单条删除与一键清空。
 */
export default function HistoryPanel({ entries, onRestore, onDelete, onClear, onClose }: HistoryPanelProps) {
  return (
    <div className="absolute inset-0 z-10 flex flex-col bg-white">
      {/* 面板头 */}
      <div className="flex items-center justify-between border-b border-gray-100 px-4 py-2.5">
        <span className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-gray-500">
          <Icon name="clock" className="h-3.5 w-3.5" />
          请求历史（{entries.length}）
        </span>
        <div className="flex items-center gap-1">
          {entries.length > 0 && (
            <button
              onClick={onClear}
              className="rounded-md px-2 py-1 text-xs text-gray-400 transition-colors hover:bg-red-50 hover:text-red-600"
            >
              清空
            </button>
          )}
          <button
            onClick={onClose}
            className="flex h-6 w-6 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
            title="关闭"
          >
            <Icon name="x" className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* 历史列表 */}
      <div className="flex-1 overflow-y-auto p-2">
        {entries.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-gray-300">
            <Icon name="clock" className="h-8 w-8" strokeWidth={1.5} />
            <p className="text-xs text-gray-400">暂无历史记录</p>
          </div>
        ) : (
          <ul className="space-y-1">
            {entries.map((entry) => (
              <li key={entry.id} className="group relative">
                <button
                  onClick={() => onRestore(entry.body)}
                  className="w-full rounded-lg border border-gray-100 bg-gray-50/50 px-3 py-2 pr-9 text-left transition-colors hover:border-indigo-200 hover:bg-indigo-50/40"
                  title="点击回填该请求体"
                >
                  <div className="flex items-center gap-2">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${statusBadge(entry.status)}`}>
                      {entry.status === 0 ? 'ERR' : entry.status}
                    </span>
                    <span className="text-[11px] text-gray-400">{formatRelativeTime(entry.timestamp)}</span>
                  </div>
                  <p className="mt-1 truncate font-mono text-[11px] leading-relaxed text-gray-500">
                    {entry.body || '(空)'}
                  </p>
                </button>
                <button
                  onClick={() => onDelete(entry.id)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-gray-300 opacity-0 transition-all hover:bg-red-50 hover:text-red-500 group-hover:opacity-100"
                  title="删除该记录"
                >
                  <Icon name="trash" className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
