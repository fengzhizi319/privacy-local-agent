/**
 * 后端 API 调用封装。
 *
 * 所有与后端的 HTTP 交互都集中在这里，上层组件不直接使用 fetch：
 *   - ``API_BASE`` 为可变基址，由 BackendSelector 切换后端时经 setBaseUrl 更新；
 *   - 默认值为空字符串（同源），生产环境下控制台与后端同域部署。
 *
 * 健壮性约定：
 *   - 所有请求经统一 ``request()`` 发出，附带 ``AbortController`` 超时（默认 60s）；
 *   - 非 2xx 响应统一抛出携带 ``detail`` 的 Error，由调用方展示；
 *   - 可选 API Key（与后端 ``CONSOLE_API_KEY`` 对应）：设置后为请求附加
 *     ``Authorization: Bearer`` 头，未设置则完全不影响本地开发。
 */
import type { ProxyRequest, ProxyResponse, ConsoleHealth, EndpointSample, BatchRequestItem, BatchResponse, FileOperation, UploadResponse, LbTestRequest, LbTestResponse } from '@/types/api';

/** 当前后端基址（空串表示同源）。 */
let API_BASE = '';

/** 请求超时（毫秒）。 */
const REQUEST_TIMEOUT_MS = 60_000;

/**
 * 可选控制台 API Key。默认从构建期环境变量 ``VITE_CONSOLE_API_KEY`` 读取，
 * 也可经 setApiKey 运行时注入；为空时不附加任何鉴权头。
 */
let API_KEY: string = (import.meta.env.VITE_CONSOLE_API_KEY as string | undefined) ?? '';

/** 切换后端基址；去掉尾部斜杠避免拼接出双斜杠。 */
export function setBaseUrl(baseUrl: string): void {
  API_BASE = baseUrl.replace(/\/$/, '');
}

/** 运行时设置控制台 API Key（空串表示关闭鉴权头）。 */
export function setApiKey(key: string): void {
  API_KEY = key;
}

/** 在给定请求头基础上附加可选鉴权头。 */
function buildHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...extra };
  if (API_KEY) headers['Authorization'] = `Bearer ${API_KEY}`;
  return headers;
}

/**
 * 统一请求入口：附加超时与可选鉴权头，非 2xx 抛出携带 detail 的 Error。
 *
 * - 超时通过 ``AbortController`` 实现，触发时抛出友好的“请求超时”错误；
 * - ``init.headers`` 中已显式设置的头（如 JSON 的 Content-Type）会被保留，
 *   上传 multipart 时不传 Content-Type，由浏览器自动生成带 boundary 的头。
 */
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { headers, ...rest } = init;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...rest,
      headers: buildHeaders(headers as Record<string, string> | undefined),
      signal: controller.signal,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(typeof err.detail === 'string' ? err.detail : JSON.stringify(err));
    }

    return (await res.json()) as T;
  } catch (e) {
    if ((e as Error).name === 'AbortError') {
      throw new Error(`请求超时（${REQUEST_TIMEOUT_MS / 1000}s），请检查后端是否可达`);
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

/** 获取后端与 agent 的连通性状态。 */
export async function fetchHealth(): Promise<ConsoleHealth> {
  return request<ConsoleHealth>('/api/health');
}

/** 获取所有端点示例列表（后端 /api/samples 返回 { samples: [...] }）。 */
export async function fetchSamples(): Promise<EndpointSample[]> {
  const data = await request<{ samples: EndpointSample[] }>('/api/samples');
  return data.samples;
}

/**
 * 通用代理：把单个请求交给后端转发到 agent。
 * 后端返回非 2xx 时抛出 Error（携带 detail），由调用方展示。
 */
export async function proxyRequest(req: ProxyRequest): Promise<ProxyResponse> {
  return request<ProxyResponse>('/api/proxy', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

/**
 * 批量测试：将一组请求提交给后端逐个转发，返回汇总结果。
 * 单个请求失败不会中断整个批次。
 */
export async function batchRequest(requests: BatchRequestItem[]): Promise<BatchResponse> {
  return request<BatchResponse>('/api/batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ requests }),
  });
}

/**
 * 数据文件隐私处理：以 multipart 上传文件并指定操作类型。
 * 后端转发到 agent 的 process_file 端点，返回包装后的处理结果。
 * 注意：不手动设置 Content-Type，由浏览器自动生成带 boundary 的 multipart 头。
 */
export async function uploadFile(
  file: File,
  operation: FileOperation,
  params: Record<string, unknown>,
): Promise<UploadResponse> {
  const form = new FormData();
  form.append('file', file);
  form.append('operation', operation);
  form.append('params', JSON.stringify(params));

  return request<UploadResponse>('/api/upload', {
    method: 'POST',
    body: form,
  });
}

/**
 * 负载均衡测试：提交多个后端地址与策略，
 * 后端按策略分发探测请求并返回各节点统计。
 */
export async function lbTest(req: LbTestRequest): Promise<LbTestResponse> {
  return request<LbTestResponse>('/api/lb_test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}
