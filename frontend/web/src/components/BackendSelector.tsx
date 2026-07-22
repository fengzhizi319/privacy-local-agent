import { setBaseUrl } from '@/api/client';
import { Icon } from '@/components/icons';

export interface BackendOption {
  label: string;
  value: string;
}

export const DEFAULT_BACKENDS: BackendOption[] = [
  { label: 'Python REST (8080)', value: 'http://127.0.0.1:8080' },
  { label: 'Go gRPC (8081)', value: 'http://127.0.0.1:8081' },
];

// 默认后端：优先选择与当前页面同源的选项（页面由哪个后端提供服务，
// 就默认调用哪个后端）。例如由 Go 后端 (8081) 提供 UI 时默认选中 Go gRPC；
// Vite 开发模式等其他来源则回退到 Python REST。
export const DEFAULT_BACKEND: BackendOption =
  DEFAULT_BACKENDS.find((b) => b.value === window.location.origin) ?? DEFAULT_BACKENDS[0];

interface BackendSelectorProps {
  value?: BackendOption;
  onChange?: (option: BackendOption) => void;
}

export default function BackendSelector({
  value = DEFAULT_BACKEND,
  onChange,
}: BackendSelectorProps) {
  const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const option = DEFAULT_BACKENDS.find((b) => b.value === e.target.value);
    if (!option) return;
    setBaseUrl(option.value);
    onChange?.(option);
  };

  return (
    <label className="relative inline-flex items-center">
      <span className="pointer-events-none absolute left-2.5 text-gray-400">
        <Icon name="server" className="h-3.5 w-3.5" />
      </span>
      <select
        value={value.value}
        onChange={handleChange}
        className="appearance-none rounded-lg border border-gray-200 bg-gray-50 py-1.5 pl-8 pr-8 text-xs font-medium text-gray-700 transition-colors hover:border-gray-300 focus:outline-none focus:ring-2 focus:ring-indigo-500"
      >
        {DEFAULT_BACKENDS.map((b) => (
          <option key={b.value} value={b.value}>
            {b.label}
          </option>
        ))}
      </select>
      <span className="pointer-events-none absolute right-2.5 text-gray-400">
        <Icon name="chevron-down" className="h-3.5 w-3.5" />
      </span>
    </label>
  );
}
