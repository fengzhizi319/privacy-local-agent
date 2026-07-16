# 代理转发与负载均衡网关 API 参考

## 1. Python SDK

### `LoadBalancer`

位置：`privacy_local_agent.gateway.balancer.LoadBalancer`

负载均衡调度器，维护后端节点列表并提供协程安全的节点选择。

#### 构造函数

```python
LoadBalancer(strategy: str = "round_robin")
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `strategy` | `str` | 否 | 负载均衡策略，可选 `"round_robin"` / `"random"` / `"least_connections"`，默认 `"round_robin"` |

#### 主要方法

| 方法 | 签名 | 说明 |
|---|---|---|
| `add_node` | `add_node(http_url: str, grpc_address: str, weight: int = 1)` | 添加或更新后端节点；相同地址会就地更新并置为健康 |
| `remove_node` | `remove_node(http_url: str, grpc_address: str)` | 从节点池移除指定后端并关闭其 gRPC 通道 |
| `get_healthy_nodes` | `get_healthy_nodes() -> List[BackendNode]` | 返回当前健康节点列表 |
| `select_node` | `async select_node() -> Optional[BackendNode]` | 按策略选择一个健康节点，无可用节点时返回 `None` |
| `close_all` | `async close_all()` | 关闭所有后端的 gRPC 通道 |

#### 主要属性

| 属性 | 类型 | 说明 |
|---|---|---|
| `strategy` | `str` | 当前负载均衡策略 |
| `nodes` | `List[BackendNode]` | 全部后端节点 |
| `rr_index` | `int` | 轮询索引 |

---

### `BackendNode`

位置：`privacy_local_agent.gateway.balancer.BackendNode`

单个后端工作节点的封装。

| 属性 | 类型 | 说明 |
|---|---|---|
| `http_url` | `str` | 后端 REST 基准 URL，例如 `"http://127.0.0.1:8079"` |
| `grpc_address` | `str` | 后端 gRPC 地址，例如 `"127.0.0.1:50051"` |
| `weight` | `int` | 权重（预留） |
| `is_healthy` | `bool` | 健康状态 |
| `active_connections` | `int` | 当前活跃连接数 |
| `grpc_stub` | `PrivacyServiceStub` | 延迟初始化的 gRPC Stub |

---

### `health_check_loop`

位置：`privacy_local_agent.gateway.balancer.health_check_loop`

```python
async def health_check_loop(balancer: LoadBalancer, interval: float = 5.0)
```

后台健康检查协程，定时检测所有后端节点的 REST `/health` 与 gRPC `Health`。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `balancer` | `LoadBalancer` | 是 | 关联的负载均衡实例 |
| `interval` | `float` | 否 | 检测间隔（秒），默认 `5.0` |

---

### `create_http_gateway_app`

位置：`privacy_local_agent.gateway.http_proxy.create_http_gateway_app`

```python
def create_http_gateway_app(balancer: LoadBalancer) -> FastAPI
```

创建 HTTP 网关 FastAPI 应用，暴露动态注册 / 注销接口与通配代理路由。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `balancer` | `LoadBalancer` | 是 | 关联的负载均衡实例 |

---

### `start_grpc_gateway`

位置：`privacy_local_agent.gateway.grpc_proxy.start_grpc_gateway`

```python
async def start_grpc_gateway(
    host: str,
    port: int,
    balancer: LoadBalancer,
) -> grpc.aio.Server
```

启动 gRPC 网关服务器。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `host` | `str` | 是 | 监听主机 |
| `port` | `int` | 是 | 监听端口 |
| `balancer` | `LoadBalancer` | 是 | 关联的负载均衡实例 |

---

## 2. YAML / 环境变量配置参考

### 2.1 YAML 配置文件

网关默认不加载文件，可通过环境变量 `PRIVACY_GATEWAY_CONFIG` 指定 YAML 路径。

```yaml
gateway:
  rest_host: "0.0.0.0"
  rest_port: 8000
  grpc_host: "0.0.0.0"
  grpc_port: 50000
  strategy: "round_robin"
  health_check_interval: 5.0

backends:
  - http_url: "http://127.0.0.1:8079"
    grpc_address: "127.0.0.1:50051"
    weight: 1
  - http_url: "http://127.0.0.1:8080"
    grpc_address: "127.0.0.1:50052"
    weight: 1
```

### 2.2 配置项说明

| YAML 字段 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| `gateway.rest_host` | `GATEWAY_REST_HOST` | `0.0.0.0` | HTTP 网关监听地址 |
| `gateway.rest_port` | `GATEWAY_REST_PORT` | `8000` | HTTP 网关监听端口 |
| `gateway.grpc_host` | `GATEWAY_GRPC_HOST` | `0.0.0.0` | gRPC 网关监听地址 |
| `gateway.grpc_port` | `GATEWAY_GRPC_PORT` | `50000` | gRPC 网关监听端口 |
| `gateway.strategy` | `GATEWAY_STRATEGY` | `round_robin` | 负载均衡策略 |
| `gateway.health_check_interval` | `GATEWAY_HEALTH_INTERVAL` | `5.0` | 健康检查间隔（秒） |
| — | `PRIVACY_GATEWAY_CONFIG` | — | YAML 配置文件路径 |
| `backends` | `GATEWAY_BACKENDS` | `[]` | 后端节点列表 |

### 2.3 `GATEWAY_BACKENDS` 格式

多个节点以英文逗号分隔，每个节点内部 HTTP URL 与 gRPC 地址用 `|` 分隔：

```text
http://127.0.0.1:8079|127.0.0.1:50051,http://127.0.0.1:8080|127.0.0.1:50052
```

通过环境变量注册时，权重固定为 `1`。

---

## 3. REST 代理行为

### 3.1 通配转发路由

```text
/{path:path}
```

支持方法：`GET`, `POST`, `PUT`, `DELETE`, `OPTIONS`, `HEAD`, `PATCH`。

转发时会：

1. 调用 `LoadBalancer.select_node()` 选择健康后端。
2. 透传查询参数与请求体。
3. 过滤 Hop-by-hop 头（见下表）。
4. 透传后端响应状态码、响应头与响应体。
5. 连接异常时标记节点不健康并重试，最多 `3` 次。

### 3.2 Hop-by-hop 过滤头

以下头部不会被转发到后端或客户端：

| 头部 | 说明 |
|---|---|
| `connection` | 连接管理 |
| `keep-alive` | 持久连接 |
| `proxy-authenticate` | 代理认证 |
| `proxy-authorization` | 代理鉴权 |
| `te` | 传输编码协商 |
| `trailers` | 分块尾部 |
| `transfer-encoding` | 传输编码 |
| `upgrade` | 协议升级 |
| `content-length` | 内容长度 |
| `host` | 目标主机 |

### 3.3 动态节点管理接口

#### POST `/v1/gateway/register`

注册新后端节点。

请求体：

```json
{
  "http_url": "http://127.0.0.1:8080",
  "grpc_address": "127.0.0.1:50052",
  "weight": 1
}
```

响应体：

```json
{"status": "registered"}
```

#### POST `/v1/gateway/deregister`

注销后端节点。

请求体：

```json
{
  "http_url": "http://127.0.0.1:8080",
  "grpc_address": "127.0.0.1:50052"
}
```

响应体：

```json
{"status": "deregistered"}
```

---

## 4. gRPC 代理行为

### 4.1 转发方法列表

网关实现 `PrivacyService` 接口，支持反射转发以下方法：

| RPC 方法 | 说明 |
|---|---|
| `Mask` | 字段级脱敏 |
| `MaskRecord` | 记录级脱敏 |
| `Hash` | 哈希计算 |
| `DPCount` | 差分隐私计数 |
| `DPSum` | 差分隐私求和 |
| `DPMean` | 差分隐私均值 |
| `KAnonymizeRecord` | K-匿名化 |
| `ObfuscateQuery` | 查询混淆 |
| `ClassifyField` | 字段分类 |
| `ClassifyRecord` | 记录分类 |
| `ClassifyTable` | 表分类 |
| `Health` | 健康检查 |

### 4.2 转发逻辑

1. 按负载均衡策略选择健康后端节点。
2. 通过 `getattr(node.grpc_stub, method_name)` 反射调用后端对应 RPC。
3. 单次调用超时 `30` 秒。
4. 遇到 `grpc.StatusCode.UNAVAILABLE` 时标记节点不健康并重试，最多 `3` 次。
5. 其他业务错误直接透传原状态码与详情。

---

## 5. 负载均衡策略

| 策略 | 关键字 | 说明 |
|---|---|---|
| 轮询 | `round_robin` | 依次遍历健康节点，默认策略 |
| 随机 | `random` | 从健康节点中随机选择 |
| 最小连接数 | `least_connections` | 选择当前 `active_connections` 最小的健康节点 |

策略在网关启动时通过 `strategy` 配置项或 `GATEWAY_STRATEGY` 环境变量指定；运行时也允许直接修改 `LoadBalancer.strategy`。

---

## 6. 健康检查参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| 检查间隔 | `5.0` 秒 | 由 `health_check_interval` / `GATEWAY_HEALTH_INTERVAL` 控制 |
| HTTP 检查路径 | `/health` | 预期返回 `200` 且 JSON 中 `status == "ok"` |
| gRPC 检查方法 | `Health` | 预期返回 `status == "ok"` |
| HTTP 超时 | `2.0` 秒 | 单次 HTTP 健康检查超时 |
| gRPC 超时 | `2.0` 秒 | 单次 gRPC 健康检查超时 |
| 健康判定 | 双协议均通过 | 任一协议失败即标记为不健康 |

---

## 7. 异常与错误码

| 场景 | HTTP 状态码 | gRPC 状态码 | 说明 |
|---|---|---|---|
| 无健康后端节点 | `503` | `UNAVAILABLE` | 可用节点池为空 |
| 后端连接异常且重试耗尽 | `502` | `INTERNAL` | 所有后端均不可用 |
| 后端返回业务错误 | 透传后端状态码 | 透传 `grpc.RpcError` | 非连接类错误直接回传 |
| 注册 / 注销参数非法 | `422` | — | Pydantic 校验失败 |
