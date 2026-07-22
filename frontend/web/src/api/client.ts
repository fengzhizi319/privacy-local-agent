import type { ProxyRequest, ProxyResponse, ConsoleHealth, EndpointSample, BatchRequestItem, BatchResponse } from '@/types/api';

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

/**
 * 批量测试：将一组请求提交给后端逐个转发，返回汇总结果。
 * 单个请求失败不会中断整个批次。
 */
export async function batchRequest(requests: BatchRequestItem[]): Promise<BatchResponse> {
  const res = await fetch(`${API_BASE}/api/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ requests }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(typeof err.detail === 'string' ? err.detail : JSON.stringify(err));
  }

  return res.json();
}
