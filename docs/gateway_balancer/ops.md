# 代理转发与负载均衡网关运维与部署手册 (Operations Guide)

## 1. 部署配置 (Deployment Configuration)

网关支持通过 **YAML 配置文件** 或 **环境变量** 进行配置，以便于在裸机、Docker、K8s 等多种生产环境下快速部署。

### 1.1 YAML 配置文件示例 (`gateway-config.yaml`)
创建如下配置文件放置在运行目录下：
```yaml
gateway:
  rest_host: "0.0.0.0"
  rest_port: 8000
  grpc_host: "0.0.0.0"
  grpc_port: 50000
  strategy: "round_robin"  # 可选: round_robin, random, least_connections
  health_check_interval: 5.0  # 健康检查间隔时间（秒）

backends:
  - http_url: "http://127.0.0.1:8079"
    grpc_address: "127.0.0.1:50051"
    weight: 1
  - http_url: "http://127.0.0.1:8080"
    grpc_address: "127.0.0.1:50052"
    weight: 1
```

### 1.2 环境变量支持 (Docker / K8s 环境推荐)
若未提供配置文件，网关将尝试从环境变量中读取参数：
- `GATEWAY_REST_HOST`：网关 HTTP 绑定地址（默认 `0.0.0.0`）。
- `GATEWAY_REST_PORT`：网关 HTTP 监听端口（默认 `8000`）。
- `GATEWAY_GRPC_HOST`：网关 gRPC 绑定地址（默认 `0.0.0.0`）。
- `GATEWAY_GRPC_PORT`：网关 gRPC 监听端口（默认 `50000`）。
- `GATEWAY_STRATEGY`：负载均衡策略（默认 `round_robin`）。
- `GATEWAY_HEALTH_INTERVAL`：健康检查间隔（默认 `5.0`）。
- `GATEWAY_BACKENDS`：后端节点列表，多个节点用逗号分隔，每个节点的 HTTP 与 gRPC 地址用 `|` 隔开。
  - 示例：`http://127.0.0.1:8079|127.0.0.1:50051,http://127.0.0.1:8080|127.0.0.1:50052`

## 2. 启动方式 (How to Run)

### 2.1 本地脚本启动
启动网关的入口为 `privacy_local_agent.gateway.server` 模块：
```bash
# 1. 使用默认环境变量启动
PYTHONPATH=. .venv/bin/python -m privacy_local_agent.gateway.server

# 2. 指定配置文件启动
PRIVACY_GATEWAY_CONFIG=gateway-config.yaml PYTHONPATH=. .venv/bin/python -m privacy_local_agent.gateway.server
```

### 2.2 Docker 部署 (多容器扩容场景)
在一台或多台机器上分别部署多个 `privacy-local-agent` 容器作为 Worker，并启动一个网关容器分发流量。

**Docker Compose 快速编排示例 (`docker-compose.yaml`)**：
```yaml
version: '3.8'

services:
  # Agent 节点 1
  agent-worker-1:
    image: privacy-local-agent:latest
    environment:
      - PRIVACY_REST_PORT=8079
      - PRIVACY_GRPC_PORT=50051
    ports:
      - "8079:8079"
      - "50051:50051"

  # Agent 节点 2
  agent-worker-2:
    image: privacy-local-agent:latest
    environment:
      - PRIVACY_REST_PORT=8079
      - PRIVACY_GRPC_PORT=50051
    ports:
      - "8080:8079"
      - "50052:50051"

  # 网关负载均衡器
  gateway:
    image: privacy-local-agent:latest
    command: ["python", "-m", "privacy_local_agent.gateway.server"]
    environment:
      - GATEWAY_REST_PORT=8000
      - GATEWAY_GRPC_PORT=50000
      - GATEWAY_STRATEGY=round_robin
      - GATEWAY_BACKENDS=http://agent-worker-1:8079|agent-worker-1:50051,http://agent-worker-2:8079|agent-worker-2:50051
    ports:
      - "8000:8000"
      - "50000:50000"
    depends_on:
      - agent-worker-1
      - agent-worker-2
```

## 3. 水平扩容与弹性伸缩 (Scaling)

当流量持续上升，网关的扩容流程非常简单：
1. **启动新节点**：在物理机或容器云中拉起一个新的 Agent 实例，记录其 REST 及 gRPC 访问地址。
2. **修改网关配置**：
   - 若使用 K8s 部署，可利用 K8s Service 内置的 ClusterIP/DNS 负载均衡作为后端，网关仅指向单个 Service 地址，扩容直接对 Deployment 增加副本数，网关配置保持不变。
   - 若使用容器直连，修改 `gateway-config.yaml` 或 `GATEWAY_BACKENDS` 环境变量，加入新节点信息。
3. **网关重载**：网关目前支持进程重启秒级加载。由于网关状态极轻，重启对客户端仅造成瞬时网络波动，生产中建议在网关前置一层 Nginx 或 Cloud Load Balancer 以实现滚动更新与更高级的高可用。

## 4. 故障监控与日志排查 (Monitoring & Logs)

网关标准输出中会实时记录后端节点的状态切换：
- **正常状态日志**：
  ```
  Gateway HTTP server started on port 8000
  Gateway gRPC server started on port 50000
  ```
- **节点下线日志**：
  ```
  Node 127.0.0.1:50052 status changed to unhealthy (HTTP: False, gRPC: False)
  ```
- **节点恢复日志**：
  ```
  Node 127.0.0.1:50052 status changed to healthy (HTTP: True, gRPC: True)
  ```
- **请求转发失败日志**（如超时或连接重置）：
  ```
  [HTTP Proxy] Connection error forwarding to http://127.0.0.1:8080/v1/privacy/mask: ConnectTimeout
  ```
通过对接 Prometheus 等监控工具监听网关输出，可以第一时间发现后台 Agent 异常，保障服务稳定运行。
