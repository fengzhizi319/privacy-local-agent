// Package mapper maps incoming REST-style paths and JSON bodies to
// privacy-local-agent gRPC method calls.
//
// 中文说明：
// 前端使用统一的 JSON 契约发送请求，本包负责：
//   1. 根据 path 识别应调用的 gRPC RPC；
//   2. 将 JSON body 转换为对应的 protobuf 请求消息；
//   3. 调用 gRPC 客户端；
//   4. 将 protobuf 响应转换为前端可展示的 JSON 数据。
package mapper

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"

	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"

	pb "github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/proto"
)

// Handler is the function signature for mapping a JSON body to a gRPC call.
//
// 每个 path 对应一个 Handler，负责构造 protobuf 请求、调用 RPC、提取响应数据。
type Handler func(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error)

// Mapper holds the dispatch table from REST paths to gRPC handlers.
type Mapper struct {
	handlers map[string]Handler
	jobRE    *regexp.Regexp
}

// New creates a Mapper with all supported path mappings.
func New() *Mapper {
	m := &Mapper{
		jobRE: regexp.MustCompile(`^/v1/privacy/classify/jobs/([^/]+)$`),
	}
	m.handlers = map[string]Handler{
		// Health
		"/v1/privacy/health": m.handleHealth,

		// Masking
		"/v1/privacy/mask":            m.handleMask,
		"/v1/privacy/mask_record":     m.handleMaskRecord,
		"/v1/privacy/mask_batch":      m.handleMaskBatch,
		"/v1/privacy/mask_dataframe":  m.handleMaskDataFrame,
		"/v1/privacy/hash":            m.handleHash,

		// DP
		"/v1/privacy/dp/count":             m.handleDPCount,
		"/v1/privacy/dp/sum":               m.handleDPSum,
		"/v1/privacy/dp/mean":              m.handleDPMean,
		"/v1/privacy/dp/histogram":           m.handleDPHistogram,
		"/v1/privacy/dp/noisy_count":         m.handleDPNoisyCount,
		"/v1/privacy/dp/noisy_sum":           m.handleDPNoisySum,
		"/v1/privacy/dp/noisy_mean":          m.handleDPNoisyMean,
		"/v1/privacy/dp/noisy_histogram":     m.handleDPNoisyHistogram,
		"/v1/privacy/dp/chunked_count":       m.handleDPChunkedCount,
		"/v1/privacy/dp/chunked_sum":         m.handleDPChunkedSum,
		"/v1/privacy/dp/chunked_mean":        m.handleDPChunkedMean,
		"/v1/privacy/dp/chunked_histogram": m.handleDPChunkedHistogram,
		"/v1/privacy/dp/aggregate":           m.handleDPAggregate,
		"/v1/privacy/dp/vector_sum":          m.handleDPVectorSum,
		"/v1/privacy/dp/adaptive_clip":       m.handleDPAdaptiveClip,
		"/v1/privacy/dp/groupby":             m.handleDPGroupBy,

		// LDP
		"/v1/privacy/ldp/perturb/binary":      m.handlePerturbBinary,
		"/v1/privacy/ldp/perturb/categorical": m.handlePerturbCategorical,
		"/v1/privacy/ldp/estimate/binary":     m.handleEstimateBinary,
		"/v1/privacy/ldp/estimate/categorical": m.handleEstimateCategorical,

		// K-Anonymity
		"/v1/privacy/k_anonymize/record":     m.handleKAnonymizeRecord,
		"/v1/privacy/k_anonymize/table":      m.handleKAnonymizeTable,
		"/v1/privacy/k_anonymize/dataframe":  m.handleKAnonymizeDataFrame,

		// Query Obfuscation
		"/v1/privacy/qol/obfuscate":       m.handleObfuscateQuery,
		"/v1/privacy/qol/obfuscate/batch": m.handleObfuscateQueryBatch,

		// Classification
		"/v1/privacy/classify/field":           m.handleClassifyField,
		"/v1/privacy/classify/record":            m.handleClassifyRecord,
		"/v1/privacy/classify/table":             m.handleClassifyTable,
		"/v1/privacy/classify/table/async":      m.handleClassifyTableAsync,
		"/v1/privacy/classify/secretflow":       m.handleClassifySecretFlow,
		"/v1/privacy/classify/review/confirm":   m.handleConfirmReview,
		"/v1/privacy/classify/review/export":    m.handleExportReviews,

		// Profile
		"/v1/privacy/profile/recommend": m.handleRecommendParams,
	}
	return m
}

// Dispatch selects the appropriate handler for a path and invokes it.
//
// 如果 path 匹配 /v1/privacy/classify/jobs/{job_id}，则调用异步任务查询。
func (m *Mapper) Dispatch(ctx context.Context, client pb.PrivacyServiceClient, path string, body json.RawMessage) (any, error) {
	if handler, ok := m.handlers[path]; ok {
		return handler(ctx, client, body)
	}
	if matches := m.jobRE.FindStringSubmatch(path); len(matches) == 2 {
		return m.handleGetClassificationJob(ctx, client, matches[1])
	}
	return nil, fmt.Errorf("unsupported gRPC path: %s", path)
}

// ---------------------------------------------------------------------------
// JSON helpers
// ---------------------------------------------------------------------------

// decode parses a JSON body into a generic map.
func decode(body json.RawMessage) (map[string]any, error) {
	if len(body) == 0 {
		return map[string]any{}, nil
	}
	var v map[string]any
	if err := json.Unmarshal(body, &v); err != nil {
		return nil, fmt.Errorf("invalid JSON body: %w", err)
	}
	return v, nil
}

// getString returns a string field or default value.
func getString(m map[string]any, key, def string) string {
	if v, ok := m[key]; ok {
		if s, ok := v.(string); ok {
			return s
		}
	}
	return def
}

// getFloat64 returns a float64 field or default value.
func getFloat64(m map[string]any, key string, def float64) float64 {
	if v, ok := m[key]; ok {
		switch n := v.(type) {
		case float64:
			return n
		case int:
			return float64(n)
		case int64:
			return float64(n)
		}
	}
	return def
}

// getInt32 returns an int32 field or default value.
func getInt32(m map[string]any, key string, def int32) int32 {
	if v, ok := m[key]; ok {
		switch n := v.(type) {
		case float64:
			return int32(n)
		case int:
			return int32(n)
		case int64:
			return int32(n)
		}
	}
	return def
}

// getStrings returns a []string field or nil.
func getStrings(m map[string]any, key string) []string {
	if v, ok := m[key]; ok {
		if arr, ok := v.([]any); ok {
			out := make([]string, 0, len(arr))
			for _, item := range arr {
				if s, ok := item.(string); ok {
					out = append(out, s)
				}
			}
			return out
		}
	}
	return nil
}

// getFloats returns a []float64 field or nil.
func getFloats(m map[string]any, key string) []float64 {
	if v, ok := m[key]; ok {
		if arr, ok := v.([]any); ok {
			out := make([]float64, 0, len(arr))
			for _, item := range arr {
				switch n := item.(type) {
				case float64:
					out = append(out, n)
				case int:
					out = append(out, float64(n))
				case int64:
					out = append(out, float64(n))
				}
			}
			return out
		}
	}
	return nil
}

// getIntSlice returns a []int32 field or nil.
func getIntSlice(m map[string]any, key string) []int32 {
	if v, ok := m[key]; ok {
		if arr, ok := v.([]any); ok {
			out := make([]int32, 0, len(arr))
			for _, item := range arr {
				switch n := item.(type) {
				case float64:
					out = append(out, int32(n))
				case int:
					out = append(out, int32(n))
				case int64:
					out = append(out, int32(n))
				}
			}
			return out
		}
	}
	return nil
}

// getStringMap returns a map[string]string field or nil.
func getStringMap(m map[string]any, key string) map[string]string {
	if v, ok := m[key]; ok {
		if mm, ok := v.(map[string]any); ok {
			out := make(map[string]string, len(mm))
			for k, val := range mm {
				if s, ok := val.(string); ok {
					out[k] = s
				}
			}
			return out
		}
	}
	return nil
}

// getRecordEntries converts a list of map[string]string into proto RecordEntry messages.
func getRecordEntries(m map[string]any, key string) []*pb.RecordEntry {
	if v, ok := m[key]; ok {
		if arr, ok := v.([]any); ok {
			out := make([]*pb.RecordEntry, 0, len(arr))
			for _, item := range arr {
				if mm, ok := item.(map[string]any); ok {
					fields := make(map[string]string)
					for k, val := range mm {
						if k == "fields" {
							if fmap, ok := val.(map[string]any); ok {
								for fk, fv := range fmap {
									if fs, ok := fv.(string); ok {
										fields[fk] = fs
									}
								}
							}
						}
					}
					out = append(out, &pb.RecordEntry{Fields: fields})
				}
			}
			return out
		}
	}
	return nil
}

// getRecordEntry returns a single RecordEntry from a body field.
func getRecordEntry(m map[string]any, key string) *pb.RecordEntry {
	if v, ok := m[key]; ok {
		if mm, ok := v.(map[string]any); ok {
			fields := make(map[string]string)
			for k, val := range mm {
				if k == "fields" {
					if fmap, ok := val.(map[string]any); ok {
						for fk, fv := range fmap {
							if fs, ok := fv.(string); ok {
								fields[fk] = fs
							}
						}
					}
				}
			}
			return &pb.RecordEntry{Fields: fields}
		}
	}
	return nil
}

func getBool(m map[string]any, key string, def bool) bool {
	if v, ok := m[key]; ok {
		if b, ok := v.(bool); ok {
			return b
		}
	}
	return def
}

// getStringMapFromMap returns a map[string]string from a nested map field.
func getStringMapFromMap(m map[string]any, key string) map[string]string {
	if v, ok := m[key]; ok {
		if mm, ok := v.(map[string]any); ok {
			out := make(map[string]string, len(mm))
			for k, val := range mm {
				if s, ok := val.(string); ok {
					out[k] = s
				}
			}
			return out
		}
	}
	return nil
}

// getDoubleChunks converts a list of {values: [...]} objects into proto DoubleChunk.
func getDoubleChunks(m map[string]any, key string) []*pb.DoubleChunk {
	if v, ok := m[key]; ok {
		if arr, ok := v.([]any); ok {
			out := make([]*pb.DoubleChunk, 0, len(arr))
			for _, item := range arr {
				if mm, ok := item.(map[string]any); ok {
					out = append(out, &pb.DoubleChunk{Values: getFloats(mm, "values")})
				}
			}
			return out
		}
	}
	return nil
}

// getStringChunks converts a list of {values: [...]} objects into proto StringChunk.
func getStringChunks(m map[string]any, key string) []*pb.StringChunk {
	if v, ok := m[key]; ok {
		if arr, ok := v.([]any); ok {
			out := make([]*pb.StringChunk, 0, len(arr))
			for _, item := range arr {
				if mm, ok := item.(map[string]any); ok {
					out = append(out, &pb.StringChunk{Values: getStrings(mm, "values")})
				}
			}
			return out
		}
	}
	return nil
}

// getVectorEntries converts a list of {values: [...]} into proto DoubleChunk for vector RPCs.
func getVectorEntries(m map[string]any, key string) []*pb.DoubleChunk {
	if v, ok := m[key]; ok {
		if arr, ok := v.([]any); ok {
			out := make([]*pb.DoubleChunk, 0, len(arr))
			for _, item := range arr {
				if mm, ok := item.(map[string]any); ok {
					out = append(out, &pb.DoubleChunk{Values: getFloats(mm, "values")})
				}
			}
			return out
		}
	}
	return nil
}

// marshalProto converts a protobuf message to a JSON-serializable Go value.
//
// 对于分类/复核/推荐等返回 JSON 字符串字段的 RPC，本函数会自动解析内部 JSON，
// 使前端收到的是结构化的对象而非字符串。
func marshalProto(msg proto.Message) (any, error) {
	b, err := protojson.MarshalOptions{UseProtoNames: true}.Marshal(msg)
	if err != nil {
		return nil, err
	}
	var v any
	if err := json.Unmarshal(b, &v); err != nil {
		return nil, err
	}
	return v, nil
}

// extractJSONField parses a string field that itself contains JSON.
func extractJSONField(v any, field string) any {
	m, ok := v.(map[string]any)
	if !ok {
		return v
	}
	raw, ok := m[field].(string)
	if !ok || raw == "" {
		return v
	}
	var parsed any
	if err := json.Unmarshal([]byte(raw), &parsed); err != nil {
		return v
	}
	m[field] = parsed
	return m
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

func (m *Mapper) handleHealth(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	resp, err := client.Health(ctx, &pb.HealthRequest{})
	if err != nil {
		return nil, err
	}
	return map[string]string{"status": resp.Status, "namespace": resp.Namespace}, nil
}

// ---------------------------------------------------------------------------
// Masking
// ---------------------------------------------------------------------------

func (m *Mapper) handleMask(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.Mask(ctx, &pb.MaskRequest{
		FieldName: getString(v, "field_name", ""),
		Value:     getString(v, "value", ""),
		Context:   getString(v, "context", ""),
	})
	if err != nil {
		return nil, err
	}
	return map[string]string{"result": resp.Result}, nil
}

func (m *Mapper) handleMaskRecord(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.MaskRecord(ctx, &pb.MaskRecordRequest{
		Record:  getStringMap(v, "record"),
		Context: getString(v, "context", ""),
	})
	if err != nil {
		return nil, err
	}
	return map[string]map[string]string{"result": resp.Result}, nil
}

func (m *Mapper) handleMaskBatch(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.MaskBatch(ctx, &pb.MaskBatchRequest{
		FieldNames: getStrings(v, "field_names"),
		Values:     getStrings(v, "values"),
		Context:    getString(v, "context", ""),
	})
	if err != nil {
		return nil, err
	}
	return map[string][]string{"results": resp.Results}, nil
}

func (m *Mapper) handleMaskDataFrame(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.MaskDataFrame(ctx, &pb.MaskDataFrameRequest{
		Data:    getRecordEntries(v, "data"),
		Columns: getStrings(v, "columns"),
		Context: getString(v, "context", ""),
	})
	if err != nil {
		return nil, err
	}
	return map[string][]*pb.RecordEntry{"data": resp.Data}, nil
}

func (m *Mapper) handleHash(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.Hash(ctx, &pb.HashRequest{
		Value: getString(v, "value", ""),
		Salt:  getString(v, "salt", ""),
	})
	if err != nil {
		return nil, err
	}
	return map[string]string{"result": resp.Result}, nil
}

// ---------------------------------------------------------------------------
// DP helpers
// ---------------------------------------------------------------------------

// dpRequest builds a DPRequest from the JSON body.
// gRPC DPRequest uses flat fields, so values are read directly from the top-level body.
func dpRequest(body json.RawMessage) (*pb.DPRequest, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	return &pb.DPRequest{
		Values:     getFloats(v, "values"),
		Epsilon:    getFloat64(v, "epsilon", 1.0),
		Mechanism:  getString(v, "mechanism", "laplace"),
		Delta:      getFloat64(v, "delta", 0.0),
		ClipLower:  getFloat64(v, "clip_lower", 0.0),
		ClipUpper:  getFloat64(v, "clip_upper", 0.0),
	}, nil
}

func (m *Mapper) handleDPCount(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	req, err := dpRequest(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPCount(ctx, req)
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

func (m *Mapper) handleDPSum(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	req, err := dpRequest(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPSum(ctx, req)
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

func (m *Mapper) handleDPMean(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	req, err := dpRequest(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPMean(ctx, req)
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

func (m *Mapper) handleDPHistogram(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPHistogram(ctx, &pb.DPHistogramRequest{
		Values:     getStrings(v, "values"),
		Categories: getStrings(v, "categories"),
		Epsilon:    getFloat64(v, "epsilon", 1.0),
		Mechanism:  getString(v, "mechanism", "laplace"),
		Delta:      getFloat64(v, "delta", 0.0),
	})
	if err != nil {
		return nil, err
	}
	return map[string]map[string]float64{"result": resp.Result}, nil
}

func (m *Mapper) handleDPNoisyCount(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPNoisyCount(ctx, &pb.DPNoisyCountRequest{
		TrueCount: getFloat64(v, "true_count", 0.0),
		Epsilon:   getFloat64(v, "epsilon", 1.0),
		Mechanism: getString(v, "mechanism", "laplace"),
		Delta:     getFloat64(v, "delta", 0.0),
	})
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

func (m *Mapper) handleDPNoisySum(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPNoisySum(ctx, &pb.DPNoisySumRequest{
		TrueSum:   getFloat64(v, "true_sum", 0.0),
		Epsilon:   getFloat64(v, "epsilon", 1.0),
		Mechanism: getString(v, "mechanism", "laplace"),
		Delta:     getFloat64(v, "delta", 0.0),
		Sensitivity: getFloat64(v, "sensitivity", 0.0),
		ClipLower:   getFloat64(v, "clip_lower", 0.0),
		ClipUpper:   getFloat64(v, "clip_upper", 0.0),
	})
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

func (m *Mapper) handleDPNoisyMean(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPNoisyMean(ctx, &pb.DPNoisyMeanRequest{
		TrueSum:     getFloat64(v, "true_sum", 0.0),
		TrueCount:   getFloat64(v, "true_count", 0.0),
		Epsilon:     getFloat64(v, "epsilon", 1.0),
		Mechanism:   getString(v, "mechanism", "laplace"),
		Delta:       getFloat64(v, "delta", 0.0),
		Sensitivity: getFloat64(v, "sensitivity", 0.0),
		ClipLower:   getFloat64(v, "clip_lower", 0.0),
		ClipUpper:   getFloat64(v, "clip_upper", 0.0),
		MinCount:    getFloat64(v, "min_count", 5.0),
	})
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

func (m *Mapper) handleDPNoisyHistogram(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	trueCounts := map[string]float64{}
	if mm, ok := v["true_counts"].(map[string]any); ok {
		for k, val := range mm {
			if n, ok := val.(float64); ok {
				trueCounts[k] = n
			}
		}
	}
	resp, err := client.DPNoisyHistogram(ctx, &pb.DPNoisyHistogramRequest{
		TrueCounts: trueCounts,
		Epsilon:    getFloat64(v, "epsilon", 1.0),
		Mechanism:  getString(v, "mechanism", "laplace"),
		Delta:      getFloat64(v, "delta", 0.0),
	})
	if err != nil {
		return nil, err
	}
	return map[string]map[string]float64{"result": resp.Result}, nil
}

func (m *Mapper) handleDPChunkedCount(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPChunkedCount(ctx, &pb.DPChunkedCountRequest{
		Chunks:    getDoubleChunks(v, "chunks"),
		Epsilon:   getFloat64(v, "epsilon", 1.0),
		Mechanism: getString(v, "mechanism", "laplace"),
		Delta:     getFloat64(v, "delta", 0.0),
	})
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

func (m *Mapper) handleDPChunkedSum(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPChunkedSum(ctx, &pb.DPChunkedSumRequest{
		Chunks:    getDoubleChunks(v, "chunks"),
		Epsilon:   getFloat64(v, "epsilon", 1.0),
		Mechanism: getString(v, "mechanism", "laplace"),
		Delta:     getFloat64(v, "delta", 0.0),
		ClipLower: getFloat64(v, "clip_lower", 0.0),
		ClipUpper: getFloat64(v, "clip_upper", 0.0),
	})
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

func (m *Mapper) handleDPChunkedMean(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPChunkedMean(ctx, &pb.DPChunkedMeanRequest{
		Chunks:    getDoubleChunks(v, "chunks"),
		Epsilon:   getFloat64(v, "epsilon", 1.0),
		Mechanism: getString(v, "mechanism", "laplace"),
		Delta:     getFloat64(v, "delta", 0.0),
		ClipLower: getFloat64(v, "clip_lower", 0.0),
		ClipUpper: getFloat64(v, "clip_upper", 0.0),
		MinCount:  getFloat64(v, "min_count", 5.0),
	})
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

func (m *Mapper) handleDPChunkedHistogram(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPChunkedHistogram(ctx, &pb.DPChunkedHistogramRequest{
		Chunks:     getStringChunks(v, "chunks"),
		Categories: getStrings(v, "categories"),
		Epsilon:    getFloat64(v, "epsilon", 1.0),
		Mechanism:  getString(v, "mechanism", "laplace"),
		Delta:      getFloat64(v, "delta", 0.0),
	})
	if err != nil {
		return nil, err
	}
	return map[string]map[string]float64{"result": resp.Result}, nil
}

func (m *Mapper) handleDPAggregate(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPAggregate(ctx, &pb.DPAggregateRequest{
		Rows:      getRecordEntries(v, "rows"),
		SpecsJson: getString(v, "specs_json", "{}"),
		Epsilon:   getFloat64(v, "epsilon", 1.0),
		Delta:     getFloat64(v, "delta", 0.0),
		Mechanism: getString(v, "mechanism", "laplace"),
		ReturnDetails: true,
	})
	if err != nil {
		return nil, err
	}
	var results any
	if err := json.Unmarshal([]byte(resp.ResultsJson), &results); err != nil {
		return map[string]string{"results_json": resp.ResultsJson}, nil
	}
	return map[string]any{"results": results}, nil
}

func (m *Mapper) handleDPVectorSum(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPVectorSum(ctx, &pb.DPVectorSumRequest{
		Vectors:   getVectorEntries(v, "vectors"),
		MaxNorm:   getFloat64(v, "max_norm", 1.0),
		Epsilon:   getFloat64(v, "epsilon", 1.0),
		Delta:     getFloat64(v, "delta", 0.0),
		Mechanism: getString(v, "mechanism", "gaussian"),
		ReturnDetails: true,
	})
	if err != nil {
		return nil, err
	}
	resultDetails, _ := marshalProto(resp.ResultDetails)
	return map[string]any{
		"noisy_vector":    resp.NoisyVector,
		"result_details": resultDetails,
	}, nil
}

func (m *Mapper) handleDPAdaptiveClip(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPAdaptiveClip(ctx, &pb.DPAdaptiveClipRequest{
		Values:         getFloats(v, "values"),
		Epsilon:        getFloat64(v, "epsilon", 1.0),
		TargetQuantile: getFloat64(v, "target_quantile", 0.95),
		NumIterations:  getInt32(v, "num_iterations", 15),
		InitialClip:    getFloat64(v, "initial_clip", 10.0),
	})
	if err != nil {
		return nil, err
	}
	return map[string]float64{
		"clip_lower": resp.ClipLower,
		"clip_upper": resp.ClipUpper,
	}, nil
}

func (m *Mapper) handleDPGroupBy(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPGroupBy(ctx, &pb.DPGroupByRequest{
		Rows:      getRecordEntries(v, "rows"),
		GroupCol:  getString(v, "group_col", ""),
		TargetCol: getString(v, "target_col", ""),
		Agg:       getString(v, "agg", ""),
		Epsilon:   getFloat64(v, "epsilon", 1.0),
		Delta:     getFloat64(v, "delta", 0.0),
		Mechanism: getString(v, "mechanism", "laplace"),
		ClipLower: getFloat64(v, "clip_lower", 0.0),
		ClipUpper: getFloat64(v, "clip_upper", 0.0),
	})
	if err != nil {
		return nil, err
	}
	var result any
	if err := json.Unmarshal([]byte(resp.ResultJson), &result); err != nil {
		return map[string]string{"result_json": resp.ResultJson}, nil
	}
	return map[string]any{"result": result}, nil
}

// ---------------------------------------------------------------------------
// LDP
// ---------------------------------------------------------------------------

func (m *Mapper) handlePerturbBinary(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.PerturbBinaryBatch(ctx, &pb.PerturbBinaryBatchRequest{
		Values:  getIntSlice(v, "values"),
		Epsilon: getFloat64(v, "epsilon", 1.0),
	})
	if err != nil {
		return nil, err
	}
	return map[string][]int32{"results": resp.Results}, nil
}

func (m *Mapper) handlePerturbCategorical(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.PerturbCategoricalBatch(ctx, &pb.PerturbCategoricalBatchRequest{
		Values:     getStrings(v, "values"),
		Categories: getStrings(v, "categories"),
		Epsilon:    getFloat64(v, "epsilon", 1.0),
	})
	if err != nil {
		return nil, err
	}
	return map[string][]string{"results": resp.Results}, nil
}

func (m *Mapper) handleEstimateBinary(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.EstimateBinaryFrequency(ctx, &pb.EstimateBinaryFrequencyRequest{
		ReportedValues: getIntSlice(v, "reported_values"),
		Epsilon:        getFloat64(v, "epsilon", 1.0),
	})
	if err != nil {
		return nil, err
	}
	return map[string]float64{"estimated_frequency": resp.EstimatedFrequency}, nil
}

func (m *Mapper) handleEstimateCategorical(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.EstimateCategoricalHistogram(ctx, &pb.EstimateCategoricalHistogramRequest{
		ReportedValues: getStrings(v, "reported_values"),
		Categories:     getStrings(v, "categories"),
		Epsilon:        getFloat64(v, "epsilon", 1.0),
	})
	if err != nil {
		return nil, err
	}
	return map[string]map[string]float64{"estimated_histogram": resp.EstimatedHistogram}, nil
}

// ---------------------------------------------------------------------------
// K-Anonymity
// ---------------------------------------------------------------------------

func (m *Mapper) handleKAnonymizeRecord(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.KAnonymizeRecord(ctx, &pb.KAnonymizeRequest{
		Record: getStringMap(v, "record"),
		QiCols: getStrings(v, "qi_cols"),
		K:      getInt32(v, "k", 5),
	})
	if err != nil {
		return nil, err
	}
	return map[string]map[string]string{"result": resp.Result}, nil
}

func (m *Mapper) handleKAnonymizeTable(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.KAnonymizeTable(ctx, &pb.KAnonymizeTableRequest{
		Rows:     getRecordEntries(v, "rows"),
		QiCols:   getStrings(v, "qi_cols"),
		K:        getInt32(v, "k", 5),
		MaxDepth: getInt32(v, "max_depth", 10),
	})
	if err != nil {
		return nil, err
	}
	return map[string][]*pb.RecordEntry{"rows": resp.Rows}, nil
}

func (m *Mapper) handleKAnonymizeDataFrame(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.KAnonymizeDataFrame(ctx, &pb.KAnonymizeDataFrameRequest{
		Data:     getRecordEntries(v, "data"),
		QiCols:   getStrings(v, "qi_cols"),
		K:        getInt32(v, "k", 5),
		MaxDepth: getInt32(v, "max_depth", 10),
	})
	if err != nil {
		return nil, err
	}
	return map[string][]*pb.RecordEntry{"rows": resp.Data}, nil
}

// ---------------------------------------------------------------------------
// Query Obfuscation
// ---------------------------------------------------------------------------

func (m *Mapper) handleObfuscateQuery(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.ObfuscateQuery(ctx, &pb.ObfuscateQueryRequest{
		Query:     getString(v, "query", ""),
		NumDummies: getInt32(v, "num_dummies", 3),
		Domain:    getString(v, "domain", "medical"),
		MedicalPool: getStrings(v, "medical_pool"),
		GenericPool: getStrings(v, "generic_pool"),
	})
	if err != nil {
		return nil, err
	}
	return map[string][]string{"result": resp.Result}, nil
}

func (m *Mapper) handleObfuscateQueryBatch(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.ObfuscateQueryBatch(ctx, &pb.ObfuscateQueryBatchRequest{
		Queries:   getStrings(v, "queries"),
		NumDummies: getInt32(v, "num_dummies", 3),
		Domain:    getString(v, "domain", "medical"),
		MedicalPool: getStrings(v, "medical_pool"),
		GenericPool: getStrings(v, "generic_pool"),
	})
	if err != nil {
		return nil, err
	}
	results := make([][]string, 0, len(resp.Results))
	for _, r := range resp.Results {
		results = append(results, r.Result)
	}
	return map[string][][]string{"results": results}, nil
}

// ---------------------------------------------------------------------------
// Classification
// ---------------------------------------------------------------------------

func (m *Mapper) handleClassifyField(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.ClassifyField(ctx, &pb.ClassifyFieldRequest{
		FieldName: getString(v, "field_name", ""),
		Value:     getString(v, "value", ""),
		ParamsJson: getString(v, "params_json", "{}"),
	})
	if err != nil {
		return nil, err
	}
	data, err := marshalProto(resp)
	if err != nil {
		return nil, err
	}
	return extractJSONField(data, "result_json"), nil
}

func (m *Mapper) handleClassifyRecord(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.ClassifyRecord(ctx, &pb.ClassifyRecordRequest{
		Record:     getRecordEntry(v, "record"),
		ParamsJson: getString(v, "params_json", "{}"),
	})
	if err != nil {
		return nil, err
	}
	data, err := marshalProto(resp)
	if err != nil {
		return nil, err
	}
	return extractJSONField(data, "result_json"), nil
}

func (m *Mapper) handleClassifyTable(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.ClassifyTable(ctx, &pb.ClassifyTableRequest{
		Schema:     getStrings(v, "schema"),
		Rows:       getRecordEntries(v, "rows"),
		ParamsJson: getString(v, "params_json", "{}"),
	})
	if err != nil {
		return nil, err
	}
	data, err := marshalProto(resp)
	if err != nil {
		return nil, err
	}
	return extractJSONField(data, "result_json"), nil
}

func (m *Mapper) handleClassifyTableAsync(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.ClassifyTableAsync(ctx, &pb.ClassifyTableAsyncRequest{
		Schema:     getStrings(v, "schema"),
		Rows:       getRecordEntries(v, "rows"),
		ParamsJson: getString(v, "params_json", "{}"),
	})
	if err != nil {
		return nil, err
	}
	return map[string]string{
		"job_id":     resp.JobId,
		"status":     resp.Status,
		"created_at": resp.CreatedAt,
	}, nil
}

func (m *Mapper) handleGetClassificationJob(ctx context.Context, client pb.PrivacyServiceClient, jobID string) (any, error) {
	resp, err := client.GetClassificationJob(ctx, &pb.GetClassificationJobRequest{JobId: jobID})
	if err != nil {
		return nil, err
	}
	data, err := marshalProto(resp)
	if err != nil {
		return nil, err
	}
	return extractJSONField(data, "result_json"), nil
}

func (m *Mapper) handleClassifySecretFlow(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.ClassifySecretFlow(ctx, &pb.ClassifySecretFlowRequest{
		Party:      getString(v, "party", ""),
		ParamsJson: getString(v, "params_json", "{}"),
		DataJson:   getString(v, "data_json", "{}"),
	})
	if err != nil {
		return nil, err
	}
	data, err := marshalProto(resp)
	if err != nil {
		return nil, err
	}
	return extractJSONField(data, "result_json"), nil
}

func (m *Mapper) handleConfirmReview(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.ConfirmReview(ctx, &pb.ConfirmReviewRequest{
		ReviewId:       getString(v, "review_id", ""),
		CorrectedLevel: getString(v, "corrected_level", ""),
		Reviewer:       getString(v, "reviewer", ""),
		Comment:        getString(v, "comment", ""),
	})
	if err != nil {
		return nil, err
	}
	data, err := marshalProto(resp)
	if err != nil {
		return nil, err
	}
	return extractJSONField(data, "result_json"), nil
}

func (m *Mapper) handleExportReviews(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.ExportReviews(ctx, &pb.ExportReviewsRequest{
		Format:    getString(v, "format", "jsonl"),
		MaskInput: getBool(v, "mask_input", false),
	})
	if err != nil {
		return nil, err
	}
	return map[string]string{"data": resp.Data}, nil
}

// ---------------------------------------------------------------------------
// Profile
// ---------------------------------------------------------------------------

func (m *Mapper) handleRecommendParams(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.RecommendParams(ctx, &pb.RecommendRequest{
		Namespace: getString(v, "namespace", ""),
		Values:    getFloats(v, "values"),
		Rows:      getRecordEntries(v, "rows"),
		QiCols:    getStrings(v, "qi_cols"),
	})
	if err != nil {
		return nil, err
	}
	var recommended any
	if err := json.Unmarshal([]byte(resp.RecommendedParamsJson), &recommended); err != nil {
		recommended = resp.RecommendedParamsJson
	}
	return map[string]any{
		"status":             resp.Status,
		"namespace":          resp.Namespace,
		"recommended_params": recommended,
	}, nil
}

// String helpers for case-insensitive bool/enum parsing.
func lower(s string) string {
	return strings.ToLower(strings.TrimSpace(s))
}

// marshalProto helper to avoid unused import warning if not used elsewhere.
var _ = protojson.MarshalOptions{}
