// Package config provides centralized configuration parsing for the Go gRPC proxy.
//
// 中文说明：
// 所有配置项均通过环境变量读取，并附带合理的本地开发默认值。
// 这样可以在不修改代码的情况下，快速切换目标 agent 地址、监听端口或认证信息。
package config

import (
	"os"
	"strconv"
)

// Config holds all runtime settings for the Go gRPC proxy server.
type Config struct {
	// AgentGRPCHost is the hostname of the privacy-local-agent gRPC server.
	AgentGRPCHost string
	// AgentGRPCPort is the port of the privacy-local-agent gRPC server.
	AgentGRPCPort int
	// AgentAPIKey is an optional bearer token used when the agent has auth enabled.
	AgentAPIKey string
	// ConsoleHost is the bind address for the Go proxy HTTP server.
	ConsoleHost string
	// ConsolePort is the listen port for the Go proxy HTTP server.
	ConsolePort int
	// StaticDistDir is the path to the built frontend assets (frontend/web/dist).
	// When the directory exists, the Go server also serves the Console UI.
	StaticDistDir string
}

// Load reads configuration from environment variables and returns a populated Config.
//
// 环境变量说明：
//   - PRIVACY_AGENT_GRPC_HOST：agent gRPC 主机，默认 127.0.0.1
//   - PRIVACY_AGENT_GRPC_PORT：agent gRPC 端口，默认 50051
//   - PRIVACY_AGENT_API_KEY：可选的认证 API Key
//   - PRIVACY_CONSOLE_HOST：本后端监听地址，默认 127.0.0.1
//   - PRIVACY_CONSOLE_PORT：本后端监听端口，默认 8081
//   - PRIVACY_CONSOLE_STATIC_DIR：前端构建产物目录，默认 ../web/dist（相对于 backend-go 目录）
func Load() *Config {
	return &Config{
		AgentGRPCHost: getEnv("PRIVACY_AGENT_GRPC_HOST", "127.0.0.1"),
		AgentGRPCPort: getEnvInt("PRIVACY_AGENT_GRPC_PORT", 50051),
		AgentAPIKey:   getEnv("PRIVACY_AGENT_API_KEY", ""),
		ConsoleHost:   getEnv("PRIVACY_CONSOLE_HOST", "127.0.0.1"),
		ConsolePort:   getEnvInt("PRIVACY_CONSOLE_PORT", 8081),
		StaticDistDir: getEnv("PRIVACY_CONSOLE_STATIC_DIR", "../web/dist"),
	}
}

func getEnv(name, defaultValue string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return defaultValue
}

func getEnvInt(name string, defaultValue int) int {
	v := os.Getenv(name)
	if v == "" {
		return defaultValue
	}
	i, err := strconv.Atoi(v)
	if err != nil {
		return defaultValue
	}
	return i
}

// AgentAddress returns the full gRPC target address, e.g. "127.0.0.1:50051".
func (c *Config) AgentAddress() string {
	return c.AgentGRPCHost + ":" + strconv.Itoa(c.AgentGRPCPort)
}

// ConsoleAddress returns the HTTP bind address, e.g. "127.0.0.1:8081".
func (c *Config) ConsoleAddress() string {
	return c.ConsoleHost + ":" + strconv.Itoa(c.ConsolePort)
}
