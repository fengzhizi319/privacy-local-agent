# 生产安全加固产品设计 PRD

> Scope: P0 — REST/gRPC TLS（含 mTLS 可选）、认证鉴权、速率限制。

## 1. 概述

本文档定义 `privacy-local-agent` 生产安全模块的产品需求与验收标准。该模块为 REST 与 gRPC 双协议提供可选的传输安全、身份认证、权限鉴权与速率限制能力，使其能够部署于多租户、跨域或半开放的生产环境。

## 2. 设计目标

- 为 REST/gRPC 提供可选的服务器端 TLS，gRPC 额外支持可选的 mTLS。
- 区分内部服务（高信任）与外部服务（低信任）两类身份，按最小权限原则控制接口访问。
- 基于调用者身份与接口路径/方法进行速率限制，防止预算爆破、模型推理资源耗尽与 DDoS。
- 所有安全能力默认关闭，通过环境变量显式开启，保证向后兼容。

## 3. 用户故事

| 角色 | 故事 |
|---|---|
| 平台运维 | 通过 TLS 加密 REST/gRPC 流量，避免隐私原语请求在链路上被窃听或篡改。 |
| SecretPad 后端（内部服务） | 使用内部 API Key 或 mTLS 调用 agent，并拥有全部隐私原语权限。 |
| 数据门户（外部服务） | 仅获得脱敏、分类等只读/低敏能力，不能调用差分隐私消耗预算或 K-匿名。 |
| SRE | `/health` 保持匿名可访问，便于 Kubernetes 探针和负载均衡健康检查。 |
| 安全团队 | 对缺失/无效凭证返回 401/`UNAUTHENTICATED`，对越权返回 403/`PERMISSION_DENIED`，对超速返回 429/`RESOURCE_EXHAUSTED`。 |

## 4. 功能需求

### 4.1 TLS

| ID | 需求 |
|---|---|
| FR-TLS-1 | 当 `PRIVACY_TLS_ENABLED=true` 时，REST 与 gRPC 均只监听 TLS 端口。 |
| FR-TLS-2 | 支持通过环境变量指定服务器证书、私钥、CA 证书、私钥口令。 |
| FR-TLS-3 | 支持 `none`/`optional`/`require` 三种客户端认证模式。 |
| FR-TLS-4 | gRPC 在 `require` 模式下通过 mTLS 提取客户端证书身份用于内部服务鉴权。 |

### 4.2 认证与鉴权

| ID | 需求 |
|---|---|
| FR-AUTH-1 | 当 `PRIVACY_AUTH_ENABLED=true` 时，除健康检查外所有接口必须携带有效凭证。 |
| FR-AUTH-2 | 支持 internal（通配权限）与 external（受限 scope）两类服务身份。 |
| FR-AUTH-3 | REST 外部服务使用 `Authorization: Bearer <token>`；REST 内部服务使用内部 API Key。 |
| FR-AUTH-4 | gRPC 外部服务通过 metadata `authorization` 携带 token；gRPC 内部服务优先使用 mTLS 身份，也允许使用内部 API Key。 |
| FR-AUTH-5 | 鉴权失败返回明确的 HTTP/gRPC 状态码与错误信息，不泄露内部实现细节。 |

### 4.3 速率限制

| ID | 需求 |
|---|---|
| FR-RL-1 | 当 `PRIVACY_RATE_LIMIT_ENABLED=true` 时，按调用者身份 + 接口做限流。 |
| FR-RL-2 | 支持默认 RPS/Burst，并支持按接口单独覆盖。 |
| FR-RL-3 | REST 超速返回 `429 Too Many Requests`；gRPC 超速返回 `RESOURCE_EXHAUSTED`。 |
| FR-RL-4 | 健康检查接口默认不受限速影响。 |
| FR-RL-5 | 可选 Redis 后端，用于多副本共享限流计数器；未配置时使用进程内存。 |

### 4.4 健康检查

| ID | 需求 |
|---|---|
| FR-HEALTH-1 | `/health` 与 `Health` RPC 默认不认证、不限速。 |
| FR-HEALTH-2 | 可通过 `PRIVACY_HEALTH_NO_AUTH=false` 与 `PRIVACY_HEALTH_NO_RATE_LIMIT=false` 关闭豁免。 |

## 5. 非功能需求

| 维度 | 要求 |
|---|---|
| 向后兼容 | 所有安全开关默认关闭；现有本地启动命令与测试集无需修改即可通过。 |
| 性能 | 认证与限流处理耗时 < 1ms/P99（内存模式）。 |
| 可观测 | 关键拒绝事件（认证失败、越权、超速）打印结构化日志。 |
| 可配置 | 全部行为通过环境变量配置，无需改动代码即可适配不同环境。 |
| 可测试 | 提供自签名证书生成工具/测试夹具，单元测试覆盖 TLS/mTLS/Auth/RateLimit。 |

## 6. 验收标准

- [ ] 编写 `docs/production_security/prd.md`、`design.md`、`ops.md`。
- [ ] 新增 `privacy_local_agent/security/` 模块，包含 config/tls/identity/auth/ratelimit。
- [ ] REST/gRPC 在开启 TLS 后仅接受 HTTPS/gRPCs 连接。
- [ ] mTLS `require` 模式拒绝无客户端证书的调用。
- [ ] 内部 API Key 可访问所有接口；外部 API Key 越权被拦截。
- [ ] 超速调用 REST 返回 429，gRPC 返回 `RESOURCE_EXHAUSTED`。
- [ ] `/health` 与 `Health` 默认保持匿名、不限速。
- [ ] 所有现有测试在默认配置下通过；新增安全测试通过。
- [ ] `pyproject.toml` 与 `requirements.txt` 更新依赖。

## 7. 非目标

- 本次不涉及结构化日志、Prometheus、Tracing、Helm/K8s 模板（后续 P0）。
- 本次不涉及 KMS 集成、密钥轮换、模型输入沙箱（P2）。
- 本次不实现 OAuth/OIDC/复杂 RBAC，采用静态 API Key + 静态 scope 映射。
