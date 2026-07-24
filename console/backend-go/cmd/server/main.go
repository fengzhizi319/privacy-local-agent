// Command server 是 Go gRPC 代理后端的程序入口。
//
// 执行流程：
//   1. 从环境变量加载配置（agent 地址、监听端口、API Key 等）
//   2. 创建到 privacy-local-agent Python gRPC 服务的客户端连接
//   3. 初始化 Gin HTTP 路由，注册所有 REST 代理接口与静态 UI 托管
//   4. 启动 HTTP 服务器，监听前端请求
//   5. 监听系统信号（SIGINT/SIGTERM），收到后执行优雅关闭
//
// 整体架构：
//   React 前端  ──HTTP/JSON──▶  本程序(Go)  ──gRPC──▶  privacy-local-agent(Python)
package main

import (
	// context：用于优雅关闭时设置超时上下文
	"context"
	// fmt：用于打印启动信息
	"fmt"
	// log：标准库日志，用于输出错误与致命错误信息
	"log"
	// net/http：标准库 HTTP 服务器，承载 Gin 路由
	"net/http"
	// os：用于读取环境变量、接收系统信号
	"os"
	// os/signal：注册系统信号通知通道
	"os/signal"
	// syscall：定义 SIGINT/SIGTERM 等系统信号常量
	"syscall"
	// time：用于优雅关闭超时时间
	"time"

	// gin：高性能 HTTP Web 框架，用于构建 REST API 路由
	"github.com/gin-gonic/gin"

	// agent：封装到 privacy-local-agent 的 gRPC 客户端连接
	"github.com/fengzhizi319/privacy-local-agent/console/backend-go/internal/agent"
	// config：从环境变量加载代理后端配置
	"github.com/fengzhizi319/privacy-local-agent/console/backend-go/internal/config"
	// handlers：HTTP 处理器与路由注册，负责将 REST 请求转发为 gRPC 调用
	"github.com/fengzhizi319/privacy-local-agent/console/backend-go/internal/handlers"
)

// main 是程序入口函数，按以下步骤顺序执行：
//   加载配置 → 创建 gRPC 客户端 → 初始化 HTTP 路由 → 启动服务器 → 等待关闭信号
func main() {
	// ── 步骤 1：加载配置 ──────────────────────────────────────────────
	// 从环境变量读取所有配置项，包括：
	//   - PRIVACY_AGENT_HOST / PRIVACY_AGENT_PORT：上游 gRPC agent 地址
	//   - PRIVACY_CONSOLE_HOST / PRIVACY_CONSOLE_PORT：本代理 HTTP 监听地址
	//   - PRIVACY_AGENT_API_KEY：可选的认证 API Key
	//   - PRIVACY_CONSOLE_STATIC_DIR：可选的前端静态文件目录
	cfg := config.Load()

	// ── 步骤 2：创建 gRPC 客户端 ─────────────────────────────────────
	// 根据配置建立到 privacy-local-agent 的 gRPC 连接。
	// 如果配置了 API Key，会自动附加 authorization 元数据。
	// 连接失败时打印错误并立即退出进程（log.Fatalf）。
	client, err := agent.New(cfg)
	if err != nil {
		log.Fatalf("failed to create agent client: %v", err) // 致命错误：无法连接上游 agent
	}
	// 注册 defer：main 函数退出前自动关闭 gRPC 连接，释放底层 TCP 连接与 HTTP/2 流
	defer func() { _ = client.Close() }()

	// ── 步骤 3：初始化 HTTP 路由 ─────────────────────────────────────
	// 将 Gin 设置为发布模式，关闭调试日志输出，提升性能
	gin.SetMode(gin.ReleaseMode)
	// 创建 HTTP 处理器实例，持有 gRPC 客户端引用与配置信息，
	// 内部实现了 /api/health、/api/samples、/api/proxy、/api/batch 等接口
	server := handlers.New(client, cfg)
	// 创建一个新的 Gin 引擎实例（包含默认的 Logger + Recovery 中间件）
	router := gin.New()
	// 将所有 REST 代理路由与可选的静态 UI 托管路由注册到 Gin 引擎
	// 包括 CORS 中间件、健康检查、代理转发、批量测试、静态文件服务等
	server.RegisterRoutes(router)

	// ── 步骤 4：配置并启动 HTTP 服务器 ───────────────────────────────
	// 创建标准库 HTTP 服务器实例，将 Gin 引擎作为 Handler
	srv := &http.Server{
		// 监听地址，格式如 ":8081" 或 "127.0.0.1:8081"，由配置决定
		Addr:    cfg.ConsoleAddress(),
		// Gin 引擎实现了 http.Handler 接口，所有请求由 Gin 路由分发处理
		Handler: router,
	}

	// ── 步骤 5：启动优雅关闭协程 ─────────────────────────────────────
	// 在独立 goroutine 中监听系统信号，主协程继续执行到 ListenAndServe
	go func() {
		// 创建一个带缓冲的信号通道，容量为 1 避免信号丢失
		sigChan := make(chan os.Signal, 1)
		// 将 SIGINT（Ctrl+C）和 SIGTERM（kill/容器停止）信号注册到通道
		signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
		// 阻塞等待，直到收到任意一个系统信号
		<-sigChan

		// 收到关闭信号后，创建带 5 秒超时的上下文，
		// 确保优雅关闭不会无限阻塞（如存在未完成的长连接）
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		// 注册 defer：函数退出时释放上下文资源
		defer cancel()
		// 调用 Shutdown：停止接收新连接，等待所有活跃请求处理完毕或超时
		if err := srv.Shutdown(shutdownCtx); err != nil {
			// 超时或关闭异常时仅打印日志（此时主协程可能已退出）
			log.Printf("http server shutdown error: %v", err)
		}
	}()

	// ── 步骤 6：打印启动信息并开始监听 ──────────────────────────────
	// 输出本代理的 HTTP 监听地址，方便调试确认
	fmt.Printf("Go gRPC proxy listening on http://%s\n", cfg.ConsoleAddress())
	// 输出上游 agent 的 gRPC 地址，方便调试确认连接目标
	fmt.Printf("Upstream agent gRPC: %s\n", cfg.AgentAddress())
	// 启动 HTTP 服务器，开始监听并接受连接。
	// 该方法会阻塞当前 goroutine，直到服务器关闭。
	// 正常关闭时返回 http.ErrServerClosed，忽略该错误；
	// 其他错误（如端口冲突）视为致命错误，打印日志并退出进程。
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("http server failed: %v", err) // 致命错误：无法启动 HTTP 服务
	}
}
