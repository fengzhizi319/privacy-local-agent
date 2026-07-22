import type { HistoryEntry } from '@/types/api';

/**
 * 请求历史记录：持久化到 localStorage，供 EndpointView 的"历史"面板使用。
 * 仅保存请求体文本与状态码，不保存响应（避免存储过大）。
 */

const STORAGE_KEY = 'privacy-console.history';
const MAX_ENTRIES = 50;

export function loadHistory(): HistoryEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as HistoryEntry[]) : [];
  } catch {
    return [];
  }
}

function saveHistory(entries: HistoryEntry[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    /* 存储不可用（如隐私模式）时静默降级 */
  }
}

/** 新增一条历史，置顶并截断到 MAX_ENTRIES。 */
export function addHistory(entry: Omit<HistoryEntry, 'id' | 'timestamp'>): HistoryEntry[] {
  const full: HistoryEntry = {
    ...entry,
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    timestamp: Date.now(),
  };
  const next = [full, ...loadHistory()].slice(0, MAX_ENTRIES);
  saveHistory(next);
  return next;
}

/** 按 id 删除一条历史。 */
export function removeHistory(id: string): HistoryEntry[] {
  const next = loadHistory().filter((e) => e.id !== id);
  saveHistory(next);
  return next;
}

/** 清空全部历史。 */
export function clearHistory(): HistoryEntry[] {
  saveHistory([]);
  return [];
}

/** 相对时间展示：刚刚 / N 分钟前 / N 小时前 / 日期。 */
export function formatRelativeTime(ts: number): string {
  const diff = Date.now() - ts;
  const min = Math.floor(diff / 60000);
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟前`;
  const hour = Math.floor(min / 60);
  if (hour < 24) return `${hour} 小时前`;
  return new Date(ts).toLocaleDateString();
}
