# K8s/Helm 部署产品设计 PRD

> Scope: P0 — 提供 Helm Chart 与原生 K8s 部署模板，支持 core/ml 两种镜像、TLS、认证、监控探针与弹性伸缩。

## 1. 概述

本文档定义 `privacy-local-agent` 生产部署的产品需求与验收标准。通过 Helm Chart、原生 K8s manifests 与 Docker Compose 三种交付形式，覆盖从本地联调到 Kubernetes 生产部署的完整场景。

## 2. 设计目标

- 提供可配置的 Helm Chart，包含 Worker Deployment、Service、ConfigMap、Secret、Ingress、HPA、NetworkPolicy。
- 提供原生 K8s 最小可运行 manifests。
- 提供 Docker Compose 本地多服务编排示例。
- 支持 core/ml 两种镜像选择。
- 敏感配置通过 Secret 注入，不硬编码到镜像。

## 3. 用户故事

| 角色 | 故事 |
|---|---|
| SRE | 通过 `helm install` 一键部署，并配置 HPA 自动扩缩容。 |
| 运维 | 通过 K8s Secret 注入 TLS 证书与 API Key，而不是写入镜像。 |
| 开发 | 本地用 docker-compose 启动 agent + gateway 进行联调。 |
| 安全 | 通过 NetworkPolicy 限制只有 gateway 能访问 worker。 |

## 4. 功能需求

### 4.1 Helm Chart

| ID | 需求 |
|---|---|
| DEP-HELM-1 | Chart 支持部署 1 个或多个 worker replicas。 |
| DEP-HELM-2 | 通过 `values.yaml` 选择 `core` 或 `ml` 镜像 tag。 |
| DEP-HELM-3 | ConfigMap 挂载 `privacy-profile.yaml`。 |
| DEP-HELM-4 | Secret 可选挂载 TLS 证书（`/certs`）与 API Key 环境变量。 |
| DEP-HELM-5 | Service 暴露 REST（8079）与 gRPC（50051）端口。 |
| DEP-HELM-6 | Liveness/readiness 探针使用 `/health`。 |
| DEP-HELM-7 | 可选 HPA 基于 CPU/内存自动伸缩。 |
| DEP-HELM-8 | 可选 Ingress 暴露 REST 端口。 |
| DEP-HELM-9 | 可选 NetworkPolicy 限制入口流量。 |
| DEP-HELM-10 | 提供 `values.yaml`、`values-production.yaml`、`values-ml.yaml`。 |

### 4.2 原生 K8s 样例

| ID | 需求 |
|---|---|
| DEP-K8S-1 | `deploy/k8s/deployment.yaml` 可 `kubectl apply -f deploy/k8s/` 直接运行。 |
| DEP-K8S-2 | `deploy/k8s/secret.example.yaml` 展示 TLS/API Key 配置（不提交真实密钥）。 |

### 4.3 Docker Compose

| ID | 需求 |
|---|---|
| DEP-COMPOSE-1 | `deploy/docker-compose/docker-compose.yml` 启动 worker + gateway。 |

## 5. 非功能需求

| 维度 | 要求 |
|---|---|
| 安全 | 敏感配置全部来自 Secret/环境变量，不硬编码。 |
| 可维护 | Chart 模板化关键参数，注释清晰。 |
| 向后兼容 | 默认 values 不强制启用 TLS/auth，便于本地测试。 |
| 验证 | 提供 `make helm-lint` 与 `make helm-template`。 |

## 6. 验收标准

- [ ] `docs/deployment/{prd,design,ops}.md` 完成。
- [ ] `deploy/helm/privacy-local-agent/` Chart 完整。
- [ ] `deploy/k8s/` 原生 manifests 完整。
- [ ] `deploy/docker-compose/docker-compose.yml` 可用。
- [ ] `helm lint` 与 `helm template` 通过。
- [ ] `AGENTS.md` 与 `README.md` 更新部署索引。
