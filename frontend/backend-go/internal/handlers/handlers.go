// Package handlers implements the HTTP REST surface for the Go gRPC proxy.
//
// 中文说明：
// 本包将来自前端的 HTTP 请求转换为内部调用，返回与 Python 后端一致的 JSON 格式。
// 这样前端只需切换 base URL，即可在 Python REST 代理和 Go gRPC 代理之间复用同一套代码。
package handlers

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/agent"
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/config"
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/fileparse"
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/lbtest"
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/mapper"
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/models"
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/samples"
	pb "github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/proto"
)

// 本控制台后端的身份标识，随每个响应下发给前端，
// 用于在界面上明确展示“当前请求由哪个后端、以何种协议与 agent 通信”，
// 从而让 Python REST / Go gRPC 两种通信方式的切换可被直观验证。
const (
	backendVia    = "go-grpc"
	agentProtocol = "gRPC"
)

// Server aggregates the dependencies required by HTTP handlers.
//
// client 是对 agent 的 gRPC 客户端封装；
// mapper 负责将 REST 请求映射为 gRPC 调用；
// cfg 保存监听地址、目标 agent 地址等运行时配置。
type Server struct {
	client *agent.Client
	mapper *mapper.Mapper
	cfg    *config.Config
}

// New creates a Server from a gRPC client and configuration.
func New(client *agent.Client, cfg *config.Config) *Server {
	return &Server{
		client: client,
		mapper: mapper.New(),
		cfg:    cfg,
	}
}

// RegisterRoutes mounts all API routes on the provided Gin router.
func (s *Server) RegisterRoutes(r *gin.Engine) {
	r.Use(corsMiddleware())
	r.GET("/api/health", s.Health)
	r.GET("/api/samples", s.Samples)
	r.POST("/api/proxy", s.Proxy)
	r.POST("/api/batch", s.Batch)
	r.POST("/api/upload", s.Upload)
	r.POST("/api/lb_test", s.LbTest)
	s.registerStatic(r)
}

// registerStatic 挂载前端构建产物（SPA），使 Go 后端能独立提供 Console UI，
// 无需依赖 Python 后端。dist 目录不存在时跳过挂载，仅以 API 模式运行。
//
// 路由规则与 Python 后端保持一致：
//   - /assets/* 静态资源
//   - 其余非 /api 路由一律返回 index.html（SPA 回退）
func (s *Server) registerStatic(r *gin.Engine) {
	distDir := s.cfg.StaticDistDir
	if distDir == "" {
		return
	}
	info, err := os.Stat(distDir)
	if err != nil || !info.IsDir() {
		log.Printf("static dist dir not found (%s), serving API only", distDir)
		return
	}
	indexPath := filepath.Join(distDir, "index.html")
	if _, err := os.Stat(indexPath); err != nil {
		log.Printf("index.html not found in %s, serving API only", distDir)
		return
	}

	if assetsDir := filepath.Join(distDir, "assets"); dirExists(assetsDir) {
		r.Static("/assets", assetsDir)
	}

	// SPA fallback：未匹配的路由（除 /api 外）均返回 index.html。
	// index.html 不带内容哈希，必须禁止浏览器缓存（no-cache），
	// 否则重新构建前端后浏览器仍会加载旧版本；
	// 带哈希的 /assets/* 资源则由浏览器正常缓存（内容变则 URL 变）。
	r.NoRoute(func(c *gin.Context) {
		if strings.HasPrefix(c.Request.URL.Path, "/api/") {
			c.JSON(http.StatusNotFound, gin.H{"detail": "Not Found", "status": http.StatusNotFound})
			return
		}
		c.Header("Cache-Control", "no-cache, no-store, must-revalidate")
		c.File(indexPath)
	})
	log.Printf("Console UI enabled, serving static files from %s", distDir)
}

// dirExists reports whether path exists and is a directory.
func dirExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

// corsMiddleware adds permissive CORS headers so the Vite dev server can call the backend.
func corsMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Writer.Header().Set("Access-Control-Allow-Origin", "*")
		c.Writer.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		c.Writer.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(http.StatusNoContent)
			return
		}
		c.Next()
	}
}

// Health checks whether the Go proxy itself and the upstream agent are reachable.
//
// 返回字段与 Python 后端保持一致：backend、agent、agent_url、latency_ms、error。
func (s *Server) Health(c *gin.Context) {
	start := time.Now()
	resp, err := s.client.Health(c.Request.Context())
	latency := time.Since(start).Milliseconds()

	if err != nil {
		c.JSON(http.StatusOK, models.ConsoleHealth{
			Backend:   "ok",
			Agent:     "unreachable",
			AgentURL:  s.cfg.AgentAddress(),
			LatencyMs: &latency,
			Error:     err.Error(),
			Via:       backendVia,
			Protocol:  agentProtocol,
		})
		return
	}

	c.JSON(http.StatusOK, models.ConsoleHealth{
		Backend:   "ok",
		Agent:     map[string]string{"status": resp.Status, "namespace": resp.Namespace},
		AgentURL:  s.cfg.AgentAddress(),
		LatencyMs: &latency,
		Via:       backendVia,
		Protocol:  agentProtocol,
	})
}

// Samples returns the list of gRPC-supported endpoint samples.
func (s *Server) Samples(c *gin.Context) {
	c.JSON(http.StatusOK, models.SamplesResponse{Samples: samples.List()})
}

// Proxy dispatches a frontend request to the corresponding gRPC method.
//
// 请求体格式：
//
//	{
//	  "method": "POST",
//	  "path": "/v1/privacy/mask",
//	  "body": {"field_name":"email","value":"alice@example.com"}
//	}
//
// 响应体格式：
//
//	{
//	  "status": 200,
//	  "duration_ms": 12,
//	  "data": { ... }
//	}
func (s *Server) Proxy(c *gin.Context) {
	var req models.ProxyRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": fmt.Sprintf("invalid request body: %v", err)})
		return
	}

	// The frontend always sends POST to /api/proxy, but the original method is carried in the body.
	// We ignore req.Method here and rely on the path mapping to decide the gRPC call semantics.
	start := time.Now()
	data, err := s.mapper.Dispatch(c.Request.Context(), s.client.Raw(), req.Path, req.Body)
	duration := time.Since(start).Milliseconds()

	if err != nil {
		status := http.StatusBadRequest
		// gRPC status errors are wrapped; try to preserve the original gRPC code if possible.
		// For simplicity, we return 400 for client-side errors and 502 for upstream failures.
		if isUnavailable(err) {
			status = http.StatusBadGateway
		}
		c.JSON(status, gin.H{"detail": err.Error(), "status": status})
		return
	}

	c.JSON(http.StatusOK, models.ProxyResponse{
		Status:     http.StatusOK,
		DurationMs: duration,
		Data:       data,
		Via:        backendVia,
		Protocol:   agentProtocol,
	})
}

// Batch 逐个转发一组请求并汇总成功 / 失败统计。
//
// 用于前端“一键批量测试”：单个请求失败不会中断整个批次，
// 返回与 Python 后端一致的 {total, passed, failed, results} 结构。
func (s *Server) Batch(c *gin.Context) {
	var req models.BatchRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": fmt.Sprintf("invalid request body: %v", err)})
		return
	}

	results := make([]models.BatchResultItem, 0, len(req.Requests))
	passed := 0
	for _, item := range req.Requests {
		method := strings.ToUpper(item.Method)
		start := time.Now()
		data, err := s.mapper.Dispatch(c.Request.Context(), s.client.Raw(), item.Path, item.Body)
		duration := time.Since(start).Milliseconds()

		if err != nil {
			status := http.StatusBadRequest
			if isUnavailable(err) {
				status = http.StatusBadGateway
			}
			results = append(results, models.BatchResultItem{
				Method:     method,
				Path:       item.Path,
				Status:     status,
				DurationMs: duration,
				Error:      err.Error(),
			})
			continue
		}

		passed++
		results = append(results, models.BatchResultItem{
			Method:     method,
			Path:       item.Path,
			Status:     http.StatusOK,
			DurationMs: duration,
			Data:       data,
		})
	}

	c.JSON(http.StatusOK, models.BatchResponse{
		Total:    len(results),
		Passed:   passed,
		Failed:   len(results) - passed,
		Results:  results,
		Via:      backendVia,
		Protocol: agentProtocol,
	})
}

// Upload 接收前端上传的 CSV/JSON 文件并执行隐私处理。
//
// 表单字段：file（数据文件）、operation（mask_dataframe | k_anonymize | classify_table）、
// params（JSON 字符串，如 {"columns":[...],"qi_cols":[...],"k":2,"context":""}）。
// 后端按扩展名解析文件为 records，直接构造 gRPC 请求调用 agent，
// 返回与 Python 后端一致的 ProxyResponse（data 为 UploadData）。
func (s *Server) Upload(c *gin.Context) {
	file, header, err := c.Request.FormFile("file")
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": fmt.Sprintf("缺少文件: %v", err), "status": http.StatusBadRequest})
		return
	}
	defer file.Close()

	operation := c.PostForm("operation")
	params := c.PostForm("params")
	if params == "" {
		params = "{}"
	}

	content, err := io.ReadAll(file)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": fmt.Sprintf("读取文件失败: %v", err), "status": http.StatusBadRequest})
		return
	}

	// 按扩展名解析文件为 records + schema。
	var records []map[string]string
	var schema []string
	filename := strings.ToLower(header.Filename)
	switch {
	case strings.HasSuffix(filename, ".csv"):
		records, schema, err = fileparse.ParseCSV(content)
	case strings.HasSuffix(filename, ".json"):
		records, schema, err = fileparse.ParseJSON(content)
	default:
		c.JSON(http.StatusBadRequest, gin.H{"detail": "仅支持 .csv 与 .json 文件", "status": http.StatusBadRequest})
		return
	}
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": err.Error(), "status": http.StatusBadRequest})
		return
	}

	// 解析 params（JSON 对象）。
	var options map[string]any
	if err := json.Unmarshal([]byte(params), &options); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": fmt.Sprintf("params 需为合法 JSON: %v", err), "status": http.StatusBadRequest})
		return
	}

	entries := toRecordEntries(records)
	rowsIn := len(records)
	client := s.client.Raw()
	ctx := c.Request.Context()

	start := time.Now()
	var result any
	var rowsOut int

	switch operation {
	case "mask_dataframe":
		resp, e := client.MaskDataFrame(ctx, &pb.MaskDataFrameRequest{
			Data:    entries,
			Columns: stringSlice(options, "columns"),
			Context: stringVal(options, "context"),
		})
		if e != nil {
			s.writeUpstreamError(c, e)
			return
		}
		result = recordEntriesToMaps(resp.Data)
		rowsOut = len(resp.Data)

	case "k_anonymize":
		qiCols := stringSlice(options, "qi_cols")
		if len(qiCols) == 0 {
			c.JSON(http.StatusBadRequest, gin.H{"detail": "k_anonymize 操作需提供 qi_cols 参数", "status": http.StatusBadRequest})
			return
		}
		resp, e := client.KAnonymizeDataFrame(ctx, &pb.KAnonymizeDataFrameRequest{
			Data:     entries,
			QiCols:   qiCols,
			K:        int32Val(options, "k", 5),
			MaxDepth: int32Val(options, "max_depth", 10),
		})
		if e != nil {
			s.writeUpstreamError(c, e)
			return
		}
		result = recordEntriesToMaps(resp.Data)
		rowsOut = len(resp.Data)

	case "classify_table":
		schemaUse := stringSlice(options, "schema")
		if len(schemaUse) == 0 {
			schemaUse = schema
		}
		// 分类参数取 params 内嵌套的 params 字段（与 agent process_file 一致）。
		paramsJSON := "{}"
		if p, ok := options["params"]; ok {
			if b, e := json.Marshal(p); e == nil {
				paramsJSON = string(b)
			}
		}
		resp, e := client.ClassifyTable(ctx, &pb.ClassifyTableRequest{
			Schema:     schemaUse,
			Rows:       entries,
			ParamsJson: paramsJSON,
		})
		if e != nil {
			s.writeUpstreamError(c, e)
			return
		}
		var parsed any
		if e := json.Unmarshal([]byte(resp.ResultJson), &parsed); e != nil {
			parsed = resp.ResultJson
		}
		result = parsed
		rowsOut = rowsIn

	default:
		c.JSON(http.StatusBadRequest, gin.H{
			"detail": fmt.Sprintf("不支持的操作 '%s'，可选: classify_table, k_anonymize, mask_dataframe", operation),
			"status": http.StatusBadRequest,
		})
		return
	}

	duration := time.Since(start).Milliseconds()
	c.JSON(http.StatusOK, models.ProxyResponse{
		Status:     http.StatusOK,
		DurationMs: duration,
		Data: models.UploadData{
			Operation: operation,
			RowsIn:    rowsIn,
			RowsOut:   rowsOut,
			Result:    result,
		},
		Via:      backendVia,
		Protocol: agentProtocol,
	})
}

// LbTest 按策略向多个后端节点分发探测请求并统计结果。
//
// 由控制台后端自行实现策略分发（round_robin / random / least_connections），
// 探测目标为用户填写的各 agent REST 地址，返回各节点命中数与延迟分布。
func (s *Server) LbTest(c *gin.Context) {
	var req models.LbTestRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": fmt.Sprintf("invalid request body: %v", err), "status": http.StatusBadRequest})
		return
	}
	resp, err := lbtest.Run(c.Request.Context(), req, nil)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": err.Error(), "status": http.StatusBadRequest})
		return
	}
	c.JSON(http.StatusOK, resp)
}

// writeUpstreamError 把 gRPC 上游错误转换为 HTTP 响应：
// 连接类错误 → 502，其余（参数/业务错误）→ 400。
func (s *Server) writeUpstreamError(c *gin.Context, err error) {
	status := http.StatusBadRequest
	if isUnavailable(err) {
		status = http.StatusBadGateway
	}
	c.JSON(status, gin.H{"detail": err.Error(), "status": status})
}

// toRecordEntries 把 records 转换为 gRPC RecordEntry 列表。
func toRecordEntries(records []map[string]string) []*pb.RecordEntry {
	entries := make([]*pb.RecordEntry, 0, len(records))
	for _, r := range records {
		fields := make(map[string]string, len(r))
		for k, v := range r {
			fields[k] = v
		}
		entries = append(entries, &pb.RecordEntry{Fields: fields})
	}
	return entries
}

// recordEntriesToMaps 把 gRPC RecordEntry 列表转回记录数组，供前端展示。
func recordEntriesToMaps(entries []*pb.RecordEntry) []map[string]string {
	out := make([]map[string]string, 0, len(entries))
	for _, e := range entries {
		out = append(out, e.Fields)
	}
	return out
}

// stringSlice 从 JSON 对象中取出字符串数组字段。
func stringSlice(m map[string]any, key string) []string {
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

// stringVal 从 JSON 对象中取出字符串字段。
func stringVal(m map[string]any, key string) string {
	if v, ok := m[key]; ok {
		if s, ok := v.(string); ok {
			return s
		}
	}
	return ""
}

// int32Val 从 JSON 对象中取出整数字段（JSON 数字默认为 float64）。
func int32Val(m map[string]any, key string, def int32) int32 {
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

// isUnavailable returns true when the error indicates the upstream agent is unreachable.
//
// 这是一个简化的判断，用于区分参数错误与上游连接错误。
func isUnavailable(err error) bool {
	if err == nil {
		return false
	}
	msg := err.Error()
	return containsAny(msg, []string{"connection refused", "dns", "timeout", "Unavailable"})
}

func containsAny(s string, subs []string) bool {
	for _, sub := range subs {
		if strings.Contains(s, sub) {
			return true
		}
	}
	return false
}
