# 部署设计文档

## 1. 概述

本文档定义 `privacy-local-agent` 的部署架构、交付形式与配置管理策略。通过 Helm Chart、原生 K8s manifests 与 Docker Compose 三种形式，覆盖从本地联调到 Kubernetes 生产部署的完整场景。

## 2. 设计目标

- 提供可配置的 Helm Chart。
- 提供原生 K8s 最小可运行 manifests。
- 提供 Docker Compose 本地多服务编排示例。
- 支持 core/ml 两种镜像选择。
- 敏感配置通过 Secret 注入，不硬编码到镜像。

## 3. 架构选型

| 组件 | 选型 | 说明 |
|---|---|---|
| 容器编排 | Kubernetes / Helm | 生产环境标准方案 |
| 本地开发 | Docker Compose | 快速拉起 worker + gateway |
| 镜像 | single Dockerfile multi-target | `--target core` / `--target ml` |
| 配置 | ConfigMap + Secret | 非敏感配置用 ConfigMap，证书/密钥用 Secret |
| 入口 | ClusterIP Service + Ingress | REST 通过 Ingress 暴露，gRPC 通过 Service 内部调用 |
| 弹性 | HPA v2 | 基于 CPU/内存横向扩展 |
| 隔离 | NetworkPolicy | 可选，限制仅指定 label 的 Pod 可访问 |

## 4. Helm Chart 结构

```text
deploy/helm/privacy-local-agent/
├── Chart.yaml
├── values.yaml
├── values-production.yaml
├── values-ml.yaml
└── templates/
    ├── _helpers.tpl
    ├── namespace.yaml
    ├── serviceaccount.yaml
    ├── deployment.yaml
    ├── service.yaml
    ├── configmap.yaml
    ├── secret.yaml
    ├── ingress.yaml
    ├── hpa.yaml
    └── networkpolicy.yaml
```

### 4.1 关键 values 说明

```yaml
image:
  repository: privacy-local-agent
  tag: ""  # 默认使用 Chart.appVersion
  pullPolicy: IfNotPresent

flavor: core  # core | ml

service:
  type: ClusterIP
  restPort: 8079
  grpcPort: 50051

security:
  enabled: false
  tls:
    enabled: false
    existingSecret: ""
  auth:
    enabled: false
    apiKeysSecret: ""

resources:
  requests:
    cpu: 100m
    memory: 256Mi

autoscaling:
  enabled: false
  minReplicas: 1
  maxReplicas: 5
  targetCPUUtilizationPercentage: 80
```

### 4.2 Deployment 环境变量

| 环境变量 | 来源 | 说明 |
|---|---|---|
| `APP_PROFILE_PATH` | ConfigMap 挂载 | `/etc/privacy-local-agent/privacy-profile.yaml` |
| `AGENT_LOG_LEVEL` | values | `info` / `debug` |
| `AGENT_LOG_FORMAT` | values | `json` / `text` |
| `AGENT_ENABLE_TLS` | values | `true` / `false` |
| `AGENT_TLS_CERT_FILE` | Secret 挂载 | `/certs/tls.crt` |
| `AGENT_TLS_KEY_FILE` | Secret 挂载 | `/certs/tls.key` |
| `AGENT_API_KEYS_FILE` | Secret 挂载 | `/secrets/api_keys` |

### 4.3 探针配置

- **livenessProbe**: `GET /health` 每 10s，失败 3 次重启。
- **readinessProbe**: `GET /health` 每 5s，失败 3 次移出流量。

## 5. core/ml 镜像分层

| 镜像 | 内容 | 适用场景 |
|---|---|---|
| core | 仅核心依赖 | 脱敏、DP、K-匿名、规则分类 |
| ml | core + torch/transformers/onnxruntime | 完整三层分类 |

分层设计减少默认镜像体积与攻击面，用户按需选择。

## 6. 安全设计

- TLS 证书、API Key 均通过 `existingSecret` 注入，Chart 不生成随机密钥。
- NetworkPolicy 默认关闭，生产 values 中启用。
- ServiceAccount 默认创建，RBAC 最小化（无需访问 K8s API）。

## 7. 可观测性设计

- Prometheus 通过 ServiceMonitor 抓取 `/metrics`（values 可选）。
- 日志输出到 stdout/stderr，由集群日志系统采集。

## 8. 部署流程

```bash
# 1. 构建镜像（core）
docker build --target core -t privacy-local-agent:0.1.0 .

# 2. 安装 Helm chart（开发模式）
helm install privacy-local-agent ./deploy/helm/privacy-local-agent

# 3. 生产模式 + 自管证书
helm install privacy-local-agent ./deploy/helm/privacy-local-agent \
  -f ./deploy/helm/privacy-local-agent/values-production.yaml \
  --set security.tls.existingSecret=my-tls-secret \
  --set security.auth.apiKeysSecret=my-apikeys-secret

# 4. 原生 K8s
kubectl apply -k ./deploy/k8s/

# 5. Docker Compose
cd deploy/docker-compose && docker-compose up -d
```

## 9. 测试策略

- `helm lint` 与 `helm template` 通过。
- 原生 K8s manifests 可 `kubectl apply`。
- Docker Compose 可启动 worker + gateway。
- core/ml 镜像构建成功。
