# Data Classification PRD / 数据分类产品需求文档

## 1. 背景与目标 / Background & Goals

在隐私计算场景中，数据在脱敏、入湖、建模前需要被自动分级，以便后续按等级采取不同的保护措施（如掩码、差分隐私、访问控制）。本原语提供跨语言统一的数据分类能力，Python 侧由 `privacy-local-agent` 实现。

In privacy-preserving computing, data needs to be automatically classified before masking, ingestion or modeling so that different protection measures can be applied by level. This primitive provides a cross-language unified data classification capability; the Python side is implemented by `privacy-local-agent`.

## 2. 用户价值 / User Value

- **自动识别**：无需人工标注即可识别身份证、手机号、ICD-10、基因序列等敏感数据。
- **统一语义**：跨 Java/Go/Python 使用一致的敏感度等级（L1~L5）与标签体系。
- **可扩展**：支持后续接入 Small-NER、LLM 等更高级的分类引擎。

## 3. 功能范围 / Scope

### In Scope

- 字段级、记录级、表级分类。
- 规则引擎（Layer 1）：身份证、手机号、上海医保卡、ICD-10、基因相关字段/值、公开报表白名单、运营统计字段。
- 命名实体识别引擎（Layer 2）：基于 ONNX Runtime 或 ModelScope 管道的医学实体提取（支持疾病、症状、微生物、药物、手术等）。
- 多模态大模型引擎（Layer 3）：基于本地部署的 Qwen2-VL-2B-Instruct 大模型进行图片/文本零样本分类。
- 参数治理：default → YAML profile → request params → manual_override。
- 多格式输入：dict、JSON、list[dict]、pandas DataFrame、pyarrow Table、SQL 结果集。
- REST/gRPC 接口暴露。
- 跨平台支持：保证代码在 Linux/Windows (CUDA) 以及 macOS ARM (Apple Silicon MPS) 上均能执行硬件加速推理。

### Out of Scope

- SecretFlow 组件化输出（设计预留，本次仅实现本地模式）。

## 4. 术语 / Terminology

| 术语 | 说明 |
|------|------|
| SensitivityLevel | L1~L5，数字越大越敏感 |
| SecurityTag | 分类标签：level + category + confidence + sourceEngine + ruleId |
| EngineLayer | L1_RULE / L2_SMALL_NER / L3_LLM |
| AuditInfo | 审计元数据：版本、时间戳、参数来源等 |

## 5. 关键验收标准 / Acceptance Criteria

- 通过规范中的 20 个通用测试用例。
- `PYTHONPATH=. pytest tests -q` 全部通过。
- REST/gRPC 接口与本地 SDK 输出字段一致。
- 不引入 `pyproject.toml` 之外的新运行时依赖。
