# Data Classification Operations / 数据分类运维文档

## 1. 运行方式 / Deployment

数据分类能力已内置于 `privacy-local-agent`，启动方式与其他原语一致。

```bash
# REST 服务
python -m privacy_local_agent.main

# gRPC 服务
python -m privacy_local_agent.grpc_server

# REST + gRPC
python -m privacy_local_agent.server
```

## 2. 配置文件 / Profile

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

## 3. REST 接口 / REST Endpoints

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/privacy/classify/field` | 单字段分类 |
| POST | `/v1/privacy/classify/record` | 单条记录分类 |
| POST | `/v1/privacy/classify/table` | 整张表分类 |

示例：

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/classify/field \
  -H "Content-Type: application/json" \
  -d '{"field_name":"id_card","value":"110101199001011237","params":{}}'
```

## 4. gRPC 接口 / gRPC Endpoints

- `ClassifyField`
- `ClassifyRecord`
- `ClassifyTable`

请求中的 `params_json` 为 JSON 序列化的参数字符串；响应中的 `result_json` 为 JSON 序列化的结果。

## 5. 日志与监控 / Monitoring

- 分类结果包含 `auditInfo`，可记录版本、时间戳、参数来源。
- 建议对高敏感（L4/L5）命中记录审计日志。
- `needsHumanReview=true` 的结果应进入人工复核队列。

## 6. 常见问题 / FAQ

**Q: 本地 Small-NER 和 LLM 大模型引擎目前是否已经可用？**
A: 是的，系统已经完整集成了基于 ONNX Runtime / ModelScope 的 Layer-2 医疗实体提取（Small-NER）引擎，以及基于 Qwen2-VL-2B-Instruct 的 Layer-3 本地多模态语义定级器。在系统检测到模型和依赖可用时，会自动启用。若模型不可用，将安全退化至 Layer-1 规则引擎或 No-Op 状态。

**Q: 对不同硬件平台的兼容性如何？**
A: 本系统经过优化，具有优秀的跨平台适应性：
- **Linux/Windows**：只要安装了 CUDA 版本的 PyTorch，将自动启用显卡硬件加速（首选 GPU 推理，使用 FP16 精度控制显存）。
- **macOS Apple Silicon (M系列芯片)**：系统启动时会自动通过 `torch.backends.mps` 接口检测，并利用 Metal (MPS) 进行硬件显卡加速推理（使用 FP32 单精度确保算子兼容性）。
- **CPU 兜底**：如果在没有 GPU/MPS 显卡的普通服务器（或虚拟机）中运行，推理引擎会自动将算子分配至 CPU 运行，系统保持正常服务。

