// Package agent wraps the gRPC client connection to privacy-local-agent.
//
// 中文说明：
// 该包负责建立到 agent gRPC 服务的连接，并在调用时附加可选的认证元数据。
// 所有 RPC 超时通过调用方传入的 context 控制。
package agent

import (
	"context"
	"fmt"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"

	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/config"
	pb "github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/proto"
)

// Client wraps a gRPC connection and the generated PrivacyService client.
type Client struct {
	conn   *grpc.ClientConn
	client pb.PrivacyServiceClient
	cfg    *config.Config
}

// New creates a Client connected to the configured agent gRPC address.
//
// 连接使用非安全传输（insecure），适用于本地开发环境。
// 生产环境建议配置 TLS/mTLS。
func New(cfg *config.Config) (*Client, error) {
	target := cfg.AgentAddress()
	conn, err := grpc.NewClient(
		target,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithDefaultCallOptions(grpc.MaxCallRecvMsgSize(64<<20)), // 64 MiB
	)
	if err != nil {
		return nil, fmt.Errorf("failed to dial agent gRPC %s: %w", target, err)
	}

	return &Client{
		conn:   conn,
		client: pb.NewPrivacyServiceClient(conn),
		cfg:    cfg,
	}, nil
}

// NewFromConnection creates a Client from an existing gRPC connection.
//
// 该构造器主要用于测试场景：测试可以传入 bufconn 等内存连接，
// 无需真正连到 agent 的 TCP 地址。生产代码仍应使用 New。
func NewFromConnection(conn *grpc.ClientConn) *Client {
	return &Client{
		conn:   conn,
		client: pb.NewPrivacyServiceClient(conn),
		cfg:    &config.Config{},
	}
}

// Close closes the underlying gRPC connection.
func (c *Client) Close() error {
	return c.conn.Close()
}

// Raw returns the generated gRPC client so handlers can call any RPC method.
func (c *Client) Raw() pb.PrivacyServiceClient {
	return c.client
}

// WithAuth returns a context with authorization metadata when an API key is configured.
//
// 当 PRIVACY_AGENT_API_KEY 非空时，在 gRPC 元数据中附加 "authorization: Bearer <key>"。
func (c *Client) WithAuth(ctx context.Context) context.Context {
	if c.cfg.AgentAPIKey == "" {
		return ctx
	}
	return metadata.AppendToOutgoingContext(ctx, "authorization", "Bearer "+c.cfg.AgentAPIKey)
}

// Health checks the agent gRPC health endpoint and returns the parsed response.
func (c *Client) Health(ctx context.Context) (*pb.HealthResponse, error) {
	return c.client.Health(c.WithAuth(ctx), &pb.HealthRequest{})
}
