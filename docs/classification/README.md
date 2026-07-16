# 数据分类分级文档索引

本目录包含 `privacy-local-agent` 数据分类分级模块的全套 SDLC 文档。

## 文档清单

| 文档 | 说明 | 目标读者 |
|---|---|---|
| [prd.md](./prd.md) | 产品需求文档 | 产品经理、项目经理 |
| [design.md](./design.md) | 算法原理、三层漏斗架构与实现细节 | 算法工程师、后端开发 |
| [api_reference.md](./api_reference.md) | Python SDK / REST / gRPC API 参考 | 接入开发者 |
| [examples.md](./examples.md) | Python SDK 与 REST 使用示例 | 接入开发者 |
| [examples/classification_usage.py](./examples/classification_usage.py) | 可运行的完整示例脚本 | 接入开发者 |
| [testing.md](./testing.md) | 测试策略与 20 个通用测试用例 | QA、测试开发 |
| [performance.md](./performance.md) | 三层引擎性能基准测试报告 | 架构师、SRE |
| [ops.md](./ops.md) | 运维手册、参数建议与故障排查 | SRE、运维 |

## 快速开始

1. 阅读 [prd.md](./prd.md) 了解产品需求与验收标准。
2. 阅读 [design.md](./design.md) 掌握三层漏斗分类架构与参数治理模型。
3. 查看 [examples.md](./examples.md) 或运行 [examples/classification_usage.py](./examples/classification_usage.py) 快速上手。
4. 开发时参考 [api_reference.md](./api_reference.md)。
5. 部署与排障参考 [ops.md](./ops.md)。
6. 编写测试参考 [testing.md](./testing.md)。

## 运行示例

```bash
cd /home/charles/code/sfwork/privacy-local-agent
source .venv/bin/activate
PYTHONPATH=. python docs/classification/examples/classification_usage.py
```

> 示例脚本内置规则引擎用例，不依赖 Small-NER / LLM 模型权重；若模型未下载，相关层会自动降级为 No-Op，不会报错。
