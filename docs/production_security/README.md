# 生产安全加固文档索引

本目录包含 `privacy-local-agent` 生产安全模块的全套 SDLC 文档，覆盖 REST/gRPC 的 TLS/mTLS、认证鉴权与速率限制。

## 文档清单

| 文档 | 说明 | 目标读者 |
|---|---|---|
| [prd.md](./prd.md) | 产品需求文档 | 产品经理、项目经理 |
| [design.md](./design.md) | 技术架构、威胁模型与实现细节 | 安全架构师、后端开发 |
| [api_reference.md](./api_reference.md) | 环境变量、配置项与 TLS/Auth/RateLimit 接口参考 | 接入开发者、SRE |
| [examples.md](./examples.md) | TLS、API Key、速率限制的 Python/REST/gRPC 配置示例 | 接入开发者 |
| [examples/security_usage.py](./examples/security_usage.py) | 可运行的完整示例脚本 | 接入开发者 |
| [testing.md](./testing.md) | 安全测试策略与测试代码示例 | QA、测试开发 |
| [ops.md](./ops.md) | 运维手册、参数建议与故障排查 | SRE、运维 |

## 快速开始

1. 阅读 [prd.md](./prd.md) 了解安全能力范围与验收标准。
2. 阅读 [design.md](./design.md) 掌握 TLS/mTLS、认证鉴权、限速架构。
3. 查看 [examples.md](./examples.md) 或运行 [examples/security_usage.py](./examples/security_usage.py) 快速上手。
4. 开发/部署时参考 [api_reference.md](./api_reference.md) 与 [ops.md](./ops.md)。
5. 编写安全测试参考 [testing.md](./testing.md)。

## 运行示例

```bash
cd /home/charles/code/sfwork/privacy-local-agent
source .venv/bin/activate
PYTHONPATH=. python docs/production_security/examples/security_usage.py
```

## 安全开关速查

| 能力 | 总开关 | 说明 |
|---|---|---|
| TLS | `PRIVACY_TLS_ENABLED=true` | REST/gRPC 仅接受加密连接 |
| mTLS | `PRIVACY_TLS_CLIENT_AUTH=require` | 强制校验客户端证书 |
| API Key 认证 | `PRIVACY_AUTH_ENABLED=true` | 静态 Bearer Token 鉴权 |
| 速率限制 | `PRIVACY_RATE_LIMIT_ENABLED=true` | 按身份 + 接口限流 |

> 所有开关默认关闭，保证本地开发与既有测试不受影响；生产环境通过环境变量显式启用。
