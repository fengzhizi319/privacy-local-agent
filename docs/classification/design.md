# 数据分类分级设计文档

## 1. 概述

本文档定义 `privacy-local-agent` 数据分类分级模块的算法原理、技术架构与实现细节。该模块通过三层漏斗式分类引擎，自动识别敏感数据并输出统一的敏感度等级与标签。

本版本在原有架构基础上新增：

- SecretFlow 联邦数据结构输入适配。
- 复合/上下文敏感规则识别。
- Layer 3 LLM 同步与异步两套推理接口。
- 基于 `needsHumanReview` 的轻量复核闭环。
- Zero-Knowledge 扫描原则落地。
- 内置 JR/T 0197、GB/T 35273、GDPR 合规规则模板。
- 规则集版本化与影子模式。

## 2. 设计目标

- 建立 L1~L5 敏感度等级体系与统一标签体系。
- 实现规则引擎 → 轻量级 NER → 多模态大模型的三层递进分类架构。
- 支持字段级、记录级、表级分类。
- 支持多种输入格式与参数治理模型。
- 支持跨字段组合规则识别，解决上下文敏感场景。
- 提供同步与异步两套 Layer 3 推理接口，异步接口避免阻塞主链路。
- 提供轻量人工复核 API 与样本导出能力。
- 保障扫描过程不泄露原始数据。
- 支持规则模板切换、版本化与影子模式灰度评估。
- 暴露一致的 REST/gRPC 接口与 Prometheus 指标。

## 3. 算法原理

### 3.1 三层漏斗分类架构

```text
Layer 1 RULE        → 规则匹配，置信度 1.0
   ↓ (level <= L3 或未命中且启用 Small-NER)
Layer 2 SMALL_NER   → 医学实体识别，可选升级 / 人工复核
   ↓ (启用 LLM 或置信度 < 0.6)
Layer 3 LLM         → 零样本语义分类，结构化 JSON 输出
   ↓
Composite Rules     → 跨字段组合后处理，升级敏感度
   ↓
manual_override     → 字段级最终等级覆盖
   ↓
Review Store        → 收集 needsHumanReview 样本
```

#### 3.1.1 规则引擎（Layer 1）

`DefaultRuleEngine.evaluate(field_name, value, params)` 按以下顺序收集标签：

1. **字段名规则**：匹配 `brca1/brca2/tp53`、`rs/snp/cnv/genome/genic`、`gene/mutation/variant`、`bam/vcf/fastq` 等模式。
2. **值规则**：身份证（含校验和）、手机号、医保卡（含校验和）、ICD-10（含区间映射）、BAM/VCF/FASTQ 头、基因序列片段。
3. **白名单/运营统计字段**：`public_report`、`annual_summary`、`turnover_rate`、`device_usage` 等。

所有命中标签的 `confidence = 1.0`，`source_engine = RULE`，`engine_layer = L1_RULE`。

合规模板（JR/T 0197、GB/T 35273、GDPR）通过扩展默认规则集合实现。

为提升大数据集表分类吞吐量，系统还提供可选的 `VectorizedRuleEngine`（`privacy_local_agent/privacy/classification_vectorized.py`）。该引擎基于 pandas Series 对 Layer-1 规则做列式向量化匹配，语义与 `DefaultRuleEngine` 保持一致，可通过 `ClassificationAPI(use_vectorized=True)` 启用；未安装 pandas 时自动回退到 `DefaultRuleEngine`。

#### 3.1.2 Small-NER（Layer 2）

基于 ONNX Runtime 或 ModelScope 的医疗领域 NER 模型，识别疾病、症状、药物、手术、解剖部位等实体。详细设计参见 `docs/classification_ner/design.md`。

#### 3.1.3 LLM/VLM（Layer 3）

基于本地 Qwen2-VL-2B-Instruct，处理图片、手写病历与非结构化文本。详细设计参见 `docs/classification_llm/design.md`。

### 3.2 复合/上下文敏感规则

#### 3.2.1 问题定义

单字段敏感度不足以描述真实风险。例如：

- `name=L3`、`id_card=L3`、`mobile=L3` 单独出现均为 L3。
- 三者同时出现在同一条记录中时，应升级为 L5 `COMPOSITE_PII_COMBO`。

#### 3.2.2 复合规则 DSL

每条复合规则包含：

- `name`：规则名称。
- `field_patterns`：字段名正则列表。
- `min_matches`：最少命中字段数。
- `target_level`：升级后的敏感度等级。
- `category`：升级后的类别标签。
- `rule_id`：规则 ID。

示例：

```json
{
  "name": "高敏感个人信息组合",
  "field_patterns": ["name", "id_card", "mobile", "phone"],
  "min_matches": 3,
  "target_level": "L5",
  "category": "COMPOSITE_PII_COMBO",
  "rule_id": "COMP_001"
}
```

#### 3.2.3 执行时机

复合规则作为 `classify_record` 的后处理步骤执行：

1. 先对记录内每个字段执行三层漏斗分类，得到 `field_results`。
2. `CompositeRuleEngine.evaluate(record, field_results)` 检查字段名组合。
3. 若命中，则向记录添加 `SecurityTag`，并重新计算 `final_level`。
4. 复合规则标签的 `source_engine = COMPOSITE`。

### 3.3 参数治理模型

参数优先级（高到低）：

```
manual_override > request params > YAML profile > template defaults > default
```

`ParameterResolver` 负责加载 profile；`ClassificationAPI` 通过 `_resolve_classification_params` 合并多层参数。

### 3.4 敏感度等级定义

| 等级 | 含义 | 典型示例 |
|---|---|---|
| L1 | 公开/可自由使用 | 运营统计字段、公开报表字段 |
| L2 | 内部使用 | 一般业务字段 |
| L3 | 受限使用 | 普通个人信息 |
| L4 | 高风险 | 身份证号、病史、敏感疾病 |
| L5 | 极高风险 | 基因数据、生物特征、多字段组合 |

### 3.5 Zero-Knowledge 扫描

> **Zero-Knowledge Scan 的核心原则：sidecar 不将原始数据持久化到日志、指标、复核存储或任何外部系统。**

具体措施：

- 访问日志仅记录 method/path/status/duration/byte size，不记录请求/响应体。
- 错误日志中对用户输入进行 `redact` 处理，最多保留前 8 字符。
- `ClassificationParams.return_field_values` 控制是否在结果中返回 `field_value`。
- 复核导出时支持对 `input` 字段哈希或掩码。

## 4. 架构设计

### 4.1 整体架构

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
         ┌───────────────┼───────────────┬───────────────┬───────────────┐
         ▼               ▼               ▼               ▼               ▼
   DefaultRuleEngine  Small-NER        LLM Classifier  CompositeRules  ReviewStore
   (Layer 1 RULE)     (Layer 2)        (Layer 3)       (Post-process)  (Feedback)
         │               │               │               │               │
         └───────────────┴───────────────┴───────────────┘               │
                         ▼                                               │
              SecurityTag / Result Models                                 │
                         ▼                                               │
              Sync Response / Async Job                                   │
                         ▼                                               │
                   Review Export → Fine-tuning
```

### 4.2 异步推理架构

```text
Client → POST /v1/privacy/classify/table/async
              │
              ▼
   AsyncLlmManager.submit(classify_table, ...)
              │
              ▼
   ThreadPoolExecutor.submit(...)
              │
              ▼
   Memory job store[job_id] = ClassificationJob
              │
              ▼
   Client → GET /v1/privacy/classify/jobs/{job_id}
```

- 异步任务不阻塞 REST/gRPC 主线程。
- 使用内存 `dict` 存储 job，带 TTL 清理。
- 任务失败时记录错误信息，不泄露原始数据。

### 4.3 复核闭环架构

```text
classify_table/record
       │
       ▼
needs_human_review=True?
       │
       ▼
ReviewStore.add(record_result)
       │
       ▼
POST /v1/privacy/classify/review/confirm
       │
       ▼
POST /v1/privacy/classify/review/export → JSONL/CSV → Fine-tuning
```

## 5. 数据模型

参见 `privacy_local_agent/privacy/classification_models.py`：

- `SensitivityLevel`: L1~L5 枚举。
- `EngineLayer`: L1_RULE / L2_SMALL_NER / L3_LLM。
- `SecurityTag`: 单个分类标签。
- `FieldClassificationResult`: 字段级结果。
- `RecordClassificationResult`: 记录级结果，聚合字段结果，可含复合规则标签。
- `TableClassificationResult`: 表级结果，聚合记录结果，可含 `review_entries` 与 `shadow_diff`。
- `ClassificationResult`: 包装器，可含 record/table + audit。
- `ClassificationParams`: 参数治理模型，新增 `template`、`rule_set_version`、`shadow_mode`、`shadow_version`、`return_field_values`、`async_llm`。
- `CompositeRule`: 复合规则定义。
- `ShadowDiff`: 影子模式差异。
- `ClassificationJob` / `ClassificationJobResult`: 异步任务模型。
- `ReviewEntry`: 复核条目模型。

## 6. 多格式适配

| 输入 | 方法 | 说明 |
|---|---|---|
| dict | `classify_record` | 单条记录 |
| list[dict] | `classify_table` | 表 |
| JSON str/dict | `classify_json` | 自动识别 record/table |
| pandas.DataFrame | `classify_dataframe` | 可选依赖 |
| pyarrow.Table | `classify_arrow` | 可选依赖 |
| list[dict] | `classify_sql_result` | SQL 结果集 |
| sf.data.DataFrame | `classify_secretflow` | SecretFlow 联邦 DataFrame |
| HDataFrame | `classify_secretflow` | 水平联邦 DataFrame |
| VDataFrame | `classify_secretflow` | 垂直联邦 DataFrame |
| FedNdarray | `classify_secretflow` | 联邦 Ndarray |

SecretFlow 适配器通过 `privacy/data_adapters.py` 的 `to_records/from_records` 转换为内部 records 表示。

## 7. 合规模板设计

内置模板定义于 `privacy_local_agent/privacy/classification_utils.py`（`TEMPLATES` 常量）：

| 模板 | 适用场景 | 主要扩展 |
|---|---|---|
| `jrt0197` | 金融数据分类分级 | 银行卡号、交易账号、客户资产、征信信息 |
| `gbt35273` | 通用个人信息 | 姓名、身份证、手机号、住址、行踪轨迹 |
| `gdpr` | 欧盟个人数据 | 生物识别、健康、基因、种族、政治观点 |

模板通过 `ClassificationParams.template` 激活，仅在默认参数之上叠加模板默认值，不影响请求级覆盖。

## 8. 规则版本化与影子模式

### 8.1 版本化

- `rule_set_version` 写入 `AuditInfo`，便于结果追溯。
- 模板与规则引擎版本独立管理。

### 8.2 影子模式

当 `shadow_mode=True` 时：

1. 使用当前参数运行分类，得到主结果。
2. 使用 `shadow_version` 替换 `rule_set_version`，重新运行分类，得到影子结果。
3. 对比每条记录的 `final_level` 与 `tags`，生成 `ShadowDiff`。
4. 主结果保持不变，影子差异作为 `shadow_diff` 字段返回。

影子模式仅用于评估，不影响实际分级决策。

## 9. 延迟加载与降级

- **Small-NER**：检测本地 ONNX 模型，存在则加载 `ONNXSmallNerEngine`；否则尝试 `ModelScopeSmallNerEngine`；缺失依赖则降级为 `NoOpSmallNerEngine`。
- **LLM Classifier**：加载 `Qwen2VLClassifier`，根据 CUDA/MPS 自动启用硬件加速；加载失败降级为 `NoOpLlmClassifier`。
- **SecretFlow**：可选依赖，缺失时 `classify_secretflow` 抛出明确 `ImportError`。
- **Async Manager**：线程池大小可配置，默认 4；超限时拒绝新任务。

## 10. 扩展点

- 继承 `RuleEngine` 接口扩展规则匹配逻辑。
- 继承 `SmallNerEngine` 接口对接其他微型 NER 框架。
- 继承 `LlmClassifier` 接口接入其他私有部署大模型服务。
- 继承 `CompositeRuleEngine` 接口实现更复杂的上下文推理。
- 通过 `ClassificationParams.template` 切换或新增合规模板。

## 11. 测试策略

- 20 个通用分类测试用例。
- 三层引擎协同与降级路径测试。
- 参数治理优先级测试。
- SecretFlow 适配器测试（mock）。
- 复合规则命中/未命中测试。
- 异步任务状态流转与结果获取测试。
- 复核队列确认与导出测试。
- Zero-Knowledge 日志测试。
- 合规模板切换测试。
- 影子模式差异检测测试。
- REST/gRPC 接口字段一致性测试。
