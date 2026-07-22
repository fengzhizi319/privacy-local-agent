import { setBaseUrl } from '@/api/client';

export interface BackendOption {
  label: string;
  value: string;
}

export const DEFAULT_BACKENDS: BackendOption[] = [
  { label: 'Python REST (8080)', value: 'http://127.0.0.1:8080' },
  { label: 'Go gRPC (8081)', value: 'http://127.0.0.1:8081' },
];

export const DEFAULT_BACKEND = DEFAULT_BACKENDS[0];

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
