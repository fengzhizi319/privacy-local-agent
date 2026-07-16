# 数据分类分级设计文档

## 1. 概述

本文档定义 `privacy-local-agent` 数据分类分级模块的算法原理、技术架构与实现细节。该模块通过三层漏斗式分类引擎，自动识别敏感数据并输出统一的敏感度等级与标签。

## 2. 设计目标

- 建立 L1~L5 敏感度等级体系与统一标签体系。
- 实现规则引擎 → 轻量级 NER → 多模态大模型的三层递进分类架构。
- 支持字段级、记录级、表级分类。
- 支持多种输入格式与参数治理模型。
- 暴露一致的 REST/gRPC 接口。

## 3. 算法原理

### 3.1 三层漏斗分类架构

```text
Layer 1 RULE        → 规则匹配，置信度 1.0
   ↓ (level <= L3 或未命中且启用 Small-NER)
Layer 2 SMALL_NER   → 医学实体识别，可选升级 / 人工复核
   ↓ (启用 LLM 或置信度 < 0.6)
Layer 3 LLM         → 零样本语义分类，结构化 JSON 输出
   ↓
manual_override     → 字段级最终等级覆盖
```

#### 3.1.1 规则引擎（Layer 1）

`DefaultRuleEngine.evaluate(field_name, value, params)` 按以下顺序收集标签：

1. **字段名规则**：匹配 `brca1/brca2/tp53`、`rs/snp/cnv/genome/genic`、`gene/mutation/variant`、`bam/vcf/fastq` 等模式。
2. **值规则**：身份证（含校验和）、手机号、医保卡（含校验和）、ICD-10（含区间映射）、BAM/VCF/FASTQ 头、基因序列片段。
3. **白名单/运营统计字段**：`public_report`、`annual_summary`、`turnover_rate`、`device_usage` 等。

所有命中标签的 `confidence = 1.0`，`source_engine = RULE`，`engine_layer = L1_RULE`。

#### 3.1.2 Small-NER（Layer 2）

基于 ONNX Runtime 或 ModelScope 的医疗领域 NER 模型，识别疾病、症状、药物、手术、解剖部位等实体。详细设计参见 `docs/classification_ner/design.md`。

#### 3.1.3 LLM/VLM（Layer 3）

基于本地 Qwen2-VL-2B-Instruct，处理图片、手写病历与非结构化文本。详细设计参见 `docs/classification_llm/design.md`。

### 3.2 参数治理模型

参数优先级（高到低）：

```
manual_override > request params > YAML profile > default
```

`ParameterResolver` 负责加载 profile；`ClassificationAPI` 通过 `_resolve_classification_params` 合并四层参数。

### 3.3 敏感度等级定义

| 等级 | 含义 | 典型示例 |
|---|---|---|
| L1 | 公开/可自由使用 | 运营统计字段、公开报表字段 |
| L2 | 内部使用 | 一般业务字段 |
| L3 | 受限使用 | 普通个人信息 |
| L4 | 高风险 | 身份证号、病史、敏感疾病 |
| L5 | 极高风险 | 基因数据、生物特征 |

## 4. 架构设计

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

## 5. 数据模型

参见 `privacy_local_agent/privacy/classification_models.py`：

- `SensitivityLevel`: L1~L5 枚举。
- `EngineLayer`: L1_RULE / L2_SMALL_NER / L3_LLM。
- `SecurityTag`: 单个分类标签。
- `FieldClassificationResult`: 字段级结果。
- `RecordClassificationResult`: 记录级结果，聚合字段结果。
- `TableClassificationResult`: 表级结果，聚合记录结果。
- `ClassificationResult`: 包装器，可含 record/table + audit。
- `ClassificationParams`: 参数治理模型。

## 6. 多格式适配

| 输入 | 方法 | 说明 |
|---|---|---|
| dict | `classify_record` | 单条记录 |
| list[dict] | `classify_table` | 表 |
| JSON str/dict | `classify_json` | 自动识别 record/table |
| pandas.DataFrame | `classify_dataframe` | 可选依赖 |
| pyarrow.Table | `classify_arrow` | 可选依赖 |
| list[dict] | `classify_sql_result` | SQL 结果集 |

所有适配器最终转换为 `schema + rows` 内部表示。

## 7. 延迟加载与降级

- **Small-NER**：检测本地 ONNX 模型，存在则加载 `ONNXSmallNerEngine`；否则尝试 `ModelScopeSmallNerEngine`；缺失依赖则降级为 `NoOpSmallNerEngine`。
- **LLM Classifier**：加载 `Qwen2VLClassifier`，根据 CUDA/MPS 自动启用硬件加速；加载失败降级为 `NoOpLlmClassifier`。

## 8. 扩展点

- 继承 `RuleEngine` 接口扩展规则匹配逻辑。
- 继承 `SmallNerEngine` 接口对接其他微型 NER 框架。
- 继承 `LlmClassifier` 接口接入其他私有部署大模型服务。

## 9. 测试策略

- 20 个通用分类测试用例。
- 三层引擎协同与降级路径测试。
- 参数治理优先级测试。
- REST/gRPC 接口字段一致性测试。
