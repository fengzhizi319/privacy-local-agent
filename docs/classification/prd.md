# 数据分类分级产品设计 PRD

## 1. 概述

本文档定义 `privacy-local-agent` 数据分类分级模块的产品需求与验收标准。该模块在数据脱敏、入湖、建模前自动判定字段/记录/表的敏感度等级，为后续隐私保护措施提供决策依据。

## 2. 设计目标

- 建立统一的敏感度等级体系（L1~L5）与标签体系，支持跨语言一致调用。
- 提供规则引擎 → 轻量级 NER → 多模态大模型的三层递进分类能力。
- 支持字段级、记录级、表级分类。
- 支持 dict、JSON、list[dict]、pandas DataFrame、pyarrow Table、SQL 结果集等多种输入格式。
- 暴露一致的 REST/gRPC 接口。

## 3. 功能需求

| ID | 需求 |
|---|---|
| CLS-SCOPE-1 | 支持字段级、记录级、表级分类。 |
| CLS-LAYER-1 | 第一层规则引擎覆盖身份证、手机号、医保卡、ICD-10、基因相关字段/值、公开报表白名单、运营统计字段。 |
| CLS-LAYER-2 | 第二层 Small-NER 支持疾病、症状、微生物、药物、手术等医学实体提取。 |
| CLS-LAYER-3 | 第三层 LLM/VLM 支持图片/文本零样本分类。 |
| CLS-GOV-1 | 参数治理：default → YAML profile → request params → manual_override。 |
| CLS-INPUT-1 | 支持 dict、JSON、list[dict]、pandas DataFrame、pyarrow Table、SQL 结果集。 |
| CLS-API-1 | 暴露 REST/gRPC 接口，输出字段与本地 SDK 一致。 |
| CLS-CROSS-1 | 保证代码在 Linux/Windows CUDA 与 macOS ARM MPS 上均可执行硬件加速推理。 |

## 4. 术语

| 术语 | 说明 |
|---|---|
| SensitivityLevel | L1~L5，数字越大越敏感 |
| SecurityTag | 分类标签：level + category + confidence + sourceEngine + ruleId |
| EngineLayer | L1_RULE / L2_SMALL_NER / L3_LLM |
| AuditInfo | 审计元数据：版本、时间戳、参数来源等 |

## 5. 接口定义

REST 与 gRPC 提供统一的分类入口，接收数据与参数，返回包含 `SecurityTag` 列表的分类结果。具体字段定义参见 `classification_models.py` 与 `proto/privacy.proto`。

## 6. 验收标准

- [ ] 通过规范中的 20 个通用测试用例。
- [ ] `PYTHONPATH=. pytest tests -q` 全部通过。
- [ ] REST/gRPC 接口与本地 SDK 输出字段一致。
- [ ] 不引入 `pyproject.toml` 之外的新运行时依赖。
