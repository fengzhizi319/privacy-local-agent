import { useEffect, useState } from 'react';
import type { EndpointSample, ConsoleHealth } from '@/types/api';
import { fetchSamples, fetchHealth } from '@/api/client';
import Sidebar from '@/components/Sidebar';
import RequestForm from '@/components/RequestForm';

export default function App() {
  const [samples, setSamples] = useState<EndpointSample[]>([]);
  const [selected, setSelected] = useState<EndpointSample | null>(null);
  const [health, setHealth] = useState<ConsoleHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const [samplesData, healthData] = await Promise.all([fetchSamples(), fetchHealth()]);
        setSamples(samplesData);
        setHealth(healthData);
        if (samplesData.length > 0) {
          setSelected(samplesData[0]);
        }
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  return (
    <div className="h-screen flex flex-col">
      <header className="bg-indigo-700 text-white px-4 py-2 flex items-center justify-between shadow">
        <div className="font-semibold">Privacy Local Agent Test Console</div>
        <div className="text-xs flex items-center gap-4">
          {health && (
            <>
              <span>
                Backend: <span className="text-green-300">{health.backend}</span>
              </span>
              <span>
                Agent:{' '}
                <span className={health.error ? 'text-red-300' : 'text-green-300'}>
                  {health.error ? 'unreachable' : 'ok'}
                </span>
              </span>
              <span className="text-indigo-200">{health.agent_url}</span>
            </>
          )}
          {!health && loading && <span className="text-indigo-200">Checking health...</span>}
        </div>
      </header>

      <div className="flex-1 flex overflow-hidden">
        {loading ? (
          <div className="flex-1 flex items-center justify-center text-gray-500">
            Loading endpoints...
          </div>
        ) : error ? (
          <div className="flex-1 flex items-center justify-center text-red-600">
            {error}
          </div>
        ) : (
          <>
            <Sidebar samples={samples} selected={selected} onSelect={setSelected} />
            <main className="flex-1 bg-gray-50 overflow-hidden">
              {selected ? <RequestForm sample={selected} /> : (
                <div className="h-full flex items-center justify-center text-gray-400">
                  Select an endpoint from the sidebar
                </div>
              )}
            </main>
          </>
        )}
      </div>
    </div>
  );
}
