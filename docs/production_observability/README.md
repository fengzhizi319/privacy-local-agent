# 可观测性文档索引

本目录包含 `privacy-local-agent` 可观测性模块的全套 SDLC 文档。

## 文档清单

| 文档 | 说明 | 目标读者 |
|---|---|---|
| [prd.md](./prd.md) | 产品需求文档 | 产品经理、项目经理 |
| [design.md](./design.md) | 架构原理、组件设计与实现细节 | 后端开发、SRE |
| [api_reference.md](./api_reference.md) | 日志字段、Prometheus 指标、OpenTelemetry 配置参考 | 接入开发者、SRE |
| [examples.md](./examples.md) | JSON 日志、指标抓取、Tracing 初始化示例 | 接入开发者 |
| [examples/observability_usage.py](./examples/observability_usage.py) | 可运行的完整示例脚本 | 接入开发者 |
| [testing.md](./testing.md) | 可观测性测试策略与测试代码示例 | QA、测试开发 |
| [ops.md](./ops.md) | 运维手册、参数建议与故障排查 | SRE、运维 |

## 快速开始

1. 阅读 [prd.md](./prd.md) 了解可观测性产品需求。
2. 阅读 [design.md](./design.md) 掌握日志、Metrics、Tracing 的架构设计。
3. 查看 [examples.md](./examples.md) 或运行 [examples/observability_usage.py](./examples/observability_usage.py) 快速上手。
4. 开发时参考 [api_reference.md](./api_reference.md)。
5. 部署与排障参考 [ops.md](./ops.md)。
6. 编写测试参考 [testing.md](./testing.md)。

## 运行示例

```bash
cd /home/charles/code/sfwork/privacy-local-agent
source .venv/bin/activate
PYTHONPATH=. python docs/production_observability/examples/observability_usage.py
```

## 一键启动 REST 服务并查看指标

```bash
# 文本日志（默认）
python -m privacy_local_agent.main

# JSON 日志
PRIVACY_LOG_FORMAT=json python -m privacy_local_agent.main

# 另开终端抓取指标
curl http://127.0.0.1:8079/metrics
```
