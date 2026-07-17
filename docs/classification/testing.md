# 数据分类分级测试文档

## 1. 测试目标

- 验证规则引擎覆盖规范中的 20 个通用测试用例。
- 验证参数治理、YAML profile、人工覆盖。
- 验证多格式适配器（JSON、pandas、Arrow、SQL 结果集、SecretFlow）。
- 验证复合/上下文敏感规则识别。
- 验证同步与异步 Layer 3 推理接口。
- 验证人工复核队列的收集、确认与导出。
- 验证 Zero-Knowledge 扫描原则落地。
- 验证内置合规模板（JR/T 0197、GB/T 35273、GDPR）。
- 验证规则集版本化与影子模式。
- 验证 REST/gRPC 接口与本地 SDK 输出一致。
- 确保不破坏既有接口的测试。

## 2. 测试结构

```text
tests/
├── test_classification.py                 # 分类原语单元测试
├── test_classification_composite.py      # 复合规则测试
├── test_classification_async.py          # 异步 LLM 测试
├── test_classification_review.py         # 人工复核测试
├── test_classification_secretflow.py     # SecretFlow 适配器测试
├── test_classification_templates.py      # 合规模板测试
├── test_classification_vectorized.py     # 向量化规则引擎测试
├── test_classification_zk.py             # Zero-Knowledge 测试
├── test_classification_rest.py           # REST 接口测试
└── test_classification_grpc.py           # gRPC 接口测试
```

> 注：合规模板、Zero-Knowledge 工具与 SecretFlow 适配器已合并到
> `privacy_local_agent/privacy/classification_utils.py`，对应测试文件保持不变。

## 3. 运行测试

```bash
cd /home/charles/code/sfwork/privacy-local-agent
source .venv/bin/activate
PYTHONPATH=. pytest tests -q
```

pandas / pyarrow / secretflow 相关测试在未安装对应包时自动跳过或使用 mock。

## 4. 20 个通用测试用例

| # | 字段 | 值 | 期望等级 | 期望类别 |
|---|------|-----|----------|----------|
| 1 | id_card | 110101199001011237 | L3 | PII_ID_CARD |
| 2 | id_card | 110101199001011234 | 回退 | 无 PII_ID_CARD |
| 3 | mobile | 13800138000 | L3 | PII_MOBILE |
| 4 | mobile | 12800138000 | 回退 | 无 PII_MOBILE |
| 5 | medical_card | 123456789 | L3 | PII_MEDICAL_CARD |
| 6 | diagnosis | B21.1 | L4 | MEDICAL_ICD10_HIV |
| 7 | diagnosis | F25 | L4 | MEDICAL_ICD10_PSYCHIATRIC |
| 8 | diagnosis | C78.0 | L4 | MEDICAL_ICD10_CANCER |
| 9 | diagnosis | J18.9 | L3 | MEDICAL_ICD10_GENERAL |
| 10 | brca1_status | positive | L5 | GENOMIC_BRCA_TP53 |
| 11 | rs_number | rs12345 | L5 | GENOMIC_VARIANT |
| 12 | file_content | BAM\x01... | L5 | GENOMIC_BAM |
| 13 | file_content | ##fileformat=VCFv4.2 | L5 | GENOMIC_VCF |
| 14 | file_content | @SQ SN:chr1 LN:1000 | L5 | GENOMIC_BAM |
| 15 | sequence | ATCG... (>=50) | L5 | GENOMIC_SEQUENCE |
| 16 | public_report | 2023 annual summary | L1 | PUBLIC_REPORT |
| 17 | turnover_rate | 0.85 | L2 | OPERATIONAL_STAT |
| 18 | name | Alice | 回退 | 无高敏感标签 |
| 19 | record | id_card + mobile + B21.1 | L4 | 聚合 |
| 20 | table | id_card + brca1 + diagnosis | L5 | 聚合 |

> 注：规范用例 1 的原始值 `11010119900101123X` 校验和不通过；测试使用有效身份证 `110101199001011237`。

## 5. 新增测试用例

### 5.1 SecretFlow 适配器

| ID | 场景 | 验证点 |
|---|---|---|
| SF-1 | HDataFrame 单 partition | 自动选择 partition 并返回表结果 |
| SF-2 | HDataFrame 多 partition + party | 按 party 提取 partition |
| SF-3 | VDataFrame 自动定位列 | 找到包含 schema 列的 partition |
| SF-4 | FedNdarray 输入 | 转换为 records 后分类 |

### 5.2 复合规则

| ID | 场景 | 验证点 |
|---|---|---|
| COMP-1 | 姓名 + 身份证 + 手机号 | 升级为 L5 COMPOSITE_PII_COMBO |
| COMP-2 | 仅姓名 + 手机号 | 不命中复合规则 |
| COMP-3 | 自定义复合规则 | 按请求参数生效 |
| COMP-4 | 诊断 + 基因字段 | 升级为 L5 COMPOSITE_MEDICAL_GENOMIC |

### 5.3 异步 LLM

| ID | 场景 | 验证点 |
|---|---|---|
| ASYNC-1 | 提交异步任务 | 返回 job_id，状态为 PENDING |
| ASYNC-2 | 任务完成 | 轮询到 DONE，结果可获取 |
| ASYNC-3 | 任务失败 | 状态为 FAILED，error 不含原始值 |
| ASYNC-4 | 队列满载 | 超过最大并发数时拒绝新任务 |
| ASYNC-5 | TTL 清理 | 过期任务被自动清理 |

### 5.4 人工复核

| ID | 场景 | 验证点 |
|---|---|---|
| REVIEW-1 | 自动收集 | needsHumanReview=True 字段进入复核队列 |
| REVIEW-2 | 确认复核 | corrected_level 更新成功 |
| REVIEW-3 | 导出 JSONL | 格式正确，含 fine_tuning_text |
| REVIEW-4 | 掩码导出 | mask_input=True 时 input 被掩码 |

### 5.5 Zero-Knowledge

| ID | 场景 | 验证点 |
|---|---|---|
| ZK-1 | 关闭 field_value 返回 | `returnFieldValues=false` 时 fieldValue 为空 |
| ZK-2 | 日志不脱敏 | LLM 失败日志不含原始输入 |
| ZK-3 | 指标不脱敏 | Prometheus 指标 label 不含原始值 |

### 5.6 合规模板

| ID | 场景 | 验证点 |
|---|---|---|
| TPL-1 | jrt0197 模板 | 金融字段识别增强 |
| TPL-2 | gbt35273 模板 | 通用个人信息识别增强 |
| TPL-3 | gdpr 模板 | 生物识别/基因等识别增强 |
| TPL-4 | 请求参数覆盖模板 | 请求优先级高于模板 |

### 5.7 规则版本化与影子模式

| ID | 场景 | 验证点 |
|---|---|---|
| VERS-1 | 版本写入审计 | `auditInfo.ruleEngineVersion` 与请求一致 |
| SHADOW-1 | 影子模式差异 | `shadowDiff` 记录等级变化的字段 |
| SHADOW-2 | 影子模式不影响主结果 | 主结果保持当前规则集输出 |

## 6. 单元测试示例

### 6.1 复合规则测试

```python
from privacy_local_agent.privacy.classification import ClassificationAPI

def test_composite_pii_combo():
    api = ClassificationAPI()
    record = {"name": "张三", "id_card": "110101199001011237", "mobile": "13800138000"}
    result = api.classify_record(record)
    assert result.final_level.value == "L5"
    assert any(t.category == "COMPOSITE_PII_COMBO" for t in result.aggregated_tags)
```

### 6.2 异步任务测试

```python
import time

def test_async_job_lifecycle():
    api = ClassificationAPI()
    job_id = api.submit_classify_table_async(
        schema=["id_card"],
        rows=[{"id_card": "110101199001011237"}],
        params={"enable_rule_engine": True},
    )
    assert job_id

    for _ in range(10):
        job = api.get_job_result(job_id)
        if job.status in ("DONE", "FAILED"):
            break
        time.sleep(0.5)

    assert job.status == "DONE"
    assert job.result is not None
```

### 6.3 Zero-Knowledge 测试

```python
import logging

def test_zk_no_raw_value_in_log(caplog):
    api = ClassificationAPI()
    with caplog.at_level(logging.WARNING):
        api.classify_field("id_card", "110101199001011237")
    assert "110101199001011237" not in caplog.text
```

### 6.4 影子模式测试

```python
def test_shadow_mode_detects_diff():
    api = ClassificationAPI()
    result = api.classify_table(
        schema=["mobile"],
        rows=[{"mobile": "13800138000"}],
        params={
            "ruleSetVersion": "1.0.0",
            "shadowMode": True,
            "shadowVersion": "2.0.0",
        },
    )
    assert result.table_result.shadow_diff is not None
```

## 7. 集成测试

### 7.1 REST 接口

使用 `fastapi.testclient.TestClient` 验证：

- `/v1/privacy/classify/field`
- `/v1/privacy/classify/record`
- `/v1/privacy/classify/table`
- `/v1/privacy/classify/table/async`
- `/v1/privacy/classify/jobs/{job_id}`
- `/v1/privacy/classify/secretflow`
- `/v1/privacy/classify/review/confirm`
- `/v1/privacy/classify/review/export`

### 7.2 gRPC 接口

使用 `grpc.aio` 或 `grpc` 同步 stub 验证：

- `ClassifyField`
- `ClassifyRecord`
- `ClassifyTable`
- `ClassifyTableAsync`
- `GetClassificationJob`
- `ClassifySecretFlow`
- `ConfirmReview`
- `ExportReviews`

## 8. 性能测试

- 测量 Layer 1 规则引擎在 1k/10k/100k 记录下的吞吐量。
- 测量异步 LLM 接口的提交延迟与结果获取延迟。
- 测量影子模式双倍计算对吞吐量的影响。
- 参见 `docs/classification/performance.md`。

## 9. 验收检查清单

- [ ] 20 个通用测试用例通过。
- [ ] SecretFlow 适配器测试通过（mock）。
- [ ] 复合规则命中/未命中测试通过。
- [ ] 异步任务生命周期测试通过。
- [ ] 复核队列收集、确认、导出测试通过。
- [ ] Zero-Knowledge 日志测试通过。
- [ ] 合规模板切换测试通过。
- [ ] 影子模式差异检测测试通过。
- [ ] REST/gRPC 接口字段一致性测试通过。
- [ ] `PYTHONPATH=. pytest tests -q` 全部通过。
