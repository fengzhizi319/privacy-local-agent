# 测试控制台后端（Python）文档索引

本目录包含 Privacy 测试控制台 **Python 后端**（`frontend/backend`）的全套 SDLC 文档。

该后端是一个基于 **FastAPI** 的轻量代理层：一方面把构建好的 React SPA 以静态资源形式对外提供，另一方面把前端发来的请求透明转发到运行中的 `privacy-local-agent` REST 服务，并统一响应格式、解析二进制载荷（如 Arrow IPC）。它本身**不实现任何隐私算法**，仅负责转发与格式适配。

## 文档清单

| 文档 | 说明 | 目标读者 |
|---|---|---|
| [prd.md](./prd.md) | 产品需求文档 | 产品经理、项目经理 |
| [design.md](./design.md) | 技术架构、模块设计与实现细节 | 后端开发、SRE |
| [api_reference.md](./api_reference.md) | REST 接口、请求/响应模型、环境变量参考 | 接入开发者、SRE |
| [ops.md](./ops.md) | 部署、配置与故障排查 | SRE、运维 |
| [testing.md](./testing.md) | 测试策略与测试用例说明 | QA、测试开发 |

## 快速开始

1. 阅读 [prd.md](./prd.md) 了解控制台后端的产品定位与验收标准。
2. 阅读 [design.md](./design.md) 掌握代理转发、静态托管与响应解析架构。
3. 接入或联调时参考 [api_reference.md](./api_reference.md)。
4. 部署与排障参考 [ops.md](./ops.md)。
5. 编写或回归测试时参考 [testing.md](./testing.md)。

## 本地运行

```bash
# 1. 启动 privacy-local-agent（REST: 8079 / gRPC: 50051）
python -m privacy_local_agent.server

# 2. 启动控制台后端（默认监听 127.0.0.1:8080）
cd frontend/backend
./run.sh

# 3. 浏览器访问
open http://127.0.0.1:8080
```

也可以直接使用一键脚本（同时拉起 agent 与控制台后端）：

```bash
./frontend/start.sh
```
