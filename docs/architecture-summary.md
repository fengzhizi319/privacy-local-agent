# privacy-local-agent 设计实现总结

> 本文档总结 privacy-local-agent 的架构设计、工程实践与注意事项，供其他项目参照。

---

## 一、项目定位

Python REST + gRPC 双协议隐私计算 Sidecar，以独立进程/容器形式部署，为任意语言的后端服务
提供隐私计算能力。相比 Java/Go SDK 的嵌入式库模式，Agent 模式适合：
- 多语言微服务共享同一隐私计算实例
- 无法嵌入 SDK 的遗留系统
- 需要独立扩缩容的高并发场景

---

## 二、应该做的（标准实践）

### 2.1 分层架构

```
privacy_local_agent/
├── privacy/           → 核心隐私原语 (dp, masking, kano, qol, budget, classification*)
├── security/          → 安全层 (auth, ratelimit, tls, identity, config)
├── observability/     → 可观测性 (metrics, tracing, logging_config, middleware, context)
├── gateway/           → 网关/负载均衡 (balancer, http_proxy, grpc_proxy, server)
├── main.py            → REST API 入口 (FastAPI)
├── grpc_server.py     → gRPC 服务入口
├── service.py         → 业务服务层 (PrivacyService)
├── classification_routes.py → 分类路由 (独立 Router)
└── classification_service.py → 分类业务逻辑
```

**要点**：
- 隐私原语、安全、可观测性三大关注点独立包
- REST 和 gRPC 共享同一 `PrivacyService` 业务层
- 分类功能因复杂度高，独立拆分 routes/service/engine 三层

### 2.2 双协议支持

```
REST (FastAPI, port 8079)  ←→  PrivacyService  ←→  隐私原语
gRPC (grpcio, port 50051)  ←→  PrivacyService  ←→  隐私原语
```
- Protobuf 定义在 `proto/privacy.proto`
- 生成代码 `privacy_pb2.py` / `privacy_pb2_grpc.py`
- 两种协议共享同一业务逻辑，避免重复实现

### 2.3 安全体系

| 层次 | 实现 | 说明 |
|------|------|------|
| 认证 | API Key (Bearer Token) | 内部/外部 Key 分离 |
| 认证 | mTLS 客户端证书 | gRPC 场景 |
| 授权 | RBAC (scope-based) | `require_permission("dp:query")` |
| 限速 | 令牌桶 | 按 identity 独立限速 |
| 传输 | TLS 1.3 | 可选，环境变量配置 |

### 2.4 可观测性三支柱

| 支柱 | 实现 | 说明 |
|------|------|------|
| Metrics | prometheus-client | 20+ 指标 (请求/DP/分类/预算) |
| Tracing | OpenTelemetry (可选) | OTLP HTTP 导出，未安装时 no-op |
| Logging | structlog 风格 | JSON/Text 双格式，request_id 透传 |

### 2.5 隐私预算增强

相比 Java/Go SDK 的基础预算管控，Agent 版本额外提供：
- **时间窗口重置**：`PRIVACY_BUDGET_WINDOW_SECONDS` 防止长期运行耗尽预算
- **RDP Accountant**：Rényi DP 会计，更紧的隐私损失组合
- **HMAC 审计日志**：不可篡改的预算消耗记录
- **Prometheus Gauge**：实时暴露剩余预算

### 2.6 测试体系

| 层次 | 文件数 | 说明 |
|------|--------|------|
| 单元测试 | 36 个 test_*.py | 覆盖所有原语 + 安全 + 可观测 |
| 属性测试 | hypothesis | DP 统计保证 |
| 分布验证 | scipy | 噪声分布 KS 检验 |
| 并发测试 | test_budget_concurrency.py | 多线程预算安全 |
| 基准测试 | benchmark_*.py | pytest-benchmark |
| 集成测试 | @pytest.mark.integration | 需外部依赖 |

### 2.7 CI/CD 流水线

```yaml
lint (ruff + mypy)
  → test (Python 3.10/3.11/3.12 矩阵, coverage)
    → security (pip-audit)
      → docker build
```

### 2.8 部署方案

| 方式 | 路径 | 说明 |
|------|------|------|
| Docker | `Dockerfile` | 多阶段构建 |
| Docker Compose | `deploy/docker-compose/` | 本地开发 |
| Kubernetes | `deploy/k8s/` | Kustomize |
| Helm | `deploy/helm/` | 生产级 Chart (HPA/NetworkPolicy/ServiceMonitor) |

### 2.9 工程规范文件

| 文件 | 作用 |
|------|------|
| `pyproject.toml` | ruff + mypy + pytest-cov + markers 配置 |
| `.pre-commit-config.yaml` | ruff + ruff-format + mypy + hooks |
| `Makefile` | lint/format/typecheck/test/cover/bench |
| `CONTRIBUTING.md` | 贡献指南 |
| `SECURITY.md` | 安全漏洞报告 |
| `CHANGELOG.md` | Keep a Changelog |
| `mkdocs.yml` | 文档站点生成 |

---

## 三、额外做的优秀设计

### 3.1 分布式无噪累加器 (Accumulator)

```python
@dataclass
class Accumulator:
    """MapReduce 场景：Worker 本地累加 → Master 合并 → 统一注入一次噪声"""
    count: int = 0
    sum: float = 0.0
    sum_squares: float = 0.0

    def __add__(self, other): ...  # 合并
    def finalize_dp(self, epsilon, delta, mechanism): ...  # 统一加噪
```
**优势**：分布式/联邦场景下避免每个 Worker 各自加噪导致噪声放大。

### 3.2 向量化加速 + 零拷贝

```python
# NumPy C-contiguous 加速
arr = np.ascontiguousarray(values, dtype=np.float64)

# PyArrow Table 元数据传递
def to_arrow(self, result) -> pa.Table: ...

# scipy.sparse 稀疏矩阵优化
if _is_sparse_matrix(data): ...
```
- 大数据量下比纯 Python 循环快 10-100x
- `data_adapters.py` 统一处理 list/ndarray/arrow/sparse 输入

### 3.3 分类引擎五层架构

```
RuleEngine (正则/ICD10/身份证/基因组)
  → SmallNerEngine (本地 NER 模型)
    → LlmClassifier (本地 VLM/LLM)
      → VectorizedClassifier (向量化批量)
        → CompositeClassifier (组合策略)
```
- 异步分类 (`classification_async.py`)：`asyncio` 并发多引擎
- 审核机制 (`classification_review.py`)：低置信度结果人工复核
- 影子模式 (`test_classification_shadow.py`)：新引擎灰度验证

### 3.4 网关负载均衡

```python
class LoadBalancer:
    """支持多后端 Agent 实例的负载均衡"""
    - 加权轮询 / 最少连接 / 随机
    - 异步健康检查 (HTTP + gRPC)
    - 自动摘除/恢复不健康节点
```
**优势**：生产环境多副本部署时，客户端无需感知后端拓扑。

### 3.5 可选依赖优雅降级

```python
# OpenTelemetry 未安装时零开销
try:
    from opentelemetry import trace
    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False

# pyproject.toml 可选依赖组
[project.optional-dependencies]
observability = ["opentelemetry-sdk", "opentelemetry-exporter-otlp"]
ml = ["torch", "transformers"]
```
- 核心功能不依赖可选包
- 未安装时自动降级为 no-op，不报错

### 3.6 环境变量驱动配置

```bash
PRIVACY_PROFILE=privacy-profile.yaml    # 配置文件路径
PRIVACY_NAMESPACE=default               # 预算命名空间
PRIVACY_LOG_LEVEL=INFO                  # 日志级别
PRIVACY_LOG_FORMAT=json                 # 日志格式
PRIVACY_WARMUP_LLM=true                # LLM 预热
PRIVACY_BUDGET_WINDOW_SECONDS=3600     # 预算重置窗口
PRIVACY_AUDIT_KEY=<secret>             # 审计 HMAC 密钥
OTEL_EXPORTER_OTLP_ENDPOINT=http://... # Tracing 导出
```
**优势**：12-Factor App 规范，容器/K8s 部署零代码修改。

### 3.7 LLM 异步预热

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("PRIVACY_WARMUP_LLM", "false").lower() == "true":
        warmup_task = asyncio.create_task(service.classification_api.warmup_async())
    yield
    # shutdown: cancel warmup
```
**优势**：避免首个分类请求因模型加载而超时。

### 3.8 Mechanism 枚举继承 str

```python
class Mechanism(str, Enum):
    LAPLACE = "laplace"
    GAUSSIAN = "gaussian"

# 向后兼容：Mechanism.LAPLACE == "laplace" 为 True
# 同时获得 IDE 自动补全 + 类型检查
```

---

## 四、注意事项

### 4.1 Protobuf 生成文件不入 lint

```toml
# pyproject.toml
[tool.mypy]
exclude = ["privacy_local_agent/privacy_pb2.*"]

[tool.ruff.lint]
# protobuf 生成文件豁免
```
**注意**：修改 `proto/privacy.proto` 后需重新生成：
```bash
python -m grpc_tools.protoc -I proto --python_out=. --grpc_python_out=. proto/privacy.proto
```

### 4.2 预算时间窗口重置

生产环境长运行 Sidecar 需配置 `PRIVACY_BUDGET_WINDOW_SECONDS`，否则预算永久耗尽后服务拒绝所有 DP 请求。
**注意**：窗口重置有隐私风险（组合攻击），需根据业务场景评估。

### 4.3 审计密钥必须外部配置

```python
# 默认密钥仅用于开发，生产必须设置 PRIVACY_AUDIT_KEY
self.secret_key = b"privacy-local-agent-default-audit-key"  # 不安全！
```
**注意**：K8s 部署时通过 Secret 注入，不要硬编码。

### 4.4 FastAPI 依赖注入链

```python
SECURITY_DEPS = [Depends(get_current_identity), Depends(rate_limit_dependency)]

@app.post("/api/v1alpha1/dp/count", dependencies=SECURITY_DEPS)
async def dp_count(req: DPCountRequest): ...
```
**注意**：健康检查 `/health` 不挂安全依赖，否则 K8s liveness probe 会失败。

### 4.5 gRPC 与 asyncio 事件循环

```python
# BackendNode.grpc_stub 延迟初始化，确保绑定当前 Event Loop
@property
def grpc_stub(self):
    if self._grpc_stub is None:
        self._grpc_channel = grpc.aio.insecure_channel(self.grpc_address)
        self._grpc_stub = privacy_pb2_grpc.PrivacyServiceStub(self._grpc_channel)
    return self._grpc_stub
```
**注意**：不要在模块加载时创建 gRPC channel，必须在 async 上下文中延迟创建。

### 4.6 覆盖率门禁

```toml
[tool.pytest.ini_options]
--cov-fail-under=60
```
当前设为 60%，随着测试完善应逐步提高到 80%。

### 4.7 多 Python 版本兼容

- 使用 `from __future__ import annotations` 支持 3.10 的 `X | Y` 类型语法
- CI 矩阵覆盖 3.10/3.11/3.12
- 避免使用 3.12+ 独有特性

---

## 五、技术栈速查

| 组件 | 版本/说明 |
|------|-----------|
| Python | 3.10+ (CI 矩阵 3.10/3.11/3.12) |
| FastAPI | REST API 框架 |
| grpcio | gRPC 服务 |
| Pydantic | 请求/响应模型验证 |
| prometheus-client | Prometheus 指标 |
| OpenTelemetry | 分布式追踪 (可选) |
| NumPy | 向量化计算 |
| PyArrow | 零拷贝数据交换 |
| scipy | 稀疏矩阵 + 统计检验 |
| hypothesis | 属性测试 |
| pytest-benchmark | 性能基准 |
| ruff | Lint + Format |
| mypy | 类型检查 |

---

## 六、可复用的设计模式清单

| 模式 | 应用场景 | 本项目实现 |
|------|----------|------------|
| Sidecar | 语言无关服务化 | 独立进程提供 REST/gRPC |
| Dependency Injection | 安全/限速解耦 | FastAPI `Depends()` |
| Graceful Degradation | 可选依赖 | `try: import otel` / no-op fallback |
| Accumulator (MapReduce) | 分布式聚合 | `Accumulator.__add__` + `finalize_dp` |
| Pipeline | 分类引擎 | Rule → NER → LLM → Vectorized → Composite |
| Token Bucket | 限速 | `ratelimit.py` 按 identity |
| HMAC Audit | 不可篡改日志 | `BudgetAuditLogger` |
| Lifespan Hook | 启动预热/关闭清理 | FastAPI `lifespan` |
| 12-Factor Config | 环境适配 | 全量环境变量驱动 |
| Health Check 分离 | 探针不触发认证 | `/health` 无安全依赖 |
| Load Balancer | 多副本扩缩 | `gateway/balancer.py` |
| str Enum | 向后兼容 + 类型安全 | `Mechanism(str, Enum)` |

---

## 七、与 Java/Go SDK 的对比

| 维度 | Java SDK | Go SDK | Python Agent |
|------|----------|--------|--------------|
| 集成方式 | 嵌入式库 | 嵌入式库 | 独立 Sidecar |
| 协议 | 进程内调用 | 进程内调用 | REST + gRPC |
| 依赖量 | slf4j + snakeyaml | 仅 yaml.v3 | FastAPI + grpcio + numpy... |
| 可观测性 | Micrometer (optional) | 接口化 no-op | Prometheus + OTel |
| 安全 | 无 (库级) | 无 (库级) | API Key + mTLS + RBAC + 限速 |
| 预算增强 | 基础 | 基础 | RDP + 时间窗口 + HMAC 审计 |
| 部署 | jar 依赖 | go module | Docker / K8s / Helm |
| 适用场景 | Java 后端 | Go 微服务 | 多语言 / 遗留系统 |
