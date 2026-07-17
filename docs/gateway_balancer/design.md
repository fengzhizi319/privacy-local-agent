# 代理转发与负载均衡网关设计文档

## 1. 概述

本文档定义 `privacy-local-agent` 代理转发与负载均衡网关（API Gateway & Load Balancer）的技术架构、算法原理与实现细节。该网关作为统一的请求入口，将 REST 与 gRPC 流量分发到后端多个健康的工作节点，实现水平扩展与高可用。

## 2. 设计目标

- 作为轻量级独立组件（也可与 Agent 运行于同镜像中），透明化后端节点集群。
- 支持横向扩展，能够弹性挂载任意数量的后端工作实例。
- 提供多种负载均衡策略，提高整体系统吞吐量。
- 实现故障隔离与自动恢复，动态维护可用节点池。
- 支持分布式共享隐私预算账本，保证多实例场景下预算消耗的全局一致性。

## 3. 系统架构

```mermaid
graph TD
    Client[客户端] -- HTTP/gRPC --> Gateway[网关]

    subgraph Gateway [网关内部]
        HTTPProxy[HTTP 代理服务 (FastAPI)]
        gRPCProxy[gRPC 代理服务 (grpc.aio)]
        LB[负载均衡引擎]
        HC[健康检查器]

        HTTPProxy --> LB
        gRPCProxy --> LB
        HC -->|定时检测| LB
    end

    LB -- HTTP 转发 --> Node1[Agent Node 1]
    LB -- gRPC 转发 --> Node2[Agent Node 2]
    LB -- HTTP/gRPC --> NodeN[Agent Node N]
```

网关代码位于 `privacy_local_agent.gateway`，保持良好内聚与低耦合。

## 4. 核心模块设计

### 4.1 BackendNode

封装单个后端节点：

- `http_url`：REST 地址
- `grpc_address`：gRPC 地址
- `is_healthy`：健康状态
- `active_connections`：当前活跃连接数
- `grpc_channel` / `grpc_stub`：复用的 gRPC 通道与 stub

### 4.2 LoadBalancer

维护节点列表并提供协程安全的节点选择：

- `add_node()`：添加节点
- `get_healthy_nodes()`：获取健康节点
- `select_node()`：按策略选择节点

使用 `asyncio.Lock` 保护轮询计数器，防止多协程并发冲突。

### 4.3 HTTP 代理模块 (`http_proxy.py`)

基于 FastAPI，使用路径通配符 `{path:path}` 匹配所有路径：

```python
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
async def proxy_http(path: str, request: Request): ...
```

转发流程：

1. 通过 `LoadBalancer.select_node()` 选择健康后端节点。
2. 提取 Method、Headers、Query Params、Body。
3. 复用应用级全局单例 `httpx.AsyncClient` 发送异步请求。
4. 将 Response 状态码、响应头、内容原样返回。

为避免 Event Loop 切换导致的 `Event loop is closed` 错误，网关实现事件循环感知机制：当检测到当前 Loop 与缓存客户端绑定的 Loop 不一致时，自动重建 `httpx.AsyncClient`。

### 4.4 gRPC 代理模块 (`grpc_proxy.py`)

基于 `grpc.aio`，实现 `PrivacyServiceServicer`：

- 通用私有方法 `_forward(method_name, request, context)`。
- 通过 `getattr(node.grpc_stub, method_name)` 动态反射调用。
- 捕获 `grpc.RpcError` 并通过 `context.abort()` 回传。
- **泛化转发**：初始化时自动为 `PrivacyService` 中的所有 RPC 方法绑定转发函数，无需为每个接口手写代理方法。`privacy.proto` 新增方法后，只要重新生成 Python 存根，网关即可自动转发。

## 5. 负载均衡算法

### 5.1 轮询（Round-Robin）

- 维护索引 `rr_index`，初始为 0。
- 返回 `healthy_nodes[rr_index % len(healthy_nodes)]`，索引自增 1。

### 5.2 随机选择（Random）

- `random.choice(healthy_nodes)`。

### 5.3 最小连接数（Least Connections）

- 分发前 `active_connections += 1`，完成后 `-= 1`。
- 返回 `min(healthy_nodes, key=lambda node: node.active_connections)`。

## 6. 健康检查与自愈

后台守护任务默认每 5 秒执行一次：

- 发送 HTTP `GET <http_url>/health`，预期 200 且 `status == "ok"`。
- 发送 gRPC `Health` RPC，预期 `status == "ok"`。
- 必须两者均通过才标记为健康。
- 不健康节点被排除出可用池，直到下一次检查通过。
- 可用池为空时返回 HTTP 503 / gRPC UNAVAILABLE。

## 7. 错误处理与容错

| 场景 | 处理 |
|---|---|
| 无可用节点 | HTTP 503 / gRPC UNAVAILABLE |
| 连接超时/网络波动 | 捕获 `httpx.RequestError`，返回 502 Bad Gateway |
| 后端 gRPC 错误 | 透传 `grpc.RpcError` 状态码与描述 |

## 8. 分布式隐私预算记账

为避免多实例部署时预算管理失效，`BudgetAccountant` 实现双模式：

1. **内存模式**：未配置持久化数据库时运行，通过线程锁保护单例对象，适用于单进程高吞吐场景。
2. **SQLite 持久化模式**：
   - 通过 `PRIVACY_BUDGET_DB` 启用。
   - 自动创建 `privacy_budgets` 表，记录预算上限与累计消费。
   - `spend()` 操作包裹在 `BEGIN IMMEDIATE` 独占事务中，保证多节点并发下的强一致性。
   - 超扣时触发 ROLLBACK。

## 9. 配置方式

支持 YAML 配置文件或环境变量：

- 网关监听的 REST 端口与 gRPC 端口
- 后端节点列表（`http_url` + `grpc_address`）
- 负载均衡策略（`round_robin` / `random` / `least_connections`）
- 健康检查间隔、超时时间

## 10. 非功能设计

| 维度 | 要求 |
|---|---|
| 转发延迟 | 网关引入的额外耗时 ≤ 5ms |
| 并发模型 | REST 基于 FastAPI 异步；gRPC 基于 `grpc.aio` |
| 连接复用 | 应用级单例 HTTP 客户端连接池与 gRPC 连接池 |
| 鲁棒性 | 优雅处理超时、重试、连接被拒绝等异常 |
| 模块隔离 | 新增代码位于 `privacy_local_agent.gateway`，减少侵入 |

## 11. 测试策略

- REST/gRPC 请求转发正确性测试。
- 三种负载均衡策略单元测试。
- 健康检查动态增删节点测试。
- 分布式共享预算一致性测试。
- 网关转发延迟与异常处理测试。
