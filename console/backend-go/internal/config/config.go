// Package config 提供 Go gRPC 代理后端的集中化配置管理。
//
// 设计原则：
//   - 所有配置项均通过环境变量读取，零配置文件依赖
//   - 每项配置均有合理的本地开发默认值，开箱即用
//   - 支持通过环境变量快速切换目标 agent 地址、监听端口、认证信息等
//
// 环境变量清单：
//
//	| 变量名                          | 默认值        | 说明                              |
//	|---------------------------------|---------------|-----------------------------------|
//	| PRIVACY_AGENT_GRPC_HOST         | 127.0.0.1     | 上游 agent gRPC 主机               |
//	| PRIVACY_AGENT_GRPC_PORT         | 50051         | 上游 agent gRPC 端口               |
//	| PRIVACY_AGENT_API_KEY           | (空)          | 可选的 Bearer Token 认证密钥        |
//	| PRIVACY_CONSOLE_HOST            | 127.0.0.1     | 本代理 HTTP 监听地址               |
//	| PRIVACY_CONSOLE_PORT            | 8081          | 本代理 HTTP 监听端口               |
//	| PRIVACY_CONSOLE_STATIC_DIR      | ../web/dist   | 前端构建产物目录，设为空则禁用静态托管 |
//	| PRIVACY_AGENT_TLS_ENABLED       | false         | 是否启用上游 gRPC 连接的 TLS/mTLS    |
//	| PRIVACY_AGENT_TLS_CERT_FILE     | (空)          | 客户端证书文件（mTLS 双向认证）       |
//	| PRIVACY_AGENT_TLS_KEY_FILE      | (空)          | 客户端私钥文件（mTLS 双向认证）       |
//	| PRIVACY_AGENT_TLS_CA_FILE       | (空)          | 校验服务端证书的 CA 文件，TLS 启用时必填 |
//	| PRIVACY_AGENT_TLS_SERVER_NAME   | (空)          | 服务端证书主机名覆盖值               |
//	| PRIVACY_AGENT_TLS_INSECURE_SKIP_VERIFY | false  | 是否跳过服务端证书校验（仅测试）     |
package config

import (
	// os：用于读取系统环境变量
	"os"
	// strconv：用于字符串与整数之间的类型转换（端口号解析）
	"strconv"
	// strings：用于布尔环境变量的大小写归一化与去空白
	"strings"
)

// Config 保存 Go gRPC 代理服务器运行时的所有配置项。
// 通过 Load() 从环境变量一次性加载，运行期间只读不修改。
type Config struct {
	// AgentGRPCHost：上游 privacy-local-agent gRPC 服务的主机名或 IP 地址。
	// 对应环境变量 PRIVACY_AGENT_GRPC_HOST，默认 "127.0.0.1"。
	AgentGRPCHost string

	// AgentGRPCPort：上游 agent gRPC 服务的监听端口。
	// 对应环境变量 PRIVACY_AGENT_GRPC_PORT，默认 50051。
	// 与 AgentGRPCHost 组合后形成完整的 gRPC 目标地址（如 "127.0.0.1:50051"）。
	AgentGRPCPort int

	// AgentAPIKey：可选的 Bearer Token，用于上游 agent 开启认证时的身份验证。
	// 对应环境变量 PRIVACY_AGENT_API_KEY，默认为空（不认证）。
	// 非空时每次 gRPC 调用会自动附加 "authorization: Bearer <key>" 元数据。
	AgentAPIKey string

	// ConsoleHost：本 Go 代理 HTTP 服务器的绑定地址。
	// 对应环境变量 PRIVACY_CONSOLE_HOST，默认 "127.0.0.1"。
	ConsoleHost string

	// ConsolePort：本 Go 代理 HTTP 服务器的监听端口。
	// 对应环境变量 PRIVACY_CONSOLE_PORT，默认 8081。
	// 与 ConsoleHost 组合后形成完整的 HTTP 监听地址（如 "127.0.0.1:8081"）。
	ConsolePort int

	// StaticDistDir：前端 React 构建产物的目录路径。
	// 对应环境变量 PRIVACY_CONSOLE_STATIC_DIR，默认 "../web/dist"。
	// 当该目录存在时，Go 服务器同时托管 Console UI 静态文件；
	// 设为空字符串则禁用静态托管，仅作为纯 API 代理。
	StaticDistDir string

	// AgentTLSEnabled：是否对上游 agent 的 gRPC 连接启用 TLS/mTLS。
	// 对应环境变量 PRIVACY_AGENT_TLS_ENABLED，默认 false（使用非安全传输）。
	// 启用后必须提供 CA 证书（AgentTLSCAFile）以校验服务端身份。
	AgentTLSEnabled bool

	// AgentTLSCertFile：本代理作为 gRPC 客户端的证书文件路径（PEM）。
	// 对应环境变量 PRIVACY_AGENT_TLS_CERT_FILE，默认空。
	// 与 AgentTLSKeyFile 配对使用，用于向服务端证明客户端身份（mTLS 双向认证）。
	AgentTLSCertFile string

	// AgentTLSKeyFile：本代理作为 gRPC 客户端的私钥文件路径（PEM）。
	// 对应环境变量 PRIVACY_AGENT_TLS_KEY_FILE，默认空。
	// 必须与 AgentTLSCertFile 同时提供，否则无法完成客户端身份认证。
	AgentTLSKeyFile string

	// AgentTLSCAFile：用于校验上游 agent 服务端证书的 CA 证书文件路径（PEM）。
	// 对应环境变量 PRIVACY_AGENT_TLS_CA_FILE，默认空。
	// TLS 启用时必填：客户端用它验证服务端证书是否由受信任 CA 签发。
	AgentTLSCAFile string

	// AgentTLSServerName：TLS 握手时用于校验服务端证书的主机名覆盖值。
	// 对应环境变量 PRIVACY_AGENT_TLS_SERVER_NAME，默认空（使用连接目标地址）。
	// 典型场景：连接 127.0.0.1 但证书 SAN 仅含 localhost 时，设为 "localhost"。
	AgentTLSServerName string

	// AgentTLSInsecureSkipVerify：是否跳过服务端证书校验（仅限测试）。
	// 对应环境变量 PRIVACY_AGENT_TLS_INSECURE_SKIP_VERIFY，默认 false。
	// 设为 true 时不校验服务端证书链与主机名，存在中间人攻击风险，生产环境严禁启用。
	AgentTLSInsecureSkipVerify bool

	// ── 可选安全加固配置（默认关闭 / 宽松，本地开发零配置即可运行）──────────────

	// ConsoleAPIKey：可选的控制台 API Key。
	// 对应环境变量 CONSOLE_API_KEY，默认空（不鉴权）。
	// 非空时 /api/*（除 /api/health）需携带 Authorization: Bearer <key>。
	ConsoleAPIKey string

	// ConsoleRateLimit：每分钟每客户端 IP 的最大请求数。
	// 对应环境变量 CONSOLE_RATE_LIMIT，默认 600；设为 0 关闭限流。
	ConsoleRateLimit int

	// MaxUploadBytes：上传文件大小上限（字节）。
	// 对应环境变量 CONSOLE_MAX_UPLOAD_BYTES，默认 10MB；超限返回 413。
	MaxUploadBytes int64

	// LBAllowedHosts：负载均衡探测目标 host 白名单（逗号分隔）。
	// 对应环境变量 LB_ALLOWED_HOSTS，默认空（不限制，本地探测默认行为）。
	LBAllowedHosts string
}

// Load 从环境变量读取所有配置项，返回填充完毕的 Config 实例。
//
// 执行逻辑：
//  1. 依次读取各环境变量，不存在则使用默认值
//  2. 端口号类配置自动解析为 int 类型，解析失败时回退到默认值
//  3. StaticDistDir 使用 getEnvOptional：显式设为空字符串即禁用静态托管
//
// 典型用法：
//
//	cfg := config.Load()  // 在 main 函数启动时调用一次
func Load() *Config {
	return &Config{
		// 上游 agent gRPC 主机地址，默认 127.0.0.1（本地开发场景）
		AgentGRPCHost: getEnv("PRIVACY_AGENT_GRPC_HOST", "127.0.0.1"),
		// 上游 agent gRPC 端口，默认 50051（与 privacy-local-agent 默认 gRPC 端口一致）
		AgentGRPCPort: getEnvInt("PRIVACY_AGENT_GRPC_PORT", 50051),
		// 认证 API Key，默认为空（不启用认证）
		AgentAPIKey: getEnv("PRIVACY_AGENT_API_KEY", ""),
		// 本代理 HTTP 监听地址，默认 127.0.0.1
		ConsoleHost: getEnv("PRIVACY_CONSOLE_HOST", "127.0.0.1"),
		// 本代理 HTTP 监听端口，默认 8081
		ConsolePort: getEnvInt("PRIVACY_CONSOLE_PORT", 8081),
		// 前端静态文件目录，使用 getEnvOptional 以支持"设为空即禁用"语义
		StaticDistDir: getEnvOptional("PRIVACY_CONSOLE_STATIC_DIR", "../web/dist"),
		// 是否启用上游 gRPC 连接的 TLS/mTLS，默认关闭（非安全传输）
		AgentTLSEnabled: getEnvBool("PRIVACY_AGENT_TLS_ENABLED", false),
		// 客户端证书文件（mTLS 双向认证），默认空
		AgentTLSCertFile: getEnv("PRIVACY_AGENT_TLS_CERT_FILE", ""),
		// 客户端私钥文件（mTLS 双向认证），默认空
		AgentTLSKeyFile: getEnv("PRIVACY_AGENT_TLS_KEY_FILE", ""),
		// 校验服务端证书的 CA 文件，TLS 启用时必填
		AgentTLSCAFile: getEnv("PRIVACY_AGENT_TLS_CA_FILE", ""),
		// 服务端证书主机名覆盖值，默认空（使用连接目标地址）
		AgentTLSServerName: getEnv("PRIVACY_AGENT_TLS_SERVER_NAME", ""),
		// 是否跳过服务端证书校验（仅测试用），默认关闭
		AgentTLSInsecureSkipVerify: getEnvBool("PRIVACY_AGENT_TLS_INSECURE_SKIP_VERIFY", false),
		// 可选控制台 API Key，默认空（不鉴权）
		ConsoleAPIKey: getEnv("CONSOLE_API_KEY", ""),
		// 限流：每分钟每 IP 最大请求数，默认 600（0 关闭）
		ConsoleRateLimit: getEnvInt("CONSOLE_RATE_LIMIT", 600),
		// 上传文件大小上限，默认 10MB
		MaxUploadBytes: int64(getEnvInt("CONSOLE_MAX_UPLOAD_BYTES", 10*1024*1024)),
		// 负载均衡探测 host 白名单，默认空（不限制）
		LBAllowedHosts: getEnv("LB_ALLOWED_HOSTS", ""),
	}
}

// getEnv 读取指定环境变量的字符串值，不存在或为空时返回默认值。
//
// 执行逻辑：
//  1. 调用 os.Getenv 获取环境变量值
//  2. 值非空则直接返回
//  3. 值为空或变量未设置则返回 defaultValue
//
// 适用场景：字符串类型配置项（主机名、API Key 等）。
func getEnv(name, defaultValue string) string {
	// os.Getenv 在变量未设置时返回空字符串，无法区分"未设置"与"显式设为空"
	if v := os.Getenv(name); v != "" {
		return v // 环境变量存在且非空，直接使用
	}
	return defaultValue // 环境变量不存在或为空，回退到默认值
}

// getEnvOptional 读取环境变量，区分"未设置"与"显式设为空字符串"。
//
// 与 getEnv 的核心区别：
//   - getEnv：空字符串等同于未设置，回退到默认值
//   - getEnvOptional：空字符串是合法值，仅在变量完全未设置时才使用默认值
//
// 这样支持"设为空即禁用"的语义，例如：
//
//	PRIVACY_CONSOLE_STATIC_DIR=  → 禁用静态文件托管
//	不设置该变量              → 使用默认值 "../web/dist"
func getEnvOptional(name, defaultValue string) string {
	// os.LookupEnv 返回 (value, exists)，可区分"未设置"与"设为空"
	if v, ok := os.LookupEnv(name); ok {
		return v // 环境变量存在（即使是空字符串也返回）
	}
	return defaultValue // 环境变量完全未设置，使用默认值
}

// getEnvInt 读取环境变量并解析为 int 类型，解析失败或不存在时返回默认值。
//
// 执行逻辑：
//  1. 读取环境变量字符串值
//  2. 为空则返回默认值（快速路径）
//  3. 调用 strconv.Atoi 尝试解析为整数
//  4. 解析失败（如非数字字符）则静默回退到默认值，不报错
//
// 适用场景：端口号等整数类型配置项。
func getEnvInt(name string, defaultValue int) int {
	// 读取环境变量原始值
	v := os.Getenv(name)
	// 未设置或为空字符串时直接返回默认值，避免无效解析
	if v == "" {
		return defaultValue
	}
	// 尝试将字符串解析为十进制整数
	i, err := strconv.Atoi(v)
	if err != nil {
		// 解析失败（如用户误输入 "abc"）时静默回退到默认值，
		// 不中断程序启动，降低配置错误导致的启动失败风险
		return defaultValue
	}
	// 解析成功，返回整数值
	return i
}

// getEnvBool 读取环境变量并解析为 bool 类型，不存在或无法识别时返回默认值。
//
// 执行逻辑：
//  1. 读取环境变量字符串值并转为小写
//  2. 值为空则返回默认值
//  3. 值为 "true"/"1"/"yes"/"on" 之一时返回 true，其余一律返回 false
//
// 适用场景：TLS 开关等布尔类型配置项。
func getEnvBool(name string, defaultValue bool) bool {
	// 读取环境变量原始值
	v := os.Getenv(name)
	// 未设置或为空字符串时直接返回默认值
	if v == "" {
		return defaultValue
	}
	// 统一转小写后匹配常见真值字面量，其余值（含 "false"/"0"）视为 false
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "true", "1", "yes", "on":
		return true
	default:
		return false
	}
}

// AgentAddress 拼接并返回上游 agent 的完整 gRPC 目标地址。
//
// 返回格式："host:port"，如 "127.0.0.1:50051"。
// 用于 grpc.NewClient() 的 target 参数。
func (c *Config) AgentAddress() string {
	// 将主机名与端口号通过冒号拼接，strconv.Itoa 将 int 端口转为字符串
	return c.AgentGRPCHost + ":" + strconv.Itoa(c.AgentGRPCPort)
}

// ConsoleAddress 拼接并返回本 Go 代理的完整 HTTP 监听地址。
//
// 返回格式："host:port"，如 "127.0.0.1:8081"。
// 用于 http.Server.Addr 参数。
func (c *Config) ConsoleAddress() string {
	// 将主机名与端口号通过冒号拼接，strconv.Itoa 将 int 端口转为字符串
	return c.ConsoleHost + ":" + strconv.Itoa(c.ConsolePort)
}
