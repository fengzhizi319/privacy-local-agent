/**
 * 侧边栏：接口导航树。
 *
 * 按分类分组展示全部接口，支持搜索过滤与分组折叠/展开；
 * 顶部提供“接口总览”与“批量测试”两个快捷入口。
 */
import { useEffect, useMemo, useState } from 'react';
import type { EndpointSample } from '@/types/api';
import { categoryMeta, orderCategories } from '@/lib/categories';
import { Icon } from '@/components/icons';

interface SidebarProps {
  samples: EndpointSample[];
  selected: EndpointSample | null;
  onSelect: (sample: EndpointSample) => void;
  /** 返回总览页 */
  onHome?: () => void;
  /** 进入批量测试 */
  onBatch?: () => void;
  /** 当前是否处于批量测试视图 */
  batchActive?: boolean;
}

/** method 徽章配色。 */
function methodBadge(method: string): string {
  switch (method.toUpperCase()) {
    case 'GET':
      return 'bg-emerald-50 text-emerald-600';
    case 'POST':
      return 'bg-sky-50 text-sky-600';
    default:
      return 'bg-gray-100 text-gray-500';
  }
}

function groupSamples(samples: EndpointSample[]): Map<string, EndpointSample[]> {
  const grouped = new Map<string, EndpointSample[]>();
  for (const s of samples) {
    const list = grouped.get(s.category) || [];
    list.push(s);
    grouped.set(s.category, list);
  }
  return grouped;
}

export default function Sidebar({ samples, selected, onSelect, onHome, onBatch, batchActive = false }: SidebarProps) {
  const [query, setQuery] = useState('');
  // 默认全部折叠，避免首页侧边栏过长；用户点击分组头展开。
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const grouped = useMemo(() => groupSamples(samples), [samples]);
  const categories = useMemo(() => orderCategories([...grouped.keys()]), [grouped]);

  // 搜索过滤：匹配 label / path / category
  const q = query.trim().toLowerCase();
  const filtered = useMemo(() => {
    if (!q) return grouped;
    const map = new Map<string, EndpointSample[]>();
    for (const [cat, list] of grouped) {
      const hits = list.filter(
        (s) =>
          s.label.toLowerCase().includes(q) ||
          s.path.toLowerCase().includes(q) ||
          cat.toLowerCase().includes(q),
      );
      if (hits.length > 0) map.set(cat, hits);
    }
    return map;
  }, [grouped, q]);

  // 选中项变化时（包括从总览卡片进入）自动展开所属分组，
  // 保证选中接口在侧边栏中始终可见。
  useEffect(() => {
    if (selected) {
      setExpanded((prev) =>
        prev.has(selected.category) ? prev : new Set(prev).add(selected.category),
      );
    }
  }, [selected]);

  const toggle = (cat: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  const visibleCategories = categories.filter((c) => filtered.has(c));

  return (
    <aside className="flex w-72 shrink-0 flex-col border-r border-gray-200 bg-white">
      {/* 搜索框 */}
      <div className="border-b border-gray-100 p-3">
        <label className="relative block">
          <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400">
            <Icon name="search" className="h-3.5 w-3.5" />
          </span>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索接口…"
            className="w-full rounded-lg border border-gray-200 bg-gray-50 py-1.5 pl-8 pr-3 text-sm text-gray-700 placeholder-gray-400 transition-colors focus:border-indigo-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-100"
          />
        </label>
      </div>

      {/* 分组列表 */}
      <nav className="flex-1 overflow-y-auto px-2 py-2">
        {/* 总览入口 */}
        <button
          onClick={onHome}
          className={[
            'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors',
            !selected && !batchActive
              ? 'bg-indigo-50 font-medium text-indigo-700'
              : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900',
          ].join(' ')}
        >
          <span className="flex h-5 w-5 items-center justify-center rounded bg-gray-100 text-gray-500">
            <Icon name="inbox" className="h-3 w-3" />
          </span>
          接口总览
        </button>
        {/* 批量测试入口 */}
        <button
          onClick={onBatch}
          className={[
            'mb-2 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors',
            batchActive
              ? 'bg-indigo-50 font-medium text-indigo-700'
              : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900',
          ].join(' ')}
        >
          <span className="flex h-5 w-5 items-center justify-center rounded bg-gray-100 text-gray-500">
            <Icon name="play" className="h-3 w-3" />
          </span>
          批量测试
        </button>
        {visibleCategories.length === 0 && (
          <div className="px-3 py-8 text-center text-sm text-gray-400">
            未找到匹配的接口
          </div>
        )}
        {visibleCategories.map((category) => {
          const meta = categoryMeta(category);
          const list = filtered.get(category)!;
          // 搜索时强制展开命中分组；否则尊重用户的折叠状态
          const isCollapsed = q ? false : !expanded.has(category);
          return (
            <div key={category} className="mb-2.5">
              <button
                onClick={() => toggle(category)}
                className="flex w-full items-center gap-2 rounded-md border border-gray-300 bg-gray-100 px-2 py-1.5 text-left transition-colors hover:border-gray-400 hover:bg-gray-200"
              >
                <span className="text-gray-500">
                  <Icon
                    name={isCollapsed ? 'chevron-right' : 'chevron-down'}
                    className="h-3.5 w-3.5"
                  />
                </span>
                <span className={`flex h-5 w-5 items-center justify-center rounded ${meta.chip}`}>
                  <Icon name={meta.icon} className="h-3 w-3" />
                </span>
                <span className="flex-1 truncate text-xs font-semibold uppercase tracking-wide text-gray-600">
                  {category}
                </span>
                <span className="rounded-full border border-gray-200 bg-white px-1.5 py-0.5 text-[10px] font-medium text-gray-500">
                  {list.length}
                </span>
              </button>

              {!isCollapsed && (
                <ul className="mt-1 space-y-0.5 pl-3">
                  {list.map((sample) => {
                    const isActive =
                      selected?.path === sample.path && selected?.method === sample.method;
                    return (
                      <li key={`${sample.method}-${sample.path}`}>
                        <button
                          onClick={() => onSelect(sample)}
                          className={[
                            'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors',
                            isActive
                              ? 'bg-indigo-50 font-medium text-indigo-700'
                              : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900',
                          ].join(' ')}
                        >
                          <span
                            className={`w-10 shrink-0 rounded px-1 py-0.5 text-center text-[10px] font-bold ${methodBadge(sample.method)}`}
                          >
                            {sample.method}
                          </span>
                          <span className="flex-1 truncate">{sample.label}</span>
                          {sample.backend === 'rest' && (
                            <span
                              className="shrink-0 rounded bg-amber-50 px-1 py-0.5 text-[9px] font-semibold uppercase text-amber-600"
                              title="仅 Python REST 后端支持"
                            >
                              REST
                            </span>
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
