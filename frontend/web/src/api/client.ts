import type { ProxyRequest, ProxyResponse, ConsoleHealth, EndpointSample } from '@/types/api';

let API_BASE = '';

export function setBaseUrl(baseUrl: string): void {
  API_BASE = baseUrl.replace(/\/$/, '');
}

export async function fetchHealth(): Promise<ConsoleHealth> {
  const res = await fetch(`${API_BASE}/api/health`);
  return res.json();
}

export async function fetchSamples(): Promise<EndpointSample[]> {
  const res = await fetch(`${API_BASE}/api/samples`);
  const data = await res.json();
  return data.samples as EndpointSample[];
}

export async function proxyRequest(req: ProxyRequest): Promise<ProxyResponse> {
  const res = await fetch(`${API_BASE}/api/proxy`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(typeof err.detail === 'string' ? err.detail : JSON.stringify(err));
  }

  return res.json();
}
