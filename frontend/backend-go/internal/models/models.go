// Package models defines the request/response structures shared between
// the frontend and the Go gRPC proxy backend.
//
// 中文说明：
// 这些结构体与 Python 后端保持一致，使前端可以用同一套 JSON 契约
// 与 Python REST 代理或 Go gRPC 代理通信。
package models

import "encoding/json"

// EndpointSample describes one testable endpoint together with a sample payload.
//
// backend 字段用于前端过滤：
//   - "rest"：仅 Python REST 后端支持
//   - "grpc"：仅 Go gRPC 后端支持
//   - "both"：两者均支持
type EndpointSample struct {
	Method        string          `json:"method"`
	Path          string          `json:"path"`
	Label         string          `json:"label"`
	Category      string          `json:"category"`
	Description   string          `json:"description"`
	Body          json.RawMessage `json:"body,omitempty"`
	ContentType   string          `json:"contentType,omitempty"`
	RawPayloadB64 string          `json:"rawPayloadB64,omitempty"`
	Backend       string          `json:"backend"`
}

// ProxyRequest is the JSON body sent by the frontend to /api/proxy.
type ProxyRequest struct {
	Method        string          `json:"method"`
	Path          string          `json:"path"`
	Body          json.RawMessage `json:"body,omitempty"`
	RawPayloadB64 string          `json:"raw_payload_b64,omitempty"`
	ContentType   string          `json:"content_type,omitempty"`
}

// ProxyResponse is the unified JSON wrapper returned by /api/proxy.
type ProxyResponse struct {
	Status     int    `json:"status"`
	DurationMs int64  `json:"duration_ms"`
	Data       any    `json:"data"`
}

// ConsoleHealth is returned by GET /api/health.
type ConsoleHealth struct {
	Backend   string `json:"backend"`
	Agent     any    `json:"agent"`
	AgentURL  string `json:"agent_url"`
	LatencyMs *int64 `json:"latency_ms,omitempty"`
	Error     string `json:"error,omitempty"`
}

// SamplesResponse wraps the list of endpoint samples.
type SamplesResponse struct {
	Samples []EndpointSample `json:"samples"`
}
