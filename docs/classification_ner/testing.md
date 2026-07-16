# 本地轻量级 Small-NER 测试文档 (Testing Document)

## 1. 测试策略与覆盖范围

数据分类分级第二层（Small-NER）包含以下关键模块，测试设计对所有核心功能进行了全覆盖：
1. **SimpleChineseBertTokenizer**：测试中文单字分词、未登录词替换为 `[UNK]`、魔数 ID（CLS/SEP/PAD）添加以及序列填充与截断。
2. **BIO 实体状态机解析器**：测试相邻的 `B-` 和 `I-` 标识符能否被状态机正确识别、拼接为完整的词语，并验证置信度最小概率合并逻辑。
3. **ONNX 推理 session**：使用 `unittest.mock` 对 ONNX 会话输入输出和 Logits 矩阵进行模拟，验证端到端的文本实体提取及敏感类别标准化映射。
4. **ModelScope 推理管道**：模拟魔搭推理管道输出，验证返回的字典数据结构和实体类别能够完全映射成功。
5. **异常与安全降级**：验证本地模型文件或依赖缺失时，分类器能够静默记录警告并自动回退至 Rules/No-Op 模式，不发生运行时崩溃。

---

## 2. 单元测试设计与运行 (Unit Tests)

测试代码保存在 [test_classification_ner.py](file:///home/charles/code/sfwork/privacy-local-agent/tests/test_classification_ner.py) 中，包含 5 个独立的自动化测试用例。

### 2.1 运行测试命令
在项目根目录下，激活虚拟环境并执行：
```bash
PYTHONPATH=. .venv/bin/pytest tests/test_classification_ner.py -v
```

### 2.2 测试日志 (Test Log)
```text
============================= test session starts ==============================
platform linux -- Python 3.13.13, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/charles/code/sfwork/privacy-local-agent
configfile: pyproject.toml
plugins: anyio-4.14.1
collected 5 items                                                              

tests/test_classification_ner.py::test_simple_bert_tokenizer PASSED       [ 20%]
tests/test_classification_ner.py::test_parse_bio_tags PASSED              [ 40%]
tests/test_classification_ner.py::test_ner_extract_success PASSED         [ 60%]
tests/test_classification_ner.py::test_ner_fallback_when_uninitialized PASSED [ 80%]
tests/test_classification_ner.py::test_modelscope_ner_extract_success PASSED [100%]

============================== 5 passed in 0.12s ===============================
```

---

## 3. 手工验证测试 (Manual Integration Verification)

您可以通过以下单行 Python 指令直接调用集成的 API，验证大模型/NER 引擎对输入文本的定级：

```bash
# 启用 Small-NER 并测试敏感传染病（HIV）升级到 L4 的定级逻辑
PYTHONPATH=. .venv/bin/python -c "
from privacy_local_agent.privacy.classification import ClassificationAPI
api = ClassificationAPI()
res = api.classify_field('content', '患者张三，诊断为HIV阳性', params={'enable_small_ner': True})
print('定级结果:', res.final_level)
print('命中标签:', [str(t) for t in res.tags])
"
```

**预期输出**：
```text
定级结果: SensitivityLevel.L4
命中标签: ['L4_MEDICAL_SENSITIVE_DISEASE']
```
