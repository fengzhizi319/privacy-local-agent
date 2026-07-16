"""本地多模态大模型分类分级使用示例。

运行方式：
    source .venv/bin/activate
    PYTHONPATH=. python docs/classification_llm/examples/llm_usage.py

说明：
    - 本示例不依赖已下载的 Qwen2-VL-2B-Instruct 模型权重。
    - 若模型未下载或 ML 依赖未安装，LLM 层会自动降级，示例继续运行并打印提示。
    - 需要 Pillow 库生成示例图片；若未安装，图片相关示例会自动跳过。
"""

import base64
import os
import sys
from io import BytesIO

# 项目根目录
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.insert(0, project_root)

try:
    from PIL import Image

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

from privacy_local_agent.privacy.classification import ClassificationAPI
from privacy_local_agent.privacy.classification_llm import Qwen2VLClassifier
from privacy_local_agent.privacy.classification_models import SensitivityLevel


def make_example_image(path: str) -> None:
    """生成一张示例医疗报告图片。"""
    if not HAS_PILLOW:
        return
    img = Image.new("RGB", (200, 100), color="white")
    img.save(path)


def image_to_base64(path: str) -> str:
    """将图片文件转为 Data URI 格式的 Base64 字符串。"""
    with open(path, "rb") as f:
        data = f.read()
    ext = os.path.splitext(path)[1].lstrip(".") or "png"
    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:image/{ext};base64,{encoded}"


def print_llm_result(result):
    """打印 LLM 分类结果。"""
    if result is None:
        print("  [LLM 结果] 无（已降级）")
        return
    print(f"  等级: {result.get('final_level')}")
    print(f"  类别: {result.get('sub_category')}")
    print(f"  置信度: {result.get('confidence')}")
    print(f"  需要复核: {result.get('needs_human_review')}")
    print(f"  推理: {result.get('reasoning', '')[:80]}...")


def main():
    model_dir = os.path.join(project_root, ".models", "Qwen2-VL-2B-Instruct")
    model_exists = os.path.isdir(model_dir) and any(
        f.endswith(".safetensors") or f == "config.json"
        for f in os.listdir(model_dir) if os.path.isfile(os.path.join(model_dir, f))
    )

    print("=" * 60)
    print("本地多模态大模型分类分级使用示例")
    print("=" * 60)
    print(f"模型目录: {model_dir}")
    print(f"模型权重状态: {'已下载' if model_exists else '未下载（将自动降级）'}")
    print()

    # 示例 1：纯文本输入
    print("[示例 1] 纯文本输入")
    classifier = Qwen2VLClassifier()
    text_input = "患者诊断为 HIV 阳性，正在接受抗逆转录病毒治疗。"
    result = classifier.classify(
        text=text_input,
        upstream_level=SensitivityLevel.L3,
        upstream_confidence=0.5,
    )
    print_llm_result(result)
    print()

    # 示例 2：本地图片路径输入
    print("[示例 2] 本地图片路径输入")
    if HAS_PILLOW:
        image_path = os.path.join(project_root, ".models", "example_report.png")
        os.makedirs(os.path.dirname(image_path), exist_ok=True)
        make_example_image(image_path)
        result = classifier.classify(
            text=image_path,
            upstream_level=SensitivityLevel.L1,
            upstream_confidence=0.1,
        )
        print_llm_result(result)
    else:
        print("  跳过：未安装 Pillow 库，无法生成/读取示例图片。")
    print()

    # 示例 3：Base64 图片输入
    print("[示例 3] Base64 图片输入")
    if HAS_PILLOW:
        image_path = os.path.join(project_root, ".models", "example_report.png")
        data_uri = image_to_base64(image_path)
        result = classifier.classify(
            text=data_uri,
            upstream_level=SensitivityLevel.L1,
            upstream_confidence=0.1,
        )
        print_llm_result(result)
    else:
        print("  跳过：未安装 Pillow 库，无法生成/读取示例图片。")
    print()

    # 示例 4：通过 ClassificationAPI 使用 LLM 层
    print("[示例 4] 通过 ClassificationAPI 启用 LLM 层")
    api = ClassificationAPI()
    result = api.classify_field(
        field_name="diagnosis_note",
        value="患者诊断为 HIV 阳性，正在接受抗逆转录病毒治疗。",
        params={"enable_llm": True},
    )
    print(f"  字段名: {result.field_name}")
    print(f"  最终等级: {result.final_level.value}")
    print(f"  命中引擎: {result.engine_layer.value}")
    print(f"  置信度: {result.confidence}")
    reasoning = result.reasoning or "（无）"
    print(f"  推理: {reasoning[:80]}{'...' if len(reasoning) > 80 else ''}")
    print()

    # 示例 5：模型下载提示
    if not model_exists:
        print("[提示] 若需体验真实大模型推理，请先下载模型权重：")
        print("    python -m privacy_local_agent.privacy.download_model")
        print()

    print("=" * 60)
    print("示例执行完成。")
    print("=" * 60)


if __name__ == "__main__":
    main()
