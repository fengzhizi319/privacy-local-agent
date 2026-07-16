# 本地多模态大模型分类分级文档索引

本目录包含 `privacy-local-agent` 第三层分类引擎——本地多模态大模型（VLM）分类分级的全套 SDLC 文档。

## 文档清单

| 文档 | 说明 | 目标读者 |
|---|---|---|
| [prd.md](./prd.md) | 产品需求文档 | 产品经理、项目经理 |
| [design.md](./design.md) | 算法原理、模型选型与架构设计 | 算法工程师、后端开发 |
| [api_reference.md](./api_reference.md) | Python SDK / REST / gRPC API 参考 | 接入开发者 |
| [examples.md](./examples.md) | Python 与 REST 使用示例 | 接入开发者 |
| [examples/llm_usage.py](./examples/llm_usage.py) | 可运行的完整示例脚本 | 接入开发者 |
| [testing.md](./testing.md) | 测试策略与测试代码示例 | QA、测试开发 |
| [ops.md](./ops.md) | 模型下载、环境准备、运行配置与故障排查 | SRE、运维 |

## 快速开始

1. 阅读 [prd.md](./prd.md) 了解产品需求与验收标准。
2. 阅读 [design.md](./design.md) 掌握模型选型、输入自适应与降级策略。
3. 查看 [examples.md](./examples.md) 或运行 [examples/llm_usage.py](./examples/llm_usage.py) 快速上手。
4. 开发时参考 [api_reference.md](./api_reference.md)。
5. 部署模型与排障参考 [ops.md](./ops.md)。
6. 编写测试参考 [testing.md](./testing.md)。

## 运行示例

```bash
cd /home/charles/code/sfwork/privacy-local-agent
source .venv/bin/activate
PYTHONPATH=. python docs/classification_llm/examples/llm_usage.py
```

> 若 `.models/Qwen2-VL-2B-Instruct` 模型权重未下载，示例会自动降级为规则引擎并打印提示，不会中断运行。
