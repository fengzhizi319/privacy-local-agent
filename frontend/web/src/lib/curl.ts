/**
 * cURL 命令生成：将当前请求导出为可直接执行的 cURL 命令，
 * 目标为 privacy-local-agent 的 REST API（默认 http://127.0.0.1:8079）。
 */

/** 对 shell 单引号转义，保证生成的 cURL 可安全粘贴执行。 */
function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

export interface CurlOptions {
  method: string;
  path: string;
  /** 请求体 JSON 文本（GET 或空则忽略） */
  body?: string;
  /** agent REST 基础地址，默认 http://127.0.0.1:8079 */
  baseUrl?: string;
}

export function buildCurl({ method, path, body, baseUrl = 'http://127.0.0.1:8079' }: CurlOptions): string {
  const base = baseUrl.replace(/\/$/, '');
  const url = `${base}${path.startsWith('/') ? path : `/${path}`}`;
  const parts: string[] = ['curl'];

  const m = method.toUpperCase();
  if (m !== 'GET') {
    parts.push('-X', m);
  }

  parts.push(shellQuote(url));

  const trimmed = body?.trim();
  if (m !== 'GET' && trimmed && trimmed !== '{}') {
    parts.push('-H', shellQuote('Content-Type: application/json'));
    parts.push('-d', shellQuote(trimmed));
  }

  return parts.join(' ');
}

/**
 * 从健康信息推断 agent REST 基础地址。
 * Python 后端的 agent_url 形如 http://127.0.0.1:8079，可直接使用；
 * Go 后端的 agent_url 是 gRPC 地址（如 127.0.0.1:50051），回退到默认 REST 地址。
 */
export function deriveAgentBaseUrl(agentUrl?: string): string {
  if (agentUrl && /^https?:\/\//i.test(agentUrl)) {
    return agentUrl;
  }
  return 'http://127.0.0.1:8079';
}
