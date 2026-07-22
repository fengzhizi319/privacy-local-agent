import { setBaseUrl } from '@/api/client';

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
    <select
      value={value.value}
      onChange={handleChange}
      className="text-xs bg-indigo-800 text-white border border-indigo-600 rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-white"
    >
      {DEFAULT_BACKENDS.map((b) => (
        <option key={b.value} value={b.value}>
          {b.label}
        </option>
      ))}
    </select>
  );
}
