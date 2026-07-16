# privacy-local-agent K8s/Helm 部署 PRD

> Scope: P0 — 提供 Helm Chart 与原生 K8s 部署模板，支持 core/ml 两种镜像、TLS、认证、监控探针与弹性伸缩。

---

## 1. 背景与目标

privacy-local-agent 目前仅有 Dockerfile，缺乏 Kubernetes 与 Helm 部署能力。为便于生产环境部署，需提供：

1. **Helm Chart**：可配置的 Worker Deployment、Service、ConfigMap、Secret、Ingress、HPA、NetworkPolicy。
2. **原生 K8s 样例**：`deploy/k8s/` 下的最小可运行 manifests，便于不使 Helm 的用户直接使用。
3. **Docker Compose 样例**：本地多服务编排示例。
4. **core/ml 镜像选择**：默认使用轻量 core 镜像，可选 ml 镜像用于完整分类能力。

---

## 2. 用户故事

| 角色 | 故事 |
|---|---|
| SRE | 我希望通过 `helm install` 一键部署 privacy-local-agent，并配置 HPA 自动扩缩容。 |
| 运维 | 我希望通过 K8s Secret 注入 TLS 证书与 API Key，而不是写入镜像。 |
| 开发 | 我希望本地用 docker-compose 启动 agent + gateway 进行联调。 |
| 安全 | 我希望通过 NetworkPolicy 限制只有 gateway 能访问 worker。 |

---

## 3. 功能需求

### 3.1 Helm Chart

- DEP-HELM-1：Chart 支持部署 1 个或多个 worker replicas。
- DEP-HELM-2：通过 `values.yaml` 选择 `core` 或 `ml` 镜像 tag。
- DEP-HELM-3：ConfigMap 挂载 `privacy-profile.yaml`。
- DEP-HELM-4：Secret 可选挂载 TLS 证书（`/certs`）与 API Key 环境变量。
- DEP-HELM-5：Service 暴露 REST（8079）与 gRPC（50051）端口。
- DEP-HELM-6：Liveness/readiness 探针使用 `/health`。
- DEP-HELM-7：可选 HPA 基于 CPU/内存自动伸缩。
- DEP-HELM-8：可选 Ingress 暴露 REST 端口。
- DEP-HELM-9：可选 NetworkPolicy 限制入口流量。
- DEP-HELM-10：提供 `values.yaml`、`values-production.yaml`、`values-ml.yaml`。

### 3.2 原生 K8s 样例

- DEP-K8S-1：`deploy/k8s/deployment.yaml` 可 `kubectl apply -f deploy/k8s/` 直接运行。
- DEP-K8S-2：`deploy/k8s/secret.example.yaml` 展示 TLS/API Key 配置（不提交真实密钥）。

### 3.3 Docker Compose

- DEP-COMPOSE-1：`deploy/docker-compose/docker-compose.yml` 启动 worker + gateway。

---

## 4. 非功能需求

| 维度 | 要求 |
|---|---|
| 安全 | 敏感配置全部来自 Secret/环境变量，不硬编码。 |
| 可维护 | Chart 模板化关键参数，注释清晰。 |
| 向后兼容 | 默认 values 不强制启用 TLS/auth，便于本地测试。 |
| 验证 | 提供 `make helm-lint` 与 `make helm-template`。 |

---

## 5. 验收标准

- [ ] `docs/deployment/{prd,design,ops}.md` 完成。
- [ ] `deploy/helm/privacy-local-agent/` Chart 完整。
- [ ] `deploy/k8s/` 原生 manifests 完整。
- [ ] `deploy/docker-compose/docker-compose.yml` 可用。
- [ ] `helm lint` 与 `helm template` 通过。
- [ ] `AGENTS.md` 与 `README.md` 更新部署索引。
