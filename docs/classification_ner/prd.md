# 本地轻量级 Small-NER 分类定级 PRD

## 1. 业务背景与痛点
根据 [分类分级算法设计](file:///home/charles/code/sfwork/docs/algorithm/%E5%88%86%E7%B1%BB%E5%88%86%E7%BA%A7%E7%AE%97%E6%B3%95%E8%AE%BE%E8%AE%A1.md) 的三层漏斗检测架构，在第一层规则引擎（RE2/FST）未命中，或置信度较低时，我们需要对半结构化文本（如门诊病历、出院小结、病理报告）进行快速的实体识别（NER）。

如果直接调用大语言模型（LLM），尽管精度高，但推理延迟在秒级，且显存开销极大，无法支持高频实时 API 拦截。因此，我们需要在第二层接入**毫秒级的轻量级命名实体识别引擎（Small-NER）**。

## 2. 目标与范围
- **实体抽取能力**：精准识别中文医学文本中的核心实体：
  - **疾病/症状** (`MEDICAL_DISEASE`)
  - **药物** (`MEDICATION`)
  - **手术/操作** (`SURGERY`)
  - **解剖部位** (`BODY_PART`)
- **双运行模式支持 (Double Inference Modes)**：
  - **ONNX 极速模式**：通过 ONNX Runtime 进行推理，摆脱对 PyTorch 重型框架的依赖，减少打包体积和显存开销。
  - **ModelScope 官方管道模式**：直接利用 ModelScope 官方推理管道（Transformers Pipeline）加载 PyTorch 模型，提供开箱即用的官方高精度提取服务。
- **联动的定级与升级机制**：
  - 当抽取出敏感病种（如 HIV、精神分裂）且与姓名/身份标识同段落出现时，自动将安全标签升级为 **L4 (高风险)**。
  - 当抽取出基因相关实体时，自动标记为 **L5 (极高风险)** 并送入人工审核。
- **纯 Python Tokenizer 实现**：为了保证在没有 `transformers` 库的极简环境下的极致轻量，必须实现一个无外部依赖的 BERT Tokenizer。

## 3. 技术选型 (Technical Specification)
- **推理引擎**：
  - 模式 A：`onnxruntime` + 纯 Python 分词器 `SimpleChineseBertTokenizer`（基于 vocab.txt，速度极快）。
  - 模式 B：`modelscope` 官方推理管道 (PyTorch/Transformers)。
- **底座模型**：达摩院 RaNER 医疗实体识别微调模型 `iic/nlp_raner_named-entity-recognition_chinese-base-cmeee`（ModelScope）。


## 4. 功能性需求 (Functional Requirements)

### 4.1 实体提取接口
实现 `extract(text: str) -> List[Dict[str, Any]]` 接口，输出识别到的实体列表：
```json
[
  {
    "text": "阿司匹林",
    "label": "MEDICATION",
    "confidence": 0.98
  },
  {
    "text": "冠心病",
    "label": "MEDICAL_DISEASE",
    "confidence": 0.95
  }
]
```

### 4.2 联动打标与升级逻辑
将 NER 的结果送回 `ClassificationAPI`：
- 若提取出 `MEDICAL_DISEASE` 为敏感传染病/精神类病种，且文本中包含 PII（姓名/身份证），安全等级提升至 **L4**。
- 若提取出基因突变/检测实体，标记为 **L5**，并触发人工复核标志。

### 4.3 优雅降级设计
- 检测到 `onnxruntime` 模块未安装或 ONNX 模型文件未下载时，自动回退到 `NoOpSmallNerEngine`，仅输出警告日志，不允许抛出异常导致整条链路挂掉。

## 5. 非功能性需求 (Non-Functional Requirements)
- **延迟**：单次推理时间限制在 **30ms** 以内。
- **包体积**：ONNX 模型文件控制在 **100MB** 以内。
