# privacy-local-agent 生产安全加固 PRD

> Scope: P0 — REST/gRPC TLS（含 mTLS 可选）、认证鉴权（内网/外网两种服务类型）、速率限制。
> Status: 已批准，待实施。

---

## 1. 背景与目标

`privacy-local-agent` 目前作为 FastAPI + gRPC 双协议的本地 Sidecar 运行，默认明文、无认证、无限速，仅适用于内网 POC/MVP。为了能在多租户、跨域或半开放的生产环境中部署，必须在不破坏现有本地开发体验的前提下，补齐以下安全能力：

1. **传输安全**：REST/gRPC 均支持服务器端 TLS，gRPC 额外支持可选的 mTLS。
2. **认证与鉴权**：区分 **内部服务**（高信任、同 VPC/Sidecar 邻居）与 **外部服务**（低信任、面向用户/门户），按身份类型和最小权限原则控制接口访问。
3. **速率限制**：基于调用者身份 + 接口路径/方法做限流，防止预算爆破、模型推理资源耗尽和 DDoS。

所有能力默认关闭，通过环境变量显式开启，保证向后兼容。

---

## 2. 用户故事

| 角色 | 故事 |
|---|---|
| 平台运维 | 我希望通过 TLS 加密 REST/gRPC 流量，避免隐私原语请求在链路上被窃听或篡改。 |
| SecretPad 后端（内部服务） | 我希望使用内部 API Key 或 mTLS 调用 agent，并拥有全部隐私原语权限。 |
| 数据门户（外部服务） | 我希望仅获得脱敏、分类等只读/低敏能力，不能调用差分隐私消耗预算或 K-匿名。 |
| SRE | 我希望 `/health` 保持匿名可访问，便于 Kubernetes 探针和负载均衡健康检查。 |
| 安全团队 | 我希望对缺失/无效凭证返回 401/`UNAUTHENTICATED`，对越权返回 403/`PERMISSION_DENIED`，对超速返回 429/`RESOURCE_EXHAUSTED`。 |

---

## 3. 功能需求

### 3.1 TLS

- FR-TLS-1：当 `PRIVACY_TLS_ENABLED=true` 时，REST 与 gRPC 均只监听 TLS 端口。
- FR-TLS-2：支持通过环境变量指定服务器证书、私钥、CA 证书、私钥口令。
- FR-TLS-3：支持三种客户端认证模式：`none`（仅服务端 TLS）、`optional`（请求客户端证书但不校验）、`require`（必须提供受信客户端证书）。
- FR-TLS-4：gRPC 在 `require` 模式下通过 mTLS 提取客户端证书身份用于内部服务鉴权。

### 3.2 认证与鉴权

- FR-AUTH-1：当 `PRIVACY_AUTH_ENABLED=true` 时，除健康检查外所有接口必须携带有效凭证。
- FR-AUTH-2：支持两类服务身份：
  - **internal**：默认拥有 `*` 通配权限，可调用所有隐私原语。
  - **external**：通过配置绑定具体 scope（如 `privacy:mask`、`classification:read`），按最小权限访问。
- FR-AUTH-3：REST 外部服务使用 `Authorization: Bearer <token>`；REST 内部服务使用配置的内部 API Key。
- FR-AUTH-4：gRPC 外部服务通过 metadata `authorization` 携带 token；gRPC 内部服务优先使用 mTLS 身份，也允许使用内部 API Key。
- FR-AUTH-5：鉴权失败返回明确的 HTTP/gRPC 状态码与错误信息，不泄露内部实现细节。

### 3.3 速率限制

- FR-RL-1：当 `PRIVACY_RATE_LIMIT_ENABLED=true` 时，按调用者身份 + 接口做限流。
- FR-RL-2：支持默认 RPS/Burst，并支持按接口单独覆盖。
- FR-RL-3：REST 超速返回 `429 Too Many Requests`；gRPC 超速返回 `RESOURCE_EXHAUSTED`。
- FR-RL-4：健康检查接口默认不受限速影响。
- FR-RL-5：可选 Redis 后端，用于多副本共享限流计数器；未配置时使用进程内存。

### 3.4 健康检查

- FR-HEALTH-1：`/health` 与 `Health` RPC 默认不认证、不限速，便于探针。
- FR-HEALTH-2：可通过 `PRIVACY_HEALTH_NO_AUTH=false` 与 `PRIVACY_HEALTH_NO_RATE_LIMIT=false` 关闭豁免。

---

## 4. 非功能需求

| 维度 | 要求 |
|---|---|
| 向后兼容 | 所有安全开关默认关闭；现有本地启动命令与测试集无需修改即可通过。 |
| 性能 | 认证与限流处理耗时 < 1ms/P99（内存模式）。 |
| 可观测 | 关键拒绝事件（认证失败、越权、超速）打印结构化日志（与后续 P0 日志改造兼容）。 |
| 可配置 | 全部行为通过环境变量配置，无需改动代码即可适配不同环境。 |
| 可测试 | 提供自签名证书生成工具/测试夹具，单元测试覆盖 TLS/mTLS/Auth/RateLimit。 |

---

## 5. 验收标准

- [ ] 编写 `docs/production_security/prd.md`、`design.md`、`ops.md`。
- [ ] 新增 `privacy_local_agent/security/` 模块，包含 config/tls/identity/auth/ratelimit。
- [ ] REST/gRPC 在开启 TLS 后仅接受 HTTPS/gRPCs 连接。
- [ ] mTLS `require` 模式拒绝无客户端证书的调用。
- [ ] 内部 API Key 可访问所有接口；外部 API Key 越权被拦截。
- [ ] 超速调用 REST 返回 429，gRPC 返回 `RESOURCE_EXHAUSTED`。
- [ ] `/health` 与 `Health` 默认保持匿名、不限速。
- [ ] 所有现有测试在默认配置下通过；新增安全测试通过。
- [ ] `pyproject.toml` 与 `requirements.txt` 更新依赖。

---

## 6. 非目标

- 本次不涉及结构化日志、Prometheus、Tracing、Helm/K8s 模板（后续 P0）。
- 本次不涉及 KMS 集成、密钥轮换、模型输入沙箱（P2）。
- 本次不修复 DP/K-匿名算法正确性（P1）。
- 不实现 OAuth/OIDC/复杂 RBAC，仅支持静态 API Key + 静态 scope 映射。
