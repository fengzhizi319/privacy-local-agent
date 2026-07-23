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
	Status     int   `json:"status"`
	DurationMs int64 `json:"duration_ms"`
	Data       any   `json:"data"`
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

// BatchRequest is the JSON body sent by the frontend to /api/batch.
//
// 中文说明：前端“一键批量测试”提交一组请求，后端逐个转发并汇总结果。
type BatchRequest struct {
	Requests []ProxyRequest `json:"requests"`
}

// BatchResultItem is the outcome of a single request within a batch.
type BatchResultItem struct {
	Method     string `json:"method"`
	Path       string `json:"path"`
	Status     int    `json:"status"`
	DurationMs int64  `json:"duration_ms"`
	Data       any    `json:"data,omitempty"`
	Error      string `json:"error,omitempty"`
}

// BatchResponse is the aggregated result returned by /api/batch.
type BatchResponse struct {
	Total   int               `json:"total"`
	Passed  int               `json:"passed"`
	Failed  int               `json:"failed"`
	Results []BatchResultItem `json:"results"`
}

// UploadData 是 /api/upload 包装在 ProxyResponse.Data 中的处理结果。
//
// 与 Python 后端保持一致：operation 为操作类型，rows_in/rows_out 为输入/输出
// 记录数，result 为具体处理结果（脱敏/K-匿名为记录数组，分类为结果对象）。
type UploadData struct {
	Operation string `json:"operation"`
	RowsIn    int    `json:"rows_in"`
	RowsOut   int    `json:"rows_out"`
	Result    any    `json:"result"`
}

// LbBackend 是负载均衡测试中的单个目标后端节点。
type LbBackend struct {
	Name string `json:"name"`
	URL  string `json:"url"`
}

// LbTestRequest 是 /api/lb_test 的请求体。
//
// 控制台后端按 Strategy 策略把 NumRequests 个探测请求分发到 Backends 中的各节点：
//   - ProbePath：探测路径，默认 /health；
//   - ProbeBody：提供时以 POST 发送该 JSON 体，否则用 GET。
type LbTestRequest struct {
	Backends    []LbBackend     `json:"backends"`
	NumRequests int             `json:"num_requests"`
	Strategy    string          `json:"strategy"`
	ProbePath   string          `json:"probe_path"`
	ProbeBody   json.RawMessage `json:"probe_body,omitempty"`
}

// LbDistItem 是负载均衡测试中单个节点的统计结果。
//
// 记录该节点被命中的次数、成功/失败数以及延迟分布（毫秒）。
type LbDistItem struct {
	Name         string  `json:"name"`
	URL          string  `json:"url"`
	Count        int     `json:"count"`
	Success      int     `json:"success"`
	Failed       int     `json:"failed"`
	AvgLatencyMs float64 `json:"avg_latency_ms"`
	MinLatencyMs float64 `json:"min_latency_ms"`
	MaxLatencyMs float64 `json:"max_latency_ms"`
}

// LbTestResponse 是 /api/lb_test 的汇总结果。
//
// Distribution 按 Backends 顺序给出各节点统计，恒有 Total == Success + Failed。
type LbTestResponse struct {
	Strategy     string       `json:"strategy"`
	Total        int          `json:"total"`
	Success      int          `json:"success"`
	Failed       int          `json:"failed"`
	DurationMs   float64      `json:"duration_ms"`
	Distribution []LbDistItem `json:"distribution"`
}
