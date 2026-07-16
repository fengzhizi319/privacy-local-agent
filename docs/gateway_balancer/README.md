# 代理转发与负载均衡网关文档索引

本目录包含 `privacy-local-agent` 代理转发与负载均衡网关（API Gateway & Load Balancer）的全套 SDLC 文档。

## 文档清单

| 文档 | 说明 | 目标读者 |
|---|---|---|
| [prd.md](./prd.md) | 产品需求文档 | 产品经理、项目经理 |
| [design.md](./design.md) | 技术架构、算法原理与实现细节 | 后端开发、SRE |
| [api_reference.md](./api_reference.md) | YAML / 环境变量配置、REST / gRPC 代理行为、负载均衡策略参考 | 接入开发者、SRE |
| [examples.md](./examples.md) | 命令行与 Python SDK 使用示例 | 接入开发者 |
| [examples/gateway_usage.py](./examples/gateway_usage.py) | 可运行的完整示例脚本 | 接入开发者 |
| [optimizations.md](./optimizations.md) | 高可用与性能优化设计 | 后端开发、架构师 |
| [testing.md](./testing.md) | 测试策略与测试报告 | QA、测试开发 |
| [ops.md](./ops.md) | 运维部署、扩缩容与故障排查 | SRE、运维 |

## 快速开始

1. 阅读 [prd.md](./prd.md) 了解网关产品需求与验收标准。
2. 阅读 [design.md](./design.md) 掌握 REST / gRPC 双协议代理与负载均衡架构。
3. 查看 [examples.md](./examples.md) 或运行 [examples/gateway_usage.py](./examples/gateway_usage.py) 快速上手。
4. 配置网关时参考 [api_reference.md](./api_reference.md)。
5. 生产部署与排障参考 [ops.md](./ops.md)。
6. 优化细节参考 [optimizations.md](./optimizations.md)。

## 运行示例

```bash
cd /home/charles/code/sfwork/privacy-local-agent
source .venv/bin/activate
PYTHONPATH=. python docs/gateway_balancer/examples/gateway_usage.py
```
