# privacy-local-agent 部署运维手册

## 目录

- [1. 环境准备](#1-环境准备)
- [2. 镜像构建](#2-镜像构建)
  - [2.1 多阶段构建架构](#21-多阶段构建架构)
  - [2.2 构建命令](#22-构建命令)
  - [2.3 依赖清单](#23-依赖清单)
- [3. Helm 部署](#3-helm-部署)
  - [3.1 Chart 结构](#31-chart-结构)
  - [3.2 默认安装（开发/测试）](#32-默认安装开发测试)
  - [3.3 生产安装（TLS + 认证 + HPA）](#33-生产安装tls--认证--hpa)
  - [3.4 ML 镜像部署](#34-ml-镜像部署)
  - [3.5 升级与回滚](#35-升级与回滚)
  - [3.6 卸载](#36-卸载)
- [4. 原生 K8s 部署](#4-原生-k8s-部署)
  - [4.1 资源清单](#41-资源清单)
  - [4.2 部署步骤](#42-部署步骤)
- [5. Docker Compose 部署](#5-docker-compose-部署)
- [6. 安全配置](#6-安全配置)
  - [6.1 TLS / mTLS](#61-tls--mtls)
  - [6.2 API Key 认证](#62-api-key-认证)
  - [6.3 速率限制](#63-速率限制)
- [7. 服务启动与优雅关闭](#7-服务启动与优雅关闭)
- [8. 健康检查与探针](#8-健康检查与探针)
- [9. 监控与告警](#9-监控与告警)
  - [9.1 Prometheus 指标](#91-prometheus-指标)
  - [9.2 告警规则](#92-告警规则)
  - [9.3 Grafana 仪表盘](#93-grafana-仪表盘)
  - [9.4 ServiceMonitor（Prometheus Operator）](#94-servicemonitorprometheus-operator)
- [10. 自动伸缩（HPA）](#10-自动伸缩hpa)
- [11. 网络策略（NetworkPolicy）](#11-网络策略networkpolicy)
- [12. 环境变量参考](#12-环境变量参考)
- [13. 验证与冒烟测试](#13-验证与冒烟测试)
- [14. 故障排查](#14-故障排查)
- [15. 日常运维操作](#15-日常运维操作)

---

## 1. 环境准备

| 组件 | 最低版本 | 用途 |
|---|---|---|
| Kubernetes | >= 1.25 | 容器编排（HPA v2、NetworkPolicy v1） |
| Helm | >= 3.12 | Chart 安装与管理 |
| Docker | >= 20.10 | 镜像构建（BuildKit 多阶段） |
| Docker Compose | >= 2.0 | 本地联调 |
| Prometheus Operator | 可选 | ServiceMonitor 自动发现 |
| Grafana | >= 9.0 | 可视化仪表盘 |

**Python 运行时**（仅本地开发需要）：Python >= 3.10。

---

## 2. 镜像构建

### 2.1 多阶段构建架构

Dockerfile 采用三阶段构建，通过 `--target` 选择最终镜像：

```text
base (python:3.10-slim)
 ├── 安装 curl / ca-certificates（K8s 探针依赖）
 ├── 安装 requirements-core.txt（核心运行时依赖）
 │
 ├──► core 目标
 │     ├── COPY 全部源码
 │     ├── EXPOSE 8079 50051
 │     ├── ENV PRIVACY_REST_HOST=0.0.0.0 / PRIVACY_GRPC_HOST=0.0.0.0
 │     └── CMD python -m privacy_local_agent.server
 │
 └──► ml 目标（继承 core）
       ├── 安装 requirements-ml.txt（torch/transformers/onnxruntime 等）
       └── CMD python -m privacy_local_agent.server
```

- **core 镜像**（~350 MB）：仅含隐私原语（DP / K-匿名 / 脱敏 / 规则分类），适合绝大多数生产场景。
- **ml 镜像**（~4 GB）：额外包含 PyTorch / Transformers / ONNX Runtime，用于本地 NER（Layer-2）和 VLM/LLM（Layer-3）分类。

### 2.2 构建命令

```bash
# 在仓库根目录执行
# core 镜像（推荐生产默认）
docker build --target core -t privacy-local-agent:0.1.0 .

# ml 镜像（含 torch/transformers/onnxruntime，用于完整三层分类）
docker build --target ml -t privacy-local-agent:0.1.0-ml .

# 也可使用 Makefile 快捷命令
make docker-core          # 等价于 --target core
make docker-ml            # 等价于 --target ml
```

> 自定义版本号：`make docker-core VERSION=0.2.0`

### 2.3 依赖清单

**core 运行时依赖**（`requirements-core.txt`）：

| 包 | 版本约束 | 用途 |
|---|---|---|
| fastapi | >= 0.110.0 | REST 框架 |
| uvicorn[standard] | >= 0.27.0 | ASGI 服务器 |
| pydantic | >= 2.6.0 | 数据校验 |
| grpcio | >= 1.62.0, < 2.0.0 | gRPC 通信 |
| protobuf | >= 4.25.0, < 8.0.0 | 序列化 |
| pyyaml | >= 6.0.1 | Profile 配置解析 |
| httpx | >= 0.27.0 | 网关代理 HTTP 客户端 |
| limits | >= 3.10.0 | 速率限制 |
| cryptography | >= 42.0.0 | TLS / 加密 |
| python-json-logger | >= 2.0.7 | 结构化日志 |
| prometheus-client | >= 0.20.0 | 指标暴露 |
| numpy / pandas / pyarrow | — | 向量化规则引擎 |

**ml 扩展依赖**（`requirements-ml.txt`，在 core 基础上追加）：

| 包 | 版本约束 | 用途 |
|---|---|---|
| torch | >= 2.2.0 | 深度学习推理 |
| torchvision | >= 0.17.0 | Qwen2-VL 图片预处理 |
| transformers | >= 4.45.0, < 5.0 | 模型加载 |
| accelerate | >= 0.30.0 | 模型加速 |
| onnxruntime | >= 1.17.0 | NER ONNX 推理 |
| pillow | >= 10.2.0 | 图片处理 |
| qwen-vl-utils | >= 0.0.8 | Qwen2-VL 多模态预处理 |
| modelscope | >= 1.20.0 | ModelScope NER 管道 |
| datasets | >= 4.0.0, <= 4.8.4 | ModelScope 运行时依赖 |

---

## 3. Helm 部署

### 3.1 Chart 结构

```text
deploy/helm/privacy-local-agent/
├── Chart.yaml                  # Chart 元数据（version: 0.1.0, appVersion: 0.1.0）
├── values.yaml                 # 默认 values（开发模式，TLS/Auth 关闭）
├── values-production.yaml      # 生产覆盖值（TLS/Auth/HPA/NetworkPolicy 开启）
├── values-ml.yaml              # ML 镜像覆盖值（资源上限提升）
└── templates/
    ├── configmap.yaml          # privacy-profile.yaml 配置注入
    ├── deployment.yaml         # 主 Deployment（含探针、安全上下文、TLS 挂载）
    ├── hpa.yaml                # HorizontalPodAutoscaler（autoscaling/v2）
    ├── ingress.yaml            # 可选 Ingress
    ├── namespace.yaml          # 可选 Namespace 创建
    ├── networkpolicy.yaml      # 可选 NetworkPolicy
    ├── secret.yaml             # 内置 Secret（TLS 证书 / API Key）
    ├── service.yaml            # ClusterIP Service（REST 8079 + gRPC 50051）
    ├── serviceaccount.yaml     # ServiceAccount
    └── servicemonitor.yaml     # Prometheus Operator ServiceMonitor
```

### 3.2 默认安装（开发/测试）

```bash
helm install pla ./deploy/helm/privacy-local-agent
```

默认配置要点：
- `replicaCount: 1`，单副本
- `flavor: core`，使用轻量镜像
- TLS / Auth / RateLimit 均关闭
- 资源：requests 100m CPU / 256Mi，limits 1000m CPU / 1Gi
- 探针：liveness `/health`（10s 间隔），readiness `/health`（5s 间隔）
- HPA / Ingress / NetworkPolicy / ServiceMonitor 均关闭

### 3.3 生产安装（TLS + 认证 + HPA）

```bash
# 1. 准备 TLS Secret（包含 tls.crt 和 tls.key）
kubectl create secret tls pla-tls \
  --cert=path/to/tls.crt --key=path/to/tls.key \
  -n privacy-local-agent

# 2. 准备 API Key Secret（包含 api-keys.json 文件）
# api-keys.json 格式示例：
# {
#   "my-api-key": { "name": "gateway", "scopes": ["*"] },
#   "readonly-key": { "name": "auditor", "scopes": ["read"] }
# }
kubectl create secret generic pla-apikeys \
  --from-file=api-keys.json=path/to/api-keys.json \
  -n privacy-local-agent

# 3. 安装（使用生产 values 覆盖）
helm install pla ./deploy/helm/privacy-local-agent \
  -f ./deploy/helm/privacy-local-agent/values-production.yaml \
  --set security.tls.existingSecret=pla-tls \
  --set security.auth.apiKeysSecret=pla-apikeys \
  --set image.repository=myregistry/privacy-local-agent \
  --set image.tag=0.1.0
```

**生产 values 关键差异**（`values-production.yaml`）：

| 配置项 | 默认值 | 生产值 |
|---|---|---|
| replicaCount | 1 | 2 |
| agent.logFormat | text | json |
| security.enabled | false | true |
| security.tls.enabled | false | true |
| security.auth.enabled | false | true |
| resources.requests | 100m / 256Mi | 500m / 512Mi |
| resources.limits | 1000m / 1Gi | 2000m / 2Gi |
| autoscaling.enabled | false | true（2~10 副本） |
| networkPolicy.enabled | false | true |
| serviceMonitor.enabled | false | true |

### 3.4 ML 镜像部署

```bash
helm install pla-ml ./deploy/helm/privacy-local-agent \
  -f ./deploy/helm/privacy-local-agent/values-ml.yaml \
  --set image.repository=myregistry/privacy-local-agent \
  --set image.tag=0.1.0-ml
```

**ML values 关键差异**（`values-ml.yaml`）：

| 配置项 | 默认值 | ML 值 |
|---|---|---|
| flavor | core | ml |
| resources.requests | 100m / 256Mi | 1000m / 2Gi |
| resources.limits | 1000m / 1Gi | 4000m / 8Gi |
| autoscaling | 关闭 | 开启（1~3 副本） |

> ML 镜像包含 PyTorch + Transformers，内存占用显著增大，建议节点至少 16Gi 可用内存。

### 3.5 升级与回滚

```bash
# 升级（修改 values 或镜像版本后）
helm upgrade pla ./deploy/helm/privacy-local-agent \
  -f ./deploy/helm/privacy-local-agent/values-production.yaml \
  --set image.tag=0.2.0

# 查看历史版本
helm history pla

# 回滚到上一版本
helm rollback pla

# 回滚到指定版本
helm rollback pla 2
```

### 3.6 卸载

```bash
helm uninstall pla

# 如需同时清理 PVC / Secret 等手动创建的资源
kubectl delete secret pla-tls pla-apikeys -n privacy-local-agent
```

---

## 4. 原生 K8s 部署

### 4.1 资源清单

`deploy/k8s/` 目录包含以下资源（通过 Kustomize 管理）：

| 文件 | Kind | 说明 |
|---|---|---|
| `namespace.yaml` | Namespace | 创建 `privacy-local-agent` 命名空间 |
| `configmap.yaml` | ConfigMap | 注入 `privacy-profile.yaml` 配置 |
| `deployment.yaml` | Deployment | 主工作负载（含探针、资源限制） |
| `service.yaml` | Service | ClusterIP，暴露 8079（REST）+ 50051（gRPC） |
| `secret.example.yaml` | Secret | TLS 证书 + API Key 示例（需复制修改） |
| `kustomization.yaml` | Kustomization | 资源编排入口 |

### 4.2 部署步骤

```bash
# 1. 准备 Secret（复制示例并填入真实证书/密钥）
cp deploy/k8s/secret.example.yaml deploy/k8s/secret.yaml
# 编辑 secret.yaml，替换 REPLACE_WITH_YOUR_CERT / REPLACE_WITH_YOUR_KEY

# 2. 如需启用 Secret，取消 kustomization.yaml 中的注释：
#    resources:
#      - secret.yaml

# 3. 一键部署
kubectl apply -k deploy/k8s/

# 4. 验证
kubectl get pods -n privacy-local-agent
kubectl get svc -n privacy-local-agent
```

**Deployment 关键配置说明**：

```yaml
# 容器端口
ports:
  - name: http
    containerPort: 8079    # REST API
  - name: grpc
    containerPort: 50051   # gRPC

# 探针配置
livenessProbe:
  httpGet:
    path: /health
    port: http
  initialDelaySeconds: 10
  periodSeconds: 10
readinessProbe:
  httpGet:
    path: /health
    port: http
  initialDelaySeconds: 5
  periodSeconds: 5

# 资源限制
resources:
  requests: { cpu: 100m, memory: 256Mi }
  limits:   { cpu: 1000m, memory: 1Gi }

# 配置挂载
volumeMounts:
  - name: config
    mountPath: /etc/privacy-local-agent
    readOnly: true
```

---

## 5. Docker Compose 部署

适用于本地联调和快速验证，无需 K8s 集群。

```bash
cd deploy/docker-compose
docker-compose up -d
```

**docker-compose.yml 关键配置**：

```yaml
services:
  privacy-local-agent:
    build:
      context: ../..
      dockerfile: Dockerfile
      target: core                    # 使用 core 镜像
    ports:
      - "8079:8079"                   # REST
      - "50051:50051"                 # gRPC
    environment:
      PRIVACY_REST_HOST: "0.0.0.0"
      PRIVACY_GRPC_HOST: "0.0.0.0"
      PRIVACY_PROFILE: "/etc/privacy-local-agent/privacy-profile.yaml"
      PRIVACY_LOG_LEVEL: "INFO"
      PRIVACY_LOG_FORMAT: "text"
    volumes:
      - ./privacy-profile.yaml:/etc/privacy-local-agent/privacy-profile.yaml:ro
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:8079/health"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 5s
    restart: unless-stopped
```

**启用 TLS + 认证**（取消 docker-compose.yml 中的注释）：

```yaml
environment:
  PRIVACY_TLS_ENABLED: "true"
  PRIVACY_TLS_CERT_FILE: "/certs/tls.crt"
  PRIVACY_TLS_KEY_FILE: "/certs/tls.key"
  PRIVACY_AUTH_ENABLED: "true"
  PRIVACY_AUTH_EXTERNAL_KEYS_JSON: '{"dev-key":{"name":"local","scopes":["*"]}}'
volumes:
  - ./certs:/certs:ro
```

**停止服务**：

```bash
docker-compose down
```

---

## 6. 安全配置

所有安全开关默认关闭，生产环境通过环境变量显式启用。配置由 `privacy_local_agent/security/config.py` 中的 `SecuritySettings` 统一解析。

### 6.1 TLS / mTLS

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PRIVACY_TLS_ENABLED` | `false` | 启用 TLS |
| `PRIVACY_TLS_CERT_FILE` | — | 服务端证书路径（必填） |
| `PRIVACY_TLS_KEY_FILE` | — | 服务端私钥路径（必填） |
| `PRIVACY_TLS_CA_FILE` | — | CA 证书路径（mTLS 时必填） |
| `PRIVACY_TLS_CLIENT_AUTH` | `none` | 客户端认证模式：`none` / `optional` / `require` |
| `PRIVACY_TLS_KEY_PASSWORD` | — | 私钥密码（可选） |

**实现细节**：
- REST 端：通过 `uvicorn_ssl_kwargs()` 构造 SSL 参数传递给 Uvicorn。
- gRPC 端：通过 `grpc_server_credentials()` 构造 `grpc.ServerCredentials`。
- 当 `tls_client_auth=require` 时启用双向 mTLS，客户端必须出示受信任证书。

**Helm 部署时**：TLS 证书通过 K8s Secret 挂载到 `/certs/` 目录，Deployment 模板自动配置探针使用 `curl -k https://...` 方式探测。

### 6.2 API Key 认证

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PRIVACY_AUTH_ENABLED` | `false` | 启用 API Key 认证 |
| `PRIVACY_AUTH_EXTERNAL_KEYS_JSON` | `{}` | 外部 API Key JSON 映射 |
| `PRIVACY_AUTH_INTERNAL_KEYS_JSON` | `{}` | 内部 API Key JSON 映射 |
| `PRIVACY_AUTH_INTERNAL_MTLS_ENABLED` | `true` | 内部 mTLS 免 Key 认证 |

**API Key JSON 格式**：

```json
{
  "your-api-key-string": {
    "name": "gateway-service",
    "scopes": ["*"]
  }
}
```

- `name`：人类可读标识，用于日志和速率限制键。
- `scopes`：权限列表，`["*"]` 表示完全访问。

**请求携带方式**：HTTP Header `X-API-Key: your-api-key-string`。

### 6.3 速率限制

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PRIVACY_RATE_LIMIT_ENABLED` | `false` | 启用速率限制 |
| `PRIVACY_RATE_LIMIT_DEFAULT_RPS` | `10` | 默认每秒请求数 |
| `PRIVACY_RATE_LIMIT_DEFAULT_BURST` | `20` | 默认突发上限 |
| `PRIVACY_RATE_LIMIT_PER_ENDPOINT_JSON` | `{}` | 按端点覆盖限制 |
| `PRIVACY_RATE_LIMIT_REDIS_URL` | — | Redis URL（多实例共享限流状态） |

> 健康检查端点默认跳过认证和限速（`PRIVACY_HEALTH_NO_AUTH=true`、`PRIVACY_HEALTH_NO_RATE_LIMIT=true`）。

---

## 7. 服务启动与优雅关闭

容器入口为 `python -m privacy_local_agent.server`，该模块实现 REST + gRPC 双协议统一启动：

```text
启动流程：
1. 解析命令行参数（--rest-host/--rest-port/--grpc-host/--grpc-port），优先级高于环境变量
2. 构造 Uvicorn SSL 参数（若 TLS 启用）
3. 在非守护线程中启动 REST 服务（uvicorn.Server）
4. 启动 gRPC 服务（非阻塞模式）
5. 注册 SIGTERM / SIGINT 信号处理器
6. 主线程等待终止信号

优雅关闭流程：
1. 捕获 SIGTERM/SIGINT 信号
2. 停止 gRPC 服务（保留 5 秒在途请求处理时间）
3. 设置 REST 服务 should_exit = True
4. 等待 REST 线程退出（超时 10 秒）
5. 进程安全退出（exit code 0）
```

**REST 应用生命周期**（`main.py` lifespan）：
1. 初始化结构化日志（`configure_logging`）
2. 初始化 OpenTelemetry Tracing（若配置了 OTLP endpoint）
3. 异步预热 LLM 模型（若 `PRIVACY_WARMUP_LLM=true`）
4. 注册可观测性中间件 + 挂载 `/metrics`
5. 挂载所有业务路由

---

## 8. 健康检查与探针

| 端点 | 用途 | 返回 |
|---|---|---|
| `GET /health` | 通用健康检查（K8s liveness/readiness 默认） | `{"status": "ok", "namespace": "..."}` |
| `GET /livez` | 存活探针 | `{"status": "alive"}` |
| `GET /readyz` | 就绪探针（检查配置解析器 + 预算 DB 连通性） | `{"status": "ready", "llm_ready": true/false}` |
| `GET /readyz/llm` | LLM 分类器就绪探针 | 200 或 503 |

**就绪探针检查逻辑**（`/readyz`）：
1. 验证 `Configuration resolver` 已初始化，否则返回 503。
2. 若配置了 `PRIVACY_BUDGET_DB`，尝试 SQLite 连接（2s 超时），失败返回 503。
3. 返回 LLM 就绪状态。

**LLM 探针**（`/readyz/llm`）：
- 未启用 LLM 或使用 NoOp 分类器 → 200
- 模型已加载成功 → 200
- 模型正在预热或初始化失败 → 503

**TLS 模式下的探针**：Helm 模板自动切换为 `exec` 方式（`curl -fsS -k https://...`），避免 httpGet 无法处理自签证书。

---

## 9. 监控与告警

### 9.1 Prometheus 指标

指标通过 `/metrics` 端点暴露（`prometheus-client` ASGI app），所有指标以 `privacy_` 为前缀。

**核心请求指标**：

| 指标名 | 类型 | Labels | 说明 |
|---|---|---|---|
| `privacy_requests_total` | Counter | method, path, status | REST/gRPC 请求总数 |
| `privacy_request_duration_seconds` | Histogram | method, path | 请求延迟分布 |
| `privacy_traffic_bytes_total` | Counter | method, path, direction | 请求/响应流量（字节） |

**隐私原语指标**：

| 指标名 | 类型 | Labels | 说明 |
|---|---|---|---|
| `privacy_dp_queries_total` | Counter | mechanism, aggregation | DP 查询次数 |
| `privacy_dp_duration_seconds` | Histogram | aggregation, mechanism | DP 查询延迟 |
| `privacy_budget_remaining` | Gauge | namespace, budget_type | 剩余隐私预算 |
| `privacy_masking_operations_total` | Counter | operation | 脱敏操作次数 |
| `privacy_masking_duration_seconds` | Histogram | operation | 脱敏延迟 |
| `privacy_kano_operations_total` | Counter | operation | K-匿名操作次数 |
| `privacy_kano_duration_seconds` | Histogram | operation | K-匿名延迟 |
| `privacy_qol_operations_total` | Counter | domain | 查询混淆次数 |
| `privacy_qol_duration_seconds` | Histogram | domain | 查询混淆延迟 |

**分类指标**：

| 指标名 | 类型 | Labels | 说明 |
|---|---|---|---|
| `privacy_classification_total` | Counter | final_level, layer | 分类结果统计 |
| `privacy_classification_duration_seconds` | Histogram | operation | 分类操作延迟 |
| `privacy_classification_ner_total` | Counter | status | NER 引擎调用次数 |
| `privacy_classification_ner_duration_seconds` | Histogram | engine | NER 推理延迟 |
| `privacy_classification_llm_total` | Counter | status | LLM 引擎调用次数 |
| `privacy_classification_llm_duration_seconds` | Histogram | engine | LLM 推理延迟 |
| `privacy_classification_rule_hits_total` | Counter | rule_id | Layer-1 规则命中 |
| `privacy_classification_composite_hits_total` | Counter | rule_id | 组合规则命中 |
| `privacy_classification_jobs_total` | Counter | status | 异步分类任务 |
| `privacy_classification_jobs_duration_seconds` | Histogram | status | 异步任务延迟 |
| `privacy_classification_review_queue_size` | Gauge | — | 人工审核队列大小 |
| `privacy_classification_templates_total` | Counter | template | 合规模板使用 |
| `privacy_classification_vectorized_batch_total` | Counter | field_name | 向量化批处理次数 |
| `privacy_classification_vectorized_batch_size` | Histogram | — | 批处理行数分布 |

**安全指标**：

| 指标名 | 类型 | Labels | 说明 |
|---|---|---|---|
| `privacy_auth_denials_total` | Counter | reason | 认证/授权/限速拒绝次数 |
| `privacy_auth_duration_seconds` | Histogram | result | 认证检查延迟 |

**网关指标**：

| 指标名 | 类型 | Labels | 说明 |
|---|---|---|---|
| `privacy_gateway_requests_total` | Counter | protocol, method, status | 网关代理请求 |
| `privacy_gateway_latency_seconds` | Histogram | protocol | 网关代理延迟 |
| `privacy_gateway_healthy_nodes` | Gauge | — | 健康后端节点数 |
| `privacy_gateway_retries_total` | Counter | protocol, reason | 网关重试次数 |

**其他指标**：

| 指标名 | 类型 | Labels | 说明 |
|---|---|---|---|
| `privacy_profile_resolve_total` | Counter | primitive, status | 参数解析操作 |
| `privacy_data_extraction_total` | Counter | format, status | 数据提取操作 |

### 9.2 告警规则

告警规则文件位于 `deploy/prometheus/alerts.yml`，挂载到 Prometheus rules 目录使用：

```yaml
# prometheus.yml
rule_files:
  - /etc/prometheus/rules/privacy-local-agent-alerts.yml
```

**告警规则汇总**：

| 告警名 | 组 | 严重级别 | 触发条件 | 持续时间 |
|---|---|---|---|---|
| GatewayNoHealthyNodes | availability | critical | `privacy_gateway_healthy_nodes == 0` | 1m |
| GatewayDegradedCapacity | availability | warning | `privacy_gateway_healthy_nodes < 2` | 5m |
| HighRequestLatencyP95 | latency | warning | P95 > 1s | 5m |
| HighGatewayLatencyP95 | latency | warning | 网关 P95 > 2s | 5m |
| HighClassificationLatency | latency | warning | 分类 P95 > 5s | 5m |
| HighGatewayErrorRate | errors | critical | 5xx 错误率 > 5% | 5m |
| HighAuthDenialRate | errors | warning | 认证拒绝率 > 10% | 5m |
| HighGatewayRetryRate | errors | warning | 重试率 > 10% | 5m |
| PrivacyBudgetNearlyExhausted | privacy | warning | 预算剩余 < 0.1 | 1m |
| PrivacyBudgetExhausted | privacy | critical | 预算耗尽 ≤ 0 | 1m |
| ClassificationReviewQueueBacklog | classification | warning | 审核队列 > 100 | 10m |
| HighLLMClassifierErrorRate | classification | warning | LLM 错误率 > 10% | 5m |

### 9.3 Grafana 仪表盘

预置仪表盘 JSON 位于 `deploy/grafana/dashboard.json`，包含以下面板：

| 面板 | PromQL | 说明 |
|---|---|---|
| Request Rate (by method) | `sum(rate(privacy_requests_total[5m])) by (method)` | 请求速率 |
| Request Latency (p50/p95) | `histogram_quantile(0.95/0.50, ...)` | 延迟分位数 |
| Gateway Request Rate | `sum(rate(privacy_gateway_requests_total[5m])) by (protocol, status)` | 网关流量 |
| Gateway Healthy Nodes | `privacy_gateway_healthy_nodes` | 健康节点数（Stat） |
| Gateway Latency (p95) | `histogram_quantile(0.95, ...)` | 网关延迟 |
| Classification Results | `sum(rate(privacy_classification_total[5m])) by (final_level, layer)` | 分类结果分布 |
| Auth Denials (by reason) | `sum(rate(privacy_auth_denials_total[5m])) by (reason)` | 认证拒绝 |
| Privacy Budget Remaining | `privacy_budget_remaining` | 预算余量 |
| Privacy Primitives Operations | masking / kano / dp 速率 | 原语操作速率 |

**导入方式**：Grafana → Dashboards → Import → Upload JSON file → 选择 `deploy/grafana/dashboard.json`。

### 9.4 ServiceMonitor（Prometheus Operator）

Helm 安装时设置 `serviceMonitor.enabled=true` 即可自动创建 ServiceMonitor：

```yaml
# 生成的 ServiceMonitor 规格
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: privacy-local-agent
  endpoints:
    - port: http
      path: /metrics
      interval: 30s
      scrapeTimeout: 10s
```

---

## 10. 自动伸缩（HPA）

Helm 模板使用 `autoscaling/v2` API，支持 CPU 和内存双指标：

```yaml
# values-production.yaml
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 80
```

**注意事项**：
- 启用 HPA 后，`replicaCount` 字段被忽略（Deployment 模板中有条件判断）。
- 可通过 `autoscaling.behavior` 自定义缩容策略（如冷却窗口）。
- ML 镜像建议 `maxReplicas` 不宜过大（单 Pod 内存占用高）。

---

## 11. 网络策略（NetworkPolicy）

生产环境启用 NetworkPolicy 限制入站流量：

```yaml
# values-production.yaml
networkPolicy:
  enabled: true
  ingress:
    from:
      - podSelector:
          matchLabels:
            app.kubernetes.io/part-of: privacy-local-agent
```

**生成的 NetworkPolicy 行为**：
- 仅允许同命名空间中带有 `app.kubernetes.io/part-of: privacy-local-agent` 标签的 Pod 访问。
- 开放端口：REST（8079）+ gRPC（50051）。
- 如需允许 Ingress Controller 访问，需额外添加对应的 `from` 规则。

---

## 12. 环境变量参考

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PRIVACY_REST_HOST` | `0.0.0.0`（容器）/ `127.0.0.1`（本地） | REST 监听地址 |
| `PRIVACY_REST_PORT` | `8079` | REST 监听端口 |
| `PRIVACY_GRPC_HOST` | `0.0.0.0`（容器）/ `127.0.0.1`（本地） | gRPC 监听地址 |
| `PRIVACY_GRPC_PORT` | `50051` | gRPC 监听端口 |
| `PRIVACY_PROFILE` | — | YAML 参数 Profile 路径 |
| `PRIVACY_NAMESPACE` | `default` | 预算命名空间 |
| `PRIVACY_BUDGET_DB` | — | SQLite 预算持久化路径（多实例必配） |
| `PRIVACY_BUDGET_WINDOW_SECONDS` | — | 预算自动重置时间窗口 |
| `PRIVACY_LOG_LEVEL` | `INFO` | 日志级别 |
| `PRIVACY_LOG_FORMAT` | `text` | 日志格式：`text` / `json` |
| `PRIVACY_SERVICE_NAME` | `privacy-local-agent` | 服务名（日志/Tracing） |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OpenTelemetry OTLP 端点 |
| `OTEL_SERVICE_NAME` | — | Tracing 服务名（覆盖 PRIVACY_SERVICE_NAME） |
| `PRIVACY_TLS_ENABLED` | `false` | 启用 TLS |
| `PRIVACY_TLS_CERT_FILE` | — | 证书路径 |
| `PRIVACY_TLS_KEY_FILE` | — | 私钥路径 |
| `PRIVACY_TLS_CA_FILE` | — | CA 证书路径（mTLS） |
| `PRIVACY_TLS_CLIENT_AUTH` | `none` | 客户端认证：none/optional/require |
| `PRIVACY_TLS_KEY_PASSWORD` | — | 私钥密码 |
| `PRIVACY_AUTH_ENABLED` | `false` | 启用 API Key 认证 |
| `PRIVACY_AUTH_EXTERNAL_KEYS_JSON` | `{}` | 外部 Key JSON |
| `PRIVACY_AUTH_INTERNAL_KEYS_JSON` | `{}` | 内部 Key JSON |
| `PRIVACY_RATE_LIMIT_ENABLED` | `false` | 启用速率限制 |
| `PRIVACY_RATE_LIMIT_DEFAULT_RPS` | `10` | 默认 RPS |
| `PRIVACY_RATE_LIMIT_DEFAULT_BURST` | `20` | 默认突发 |
| `PRIVACY_RATE_LIMIT_REDIS_URL` | — | Redis 限流后端 |
| `PRIVACY_WARMUP_LLM` | `false` | 启动时异步预热 LLM |
| `PRIVACY_ASYNC_MAX_WORKERS` | `4` | 异步分类线程池大小 |
| `PRIVACY_ASYNC_JOB_TTL_SECONDS` | `3600` | 异步任务 TTL |
| `PRIVACY_ASYNC_MAX_JOBS` | `1000` | 最大并发异步任务数 |
| `PRIVACY_REVIEW_DB` | — | 分类审核 SQLite 路径 |
| `PRIVACY_VLM_TIMEOUT` | `180` | VLM 推理超时（秒） |
| `PRIVACY_HEALTH_NO_AUTH` | `true` | 健康检查跳过认证 |
| `PRIVACY_HEALTH_NO_RATE_LIMIT` | `true` | 健康检查跳过限速 |

---

## 13. 验证与冒烟测试

### 13.1 K8s 环境验证

```bash
# 查看 Pod 状态
kubectl get pods -n privacy-local-agent -l app=privacy-local-agent

# 查看启动日志
kubectl logs -n privacy-local-agent deploy/privacy-local-agent -f

# 端口转发
kubectl port-forward -n privacy-local-agent svc/privacy-local-agent 8079:8079 50051:50051

# 健康检查
curl http://localhost:8079/health
# 期望：{"status":"ok","namespace":"default"}

# 就绪探针
curl http://localhost:8079/readyz
# 期望：{"status":"ready","llm_ready":true/false}

# 存活探针
curl http://localhost:8079/livez
# 期望：{"status":"alive"}

# Prometheus 指标
curl http://localhost:8079/metrics | head -20

# 脱敏接口冒烟
curl -X POST http://localhost:8079/mask \
  -H "Content-Type: application/json" \
  -d '{"data": {"name": "张三", "phone": "13800138000"}}'

# DP 查询冒烟
curl -X POST http://localhost:8079/dp/count \
  -H "Content-Type: application/json" \
  -d '{"value": 100, "epsilon": 1.0}'
```

### 13.2 TLS 环境验证

```bash
# 跳过证书验证
curl -k https://localhost:8079/health

# 指定 CA 证书
curl --cacert ca.crt https://localhost:8079/health

# 携带 API Key
curl -k -H "X-API-Key: your-api-key" https://localhost:8079/health
```

### 13.3 Docker Compose 验证

```bash
cd deploy/docker-compose

# 查看容器状态与健康检查
docker-compose ps

# 查看日志
docker-compose logs -f privacy-local-agent

# 冒烟测试
curl http://localhost:8079/health
```

---

## 14. 故障排查

| 现象 | 可能原因 | 排查步骤 |
|---|---|---|
| Pod CrashLoopBackOff | TLS 证书路径错误或 Profile YAML 语法错误 | `kubectl logs deploy/privacy-local-agent` 查看启动异常 |
| Pod Pending | 资源不足 / 节点亲和不满足 | `kubectl describe pod <name>` 查看 Events |
| 健康检查失败（liveness） | 端口未监听 / 安全中间件拦截 | 确认 `/health` 在 `publicPaths` 白名单中；检查 `PRIVACY_HEALTH_NO_AUTH=true` |
| 就绪探针 503 | 配置解析器未初始化 / SQLite DB 不可达 | 检查 `PRIVACY_PROFILE` 路径是否正确挂载；检查 `PRIVACY_BUDGET_DB` 文件权限 |
| LLM 探针 503 | 模型预热中或初始化失败 | 查看日志中 `warmup` 相关信息；确认 ml 镜像且模型文件存在 |
| gRPC 调用失败 | Service 端口 / TLS 设置不一致 | 确认 Service 暴露 50051；TLS 模式下客户端需使用 TLS channel |
| 认证 401 | API Key 未配置或格式错误 | 检查 `PRIVACY_AUTH_EXTERNAL_KEYS_JSON` 是否为合法 JSON；Header 使用 `X-API-Key` |
| 速率限制 429 | RPS 超限 | 调大 `PRIVACY_RATE_LIMIT_DEFAULT_RPS` 或配置 `PER_ENDPOINT` 覆盖 |
| 隐私预算拒绝 | 预算耗尽 | 查看 `privacy_budget_remaining` 指标；配置 `PRIVACY_BUDGET_WINDOW_SECONDS` 自动重置 |
| OOMKilled | 内存不足（尤其 ML 镜像） | 调大 `resources.limits.memory`；ML 建议至少 8Gi |
| 分类延迟过高 | LLM 推理慢 / 模型未预热 | 启用 `PRIVACY_WARMUP_LLM=true`；查看 `privacy_classification_llm_duration_seconds` |
| 多实例预算不一致 | 使用内存预算后端 | 配置 `PRIVACY_BUDGET_DB` 使用 SQLite 持久化 |

**常用诊断命令**：

```bash
# 查看 Pod 事件
kubectl describe pod -n privacy-local-agent -l app=privacy-local-agent

# 进入容器调试
kubectl exec -it -n privacy-local-agent deploy/privacy-local-agent -- /bin/sh

# 容器内测试端口
curl http://localhost:8079/health
curl http://localhost:8079/metrics | grep privacy_budget

# 查看 ConfigMap 内容
kubectl get configmap privacy-local-agent-config -n privacy-local-agent -o yaml

# 查看 Secret（base64 编码）
kubectl get secret pla-tls -n privacy-local-agent -o yaml
```

---

## 15. 日常运维操作

### 15.1 扩缩容

```bash
# 手动扩容（HPA 关闭时）
kubectl scale deploy/privacy-local-agent -n privacy-local-agent --replicas=3

# 查看 HPA 状态（HPA 开启时）
kubectl get hpa -n privacy-local-agent
```

### 15.2 滚动更新

```bash
# 更新镜像版本
kubectl set image deploy/privacy-local-agent \
  agent=myregistry/privacy-local-agent:0.2.0 \
  -n privacy-local-agent

# 查看滚动状态
kubectl rollout status deploy/privacy-local-agent -n privacy-local-agent

# 回滚
kubectl rollout undo deploy/privacy-local-agent -n privacy-local-agent
```

### 15.3 配置变更

```bash
# 编辑 ConfigMap
kubectl edit configmap privacy-local-agent-config -n privacy-local-agent

# 重启 Pod 使配置生效（ConfigMap 更新不会自动触发滚动）
kubectl rollout restart deploy/privacy-local-agent -n privacy-local-agent
```

### 15.4 证书轮换

```bash
# 更新 TLS Secret
kubectl create secret tls pla-tls \
  --cert=new-tls.crt --key=new-tls.key \
  -n privacy-local-agent --dry-run=client -o yaml | kubectl apply -f -

# 重启 Pod 加载新证书
kubectl rollout restart deploy/privacy-local-agent -n privacy-local-agent
```

### 15.5 日志查看

```bash
# 实时日志
kubectl logs -f -n privacy-local-agent deploy/privacy-local-agent

# 最近 100 行
kubectl logs --tail=100 -n privacy-local-agent deploy/privacy-local-agent

# JSON 格式日志过滤（生产模式 logFormat=json）
kubectl logs -n privacy-local-agent deploy/privacy-local-agent | jq '.level == "error"'
```

### 15.6 Helm 预检查（CI/CD）

```bash
# Chart 语法检查
make helm-lint

# 渲染模板（不实际安装）
make helm-template

# 自定义 values 渲染
helm template pla ./deploy/helm/privacy-local-agent \
  -f ./deploy/helm/privacy-local-agent/values-production.yaml \
  --set security.tls.existingSecret=pla-tls
```
