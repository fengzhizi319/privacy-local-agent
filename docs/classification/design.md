# Data Classification Design / 数据分类设计文档

## 1. 架构概览 / Architecture Overview

```text
REST /v1/privacy/classify/*          gRPC ClassifyField/ClassifyRecord/ClassifyTable
            │                                    │
            └────────────┬───────────────────────┘
                         ▼
      privacy_local_agent.classification_service.ClassificationService
                         │
                         ▼
           privacy_local_agent.privacy.classification.ClassificationAPI
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
   DefaultRuleEngine  Small-NER        LLM Classifier
   (Layer 1 RULE)     (Layer 2)        (Layer 3)
```

## 2. 数据模型 / Data Model

参见 `privacy_local_agent/privacy/classification_models.py`：

- `SensitivityLevel`: L1~L5 枚举。
- `EngineLayer`: L1_RULE / L2_SMALL_NER / L3_LLM。
- `SecurityTag`: 单个分类标签。
- `FieldClassificationResult`: 字段级结果。
- `RecordClassificationResult`: 记录级结果，聚合字段结果。
- `TableClassificationResult`: 表级结果，聚合记录结果。
- `ClassificationResult`: 包装器，可含 record/table + audit。
- `ClassificationParams`: 参数治理模型。

## 3. 规则引擎 / Rule Engine

`DefaultRuleEngine.evaluate(field_name, value, params)` 按以下顺序收集标签：

1. 字段名规则：brca1/brca2/tp53、rs/snp/cnv/genome/genic、gene/mutation/variant、bam/vcf/fastq。
2. 值规则：身份证（含校验和）、手机号、上海医保卡（含校验和）、ICD-10（含区间映射）、BAM/VCF/FASTQ 头、基因序列片段。
3. 字段名白名单/运营统计：public_report / annual_summary / 科普、turnover_rate / device_usage / inventory。

所有命中标签的 `confidence = 1.0`，`source_engine = RULE`，`engineLayer = L1_RULE`。

## 4. 多层执行流程 / Multi-Layer Execution

```text
Layer 1 RULE        → tags, final_level, confidence=1.0 if hit
   ↓ (level <= L3 or no hit and enable_small_ner)
Layer 2 SMALL_NER   → optional upgrade / needsHumanReview
   ↓ (enable_llm or confidence < 0.6)
Layer 3 LLM         → zero-shot semantics classification & structured JSON extraction
   ↓
manual_override     → final level override
```

在系统初始化时，第二层 (Small-NER) 和第三层 (LLM) 会执行延迟加载（Lazy Loading）：
- **Small-NER**：检测本地是否有 ONNX 模型，如果有加载 `ONNXSmallNerEngine`，否则尝试从魔搭社区加载 `ModelScopeSmallNerEngine`，若缺失依赖则降级回 `NoOpSmallNerEngine`。
- **LLM Classifier**：加载 `Qwen2VLClassifier`，支持根据设备硬件（如 Linux/Windows 上的 CUDA，或 macOS ARM 平台上的 MPS 显卡加速）自动启用硬件加速，若加载失败降级为 `NoOpLlmClassifier` 兜底。

## 5. 参数治理 / Parameter Governance

优先级（高到低）：

1. 内置默认值（`ClassificationParams`）。
2. YAML profile `primitives.classification`。
3. 请求级 `params`。
4. `manual_override` 字段级覆盖。

`ParameterResolver` 负责加载 profile；`ClassificationAPI` 通过 `_resolve_classification_params` 合并四层参数。

## 6. 多格式适配 / Format Adapters

| 输入 | 方法 | 说明 |
|------|------|------|
| dict | `classify_record` | 单条记录 |
| list[dict] | `classify_table` | 表 |
| JSON str/dict | `classify_json` | 自动识别 record/table |
| pandas.DataFrame | `classify_dataframe` | 可选依赖 |
| pyarrow.Table | `classify_arrow` | 可选依赖 |
| list[dict] | `classify_sql_result` | SQL 结果集 |

所有适配器最终转换为 `schema + rows` 内部表示。

## 7. 已实现与可扩展点 / Extension Points

- **已实现的引擎**：
  - `DefaultRuleEngine`：内置的 Layer-1 规则引擎。
  - `ONNXSmallNerEngine` & `ModelScopeSmallNerEngine`：内置的 Layer-2 医学命名实体识别引擎。
  - `Qwen2VLClassifier`：内置的 Layer-3 本地多模态大模型分类器。
- **扩展开发**：
  - 继承并实现 `RuleEngine` 接口以替换/扩展规则匹配逻辑。
  - 继承并实现 `SmallNerEngine` 来对接其他的微型 NER 框架。
  - 继承并实现 `LlmClassifier` 来接入企业其他的私有部署大语言模型服务。

