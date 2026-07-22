export interface EndpointSample {
  method: string;
  path: string;
  label: string;
  category: string;
  description: string;
  body?: Record<string, any> | null;
  contentType?: string | null;
  rawPayloadB64?: string | null;
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
