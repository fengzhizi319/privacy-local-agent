package integration_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/agent"
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/config"
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/handlers"
	pb "github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/proto"
)

const realAgentAddr = "127.0.0.1:50051"

// TestIntegration_HealthAndProxy 尝试连接真实 agent；如果 agent 未启动则跳过。
func TestIntegration_HealthAndProxy(t *testing.T) {
	// 尝试连接真实 agent，如果未启动则跳过。为了兼容 agent 启动较慢的情况，重试 3 次。
	var lastErr error
	for i := 0; i < 3; i++ {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		conn, err := grpc.NewClient(realAgentAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
		if err != nil {
			lastErr = err
			cancel()
			time.Sleep(500 * time.Millisecond)
			continue
		}
		grpcClient := pb.NewPrivacyServiceClient(conn)
		_, err = grpcClient.Health(ctx, &pb.HealthRequest{})
		cancel()
		conn.Close()
		if err == nil {
			lastErr = nil
			break
		}
		lastErr = err
		time.Sleep(500 * time.Millisecond)
	}
	if lastErr != nil {
		t.Skipf("跳过集成测试：agent %s 未可达：%v", realAgentAddr, lastErr)
	}

	// 使用稳定连接运行后续测试。
	conn, err := grpc.NewClient(realAgentAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("failed to create stable agent connection: %v", err)
	}
	defer func() { _ = conn.Close() }()
	cfg := &config.Config{
		AgentGRPCHost: "127.0.0.1",
		AgentGRPCPort: 50051,
		ConsoleHost:   "127.0.0.1",
		ConsolePort:   0,
	}
	agentClient := agent.NewFromConnection(conn)

	gin.SetMode(gin.TestMode)
	server := handlers.New(agentClient, cfg)
	router := gin.New()
	server.RegisterRoutes(router)
	ts := httptest.NewServer(router)
	defer ts.Close()

	// 1. 测试 /api/health
	healthResp, err := http.Get(ts.URL + "/api/health")
	if err != nil {
		t.Fatalf("GET /api/health failed: %v", err)
	}
	defer healthResp.Body.Close()
	if healthResp.StatusCode != http.StatusOK {
		t.Fatalf("expected /api/health 200, got %d", healthResp.StatusCode)
	}
	var healthBody map[string]any
	if err := json.NewDecoder(healthResp.Body).Decode(&healthBody); err != nil {
		t.Fatalf("decode health response failed: %v", err)
	}
	if healthBody["backend"] != "ok" {
		t.Fatalf("expected backend ok, got %v", healthBody["backend"])
	}
	if healthBody["agent"] == nil {
		t.Fatalf("expected agent health info, got nil")
	}

	// 2. 测试 /api/proxy 转发 /v1/privacy/mask
	reqBody := map[string]any{
		"method": "POST",
		"path":   "/v1/privacy/mask",
		"body": map[string]string{
			"field_name": "email",
			"value":      "alice@example.com",
		},
	}
	b, _ := json.Marshal(reqBody)
	proxyResp, err := http.Post(ts.URL+"/api/proxy", "application/json", bytes.NewReader(b))
	if err != nil {
		t.Fatalf("POST /api/proxy failed: %v", err)
	}
	defer proxyResp.Body.Close()
	if proxyResp.StatusCode != http.StatusOK {
		t.Fatalf("expected /api/proxy 200, got %d", proxyResp.StatusCode)
	}
	var proxyBody map[string]any
	if err := json.NewDecoder(proxyResp.Body).Decode(&proxyBody); err != nil {
		t.Fatalf("decode proxy response failed: %v", err)
	}
	data, ok := proxyBody["data"].(map[string]any)
	if !ok {
		t.Fatalf("unexpected proxy response shape: %+v", proxyBody)
	}
	result, ok := data["result"].(string)
	if !ok || result == "" {
		t.Fatalf("expected non-empty masked result, got %+v", data)
	}

	// 3. 测试 /api/samples
	samplesResp, err := http.Get(ts.URL + "/api/samples")
	if err != nil {
		t.Fatalf("GET /api/samples failed: %v", err)
	}
	defer samplesResp.Body.Close()
	if samplesResp.StatusCode != http.StatusOK {
		t.Fatalf("expected /api/samples 200, got %d", samplesResp.StatusCode)
	}
	var samplesBody struct {
		Samples []any `json:"samples"`
	}
	if err := json.NewDecoder(samplesResp.Body).Decode(&samplesBody); err != nil {
		t.Fatalf("decode samples response failed: %v", err)
	}
	if len(samplesBody.Samples) == 0 {
		t.Fatalf("expected non-empty samples list")
	}
}
