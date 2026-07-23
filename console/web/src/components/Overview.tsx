/**
 * 接口总览页：控制台的首页。
 *
 * 以分类卡片网格展示全部功能模块，点击卡片进入该分类的第一个接口。
 */
import { useMemo } from 'react';
import type { EndpointSample } from '@/types/api';
import { categoryMeta, orderCategories } from '@/lib/categories';
import { Icon } from '@/components/icons';

interface OverviewProps {
  samples: EndpointSample[];
  onSelect: (sample: EndpointSample) => void;
}

/**
 * 概览页：以分类卡片网格展示全部功能模块，点击卡片进入该分类的第一个接口。
 * 相比平铺长列表，提供更清晰的导航入口。
 */
export default function Overview({ samples, onSelect }: OverviewProps) {
  const grouped = useMemo(() => {
    const map = new Map<string, EndpointSample[]>();
    for (const s of samples) {
      const list = map.get(s.category) || [];
      list.push(s);
      map.set(s.category, list);
    }
    return map;
  }, [samples]);

  const categories = orderCategories([...grouped.keys()]);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-8 py-10">
        {/* 标题区 */}
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-gray-900">接口总览</h1>
          <p className="mt-1.5 text-sm text-gray-500">
            共 {samples.length} 个接口 · {categories.length} 个功能模块，点击卡片开始测试
          </p>
        </div>

        {/* 分类卡片网格 */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {categories.map((category) => {
            const meta = categoryMeta(category);
            const list = grouped.get(category)!;
            const first = list[0];
            return (
              <button
                key={category}
                onClick={() => onSelect(first)}
                className="group flex flex-col overflow-hidden rounded-xl border border-gray-200 bg-white text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-md"
              >
                {/* 顶部渐变色条 */}
                <div className={`h-1 bg-gradient-to-r ${meta.accent}`} />
                <div className="flex flex-1 flex-col p-4">
                  <div className="flex items-center gap-3">
                    <span
                      className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${meta.chip}`}
                    >
                      <Icon name={meta.icon} className="h-5 w-5" />
                    </span>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <h2 className="truncate text-sm font-semibold text-gray-900">{category}</h2>
                        <span className="shrink-0 rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">
                          {list.length}
                        </span>
                      </div>
                      <p className="mt-0.5 truncate text-xs text-gray-400">{meta.desc}</p>
                    </div>
                  </div>

                  {/* 前 3 个接口预览 */}
                  <ul className="mt-3 space-y-1 border-t border-gray-100 pt-3">
                    {list.slice(0, 3).map((s) => (
                      <li
                        key={`${s.method}-${s.path}`}
                        className="flex items-center gap-2 text-xs text-gray-500"
                      >
                        <span
                          className={`w-9 shrink-0 rounded px-0.5 py-px text-center text-[9px] font-bold ${
                            s.method === 'GET'
                              ? 'bg-emerald-50 text-emerald-600'
                              : 'bg-sky-50 text-sky-600'
                          }`}
                        >
                          {s.method}
                        </span>
                        <span className="truncate">{s.label}</span>
                      </li>
                    ))}
                    {list.length > 3 && (
                      <li className="text-[11px] text-gray-400">+{list.length - 3} 个更多…</li>
                    )}
                  </ul>

                  <div className="mt-auto flex items-center gap-1 pt-3 text-xs font-medium text-indigo-600 opacity-0 transition-opacity group-hover:opacity-100">
                    进入测试
                    <Icon name="chevron-right" className="h-3.5 w-3.5" />
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
