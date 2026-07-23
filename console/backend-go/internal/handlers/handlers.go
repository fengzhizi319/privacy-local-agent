// Package handlers 实现 Go gRPC 代理后端的 HTTP REST 接口层。
//
// 职责：
//   - 接收前端 React 控制台的 HTTP/JSON 请求
//   - 通过 mapper 将 REST 路径映射为对应的 gRPC 调用
//   - 将 protobuf 响应转换为前端可展示的 JSON 格式
//   - 可选托管前端静态构建产物，使 Go 后端可独立提供完整 Console UI
//
// 设计目标：
//
//	与 Python REST 代理后端保持完全一致的 JSON 契约，
//	前端只需切换 base URL 即可在两种后端之间无缝切换。
//
// 路由清单：
//
//	GET  /api/health   → 健康检查（后端自身 + 上游 agent）
//	GET  /api/samples  → 返回所有端点的示例 payload
//	POST /api/proxy    → 单请求代理转发（REST → gRPC）
//	POST /api/batch    → 批量请求转发
//	POST /api/upload   → 文件上传 + 隐私处理（脱敏/K-匿名/分类）
//	POST /api/lb_test  → 负载均衡策略测试
package handlers

import (
	// encoding/json：用于 JSON 序列化/反序列化（params 解析、RecordEntry 转换）
	"encoding/json"
	// fmt：用于格式化错误信息与日志消息
	"fmt"
	// io：用于读取上传文件内容（io.ReadAll）
	"io"
	// log：标准库日志，用于启动信息与静态文件托管状态输出
	"log"
	// net/http：HTTP 状态码常量（http.StatusOK、http.StatusBadRequest 等）
	"net/http"
	// os：用于文件系统操作（检查静态目录是否存在）
	"os"
	// path/filepath：用于跨平台路径拼接（dist 目录、index.html、assets 目录）
	"path/filepath"
	// strings：用于字符串前缀/后缀判断、大小写转换
	"strings"
	// sync：限流中间件的互斥锁（保护进程内请求计数 map）
	"sync"
	// time：用于计算请求耗时（duration_ms）
	"time"

	// gin：高性能 HTTP Web 框架，提供路由、中间件、JSON 响应等能力
	"github.com/gin-gonic/gin"

	// agent：gRPC 客户端封装，提供到上游 agent 的连接与 RPC 调用能力
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/agent"
	// config：运行时配置（监听地址、agent 地址、静态目录等）
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/config"
	// fileparse：文件解析工具，支持 CSV/JSON 格式的数据文件解析
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/fileparse"
	// lbtest：负载均衡测试模块，实现 round_robin/random/least_connections 等策略
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/lbtest"
	// mapper：REST → gRPC 路由映射核心模块，根据 path 分发到对应的 gRPC 方法
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/mapper"
	// models：与前端共享的 JSON 数据结构定义（请求/响应模型）
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/models"
	// samples：内置的示例 payload 集合，供前端 /api/samples 接口使用
	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/samples"
	// pb：由 proto/privacy.proto 生成的 gRPC 代码，包含所有 RPC 方法与消息类型
	pb "github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/proto"
)

// 本控制台后端的身份标识常量，随每个响应下发给前端。
//
// 用途：前端界面展示"当前请求由哪个后端、以何种协议与 agent 通信"，
// 使 Python REST / Go gRPC 两种通信方式的切换可被直观验证。
const (
	// backendVia：标识响应经由的后端类型，"go-grpc" 表示通过 Go 代理后端转发
	backendVia = "go-grpc"
	// agentProtocol：标识与上游 agent 通信的协议，"gRPC" 表示使用 gRPC 调用
	agentProtocol = "gRPC"
)

// Server 聚合 HTTP 处理器所需的全部依赖。
//
// 所有 handler 方法（Health/Proxy/Batch 等）均绑定到 Server 实例，
// 通过其字段访问 gRPC 客户端、路由映射器与运行时配置。
type Server struct {
	// client：到上游 privacy-local-agent 的 gRPC 客户端封装，
	// 提供 Raw() 获取底层 RPC 客户端、WithAuth() 附加认证元数据等方法
	client *agent.Client
	// mapper：REST → gRPC 路由映射器，根据请求 path 查找并调用对应的 gRPC 方法
	mapper *mapper.Mapper
	// cfg：运行时配置，包含 agent 地址、监听地址、静态文件目录等
	cfg *config.Config
}

// New 根据 gRPC 客户端与配置创建 Server 实例。
//
// 执行逻辑：
//  1. 保存 agent.Client 引用，供所有 handler 调用上游 RPC
//  2. 创建 mapper.Mapper 实例，内置所有 REST → gRPC 路径映射规则
//  3. 保存配置引用，供健康检查、静态托管等使用
func New(client *agent.Client, cfg *config.Config) *Server {
	return &Server{
		client: client,       // gRPC 客户端，用于调用上游 agent 的所有 RPC 方法
		mapper: mapper.New(), // REST → gRPC 路由映射器，内置全部路径处理规则
		cfg:    cfg,          // 运行时配置，包含地址、端口、API Key 等
	}
}

// RegisterRoutes 将所有 API 路由挂载到 Gin 引擎上。
//
// 路由注册顺序：
//  1. 全局 CORS 中间件（允许跨域，便于 Vite 开发服务器调用）
//  2. 健康检查接口
//  3. 示例数据接口
//  4. 单请求代理转发接口
//  5. 批量请求转发接口
//  6. 文件上传接口
//  7. 负载均衡测试接口
//  8. 静态文件托管（可选，取决于 dist 目录是否存在）
func (s *Server) RegisterRoutes(r *gin.Engine) {
	// 注册全局 CORS 中间件，允许任意来源的跨域请求
	r.Use(corsMiddleware())
	// 可选安全中间件（API Key 鉴权 + 限流）：默认关闭 / 宽松，
	// 仅在配置了 CONSOLE_API_KEY / CONSOLE_RATE_LIMIT 时生效。
	r.Use(securityMiddleware(s.cfg.ConsoleAPIKey, s.cfg.ConsoleRateLimit))
	// GET /api/health：健康检查，返回后端自身状态与上游 agent 连通性
	r.GET("/api/health", s.Health)
	// GET /api/samples：返回所有端点的示例 payload，供前端请求编辑器使用
	r.GET("/api/samples", s.Samples)
	// POST /api/proxy：单请求代理转发，前端将 REST 请求体发送到该接口，
	// 由 mapper 根据 path 字段分发到对应的 gRPC 方法
	r.POST("/api/proxy", s.Proxy)
	// POST /api/batch：批量请求转发，逐个执行一组请求并汇总成功/失败统计
	r.POST("/api/batch", s.Batch)
	// POST /api/upload：文件上传 + 隐私处理（支持 CSV/JSON 格式的脱敏/K-匿名/分类）
	r.POST("/api/upload", s.Upload)
	// POST /api/lb_test：负载均衡策略测试，按指定策略向多个后端节点分发探测请求
	r.POST("/api/lb_test", s.LbTest)
	// 挂载前端静态构建产物（SPA），使 Go 后端可独立提供完整 Console UI
	s.registerStatic(r)
}

// registerStatic 挂载前端构建产物（SPA），使 Go 后端能独立提供 Console UI，
// 无需依赖 Python 后端。
//
// 执行逻辑：
//  1. 检查配置中的 StaticDistDir 是否为空，空则跳过（纯 API 模式）
//  2. 检查目录是否存在且为合法目录，不存在则跳过
//  3. 检查 index.html 是否存在，不存在则跳过
//  4. 挂载 /assets 静态资源目录
//  5. 注册 SPA 回退路由：非 /api 路由一律返回 index.html
//
// 路由规则与 Python 后端保持一致：
//   - /assets/* → 静态资源（带内容哈希，可强缓存）
//   - 其余非 /api 路由 → 返回 index.html（SPA 回退，禁止缓存）
func (s *Server) registerStatic(r *gin.Engine) {
	// 读取配置中的静态文件目录路径
	distDir := s.cfg.StaticDistDir
	// 目录路径为空时直接返回，仅以 API 模式运行
	if distDir == "" {
		return
	}
	// 检查目录是否存在且为合法目录（非文件）
	info, err := os.Stat(distDir)
	if err != nil || !info.IsDir() {
		// 目录不存在或不是目录时打印日志并跳过，不阻止服务启动
		log.Printf("static dist dir not found (%s), serving API only", distDir)
		return
	}
	// 拼接 index.html 完整路径，检查其是否存在
	indexPath := filepath.Join(distDir, "index.html")
	if _, err := os.Stat(indexPath); err != nil {
		// index.html 不存在说明前端未构建，跳过静态托管
		log.Printf("index.html not found in %s, serving API only", distDir)
		return
	}

	// 检查 assets 子目录是否存在，存在则挂载为静态资源服务
	// /assets/* 路径下的文件带有内容哈希，浏览器可安全强缓存
	if assetsDir := filepath.Join(distDir, "assets"); dirExists(assetsDir) {
		// r.Static 将 /assets 路径映射到本地 assetsDir 目录，
		// Gin 会自动设置正确的 Content-Type 与 Last-Modified 头
		r.Static("/assets", assetsDir)
	}

	// 注册 NoRoute 处理器：当请求不匹配任何已注册路由时触发。
	// 用于实现 SPA 的前端路由回退：
	//   - /api/* 路径 → 返回 404 JSON 错误（API 路由未匹配说明请求无效）
	//   - 其他路径 → 返回 index.html（让前端 React Router 处理路由）
	r.NoRoute(func(c *gin.Context) {
		// 判断请求路径是否以 /api/ 开头
		if strings.HasPrefix(c.Request.URL.Path, "/api/") {
			// API 路由未匹配，返回标准 404 JSON 响应
			c.JSON(http.StatusNotFound, gin.H{"detail": "Not Found", "status": http.StatusNotFound})
			return
		}
		// 非 API 路由：设置 no-cache 响应头，防止浏览器缓存 index.html。
		// 必须禁止缓存，否则重新构建前端后浏览器仍会加载旧版本的 index.html；
		// 而 /assets/* 下的带哈希资源则由浏览器正常缓存（内容变则 URL 变）。
		c.Header("Cache-Control", "no-cache, no-store, must-revalidate")
		// 返回 index.html 文件，由前端 React Router 接管后续路由
		c.File(indexPath)
	})
	// 打印静态托管启用日志，便于调试确认
	log.Printf("Console UI enabled, serving static files from %s", distDir)
}

// dirExists 判断指定路径是否存在且为目录。
// 用于静态文件托管前检查 assets 子目录是否可用。
func dirExists(path string) bool {
	// os.Stat 获取文件/目录信息，err != nil 表示不存在
	info, err := os.Stat(path)
	// 存在且为目录时返回 true
	return err == nil && info.IsDir()
}

// corsMiddleware 返回一个宽松的 CORS 中间件，允许任意来源的跨域请求。
//
// 设计目的：本地开发时前端 Vite 服务器（如 localhost:5173）与后端（localhost:8081）
// 端口不同，浏览器会发送 CORS 预检请求（OPTIONS），必须正确响应才能正常通信。
//
// 安全说明：本控制台为本地工具，不依赖 cookie/凭证，故仅设置
// Access-Control-Allow-Origin: * 而不携带 Access-Control-Allow-Credentials，
// 避免“任意来源 + 凭证”组合带来的跨域凭证泄露风险。
//
// 执行逻辑：
//  1. 设置 Access-Control-Allow-Origin: *（允许任意来源）
//  2. 设置允许的 HTTP 方法：GET、POST、OPTIONS
//  3. 设置允许的请求头：Content-Type、Authorization
//  4. OPTIONS 预检请求直接返回 204，不继续转发到后续处理器
//  5. 非 OPTIONS 请求继续传递到下一个中间件/handler
func corsMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		// 设置 CORS 响应头：允许任意来源跨域访问
		c.Writer.Header().Set("Access-Control-Allow-Origin", "*")
		// 设置允许的 HTTP 方法
		c.Writer.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		// 设置允许的请求头（Content-Type 用于 JSON 请求，Authorization 用于认证）
		c.Writer.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
		// 对 OPTIONS 预检请求直接返回 204 No Content，不继续处理
		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(http.StatusNoContent) // 终止请求链，直接返回 204
			return
		}
		// 非 OPTIONS 请求继续传递到下一个中间件或 handler
		c.Next()
	}
}

// Health 检查 Go 代理自身与上游 agent 的连通性，返回结构化健康状态。
//
// 响应字段与 Python 后端保持一致：
//   - backend：Go 代理自身状态（始终为 "ok"）
//   - agent：上游 agent 状态（"ok" 或 "unreachable"）
//   - agent_url：上游 agent 的 gRPC 地址
//   - latency_ms：Health RPC 调用耗时（毫秒）
//   - error：连接失败时的错误信息
//   - via：后端标识 "go-grpc"
//   - protocol：协议标识 "gRPC"
//
// 前端通过该接口判断后端连接是否正常，并展示状态灯。
func (s *Server) Health(c *gin.Context) {
	// 记录请求开始时间，用于计算 Health RPC 调用耗时
	start := time.Now()
	// 通过 gRPC 客户端调用上游 agent 的 Health RPC
	resp, err := s.client.Health(c.Request.Context())
	// 计算调用耗时（毫秒）
	latency := time.Since(start).Milliseconds()

	if err != nil {
		// 上游 agent 不可达时，backend 仍为 "ok"（Go 代理自身正常），
		// agent 标记为 "unreachable" 并携带错误信息
		c.JSON(http.StatusOK, models.ConsoleHealth{
			Backend:   "ok",                 // Go 代理自身始终正常
			Agent:     "unreachable",        // 上游 agent 无法连接
			AgentURL:  s.cfg.AgentAddress(), // 上游 agent 地址，便于调试
			LatencyMs: &latency,             // 尝试连接的耗时
			Error:     err.Error(),          // 具体错误信息
			Via:       backendVia,           // "go-grpc"
			Protocol:  agentProtocol,        // "gRPC"
		})
		return
	}

	// 上游 agent 正常时，返回其状态与命名空间信息
	c.JSON(http.StatusOK, models.ConsoleHealth{
		Backend:   "ok",                                                                  // Go 代理自身正常
		Agent:     map[string]string{"status": resp.Status, "namespace": resp.Namespace}, // agent 状态详情
		AgentURL:  s.cfg.AgentAddress(),                                                  // 上游 agent 地址
		LatencyMs: &latency,                                                              // Health RPC 调用耗时
		Via:       backendVia,                                                            // "go-grpc"
		Protocol:  agentProtocol,                                                         // "gRPC"
	})
}

// Samples 返回所有 gRPC 支持端点的示例 payload 列表。
//
// 前端在启动时调用该接口获取所有端点的示例数据，
// 填充到侧边导航与请求编辑器中，供用户快速测试。
func (s *Server) Samples(c *gin.Context) {
	// samples.List() 返回内置的示例数据列表，直接序列化为 JSON 返回
	c.JSON(http.StatusOK, models.SamplesResponse{Samples: samples.List()})
}

// Proxy 将前端的单请求转发到上游 agent 的对应 gRPC 方法。
//
// 请求体格式（前端发送到 POST /api/proxy）：
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
//	  "data": { ... },
//	  "via": "go-grpc",
//	  "protocol": "gRPC"
//	}
//
// 执行逻辑：
//  1. 解析前端请求体为 ProxyRequest
//  2. 通过 mapper.Dispatch 根据 path 查找对应的 gRPC 方法并调用
//  3. 将 protobuf 响应转换为 JSON 可序列化的 map 结构
//  4. 返回统一的 ProxyResponse 格式
func (s *Server) Proxy(c *gin.Context) {
	// 解析请求体 JSON，绑定到 ProxyRequest 结构体
	var req models.ProxyRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		// 请求体格式不合法时返回 400 错误
		c.JSON(http.StatusBadRequest, gin.H{"detail": fmt.Sprintf("invalid request body: %v", err)})
		return
	}

	// 前端始终 POST 到 /api/proxy，但原始 method 携带在请求体中。
	// 这里忽略 req.Method，由 mapper 根据 path 决定 gRPC 调用语义。
	// 记录调用开始时间，用于计算 gRPC 调用耗时
	start := time.Now()
	// 核心调用：mapper 根据 req.Path 查找对应的 handler，
	// handler 负责解析 body、构造 protobuf 请求、调用 gRPC、转换响应
	data, err := s.mapper.Dispatch(c.Request.Context(), s.client.Raw(), req.Path, req.Body)
	// 计算 gRPC 调用耗时（毫秒）
	duration := time.Since(start).Milliseconds()

	if err != nil {
		// gRPC 调用失败时根据错误类型决定 HTTP 状态码：
		//   - 上游不可达（连接拒绝/超时/DNS 失败）→ 502 Bad Gateway
		//   - 其他错误（参数错误/业务错误）→ 400 Bad Request
		status := http.StatusBadRequest
		if isUnavailable(err) {
			status = http.StatusBadGateway // 上游连接类错误返回 502
		}
		c.JSON(status, gin.H{"detail": err.Error(), "status": status})
		return
	}

	// 调用成功，返回统一的 ProxyResponse 格式
	c.JSON(http.StatusOK, models.ProxyResponse{
		Status:     http.StatusOK, // HTTP 状态码 200
		DurationMs: duration,      // gRPC 调用耗时（毫秒）
		Data:       data,          // gRPC 响应转换后的 JSON 可序列化数据
		Via:        backendVia,    // "go-grpc"，标识响应经由的后端类型
		Protocol:   agentProtocol, // "gRPC"，标识与 agent 通信的协议
	})
}

// Batch 逐个转发一组请求并汇总成功/失败统计。
//
// 用于前端“一键批量测试”：单个请求失败不会中断整个批次，
// 返回与 Python 后端一致的 {total, passed, failed, results} 结构。
//
// 执行逻辑：
//  1. 解析请求体为 BatchRequest（包含多个待转发请求）
//  2. 逐个调用 mapper.Dispatch 转发到上游 agent
//  3. 每个请求独立记录成功/失败与耗时
//  4. 汇总统计后返回 BatchResponse
func (s *Server) Batch(c *gin.Context) {
	// 解析请求体 JSON，绑定到 BatchRequest 结构体
	var req models.BatchRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		// 请求体格式不合法时返回 400 错误
		c.JSON(http.StatusBadRequest, gin.H{"detail": fmt.Sprintf("invalid request body: %v", err)})
		return
	}

	// 预分配结果切片，容量为请求数量以避免多次扩容
	results := make([]models.BatchResultItem, 0, len(req.Requests))
	// 成功计数器
	passed := 0
	// 逐个转发每个请求，单个失败不中断整个批次
	for _, item := range req.Requests {
		// 将 HTTP 方法转为大写（如 "post" → "POST"），用于结果展示
		method := strings.ToUpper(item.Method)
		// 记录单个请求的开始时间
		start := time.Now()
		// 通过 mapper 转发到上游 agent 的对应 gRPC 方法
		data, err := s.mapper.Dispatch(c.Request.Context(), s.client.Raw(), item.Path, item.Body)
		// 计算单个请求耗时（毫秒）
		duration := time.Since(start).Milliseconds()

		if err != nil {
			// 单个请求失败时根据错误类型决定 HTTP 状态码
			status := http.StatusBadRequest
			if isUnavailable(err) {
				status = http.StatusBadGateway // 上游不可达返回 502
			}
			// 记录失败结果，包含错误信息，继续处理下一个请求
			results = append(results, models.BatchResultItem{
				Method:     method,      // HTTP 方法
				Path:       item.Path,   // 请求路径
				Status:     status,      // HTTP 状态码
				DurationMs: duration,    // 耗时（毫秒）
				Error:      err.Error(), // 错误信息
			})
			continue // 跳过后续成功逻辑，处理下一个请求
		}

		// 请求成功：累加成功计数并记录结果
		passed++
		results = append(results, models.BatchResultItem{
			Method:     method,        // HTTP 方法
			Path:       item.Path,     // 请求路径
			Status:     http.StatusOK, // 成功状态码 200
			DurationMs: duration,      // 耗时（毫秒）
			Data:       data,          // gRPC 响应数据
		})
	}

	// 返回批量测试汇总结果
	c.JSON(http.StatusOK, models.BatchResponse{
		Total:    len(results),          // 总请求数
		Passed:   passed,                // 成功数
		Failed:   len(results) - passed, // 失败数
		Results:  results,               // 逐条结果详情
		Via:      backendVia,            // "go-grpc"
		Protocol: agentProtocol,         // "gRPC"
	})
}

// Upload 接收前端上传的 CSV/JSON 文件并执行隐私处理。
//
// 支持的表单字段：
//   - file：数据文件（.csv 或 .json）
//   - operation：操作类型（mask_dataframe | k_anonymize | classify_table）
//   - params：JSON 字符串，如 {"columns":[...],"qi_cols":[...],"k":2,"context":""}
//
// 执行逻辑：
//  1. 从 multipart 表单中读取上传文件
//  2. 按文件扩展名解析为 records + schema
//  3. 解析 params JSON 为操作参数
//  4. 根据 operation 调用对应的 gRPC 方法
//  5. 返回统一的 ProxyResponse（data 为 UploadData）
func (s *Server) Upload(c *gin.Context) {
	// 从 multipart 表单中读取名为 "file" 的上传文件
	// file：文件读取句柄；header：文件元信息（文件名、大小等）
	file, header, err := c.Request.FormFile("file")
	if err != nil {
		// 缺少文件或读取失败时返回 400 错误
		c.JSON(http.StatusBadRequest, gin.H{"detail": fmt.Sprintf("缺少文件: %v", err), "status": http.StatusBadRequest})
		return
	}
	// 注册 defer：函数退出时自动关闭文件句柄，释放资源
	defer file.Close()

	// 上传大小限制：超限返回 413，避免大文件耗尽内存（DoS 防护）。
	if s.cfg.MaxUploadBytes > 0 && header.Size > s.cfg.MaxUploadBytes {
		c.JSON(http.StatusRequestEntityTooLarge, gin.H{
			"detail": fmt.Sprintf("文件过大（%d 字节），上限 %d 字节", header.Size, s.cfg.MaxUploadBytes),
			"status": http.StatusRequestEntityTooLarge,
		})
		return
	}

	// 读取表单中的 operation 字段，决定执行哪种隐私处理操作
	operation := c.PostForm("operation")
	// 读取表单中的 params 字段，JSON 格式的操作参数
	params := c.PostForm("params")
	// params 为空时默认为空 JSON 对象，避免后续解析失败
	if params == "" {
		params = "{}"
	}

	// 读取文件全部内容到内存（适用于中小文件）
	content, err := io.ReadAll(file)
	if err != nil {
		// 文件读取失败时返回 400 错误
		c.JSON(http.StatusBadRequest, gin.H{"detail": fmt.Sprintf("读取文件失败: %v", err), "status": http.StatusBadRequest})
		return
	}

	// 按文件扩展名解析为 records（行数据）+ schema（列名列表）
	var records []map[string]string // 每行是一个 map[column_name]value
	var schema []string             // 列名列表（从文件头自动提取）
	// 将文件名转为小写，确保扩展名匹配不区分大小写
	filename := strings.ToLower(header.Filename)
	switch {
	case strings.HasSuffix(filename, ".csv"):
		// CSV 文件：解析表头为 schema，每行解析为 map
		records, schema, err = fileparse.ParseCSV(content)
	case strings.HasSuffix(filename, ".json"):
		// JSON 文件：解析为对象数组，键名作为 schema
		records, schema, err = fileparse.ParseJSON(content)
	default:
		// 不支持的文件格式时返回 400 错误
		c.JSON(http.StatusBadRequest, gin.H{"detail": "仅支持 .csv 与 .json 文件", "status": http.StatusBadRequest})
		return
	}
	if err != nil {
		// 文件解析失败（如格式不合法）时返回 400 错误
		c.JSON(http.StatusBadRequest, gin.H{"detail": err.Error(), "status": http.StatusBadRequest})
		return
	}

	// 解析 params 字段为 map，用于提取操作参数（columns、qi_cols、k 等）
	var options map[string]any
	if err := json.Unmarshal([]byte(params), &options); err != nil {
		// params 不是合法 JSON 时返回 400 错误
		c.JSON(http.StatusBadRequest, gin.H{"detail": fmt.Sprintf("params 需为合法 JSON: %v", err), "status": http.StatusBadRequest})
		return
	}

	// 将 records 转换为 gRPC 的 RecordEntry 列表（protobuf 格式）
	entries := toRecordEntries(records)
	// 记录输入行数，用于响应中返回
	rowsIn := len(records)
	// 获取底层 gRPC 客户端，用于直接调用 RPC 方法
	client := s.client.Raw()
	// 使用请求的 context，支持客户端取消操作
	ctx := c.Request.Context()

	// 记录操作开始时间，用于计算总耗时
	start := time.Now()
	// result 保存最终操作结果（不同操作返回不同类型）
	var result any
	// rowsOut 保存输出行数
	var rowsOut int

	// 根据 operation 分发到对应的 gRPC 方法
	switch operation {
	case "mask_dataframe":
		// 脱敏操作：调用 MaskDataFrame gRPC 方法
		resp, e := client.MaskDataFrame(ctx, &pb.MaskDataFrameRequest{
			Data:    entries,                         // 输入数据
			Columns: stringSlice(options, "columns"), // 需脱敏的列名列表
			Context: stringVal(options, "context"),   // 脱敏上下文（影响脱敏策略）
		})
		if e != nil {
			// gRPC 调用失败时转换为 HTTP 错误响应
			s.writeUpstreamError(c, e)
			return
		}
		// 将 protobuf RecordEntry 列表转回 map 数组，便于 JSON 序列化
		result = recordEntriesToMaps(resp.Data)
		rowsOut = len(resp.Data) // 输出行数等于响应数据行数

	case "k_anonymize":
		// K-匿名操作：提取准标识符列名（必填参数）
		qiCols := stringSlice(options, "qi_cols")
		if len(qiCols) == 0 {
			// 缺少 qi_cols 参数时返回 400 错误
			c.JSON(http.StatusBadRequest, gin.H{"detail": "k_anonymize 操作需提供 qi_cols 参数", "status": http.StatusBadRequest})
			return
		}
		// 调用 KAnonymizeDataFrame gRPC 方法
		resp, e := client.KAnonymizeDataFrame(ctx, &pb.KAnonymizeDataFrameRequest{
			Data:     entries,                            // 输入数据
			QiCols:   qiCols,                             // 准标识符列名列表
			K:        int32Val(options, "k", 5),          // K 值，默认 5
			MaxDepth: int32Val(options, "max_depth", 10), // 最大泛化深度，默认 10
		})
		if e != nil {
			// gRPC 调用失败时转换为 HTTP 错误响应
			s.writeUpstreamError(c, e)
			return
		}
		// 将 protobuf RecordEntry 列表转回 map 数组
		result = recordEntriesToMaps(resp.Data)
		rowsOut = len(resp.Data) // 输出行数等于响应数据行数

	case "classify_table":
		// 分类操作：优先使用 params 中指定的 schema，否则使用文件解析出的 schema
		schemaUse := stringSlice(options, "schema")
		if len(schemaUse) == 0 {
			schemaUse = schema // 回退到文件解析出的列名
		}
		// 分类参数取 params 内嵌套的 params 字段（与 agent process_file 一致）
		paramsJSON := "{}"
		if p, ok := options["params"]; ok {
			// 将嵌套的 params 对象序列化回 JSON 字符串
			if b, e := json.Marshal(p); e == nil {
				paramsJSON = string(b)
			}
		}
		// 调用 ClassifyTable gRPC 方法
		resp, e := client.ClassifyTable(ctx, &pb.ClassifyTableRequest{
			Schema:     schemaUse,  // 表结构（列名列表）
			Rows:       entries,    // 输入数据
			ParamsJson: paramsJSON, // 分类参数 JSON 字符串
		})
		if e != nil {
			// gRPC 调用失败时转换为 HTTP 错误响应
			s.writeUpstreamError(c, e)
			return
		}
		// 尝试将结果 JSON 反序列化为通用对象，失败时保留原始 JSON 字符串
		var parsed any
		if e := json.Unmarshal([]byte(resp.ResultJson), &parsed); e != nil {
			parsed = resp.ResultJson // 反序列化失败时保留原始字符串
		}
		result = parsed
		rowsOut = rowsIn // 分类操作不改变行数

	default:
		// 不支持的操作类型时返回 400 错误，并列出可选操作
		c.JSON(http.StatusBadRequest, gin.H{
			"detail": fmt.Sprintf("不支持的操作 '%s'，可选: classify_table, k_anonymize, mask_dataframe", operation),
			"status": http.StatusBadRequest,
		})
		return
	}

	// 计算操作总耗时（毫秒）
	duration := time.Since(start).Milliseconds()
	// 返回统一的 ProxyResponse 格式，data 为 UploadData 结构
	c.JSON(http.StatusOK, models.ProxyResponse{
		Status:     http.StatusOK, // HTTP 状态码 200
		DurationMs: duration,      // 操作总耗时（毫秒）
		Data: models.UploadData{
			Operation: operation, // 操作类型
			RowsIn:    rowsIn,    // 输入行数
			RowsOut:   rowsOut,   // 输出行数
			Result:    result,    // 操作结果
		},
		Via:      backendVia,    // "go-grpc"
		Protocol: agentProtocol, // "gRPC"
	})
}

// LbTest 按策略向多个后端节点分发探测请求并统计结果。
//
// 由控制台后端自行实现策略分发（round_robin / random / least_connections），
// 探测目标为用户填写的各 agent REST 地址，返回各节点命中数与延迟分布。
//
// 执行逻辑：
//  1. 解析请求体为 LbTestRequest（包含节点列表、策略、探测次数等）
//  2. 调用 lbtest.Run 执行策略分发与探测
//  3. 返回各节点的命中数与延迟统计
func (s *Server) LbTest(c *gin.Context) {
	// 解析请求体 JSON，绑定到 LbTestRequest 结构体
	var req models.LbTestRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		// 请求体格式不合法时返回 400 错误
		c.JSON(http.StatusBadRequest, gin.H{"detail": fmt.Sprintf("invalid request body: %v", err), "status": http.StatusBadRequest})
		return
	}
	// SSRF 防护：逐个校验探测目标 URL 的 scheme / host 白名单。
	if err := lbtest.ValidateBackends(req.Backends, splitHosts(s.cfg.LBAllowedHosts)); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"detail": err.Error(), "status": http.StatusBadRequest})
		return
	}
	// 调用 lbtest 模块执行负载均衡测试，第三个参数为可选的自定义 HTTP 客户端（nil 使用默认）
	resp, err := lbtest.Run(c.Request.Context(), req, nil)
	if err != nil {
		// 测试执行失败时返回 400 错误，包含具体错误信息
		c.JSON(http.StatusBadRequest, gin.H{"detail": err.Error(), "status": http.StatusBadRequest})
		return
	}
	// 返回测试结果 JSON
	c.JSON(http.StatusOK, resp)
}

// writeUpstreamError 将 gRPC 上游错误转换为 HTTP JSON 响应。
//
// 错误分类策略：
//   - 连接类错误（上游不可达/超时/DNS 失败）→ 502 Bad Gateway
//   - 其他错误（参数错误/业务错误）→ 400 Bad Request
//
// 该方法是 Proxy/Upload 等多个 handler 的公共错误处理入口。
func (s *Server) writeUpstreamError(c *gin.Context, err error) {
	// 默认返回 400（客户端错误）
	status := http.StatusBadRequest
	// 如果是上游连接类错误，则返回 502（网关错误）
	if isUnavailable(err) {
		status = http.StatusBadGateway
	}
	// 返回 JSON 格式的错误响应，包含错误详情与状态码
	c.JSON(status, gin.H{"detail": err.Error(), "status": status})
}

// toRecordEntries 将 Go map 数组转换为 gRPC RecordEntry 列表。
//
// 前端上传的文件解析结果为 []map[string]string，
// 而 gRPC 接口要求 []*pb.RecordEntry 格式，
// 本函数负责完成两种表示之间的转换。
func toRecordEntries(records []map[string]string) []*pb.RecordEntry {
	// 预分配切片，容量等于记录数以避免多次扩容
	entries := make([]*pb.RecordEntry, 0, len(records))
	// 遍历每条记录，将 map 转换为 RecordEntry 的 Fields 字段
	for _, r := range records {
		// 创建新 map 副本，避免修改原始数据
		fields := make(map[string]string, len(r))
		for k, v := range r {
			fields[k] = v
		}
		// 将 map 包装为 RecordEntry 并追加到结果列表
		entries = append(entries, &pb.RecordEntry{Fields: fields})
	}
	return entries
}

// recordEntriesToMaps 将 gRPC RecordEntry 列表转换回 Go map 数组。
//
// 与 toRecordEntries 相反，用于将 gRPC 响应转换为 JSON 可序列化格式，
// 便于前端直接展示。
func recordEntriesToMaps(entries []*pb.RecordEntry) []map[string]string {
	// 预分配切片，容量等于条目数
	out := make([]map[string]string, 0, len(entries))
	// 直接取出每个 RecordEntry 的 Fields map 追加到结果列表
	for _, e := range entries {
		out = append(out, e.Fields)
	}
	return out
}

// stringSlice 从 JSON 解析后的 map 中提取字符串数组字段。
//
// JSON 反序列化后数组类型为 []any，元素类型为 any，
// 本函数负责安全地类型断言并转换为 []string。
// 字段不存在或类型不匹配时返回 nil。
func stringSlice(m map[string]any, key string) []string {
	// 查找指定 key 是否存在
	if v, ok := m[key]; ok {
		// 尝试将值断言为 []any（JSON 数组反序列化后的默认类型）
		if arr, ok := v.([]any); ok {
			// 预分配切片，容量为数组长度
			out := make([]string, 0, len(arr))
			// 遍历数组元素，仅保留字符串类型的元素
			for _, item := range arr {
				if s, ok := item.(string); ok {
					out = append(out, s)
				}
			}
			return out
		}
	}
	// 字段不存在或类型不匹配时返回 nil
	return nil
}

// stringVal 从 JSON 解析后的 map 中提取字符串字段。
// 字段不存在或类型不是 string 时返回空字符串。
func stringVal(m map[string]any, key string) string {
	// 查找指定 key 是否存在
	if v, ok := m[key]; ok {
		// 尝试将值断言为 string 类型
		if s, ok := v.(string); ok {
			return s
		}
	}
	// 字段不存在或类型不匹配时返回空字符串
	return ""
}

// int32Val 从 JSON 解析后的 map 中提取整数字段。
//
// JSON 数字在 Go 中反序列化为 float64，
// 本函数支持 float64、int、int64 三种类型的安全转换。
// 字段不存在或类型不匹配时返回默认值 def。
func int32Val(m map[string]any, key string, def int32) int32 {
	// 查找指定 key 是否存在
	if v, ok := m[key]; ok {
		// 使用类型 switch 处理 JSON 数字可能的 Go 类型
		switch n := v.(type) {
		case float64:
			// JSON 数字默认反序列化为 float64，直接截断为 int32
			return int32(n)
		case int:
			// 部分场景下可能为 int 类型
			return int32(n)
		case int64:
			// 部分场景下可能为 int64 类型
			return int32(n)
		}
	}
	// 字段不存在或类型不匹配时返回默认值
	return def
}

// isUnavailable 判断错误是否表示上游 agent 不可达。
//
// 这是一个简化的启发式判断，通过检查错误消息中是否包含
// 连接类关键词来区分“上游连接错误”与“参数/业务错误”：
//   - 连接拒绝（connection refused）：agent 未启动或端口错误
//   - DNS 解析失败（dns）：主机名无法解析
//   - 超时（timeout）：网络不通或 agent 响应过慢
//   - gRPC Unavailable（Unavailable）：gRPC 标准不可用状态码
//
// 返回 true 表示应返回 502 Bad Gateway，false 表示应返回 400 Bad Request。
func isUnavailable(err error) bool {
	// nil 错误表示无异常，不属于不可达
	if err == nil {
		return false
	}
	// 获取错误消息文本
	msg := err.Error()
	// 检查是否包含任意连接类关键词
	return containsAny(msg, []string{"connection refused", "dns", "timeout", "Unavailable"})
}

// containsAny 检查字符串 s 是否包含 subs 列表中的任意一个子串。
// 用于 isUnavailable 中匹配连接类错误关键词。
func containsAny(s string, subs []string) bool {
	// 遍历子串列表，任一匹配即返回 true
	for _, sub := range subs {
		if strings.Contains(s, sub) {
			return true
		}
	}
	// 全部不匹配时返回 false
	return false
}

// securityMiddleware 返回可选的 API Key 鉴权 + 限流中间件（默认关闭 / 宽松）。
//
//   - apiKey 非空时，/api/*（除 /api/health）需携带 Authorization: Bearer <key>；
//   - rateLimit > 0 时，每分钟每客户端 IP 超过该阈值返回 429（进程内滑动窗口）。
//
// CORS 预检（OPTIONS）已由 corsMiddleware 提前返回 204，不会进入本中间件；
// 静态资源等非 /api 路径与 /api/health 均子以豁免。
func securityMiddleware(apiKey string, rateLimit int) gin.HandlerFunc {
	// 限流状态：每个客户端 IP 的请求时间戳列表（60 秒滑动窗口）。
	var mu sync.Mutex
	hits := make(map[string][]time.Time)
	return func(c *gin.Context) {
		path := c.Request.URL.Path
		// 仅对 /api/* 生效；健康检查豁免。
		if !strings.HasPrefix(path, "/api/") || path == "/api/health" {
			c.Next()
			return
		}
		// API Key 鉴权（配置了才校验）。
		if apiKey != "" {
			if extractBearer(c.GetHeader("Authorization")) != apiKey {
				c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"detail": "Unauthorized: invalid console api key"})
				return
			}
		}
		// 限流（rateLimit <= 0 时关闭）。
		if rateLimit > 0 {
			ip := c.ClientIP()
			now := time.Now()
			cutoff := now.Add(-60 * time.Second)
			mu.Lock()
			window := hits[ip]
			// 就地过滤掉 60 秒窗口外的旧记录。
			kept := window[:0]
			for _, t := range window {
				if t.After(cutoff) {
					kept = append(kept, t)
				}
			}
			if len(kept) >= rateLimit {
				hits[ip] = kept
				mu.Unlock()
				c.AbortWithStatusJSON(http.StatusTooManyRequests, gin.H{"detail": "Too many requests"})
				return
			}
			hits[ip] = append(kept, now)
			mu.Unlock()
		}
		c.Next()
	}
}

// extractBearer 从 Authorization 头提取 Bearer token，格式不符时返回空字符串。
func extractBearer(header string) string {
	parts := strings.Fields(header)
	if len(parts) == 2 && strings.EqualFold(parts[0], "bearer") {
		return parts[1]
	}
	return ""
}

// splitHosts 把逗号分隔的 host 白名单字符串拆分为去除空白后的切片；
// 空字符串返回 nil（表示不限制）。
func splitHosts(s string) []string {
	if strings.TrimSpace(s) == "" {
		return nil
	}
	parts := strings.Split(s, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}
