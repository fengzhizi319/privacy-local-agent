/**
 * 前后端数据契约（TypeScript 类型定义）。
 *
 * 本文件与后端 Pydantic 模型一一对应，是前后端的“单一事实来源”：
 *   - 字段命名保持一致（示例用 camelCase，代理转发用 snake_case）；
 *   - 修改任何接口时，需同步更新本文件与后端模型。
 */

/** 单个端点示例（来自后端 /api/samples）。 */
export interface EndpointSample {
  method: string;
  path: string;
  /** UI 展示的简短名称 */
  label: string;
  /** 功能分类（侧边栏分组依据，如 Masking / DP） */
  category: string;
  /** 中文功能描述 */
  description: string;
  /** 默认 JSON 请求体 */
  body?: Record<string, any> | null;
  /** 二进制载荷的 Content-Type（如 Arrow IPC） */
  contentType?: string | null;
  /** 二进制载荷的 base64 编码 */
  rawPayloadB64?: string | null;
  /** 可用性标识：rest 仅 Python 后端，both 两后端都支持 */
  backend?: "rest" | "grpc" | "both";
}

/** 通用代理请求体（发往 /api/proxy）。 */
export interface ProxyRequest {
  method: string;
  path: string;
  body?: Record<string, any> | null;
  raw_payload_b64?: string | null;
  content_type?: string | null;
}

/** 通用代理统一响应包装。 */
export interface ProxyResponse {
  status: number;
  /** 转发耗时（毫秒） */
  duration_ms: number;
  data: any;
}

/** 后端健康检查响应（/api/health）。 */
export interface ConsoleHealth {
  backend: string;
  /** agent 健康信息；不可达时为字符串 "unreachable" */
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
