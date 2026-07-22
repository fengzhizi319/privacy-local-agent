// Package handlers implements the HTTP REST surface for the Go gRPC proxy.
//
// 中文说明：
// 本包将来自前端的 HTTP 请求转换为内部调用，返回与 Python 后端一致的 JSON 格式。
// 这样前端只需切换 base URL，即可在 Python REST 代理和 Go gRPC 代理之间复用同一套代码。
package handlers

import (
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/agent"
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/config"
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/mapper"
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/models"
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/samples"
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
	r.NoRoute(func(c *gin.Context) {
		if strings.HasPrefix(c.Request.URL.Path, "/api/") {
			c.JSON(http.StatusNotFound, gin.H{"detail": "Not Found", "status": http.StatusNotFound})
			return
		}
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
		})
		return
	}

	c.JSON(http.StatusOK, models.ConsoleHealth{
		Backend:   "ok",
		Agent:     map[string]string{"status": resp.Status, "namespace": resp.Namespace},
		AgentURL:  s.cfg.AgentAddress(),
		LatencyMs: &latency,
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
		Total:   len(results),
		Passed:  passed,
		Failed:  len(results) - passed,
		Results: results,
	})
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
