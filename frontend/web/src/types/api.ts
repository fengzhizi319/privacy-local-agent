export interface EndpointSample {
  method: string;
  path: string;
  label: string;
  category: string;
  description: string;
  body?: Record<string, any> | null;
  contentType?: string | null;
  rawPayloadB64?: string | null;
  backend?: "rest" | "grpc" | "both";
}

export interface ProxyRequest {
  method: string;
  path: string;
  body?: Record<string, any> | null;
  raw_payload_b64?: string | null;
  content_type?: string | null;
}

export interface ProxyResponse {
  status: number;
  duration_ms: number;
  data: any;
}

export interface ConsoleHealth {
  backend: string;
  agent: string | Record<string, any>;
  agent_url: string;
  latency_ms?: number;
  error?: string;
}

/** 批量测试：单个请求项。 */
export interface BatchRequestItem {
  method: string;
  path: string;
  body?: Record<string, any> | null;
}

/** 批量测试：单个结果项。 */
export interface BatchResultItem {
  method: string;
  path: string;
  status: number;
  duration_ms: number;
  data?: any;
  error?: string | null;
}

/** 批量测试：汇总响应。 */
export interface BatchResponse {
  total: number;
  passed: number;
  failed: number;
  results: BatchResultItem[];
}

/** 请求历史记录（存于 localStorage）。 */
export interface HistoryEntry {
  id: string;
  method: string;
  path: string;
  /** 请求体 JSON 文本 */
  body: string;
  /** 响应状态码（0 表示网络错误） */
  status: number;
  timestamp: number;
}
