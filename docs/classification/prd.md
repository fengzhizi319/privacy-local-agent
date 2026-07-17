# 数据分类分级产品设计 PRD

## 1. 概述

本文档定义 `privacy-local-agent` 数据分类分级模块的产品需求与验收标准。该模块在数据脱敏、入湖、建模前自动判定字段/记录/表的敏感度等级，为后续隐私保护措施提供决策依据。

本版本重点增强 **SecretFlow 输入支持**、**复合/上下文感知识别**、**Layer 3 异步解耦**、**人工复核闭环**、**Zero-Knowledge 扫描**、**合规模板**、**规则版本化与影子模式** 等企业级能力。

## 2. 设计目标

- 建立统一的敏感度等级体系（L1~L5）与标签体系，支持跨语言一致调用。
- 提供规则引擎 → 轻量级 NER → 多模态大模型的三层递进分类能力。
- 支持字段级、记录级、表级分类。
- 支持 dict、JSON、list[dict]、pandas DataFrame、pyarrow Table、SQL 结果集、SecretFlow DataFrame / HDataFrame / VDataFrame / FedNdarray 等多种输入格式。
- 支持跨字段复合规则识别，解决“单字段不敏感、组合后敏感”的场景。
- 提供 Layer 3 LLM 同步与异步两套接口，异步接口避免阻塞主链路。
- 提供基于 `needsHumanReview` 的轻量复核 API，支持样本确认与导出。
- 落地 Zero-Knowledge 扫描原则：原始值不进入日志、指标、持久化存储。
- 内置 JR/T 0197、GB/T 35273、GDPR 等合规规则模板。
- 支持规则集版本化与影子模式，便于灰度评估规则变更。
- 暴露一致的 REST/gRPC 接口。

## 3. 功能需求

### 3.1 基础分类能力

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

### 3.2 SecretFlow 输入支持

| ID | 需求 |
|---|---|
| CLS-SF-1 | 支持对 `secretflow.data.DataFrame` 进行分类。 |
| CLS-SF-2 | 支持对 `secretflow.data.horizontal.HDataFrame` 进行分类，可通过 `party` 参数指定参与方。 |
| CLS-SF-3 | 支持对 `secretflow.data.vertical.VDataFrame` 进行分类，自动定位包含目标列的 partition。 |
| CLS-SF-4 | 支持对 `secretflow.data.FedNdarray` 进行分类。 |
| CLS-SF-5 | SecretFlow 为可选依赖，未安装时抛出明确 `ImportError` 并给出安装提示。 |

### 3.3 复合/上下文敏感规则识别

| ID | 需求 |
|---|---|
| CLS-COMP-1 | 支持复合规则 DSL：定义字段组合条件与升级后的敏感度等级/类别。 |
| CLS-COMP-2 | 默认内置常见复合规则：姓名 + 身份证号 + 手机号 → L5；诊断 + 基因字段 → L5。 |
| CLS-COMP-3 | 支持请求级自定义复合规则，通过 `composite_rules` 参数传入。 |
| CLS-COMP-4 | 复合规则在单条记录分类后执行，作为后处理步骤升级最终等级。 |

### 3.4 Layer 3 异步推理解耦

| ID | 需求 |
|---|---|
| CLS-ASYNC-1 | 保留现有同步接口 `classify_field/record/table` 行为不变。 |
| CLS-ASYNC-2 | 新增异步表分类接口：提交任务返回 `job_id`，通过 `job_id` 轮询结果。 |
| CLS-ASYNC-3 | 异步任务在进程内 `ThreadPoolExecutor` 中执行，不引入外部消息队列。 |
| CLS-ASYNC-4 | 异步任务支持状态：`PENDING`、`RUNNING`、`DONE`、`FAILED`。 |
| CLS-ASYNC-5 | 异步任务支持 TTL 与最大并发数限制，防止内存无限增长。 |
| CLS-ASYNC-6 | 暴露 `privacy_classification_jobs_total` 与 `privacy_classification_jobs_duration_seconds` 指标。 |

### 3.5 人工复核 API

| ID | 需求 |
|---|---|
| CLS-REVIEW-1 | 自动收集 `needs_human_review=True` 的字段/记录到复核队列。 |
| CLS-REVIEW-2 | 提供复核确认接口：确认或修正字段敏感度等级，记录复核人与说明。 |
| CLS-REVIEW-3 | 提供复核样本导出接口，支持 JSONL / CSV 格式。 |
| CLS-REVIEW-4 | 复核存储默认内存模式，可通过 `PRIVACY_REVIEW_DB` 环境变量启用 SQLite。 |
| CLS-REVIEW-5 | 导出样本可用于 LLM/NER 微调，包含 `input`、`predicted_level`、`corrected_level`、`fine_tuning_text` 等字段。 |

### 3.6 Zero-Knowledge 扫描

| ID | 需求 |
|---|---|
| CLS-ZK-1 | 分类日志、访问日志、指标中不打印原始字段值。 |
| CLS-ZK-2 | 提供 `return_field_values` 参数，默认 `True` 保持兼容；设为 `False` 时结果中不返回 `field_value`。 |
| CLS-ZK-3 | LLM 推理失败日志中不打印原始输入或模型完整输出。 |
| CLS-ZK-4 | 复核导出时支持对 `input` 字段进行掩码或哈希处理。 |

### 3.7 合规模板

| ID | 需求 |
|---|---|
| CLS-TPL-1 | 内置 `jrt0197`（金融 JR/T 0197）规则模板。 |
| CLS-TPL-2 | 内置 `gbt35273`（通用 GB/T 35273）规则模板。 |
| CLS-TPL-3 | 内置 `gdpr`（跨境 GDPR）规则模板。 |
| CLS-TPL-4 | 通过 `template` 参数或 YAML profile 切换模板。 |
| CLS-TPL-5 | 模板仅提供默认值，仍可通过请求参数覆盖。 |

### 3.8 规则版本化与影子模式

| ID | 需求 |
|---|---|
| CLS-VERS-1 | `ClassificationParams` 支持 `rule_set_version`，写入审计信息。 |
| CLS-VERS-2 | `AuditInfo` 记录当前规则集版本。 |
| CLS-SHADOW-1 | 支持 `shadow_mode=True`，同时用当前版本与 `shadow_version` 运行两次分类。 |
| CLS-SHADOW-2 | 影子模式返回两套结果的差异 `shadow_diff`，不影响主结果等级。 |
| CLS-SHADOW-3 | 暴露 `privacy_classification_shadow_diff_total` 指标。 |

### 3.9 样本导出与模型微调

| ID | 需求 |
|---|---|
| CLS-EXPORT-1 | 复核样本导出支持 JSONL 与 CSV 格式。 |
| CLS-EXPORT-2 | 每行样本包含 `input`、`predicted_level`、`predicted_tags`、`corrected_level`、`reviewer_comment`。 |
| CLS-EXPORT-3 | 可选生成 LLM 微调格式：`fine_tuning_text` 字段包含 prompt/response。 |

## 4. 术语

| 术语 | 说明 |
|---|---|
| SensitivityLevel | L1~L5，数字越大越敏感 |
| SecurityTag | 分类标签：level + category + confidence + sourceEngine + ruleId + needsHumanReview |
| EngineLayer | L1_RULE / L2_SMALL_NER / L3_LLM |
| AuditInfo | 审计元数据：版本、时间戳、参数来源、规则集版本等 |
| CompositeRule | 复合规则：字段组合条件 + 升级目标 |
| ShadowDiff | 影子模式差异：字段名、当前等级、目标等级、差异标签 |
| ReviewEntry | 复核条目：待复核或已确认的字段/记录信息 |
| ClassificationJob | 异步分类任务：job_id、状态、创建时间、结果、错误信息 |

## 5. 接口定义

REST 与 gRPC 提供统一的分类入口，接收数据与参数，返回包含 `SecurityTag` 列表的分类结果。新增接口详见 `api_reference.md` 与 `proto/privacy.proto`。

## 6. 验收标准

- [ ] 通过规范中的 20 个通用测试用例。
- [ ] SecretFlow 输入分类测试通过（使用 mock）。
- [ ] 复合规则命中时最终等级升级。
- [ ] 异步接口提交/查询可用，同步接口行为不变。
- [ ] `needsHumanReview` 样本可确认、可导出 JSONL。
- [ ] 日志中不泄露原始字段值。
- [ ] JR/T、GB/T、GDPR 模板可切换。
- [ ] 影子模式返回版本差异。
- [ ] `PYTHONPATH=. pytest tests -q` 全部通过。
- [ ] REST/gRPC 接口与本地 SDK 输出字段一致。
- [ ] 不引入 `pyproject.toml` 之外的新运行时依赖。
