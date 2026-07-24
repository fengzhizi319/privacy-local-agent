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

## 10. 滚动更新与回滚策略 / Rolling Update & Rollback Strategy

### 10.1 滚动更新策略

Helm Chart 默认使用 Kubernetes 原生滚动更新（RollingUpdate），确保零停机发布：

```yaml
# values.yaml 默认配置
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxUnavailable: 0      # 更新期间不允许有 Pod 不可用
    maxSurge: 1            # 最多允许 1 个额外 Pod 同时运行
```

**更新流程 / Update Flow:**

1. **新 Pod 创建**: Kubernetes 创建新版本的 Pod（maxSurge=1）。
2. **就绪检查**: 新 Pod 通过 readinessProbe（`GET /health`）后加入 Service endpoints。
3. **旧 Pod 终止**: 旧 Pod 收到 SIGTERM，开始优雅关闭（terminationGracePeriodSeconds=30）。
4. **连接排空**: 旧 Pod 在关闭前完成处理中的请求（依赖网关/客户端重试）。
5. **重复**: 直到所有 Pod 更新完成。

**生产环境推荐配置 / Production Recommendations:**

```yaml
# values-production.yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxUnavailable: 0
    maxSurge: 25%          # 大规模部署时使用百分比

terminationGracePeriodSeconds: 60  # 延长优雅关闭时间

# 启用 PodDisruptionBudget 保证最小可用性
podDisruptionBudget:
  enabled: true
  minAvailable: 1
```

### 10.2 回滚方案 / Rollback Plan

#### 10.2.1 Helm 回滚

```bash
# 查看发布历史 / View release history
helm history privacy-local-agent

# 回滚到上一版本 / Rollback to previous version
helm rollback privacy-local-agent

# 回滚到指定版本 / Rollback to specific revision
helm rollback privacy-local-agent 3

# 回滚并等待完成 / Rollback and wait for completion
helm rollback privacy-local-agent --wait --timeout 5m
```

#### 10.2.2 原生 K8s 回滚

```bash
# 查看 Deployment 历史 / View deployment history
kubectl rollout history deployment/privacy-local-agent

# 回滚到上一版本 / Rollback to previous version
kubectl rollout undo deployment/privacy-local-agent

# 回滚到指定版本 / Rollback to specific revision
kubectl rollout undo deployment/privacy-local-agent --to-revision=3

# 查看回滚状态 / Watch rollback status
kubectl rollout status deployment/privacy-local-agent
```

#### 10.2.3 自动回滚触发条件 / Auto-Rollback Triggers

建议配置以下监控告警作为自动回滚触发条件（结合 Argo Rollouts 或 Flagger）：

| 指标 | 阈值 | 持续时间 | 动作 |
|------|------|----------|------|
| 5xx 错误率 | > 5% | 2 分钟 | 自动回滚 |
| P95 延迟 | > 2s | 5 分钟 | 告警 + 人工确认 |
| Pod 重启次数 | > 3 次 | 5 分钟 | 自动回滚 |
| readinessProbe 失败 | 连续 3 次 | - | Kubernetes 自动处理 |

### 10.3 蓝绿部署（可选）/ Blue-Green Deployment (Optional)

对于关键业务场景，可使用 Argo Rollouts 实现蓝绿部署：

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: privacy-local-agent
spec:
  replicas: 3
  strategy:
    blueGreen:
      activeService: privacy-local-agent-active
      previewService: privacy-local-agent-preview
      autoPromotionEnabled: false  # 手动确认切换
      abortScaleDownDelaySeconds: 30
  selector:
    matchLabels:
      app: privacy-local-agent
  template:
    # ... Pod template spec
```

**切换流程 / Promotion Flow:**

```bash
# 1. 部署新版本（preview 环境）
kubectl apply -f rollout.yaml

# 2. 验证 preview 环境
curl http://privacy-local-agent-preview:8079/health

# 3. 手动确认切换
kubectl argo rollouts promote privacy-local-agent

# 4. 如有问题，快速回滚
kubectl argo rollouts abort privacy-local-agent
```

### 10.4 金丝雀发布（可选）/ Canary Release (Optional)

使用 Argo Rollouts 或 Istio 实现渐进式流量切换：

```yaml
strategy:
  canary:
    steps:
      - setWeight: 10      # 10% 流量到新版本
      - pause: { duration: 5m }  # 观察 5 分钟
      - setWeight: 50      # 50% 流量
      - pause: { duration: 5m }
      - setWeight: 100     # 全量切换
    canaryMetadata:
      labels:
        version: canary
    stableMetadata:
      labels:
        version: stable
```

## 11. 工业化评分 / Industrialization Scorecard

> **工业化软件 = 功能正确 + 性能稳定 + 安全可靠 + 可维护 + 可观测 + 可快速迭代**
>
> 评估框架参考 ISO/IEC 25010 与 Google SRE 实践，采用 6 维度加权评分（1–10 分）。

### 11.1 加权评分表

| 维度 | 权重 | 得分 | 说明 |
|------|------|------|------|
| 功能完整性 | 20% | 8/10 | Helm Chart + K8s manifests + Docker Compose 三种部署形式；core/ml 双镜像；HPA 弹性 |
| 性能 | 15% | 7/10 | HPA 基于 CPU/内存扩展；资源 requests/limits 可配；缺少 VPA 与自定义指标扩展 |
| 可靠性 | 20% | 8/10 | liveness/readiness 探针；多副本支持；滚动更新策略 + 回滚方案 + 蓝绿/金丝雀可选 |
| 安全性 | 15% | 8/10 | Secret 注入（不硬编码）；NetworkPolicy 可选；RBAC 最小化；镜像分层减少攻击面 |
| 可维护性 | 15% | 7/10 | values 分层（default/production/ml）；缺少 Chart 版本变更日志 |
| 工程化 | 15% | 7/10 | CI 验证 Docker build + Helm chart-testing + Trivy 镜像扫描 |
| **总分** | **100%** | **7.55** | |

### 11.2 结论

**通过（Pass）**——满足工业化要求，可进入主线。

### 11.3 亮点

- 三种部署形式覆盖从本地到生产全场景。
- core/ml 镜像分层减少默认体积与攻击面。
- 敏感配置通过 Secret 注入，不硬编码到镜像。
- NetworkPolicy + RBAC 最小权限设计。
- 完整的滚动更新 + 回滚 + 蓝绿/金丝雀发布策略。
- CI 集成 chart-testing 与 Trivy 镜像漏洞扫描。

### 11.4 改进建议

| 优先级 | 建议 | 影响维度 |
|--------|------|----------|
| P2 | 添加自定义指标 HPA（基于 privacy_requests_total） | 性能 +1 |
| P3 | 补充 Chart CHANGELOG 与版本管理策略 | 可维护性 +0.5 |
| P3 | 集成 Argo Rollouts 实现自动化金丝雀发布 | 可靠性 +0.5 |
