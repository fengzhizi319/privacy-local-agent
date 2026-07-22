import { useState, useEffect } from 'react';
import type { EndpointSample, ProxyResponse } from '@/types/api';
import { proxyRequest } from '@/api/client';
import ResponseViewer from './ResponseViewer';

interface RequestFormProps {
  sample: EndpointSample;
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export default function RequestForm({ sample }: RequestFormProps) {
  const [path, setPath] = useState(sample.path);
  const [method, setMethod] = useState(sample.method);
  const [bodyText, setBodyText] = useState(formatJson(sample.body ?? {}));
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<ProxyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [duration, setDuration] = useState<number | null>(null);

  useEffect(() => {
    setPath(sample.path);
    setMethod(sample.method);
    setBodyText(formatJson(sample.body ?? {}));
    setResponse(null);
    setError(null);
    setDuration(null);
  }, [sample]);

  const handleSend = async () => {
    setLoading(true);
    setError(null);
    setResponse(null);
    setDuration(null);

    const start = performance.now();
    try {
      let body: Record<string, any> | undefined;
      if (method !== 'GET' && bodyText.trim()) {
        try {
          body = JSON.parse(bodyText);
        } catch (e) {
          setError(`Request body JSON parse error: ${(e as Error).message}`);
          setLoading(false);
          return;
        }
      }

      const req = {
        method,
        path,
        body: body ?? null,
        raw_payload_b64: sample.rawPayloadB64 ?? null,
        content_type: sample.contentType ?? null,
      };

      const res = await proxyRequest(req);
      setResponse(res);
      setDuration(performance.now() - start);
    } catch (e) {
      setError((e as Error).message);
      setDuration(performance.now() - start);
    } finally {
      setLoading(false);
    }
  };

  const handleLoadSample = () => {
    setPath(sample.path);
    setMethod(sample.method);
    setBodyText(formatJson(sample.body ?? {}));
    setResponse(null);
    setError(null);
  };

  return (
    <div className="flex flex-col h-full">
      <div className="p-4 border-b border-gray-200 bg-white">
        <div className="flex items-center gap-3 mb-2">
          <span
            className={[
              'px-2 py-1 rounded text-xs font-semibold',
              method === 'GET'
                ? 'bg-green-100 text-green-700'
                : 'bg-blue-100 text-blue-700',
            ].join(' ')}
          >
            {method}
          </span>
          <input
            type="text"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
        </div>
        <p className="text-sm text-gray-600">{sample.description}</p>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-gray-700">Request Body</h3>
          <button
            onClick={handleLoadSample}
            className="text-xs px-3 py-1.5 bg-gray-100 hover:bg-gray-200 rounded text-gray-700"
          >
            Reload Sample
          </button>
        </div>
        <textarea
          value={bodyText}
          onChange={(e) => setBodyText(e.target.value)}
          disabled={method === 'GET'}
          className={[
            'w-full h-64 font-mono text-sm border border-gray-300 rounded p-3 focus:outline-none focus:ring-2 focus:ring-indigo-500',
            method === 'GET' ? 'bg-gray-100 text-gray-400' : 'bg-white',
          ].join(' ')}
          spellCheck={false}
        />
        {sample.contentType && (
          <p className="text-xs text-gray-500 mt-2">
            Content-Type: {sample.contentType} (binary payload handled by backend)
          </p>
        )}

        <div className="mt-4">
          <button
            onClick={handleSend}
            disabled={loading}
            className={[
              'px-6 py-2 rounded text-white font-medium',
              loading
                ? 'bg-indigo-400 cursor-not-allowed'
                : 'bg-indigo-600 hover:bg-indigo-700',
            ].join(' ')}
          >
            {loading ? 'Sending...' : 'Send Request'}
          </button>
        </div>

        <div className="mt-6">
          <ResponseViewer response={response} error={error} duration={duration} />
        </div>
      </div>
    </div>
  );
}
