/**
 * 后端 API 调用封装。
 *
 * 所有与后端的 HTTP 交互都集中在这里，上层组件不直接使用 fetch：
 *   - ``API_BASE`` 为可变基址，由 BackendSelector 切换后端时经 setBaseUrl 更新；
 *   - 默认值为空字符串（同源），生产环境下控制台与后端同域部署。
 */
import type { ProxyRequest, ProxyResponse, ConsoleHealth, EndpointSample, BatchRequestItem, BatchResponse } from '@/types/api';

/** 当前后端基址（空串表示同源）。 */
let API_BASE = '';

/** 切换后端基址；去掉尾部斜杠避免拼接出双斜杠。 */
export function setBaseUrl(baseUrl: string): void {
  API_BASE = baseUrl.replace(/\/$/, '');
}

/** 获取后端与 agent 的连通性状态。 */
export async function fetchHealth(): Promise<ConsoleHealth> {
  const res = await fetch(`${API_BASE}/api/health`);
  return res.json();
}

/** 获取所有端点示例列表（后端 /api/samples 返回 { samples: [...] }）。 */
export async function fetchSamples(): Promise<EndpointSample[]> {
  const res = await fetch(`${API_BASE}/api/samples`);
  const data = await res.json();
  return data.samples as EndpointSample[];
}

/**
 * 通用代理：把单个请求交给后端转发到 agent。
 * 后端返回非 2xx 时抛出 Error（携带 detail），由调用方展示。
 */
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
