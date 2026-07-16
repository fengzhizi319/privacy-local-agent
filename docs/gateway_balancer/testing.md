# 代理转发与负载均衡网关测试报告 (Testing Report)

## 1. 测试目的与策略

### 1.1 测试目的
验证代理转发与负载均衡网关 (Gateway / Load Balancer) 的正确性、可用性与健壮性，确保其能够在高并发环境下正确分发 REST HTTP 和 gRPC 流量，并平滑处理后端节点的健康状态更替与故障转移。

### 1.2 测试策略
- **集成测试 (Integration Testing)**：不使用繁琐的 Mock 数据，而是通过测试夹具 (pytest fixture) 在后台线程中真实启动 `privacy-local-agent` 服务（同时监听动态分配的空闲 REST 和 gRPC 端口）。
- **动态端口分配**：通过 socket 自动获取系统空闲端口，彻底规避并行测试或不同机器上的端口冲突问题。
- **双协议验证**：
  - REST：使用 FastAPI `TestClient` 发送 HTTP 请求给网关，验证网关代理的透传率、正确率及异常响应 (503)。
  - gRPC：使用 `grpc.aio` 协程环境调用网关 gRPC servicer 方法，验证 RPC 调用的动态反射、错误传播和负载均衡逻辑。

## 2. 测试用例说明

集成测试代码存放在 [tests/test_gateway.py](file:///home/charles/code/sfwork/privacy-local-agent/tests/test_gateway.py) 中，主要包含以下六个核心测试用例：

### 2.1 负载均衡算法策略测试 (`test_load_balancer_strategies`)
- **测试方法**：
  1. 初始化 `LoadBalancer(strategy="round_robin")`，添加健康的 Agent 节点，验证返回节点符合预期。
  2. 将策略修改为 `least_connections`（最小连接数）。
  3. 为当前节点人为增加 active_connections 数值，同时加入一个连接数为 0 的新节点，验证选择引擎是否能智能挑选活动连接数最低的新节点。
- **预期结果**：节点选择符合所选分发策略。

### 2.2 HTTP (REST) 代理转发测试 (`test_http_proxy_forwarding`)
- **测试方法**：
  1. 通过 `create_http_gateway_app` 实例化 HTTP 网关，用 `TestClient` 发送请求。
  2. 请求 `/health` 验证网关通配转发和健康检查 API 的透明转发。
  3. 请求 `/v1/privacy/mask` 提交脱敏数据，验证 POST Body 与 Header 被正确传递给后端且结果被原样返回。
  4. 将可用节点清空，验证在无可用后端时网关是否能准确抛出 `503 Service Unavailable` 状态码。
- **预期结果**：请求被精准代理至后端工作节点，状态码及响应体无偏差，无节点时返回 503。

### 2.3 gRPC 代理转发测试 (`test_grpc_proxy_forwarding`)
- **测试方法**：
  1. 实例化 `GatewayGrpcServicer`，并注入包含真实后端 Agent 地址的负载均衡器。
  2. 模拟 gRPC `ServicerContext` 供 servicer 方法调用。
  3. 异步调用 `Mask` 方法和 `Health` 方法，验证数据在 gRPC Client-Server 链路上的完整双向序列化与反序列化。
- **预期结果**：gRPC 调用被平滑地异步反射转发至后端并成功获得响应，无协议降级或内容缺失。

### 2.4 动态自注册与销毁 API 测试 (`test_dynamic_registration`)
- **测试方法**：
  1. 验证初始节点池为空。
  2. 发送 POST `/v1/gateway/register` 请求注册新节点，验证节点池数量增加且权重正确。
  3. 再次发送重复的注册请求，验证不会重复创建节点，而是就地更新权重等属性。
  4. 发送 POST `/v1/gateway/deregister` 请求注销节点，验证节点被安全移出地址池且连接已关闭。
- **预期结果**：自注册/注销 API 能够协程安全地动态操作后端实例池，且自动去重。

### 2.5 HTTP 代理自适应重试与被动检测测试 (`test_http_retry_and_passive_failover`)
- **测试方法**：
  1. 在网关中注册一个无效端口的故障节点以及一个真实的健康节点。
  2. 发送 `/v1/privacy/mask` 请求。网关第一轮若轮询到故障节点，应当捕获连接异常，自动将被击中节点标记为 `is_healthy = False`，并立刻在同一请求内故障转移重试至健康的备用节点。
- **预期结果**：客户端最终成功收到 `200` 正确返回，网关日志报警并成功将被动故障的节点下线。

### 2.6 gRPC 代理自适应重试与被动检测测试 (`test_grpc_retry_and_passive_failover`)
- **测试方法**：
  1. 在网关中同时挂载故障节点和健康节点。
  2. 发起 gRPC `Mask` 异步调用，如果首次轮询到不可用的 gRPC 地址，捕获 `StatusCode.UNAVAILABLE`，被动下线该节点并无缝重试至健康的 gRPC 后端。
- **预期结果**：gRPC 服务成功返回脱敏结果，且不把重试的瞬时网络异常暴露给上层客户端。

## 3. 测试执行与结果

在项目根目录下通过 pytest 命令行运行测试：

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_gateway.py
```

### 3.1 运行输出
```text
============================= test session starts ==============================
platform linux -- Python 3.13.13, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/charles/code/sfwork/privacy-local-agent
configfile: pyproject.toml
plugins: anyio-4.14.1
collecting ... collected 6 items                                                              

tests/test_gateway.py ......                                             [100%]

=============================== warnings summary ===============================
.venv/lib/python3.13/site-packages/fastapi/testclient.py:1
  /home/charles/code/sfwork/privacy-local-agent/.venv/lib/python3.13/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
========================= 6 passed, 1 warning in 1.95s =========================
```

测试结果表明：
- 负载均衡调度引擎能够根据不同分发策略作出正确调度决策。
- 网关 HTTP 通配代理对于各路由的 Request/Response 转发均正常，且具备 503 容错能力。
- 网关 gRPC 协程转发链路完全畅通，反射和异常处理工作符合设计预期。
- 新增的**自注册API**、**HTTP自适应重试**与**gRPC被动健康检测**全部测试通过，保障了网关的高可靠与零停机横向扩容。

