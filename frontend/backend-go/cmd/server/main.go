// Command server is the entry point for the Go gRPC proxy backend.
//
// 中文说明：
// 本程序加载环境变量配置，建立到 privacy-local-agent 的 gRPC 连接，
// 并启动一个 HTTP 服务器，将前端的 REST 请求转发为 gRPC 调用。
package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/agent"
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/config"
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/handlers"
)

func main() {
	cfg := config.Load()

	client, err := agent.New(cfg)
	if err != nil {
		log.Fatalf("failed to create agent client: %v", err)
	}
	defer func() { _ = client.Close() }()

	gin.SetMode(gin.ReleaseMode)
	server := handlers.New(client, cfg)
	router := gin.New()
	server.RegisterRoutes(router)

	srv := &http.Server{
		Addr:    cfg.ConsoleAddress(),
		Handler: router,
	}

	// Graceful shutdown on SIGINT/SIGTERM.
	go func() {
		sigChan := make(chan os.Signal, 1)
		signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
		<-sigChan

		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := srv.Shutdown(shutdownCtx); err != nil {
			log.Printf("http server shutdown error: %v", err)
		}
	}()

	fmt.Printf("Go gRPC proxy listening on http://%s\n", cfg.ConsoleAddress())
	fmt.Printf("Upstream agent gRPC: %s\n", cfg.AgentAddress())
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("http server failed: %v", err)
	}
}
