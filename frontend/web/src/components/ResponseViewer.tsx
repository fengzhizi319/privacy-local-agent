import type { ProxyResponse } from '@/types/api';

interface ResponseViewerProps {
  response: ProxyResponse | null;
  error: string | null;
  duration: number | null;
}

export default function ResponseViewer({ response, error, duration }: ResponseViewerProps) {
  if (!response && !error) {
    return (
      <div className="text-sm text-gray-400 italic">
        Send a request to see the response here.
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded p-4">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-red-700">Error</h3>
          {duration !== null && (
            <span className="text-xs text-gray-500">{duration.toFixed(1)} ms</span>
          )}
        </div>
        <pre className="text-sm text-red-700 font-mono">{error}</pre>
      </div>
    );
  }

  return (
    <div className="bg-green-50 border border-green-200 rounded p-4">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-green-700">
          Response {response?.status ? `(${response.status})` : ''}
        </h3>
        <div className="text-xs text-gray-500">
          {response?.duration_ms !== undefined && `${response.duration_ms.toFixed(2)} ms`}
          {duration !== null && response?.duration_ms === undefined && `${duration.toFixed(1)} ms`}
        </div>
      </div>
      <pre className="text-sm text-gray-800 font-mono bg-white border border-gray-200 rounded p-3 max-h-96 overflow-y-auto">
        {JSON.stringify(response?.data, null, 2)}
      </pre>
    </div>
  );
}
