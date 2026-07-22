package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/test/bufconn"

	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/agent"
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/config"
	pb "github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/proto"
)

// testPrivacyServer 是一个只实现 Health 和 Mask 的伪造 gRPC 服务器。
type testPrivacyServer struct {
	pb.UnimplementedPrivacyServiceServer

	HealthFunc func(context.Context, *pb.HealthRequest) (*pb.HealthResponse, error)
	MaskFunc   func(context.Context, *pb.MaskRequest) (*pb.MaskResponse, error)
}

func (s *testPrivacyServer) Health(ctx context.Context, req *pb.HealthRequest) (*pb.HealthResponse, error) {
	if s.HealthFunc != nil {
		return s.HealthFunc(ctx, req)
	}
	return s.UnimplementedPrivacyServiceServer.Health(ctx, req)
}

func (s *testPrivacyServer) Mask(ctx context.Context, req *pb.MaskRequest) (*pb.MaskResponse, error) {
	if s.MaskFunc != nil {
		return s.MaskFunc(ctx, req)
	}
	return s.UnimplementedPrivacyServiceServer.Mask(ctx, req)
}

// setupTestServer 启动内存 gRPC 服务器并创建带路由的 HTTP 测试服务器。
func setupTestServer(t *testing.T, grpcSrv *testPrivacyServer) (*httptest.Server, *config.Config) {
	t.Helper()
	listener := bufconn.Listen(1024 * 1024)
	gs := grpc.NewServer()
	pb.RegisterPrivacyServiceServer(gs, grpcSrv)
	go func() {
		if err := gs.Serve(listener); err != nil {
			t.Logf("gRPC serve error: %v", err)
		}
	}()

	conn, err := grpc.NewClient(
		"passthrough:///bufnet",
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return listener.DialContext(ctx)
		}),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		gs.Stop()
		t.Fatalf("failed to create bufconn client: %v", err)
	}
	// 测试结束时会关闭 httptest.Server，连接关闭在 cleanup 中处理。
	t.Cleanup(func() {
		_ = conn.Close()
		gs.Stop()
	})

	cfg := &config.Config{
		AgentGRPCHost: "127.0.0.1",
		AgentGRPCPort: 50051,
		ConsoleHost:   "127.0.0.1",
		ConsolePort:   0,
	}
	client := agent.NewFromConnection(conn)
	server := New(client, cfg)

	gin.SetMode(gin.TestMode)
	router := gin.New()
	server.RegisterRoutes(router)
	return httptest.NewServer(router), cfg
}

func TestHealthHandler(t *testing.T) {
	grpcSrv := &testPrivacyServer{
		HealthFunc: func(_ context.Context, _ *pb.HealthRequest) (*pb.HealthResponse, error) {
			return &pb.HealthResponse{Status: "healthy", Namespace: "default"}, nil
		},
	}
	ts, _ := setupTestServer(t, grpcSrv)
	defer ts.Close()

	resp, err := http.Get(ts.URL + "/api/health")
	if err != nil {
		t.Fatalf("GET /api/health failed: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected status 200, got %d", resp.StatusCode)
	}

	var body map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("decode response failed: %v", err)
	}
	if body["backend"] != "ok" {
		t.Fatalf("expected backend ok, got %v", body["backend"])
	}
	agent, ok := body["agent"].(map[string]any)
	if !ok || agent["status"] != "healthy" {
		t.Fatalf("unexpected agent status: %+v", body["agent"])
	}
}

func TestSamplesHandler(t *testing.T) {
	grpcSrv := &testPrivacyServer{}
	ts, _ := setupTestServer(t, grpcSrv)
	defer ts.Close()

	resp, err := http.Get(ts.URL + "/api/samples")
	if err != nil {
		t.Fatalf("GET /api/samples failed: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected status 200, got %d", resp.StatusCode)
	}

	var body struct {
		Samples []any `json:"samples"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("decode response failed: %v", err)
	}
	if len(body.Samples) == 0 {
		t.Fatalf("expected non-empty samples list")
	}
}

func TestProxyHandlerMask(t *testing.T) {
	grpcSrv := &testPrivacyServer{
		MaskFunc: func(_ context.Context, req *pb.MaskRequest) (*pb.MaskResponse, error) {
			if req.FieldName != "email" || req.Value != "alice@example.com" {
				t.Fatalf("unexpected mask request: %+v", req)
			}
			return &pb.MaskResponse{Result: "***@example.com"}, nil
		},
	}
	ts, _ := setupTestServer(t, grpcSrv)
	defer ts.Close()

	reqBody := map[string]any{
		"method": "POST",
		"path":   "/v1/privacy/mask",
		"body": map[string]string{
			"field_name": "email",
			"value":      "alice@example.com",
		},
	}
	b, _ := json.Marshal(reqBody)
	resp, err := http.Post(ts.URL+"/api/proxy", "application/json", bytes.NewReader(b))
	if err != nil {
		t.Fatalf("POST /api/proxy failed: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected status 200, got %d", resp.StatusCode)
	}

	var body map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("decode response failed: %v", err)
	}
	data, ok := body["data"].(map[string]any)
	if !ok || data["result"] != "***@example.com" {
		t.Fatalf("unexpected proxy response: %+v", body)
	}
}

// TestStaticServing 验证 Go 后端能独立提供 Console UI 静态资源与 SPA 回退。
func TestStaticServing(t *testing.T) {
	// 构造临时 dist 目录：index.html + assets/app.js
	distDir := t.TempDir()
	indexHTML := "<!doctype html><html><body>console-ui</body></html>"
	if err := os.WriteFile(filepath.Join(distDir, "index.html"), []byte(indexHTML), 0o644); err != nil {
		t.Fatalf("write index.html failed: %v", err)
	}
	if err := os.MkdirAll(filepath.Join(distDir, "assets"), 0o755); err != nil {
		t.Fatalf("mkdir assets failed: %v", err)
	}
	jsContent := "console.log('app');"
	if err := os.WriteFile(filepath.Join(distDir, "assets", "app.js"), []byte(jsContent), 0o644); err != nil {
		t.Fatalf("write app.js failed: %v", err)
	}

	grpcSrv := &testPrivacyServer{}
	listener := bufconn.Listen(1024 * 1024)
	gs := grpc.NewServer()
	pb.RegisterPrivacyServiceServer(gs, grpcSrv)
	go func() { _ = gs.Serve(listener) }()
	conn, err := grpc.NewClient(
		"passthrough:///bufnet",
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return listener.DialContext(ctx)
		}),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		gs.Stop()
		t.Fatalf("failed to create bufconn client: %v", err)
	}
	t.Cleanup(func() {
		_ = conn.Close()
		gs.Stop()
	})

	cfg := &config.Config{
		AgentGRPCHost: "127.0.0.1",
		AgentGRPCPort: 50051,
		ConsoleHost:   "127.0.0.1",
		ConsolePort:   0,
		StaticDistDir: distDir,
	}
	server := New(agent.NewFromConnection(conn), cfg)

	gin.SetMode(gin.TestMode)
	router := gin.New()
	server.RegisterRoutes(router)
	ts := httptest.NewServer(router)
	defer ts.Close()

	// 1. 根路径返回 index.html
	resp, err := http.Get(ts.URL + "/")
	if err != nil {
		t.Fatalf("GET / failed: %v", err)
	}
	bodyBytes, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || !strings.Contains(string(bodyBytes), "console-ui") {
		t.Fatalf("GET / expected index.html, got status=%d body=%s", resp.StatusCode, bodyBytes)
	}

	// 2. 静态资源正常返回
	resp, err = http.Get(ts.URL + "/assets/app.js")
	if err != nil {
		t.Fatalf("GET /assets/app.js failed: %v", err)
	}
	bodyBytes, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || string(bodyBytes) != jsContent {
		t.Fatalf("GET /assets/app.js expected js content, got status=%d body=%s", resp.StatusCode, bodyBytes)
	}

	// 3. SPA 回退：任意非 /api 路径返回 index.html
	resp, err = http.Get(ts.URL + "/some/spa/route")
	if err != nil {
		t.Fatalf("GET /some/spa/route failed: %v", err)
	}
	bodyBytes, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || !strings.Contains(string(bodyBytes), "console-ui") {
		t.Fatalf("SPA fallback expected index.html, got status=%d body=%s", resp.StatusCode, bodyBytes)
	}

	// 4. 未注册的 /api 路径返回 404 JSON而非 index.html
	resp, err = http.Get(ts.URL + "/api/nonexistent")
	if err != nil {
		t.Fatalf("GET /api/nonexistent failed: %v", err)
	}
	bodyBytes, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound || !strings.Contains(string(bodyBytes), "Not Found") {
		t.Fatalf("GET /api/nonexistent expected 404 JSON, got status=%d body=%s", resp.StatusCode, bodyBytes)
	}
}

// TestStaticServingDisabled 验证 dist 目录不存在时仅提供 API（不挂载静态路由）。
func TestStaticServingDisabled(t *testing.T) {
	grpcSrv := &testPrivacyServer{}
	ts, cfg := setupTestServer(t, grpcSrv)
	defer ts.Close()

	// setupTestServer 未设置 StaticDistDir，静态服务应跳过。
	if cfg.StaticDistDir != "" {
		t.Fatalf("expected empty StaticDistDir in test config, got %q", cfg.StaticDistDir)
	}

	resp, err := http.Get(ts.URL + "/")
	if err != nil {
		t.Fatalf("GET / failed: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("expected 404 without dist dir, got %d", resp.StatusCode)
	}
}
