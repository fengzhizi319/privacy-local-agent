# 代理转发与负载均衡网关使用示例

## 1. 概述

本文档提供网关的常用启动方式、Python SDK 示例与 REST API 示例，帮助开发者快速将请求通过网关转发到后端 `privacy-local-agent` 工作节点。

## 2. 命令行启动示例

### 2.1 使用环境变量启动

```bash
cd /home/charles/code/sfwork/privacy-local-agent
source .venv/bin/activate

export GATEWAY_REST_PORT=8000
export GATEWAY_GRPC_PORT=50000
export GATEWAY_STRATEGY=round_robin
export GATEWAY_BACKENDS="http://127.0.0.1:8079|127.0.0.1:50051,http://127.0.0.1:8080|127.0.0.1:50052"

PYTHONPATH=. python -m privacy_local_agent.gateway.server
```

### 2.2 使用 YAML 配置文件启动

```bash
export PRIVACY_GATEWAY_CONFIG=docs/gateway_balancer/examples/gateway-config.yaml
PYTHONPATH=. python -m privacy_local_agent.gateway.server
```

示例 `gateway-config.yaml`：

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

## 3. Python SDK 示例

### 3.1 创建负载均衡器并添加后端

```python
import asyncio
from privacy_local_agent.gateway.balancer import LoadBalancer

balancer = LoadBalancer(strategy="round_robin")
balancer.add_node("http://127.0.0.1:8079", "127.0.0.1:50051", weight=1)
balancer.add_node("http://127.0.0.1:8080", "127.0.0.1:50052", weight=1)

async def demo():
    for _ in range(4):
        node = await balancer.select_node()
        print(f"selected: {node.http_url}")

asyncio.run(demo())
```

### 3.2 创建 HTTP 网关应用

```python
from privacy_local_agent.gateway.balancer import LoadBalancer
from privacy_local_agent.gateway.http_proxy import create_http_gateway_app

balancer = LoadBalancer(strategy="least_connections")
balancer.add_node("http://127.0.0.1:8079", "127.0.0.1:50051")

app = create_http_gateway_app(balancer)
```

### 3.3 动态注册与注销节点

```python
import httpx

# 向运行中的网关注册新后端节点
async def register_node(gateway_url: str, http_url: str, grpc_address: str, weight: int = 1):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{gateway_url}/v1/gateway/register",
            json={"http_url": http_url, "grpc_address": grpc_address, "weight": weight},
        )
        return resp.json()

async def deregister_node(gateway_url: str, http_url: str, grpc_address: str):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{gateway_url}/v1/gateway/deregister",
            json={"http_url": http_url, "grpc_address": grpc_address},
        )
        return resp.json()
```

### 3.4 切换负载均衡策略

```python
from privacy_local_agent.gateway.balancer import LoadBalancer

balancer = LoadBalancer(strategy="round_robin")
# 运行时切换策略
balancer.strategy = "random"
# 或
balancer.strategy = "least_connections"
```

## 4. REST API 示例

### 4.1 通过网关访问后端健康检查

```bash
curl http://127.0.0.1:8000/health
```

网关会将请求转发到选中的后端节点，预期返回：

```json
{"status": "ok"}
```

### 4.2 通过网关调用脱敏接口

```bash
curl -X POST http://127.0.0.1:8000/v1/privacy/mask \
  -H "Content-Type: application/json" \
  -d '{
    "text": "My phone is 13800138000 and email is alice@example.com",
    "fields": ["phone", "email"]
  }'
```

### 4.3 动态注册节点

```bash
curl -X POST http://127.0.0.1:8000/v1/gateway/register \
  -H "Content-Type: application/json" \
  -d '{
    "http_url": "http://127.0.0.1:8090",
    "grpc_address": "127.0.0.1:50053",
    "weight": 2
  }'
```

### 4.4 动态注销节点

```bash
curl -X POST http://127.0.0.1:8000/v1/gateway/deregister \
  -H "Content-Type: application/json" \
  -d '{
    "http_url": "http://127.0.0.1:8090",
    "grpc_address": "127.0.0.1:50053"
  }'
```

## 5. 选择不同负载均衡策略

### 5.1 轮询（Round-Robin）

```bash
export GATEWAY_STRATEGY=round_robin
PYTHONPATH=. python -m privacy_local_agent.gateway.server
```

适合后端节点性能相近、请求耗时均匀的场景。

### 5.2 随机（Random）

```bash
export GATEWAY_STRATEGY=random
PYTHONPATH=. python -m privacy_local_agent.gateway.server
```

实现简单，适合节点性能一致且请求分布无明显规律的流量。

### 5.3 最小连接数（Least Connections）

```bash
export GATEWAY_STRATEGY=least_connections
PYTHONPATH=. python -m privacy_local_agent.gateway.server
```

适合后端节点处理耗时差异较大的场景，可将新请求优先分发到负载较低的节点。

## 6. 最佳实践

1. **生产环境建议前置 Nginx / Cloud LB**：网关目前通过进程重启加载新配置，前置负载均衡可实现滚动更新与更高级的高可用。
2. **使用 `/health` 做 readiness probe**：K8s 中可将网关的 REST `/health` 作为 readiness 探针，避免流量进入未就绪实例。
3. **配置共享 SQLite 预算账本**：多 Agent 实例场景下，设置 `PRIVACY_BUDGET_DB` 保证预算消耗全局一致。
4. **合理设置健康检查间隔**：默认 5 秒适合大多数场景；节点故障需更快感知时可适当降低，但过短会增加后端压力。
5. **优先使用 YAML 配置文件**：相比环境变量，YAML 更易读、易审计，且支持更复杂的后端节点列表。

## 7. 常见错误

| 错误 | 原因 | 解决 |
|---|---|---|
| HTTP 503 / gRPC UNAVAILABLE | 无健康后端节点 | 检查后端服务是否启动，健康检查是否通过 |
| HTTP 502 | 后端连接异常且重试耗尽 | 检查网络连通性、后端端口、防火墙规则 |
| 注册节点后未生效 | 地址重复导致就地更新 | 确认 `http_url` + `grpc_address` 组合是否已存在 |
| 负载均衡不均匀 | 策略选择不当 | 根据业务特征切换 `random` / `least_connections` |
| gRPC 转发超时 | 后端处理耗时超过 30 秒 | 优化后端接口或拆分请求 |
