# 数据分类分级运维文档

## 1. 运行方式

数据分类能力已内置于 `privacy-local-agent`，启动方式与其他原语一致。

```bash
# REST 服务
python -m privacy_local_agent.main

# gRPC 服务
python -m privacy_local_agent.grpc_server

# REST + gRPC
python -m privacy_local_agent.server
```

## 2. 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PRIVACY_PROFILE` | `privacy-profile.yaml` | YAML 配置文件路径 |
| `PRIVACY_REVIEW_DB` | — | SQLite 复核存储路径；未设置时使用内存模式 |
| `PRIVACY_ASYNC_MAX_WORKERS` | `4` | 异步 LLM 线程池大小 |
| `PRIVACY_ASYNC_JOB_TTL_SECONDS` | `3600` | 异步任务 TTL（秒） |
| `PRIVACY_ASYNC_MAX_JOBS` | `1000` | 最大并发异步任务数 |
| `PRIVACY_LOG_FORMAT` | `text` | `text` 或 `json` |
| `PRIVACY_LOG_LEVEL` | `INFO` | 日志级别 |

## 3. 配置文件

通过环境变量 `PRIVACY_PROFILE` 指定 YAML 文件：

```bash
export PRIVACY_PROFILE=/path/to/privacy-profile.yaml
python -m privacy_local_agent.main
```

示例配置：

```yaml
primitives:
  classification:
    version: "1.0.0"
    default_level: "L3"
    enable_rule_engine: true
    enable_small_ner: false
    enable_llm: false
    return_field_values: true
    template: "gbt35273"
    rule_set_version: "1.0.0"
    shadow_mode: false
    shadow_version: "2.0.0"
    icd10_l4_intervals:
      - { start: "B20", end: "B24" }
      - { start: "F20", end: "F29" }
      - { start: "C00", end: "C97" }
    genomic_keywords:
      - "brca1"
      - "brca2"
      - "tp53"
      - "rs"
      - "snp"
      - "cnv"
      - "genome"
      - "genomic"
      - "gene"
      - "mutation"
      - "variant"
    public_field_whitelist:
      - "public_report"
      - "annual_summary"
      - "科普"
    operational_field_patterns:
      - "turnover_rate"
      - "device_usage"
      - "inventory"
    manual_override:
      patient_id: "L4"
```

## 4. REST 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/privacy/classify/field` | 单字段分类 |
| POST | `/v1/privacy/classify/record` | 单条记录分类 |
| POST | `/v1/privacy/classify/table` | 整张表分类（同步） |
| POST | `/v1/privacy/classify/table/async` | 整张表分类（异步） |
| GET | `/v1/privacy/classify/jobs/{job_id}` | 查询异步任务结果 |
| POST | `/v1/privacy/classify/secretflow` | SecretFlow 分类 |
| POST | `/v1/privacy/classify/review/confirm` | 确认/修正复核结果 |
| POST | `/v1/privacy/classify/review/export` | 导出复核样本 |

示例：

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/classify/field \
  -H "Content-Type: application/json" \
  -d '{"field_name":"id_card","value":"110101199001011237","params":{}}'
```

## 5. gRPC 接口

- `ClassifyField`
- `ClassifyRecord`
- `ClassifyTable`
- `ClassifyTableAsync`
- `GetClassificationJob`
- `ClassifySecretFlow`
- `ConfirmReview`
- `ExportReviews`

请求中的 `params_json` 为 JSON 序列化的参数字符串；响应中的 `result_json` 为 JSON 序列化的结果。

## 6. 日志与监控

### 6.1 Prometheus 指标

| 指标 | 类型 | 说明 |
|---|---|---|
| `privacy_classification_total` | Counter | 按 `final_level` / `layer` 统计分类次数 |
| `privacy_classification_jobs_total` | Counter | 按 `status` 统计异步任务数 |
| `privacy_classification_jobs_duration_seconds` | Histogram | 异步任务执行耗时 |
| `privacy_classification_review_queue_size` | Gauge | 当前待复核队列长度 |
| `privacy_classification_shadow_diff_total` | Counter | 影子模式检测到的差异数 |
| `privacy_classification_templates_total` | Counter | 按 `template` 统计模板使用次数 |

### 6.2 告警规则示例

```yaml
- alert: HighHumanReviewRate
  expr: rate(privacy_classification_review_queue_size[5m]) > 100
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Classification human review queue is growing fast"

- alert: AsyncJobFailures
  expr: rate(privacy_classification_jobs_total{status="FAILED"}[5m]) > 10
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Async classification jobs are failing"
```

### 6.3 Zero-Knowledge 运维要求

- 访问日志与错误日志中不打印原始字段值。
- 对高敏感数据设置 `returnFieldValues=false`。
- 复核导出时按需开启 `mask_input=true`。
- 定期审计日志中是否出现疑似原始值的字符串。

## 7. 异步任务运维

- 异步任务存储在内存中，重启服务会丢失未完成的任务。
- 通过 `PRIVACY_ASYNC_MAX_JOBS` 控制最大并发数，防止内存溢出。
- 通过 `PRIVACY_ASYNC_JOB_TTL_SECONDS` 控制任务保留时间。
- 若异步任务队列满载，新请求返回 429；调用方应退回到同步接口或重试。

## 8. 复核队列运维

- 内存模式下复核数据随服务重启丢失，生产环境建议配置 `PRIVACY_REVIEW_DB`。
- 定期导出复核样本进行人工标注与模型微调。
- 对确认后的样本进行持续监控，观察模型准确率变化。

## 9. 影子模式灰度

1. 在 profile 中设置 `shadow_mode: true` 与 `shadow_version`。
2. 观察 `privacy_classification_shadow_diff_total` 指标。
3. 分析差异字段分布，评估新规则集的误报/漏报。
4. 确认无误后，将 `rule_set_version` 切换为 `shadow_version` 并关闭 shadow mode。

## 10. 常见问题

**Q: 本地 Small-NER 和 LLM 大模型引擎目前是否已经可用？**
A: 是的，系统已经完整集成了基于 ONNX Runtime / ModelScope 的 Layer-2 医疗实体提取（Small-NER）引擎，以及基于 Qwen2-VL-2B-Instruct 的 Layer-3 本地多模态语义定级器。在系统检测到模型和依赖可用时，会自动启用。若模型不可用，将安全退化至 Layer-1 规则引擎或 No-Op 状态。

**Q: 对不同硬件平台的兼容性如何？**
A: 本系统经过优化，具有优秀的跨平台适应性：
- **Linux/Windows**：只要安装了 CUDA 版本的 PyTorch，将自动启用显卡硬件加速（首选 GPU 推理，使用 FP16 精度控制显存）。
- **macOS Apple Silicon (M系列芯片)**：系统启动时会自动通过 `torch.backends.mps` 接口检测，并利用 Metal (MPS) 进行硬件显卡加速推理（使用 FP32 单精度确保算子兼容性）。
- **CPU 兜底**：如果在没有 GPU/MPS 显卡的普通服务器（或虚拟机）中运行，推理引擎会自动将算子分配至 CPU 运行，系统保持正常服务。

**Q: 异步任务结果丢失怎么办？**
A: 异步任务存储在内存中，服务重启会丢失。若需要持久化，建议调用方在提交任务后记录 `job_id` 并在合理时间内轮询；对重要任务应使用同步接口或外部任务队列。

**Q: 影子模式会影响线上结果吗？**
A: 不会。影子模式仅返回 `shadow_diff` 差异字段，主结果保持当前规则集输出。
