"""本地轻量级 Small-NER 使用示例。

运行方式：
    source .venv/bin/activate
    PYTHONPATH=. python docs/classification_ner/examples/ner_usage.py

说明：
    - 本示例优先尝试使用 ONNX 与 ModelScope 两种模式提取医疗实体。
    - 若模型未下载或依赖未安装，会自动降级并打印提示，不会报错退出。
    - 分类 API 示例不依赖本地模型，未下载时 Small-NER 自动回退为 NoOp。
"""

import os


def _model_files_present() -> bool:
    """检查默认 ONNX 模型与词表文件是否存在。"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    onnx_path = os.path.join(project_root, ".models", "raner_cmeee.onnx")
    vocab_path = os.path.join(project_root, ".models", "vocab.txt")
    return os.path.exists(onnx_path) and os.path.exists(vocab_path)


def demo_onnx_ner():
    """演示 ONNX 极速模式实体提取。"""
    print("=" * 60)
    print("示例 1: ONNX 极速模式实体提取")
    print("=" * 60)

    try:
        from privacy_local_agent.privacy.classification_ner import ONNXSmallNerEngine
    except ImportError as exc:
        print(f"[-] 无法导入 ONNXSmallNerEngine: {exc}")
        return

    engine = ONNXSmallNerEngine()
    text = "患者因急性心肌梗死入院，行冠状动脉介入治疗，术后服用阿司匹林。"
    entities = engine.extract(text)

    if not entities and not _model_files_present():
        print("[!] 未检测到本地 ONNX 模型文件，已优雅降级为空列表。")
        print("    可运行以下命令下载模型：")
        print("    PYTHONPATH=. python privacy_local_agent/privacy/download_ner_model.py")
        return

    print(f"输入文本: {text}")
    print(f"提取到 {len(entities)} 个实体:")
    for ent in entities:
        print(f"  - {ent['text']:15s} -> {ent['label']:20s} (置信度: {ent['confidence']:.2f})")
    print()


def demo_modelscope_ner():
    """演示 ModelScope 官方管道模式实体提取。"""
    print("=" * 60)
    print("示例 2: ModelScope 官方管道模式实体提取")
    print("=" * 60)

    try:
        from privacy_local_agent.privacy.classification_ner import ModelScopeSmallNerEngine
    except ImportError as exc:
        print(f"[-] 无法导入 ModelScopeSmallNerEngine: {exc}")
        return

    engine = ModelScopeSmallNerEngine()
    text = "患者诊断为2型糖尿病，处方开具二甲双胍，每日两次。"
    entities = engine.extract(text)

    if not entities:
        print("[!] ModelScope 未返回实体（可能依赖缺失或模型未就绪），已优雅降级。")
        return

    print(f"输入文本: {text}")
    print(f"提取到 {len(entities)} 个实体:")
    for ent in entities:
        print(f"  - {ent['text']:15s} -> {ent['label']:20s} (置信度: {ent['confidence']:.2f})")
    print()


def demo_classification_with_ner():
    """演示通过 ClassificationAPI 启用 Small-NER 进行分类定级。"""
    print("=" * 60)
    print("示例 3: ClassificationAPI 启用 Small-NER 定级")
    print("=" * 60)

    from privacy_local_agent.privacy.classification import ClassificationAPI

    api = ClassificationAPI()

    samples = [
        ("clinical_note", "患者诊断为2型糖尿病，建议控制饮食。", "普通疾病"),
        ("clinical_note", "患者张三，诊断为HIV阳性，需长期抗病毒治疗。", "敏感病种升级"),
        ("genetic_report", "基因检测报告显示BRCA1突变，建议遗传咨询。", "基因相关升级"),
    ]

    for field_name, text, scenario in samples:
        print(f"\n场景: {scenario}")
        print(f"输入: {text}")
        res = api.classify_field(field_name, text, params={"enable_small_ner": True})
        print(f"定级: {res.final_level.value}")
        print(f"引擎层: {res.engine_layer.value}")
        print(f"需要人工复核: {res.needs_human_review}")
        if res.tags:
            print("命中标签:")
            for tag in res.tags:
                print(f"  - {tag}")
        else:
            print("命中标签: 无（NER 未返回实体或已降级）")


def main():
    demo_onnx_ner()
    demo_modelscope_ner()
    demo_classification_with_ner()


if __name__ == "__main__":
    main()
