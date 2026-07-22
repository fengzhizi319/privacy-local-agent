import type { EndpointSample } from '@/types/api';

interface SidebarProps {
  samples: EndpointSample[];
  selected: EndpointSample | null;
  onSelect: (sample: EndpointSample) => void;
}

const categoryOrder = [
  'Health',
  'Masking',
  'Hash',
  'DP',
  'LDP',
  'K-Anonymity',
  'Query Obfuscation',
  'Classification',
  'Budget',
  'Profile',
];

export default function Sidebar({ samples, selected, onSelect }: SidebarProps) {
  const grouped = new Map<string, EndpointSample[]>();
  for (const s of samples) {
    const list = grouped.get(s.category) || [];
    list.push(s);
    grouped.set(s.category, list);
  }

  const categories = categoryOrder.filter((c) => grouped.has(c));
  for (const c of grouped.keys()) {
    if (!categories.includes(c)) categories.push(c);
  }

  return (
    <aside className="w-72 bg-white border-r border-gray-200 flex flex-col h-full">
      <div className="p-4 border-b border-gray-200">
        <h1 className="text-lg font-bold text-indigo-700">Privacy Test Console</h1>
        <p className="text-xs text-gray-500 mt-1">privacy-local-agent</p>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {categories.map((category) => (
          <div key={category}>
            <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
              {category}
            </h2>
            <ul className="space-y-1">
              {grouped.get(category)!.map((sample) => {
                const isActive = selected?.path === sample.path && selected?.method === sample.method;
                return (
                  <li key={`${sample.method}-${sample.path}`}>
                    <button
                      onClick={() => onSelect(sample)}
                      className={[
                        'w-full text-left px-2 py-1.5 rounded text-sm transition-colors',
                        isActive
                          ? 'bg-indigo-50 text-indigo-700 font-medium'
                          : 'text-gray-700 hover:bg-gray-100',
                      ].join(' ')}
                    >
                      {sample.label}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </aside>
  );
}
