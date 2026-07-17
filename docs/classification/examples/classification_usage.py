"""数据分类分级模块使用示例。

运行方式：
    source .venv/bin/activate
    PYTHONPATH=. python docs/classification/examples/classification_usage.py

说明：
- 本脚本主要依赖规则引擎，不强制要求下载 Small-NER / LLM 模型权重。
- 当 enable_small_ner / enable_llm 设为 True 但模型或依赖缺失时，系统会安全降级为
  No-Op 引擎，脚本仍可正常结束。
"""

import json
import time

from privacy_local_agent.privacy.classification import ClassificationAPI


def print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f" {title}")
    print("=" * 60)


def main() -> None:
    # 1. 初始化 ClassificationAPI（无配置文件时使用内置默认值）
    api = ClassificationAPI()

    # 2. 字段级分类
    print_section("字段级分类")
    field_result = api.classify_field("id_card", "110101199001011237")
    print(f"字段名: {field_result.field_name}")
    print(f"字段值: {field_result.field_value}")
    print(f"最终等级: {field_result.final_level.value}")
    print(f"置信度: {field_result.confidence}")
    print(f"引擎层级: {field_result.engine_layer.value}")
    print(f"标签: {[str(tag) for tag in field_result.tags]}")
    print(f"推理说明: {field_result.reasoning}")

    # 3. 记录级分类
    print_section("记录级分类")
    record = {
        "id_card": "110101199001011237",
        "mobile": "13800138000",
        "diagnosis": "B21.1",
        "public_report": "2023 annual summary",
    }
    record_result = api.classify_record(record)
    print(f"记录索引: {record_result.record_index}")
    print(f"记录最终等级: {record_result.final_level.value}")
    print(f"需要人工复核: {record_result.needs_human_review}")
    print("各字段结果:")
    for field_name, field in record_result.field_results.items():
        print(f"  - {field_name}: {field.final_level.value} ({field.engine_layer.value})")

    # 4. 表级分类
    print_section("表级分类")
    schema = ["id_card", "mobile", "diagnosis", "brca1_status"]
    rows = [
        {
            "id_card": "110101199001011237",
            "mobile": "13800138000",
            "diagnosis": "J18.9",
            "brca1_status": "positive",
        },
        {
            "id_card": "110101199001011238",
            "mobile": "13800138001",
            "diagnosis": "C78.0",
            "brca1_status": "negative",
        },
    ]
    table_result = api.classify_table(schema, rows)
    print(f"表 schema: {table_result.schema_}")
    print(f"表最终等级: {table_result.final_level.value}")
    print(f"表置信度: {table_result.confidence}")
    print(f"需要人工复核: {table_result.needs_human_review}")
    for rr in table_result.record_results:
        print(f"  记录 {rr.record_index}: {rr.final_level.value}")

    # 5. JSON 输入自动识别（单条记录）
    print_section("JSON 输入自动识别（单条记录）")
    json_record = json.dumps({
        "id_card": "110101199001011237",
        "mobile": "13800138000",
    })
    json_result = api.classify_json(json_record)
    print(f"参数来源: {json_result.audit_info.parameter_source}")
    print(f"记录最终等级: {json_result.record_result.final_level.value}")

    # 6. JSON 输入自动识别（表）
    print_section("JSON 输入自动识别（表）")
    json_table = [
        {"id_card": "110101199001011237", "diagnosis": "C78.0"},
        {"id_card": "110101199001011238", "diagnosis": "J18.9"},
    ]
    json_table_result = api.classify_json(json_table)
    print(f"表最终等级: {json_table_result.table_result.final_level.value}")

    # 7. 参数治理：请求级覆盖 + 人工覆盖
    print_section("参数治理：请求级覆盖 + 人工覆盖")
    params = {
        "icd10L4Intervals": [
            {"start": "J10", "end": "J18"},
        ],
        "manualOverride": {
            "mobile": "L4",
        },
    }
    governance_result = api.classify_record(
        {"mobile": "13800138000", "diagnosis": "J18.9"},
        params=params,
    )
    print(f"mobile 最终等级: {governance_result.field_results['mobile'].final_level.value}")
    print(f"diagnosis 最终等级: {governance_result.field_results['diagnosis'].final_level.value}")
    print(f"参数来源: {governance_result.field_results['mobile'].reasoning}")

    # 8. 复合/上下文敏感规则
    print_section("复合/上下文敏感规则")
    composite_record = {
        "name": "张三",
        "id_card": "110101199001011237",
        "mobile": "13800138000",
    }
    composite_result = api.classify_record(composite_record)
    print(f"复合规则最终等级: {composite_result.final_level.value}")
    print(f"复合规则标签: {[str(tag) for tag in composite_result.aggregated_tags]}")

    # 9. 三层引擎调用（若模型/依赖缺失会自动降级，不会报错）
    print_section("三层引擎调用（自动降级）")
    layer_params = {
        "enableSmallNer": True,
        "enableLlm": True,
    }
    layer_result = api.classify_field(
        "clinical_note",
        "患者诊断为 HIV 感染，使用拉米夫定治疗。",
        params=layer_params,
    )
    print(f"最终等级: {layer_result.final_level.value}")
    print(f"引擎层级: {layer_result.engine_layer.value}")
    print(f"置信度: {layer_result.confidence}")
    print(f"推理说明: {layer_result.reasoning}")
    if layer_result.needs_human_review:
        print("注意：该结果建议人工复核。")

    # 10. Layer 3 异步推理
    print_section("Layer 3 异步推理")
    job_id = api.submit_classify_table_async(
        schema=["id_card", "mobile"],
        rows=[{"id_card": "110101199001011237", "mobile": "13800138000"}],
        params={"enableRuleEngine": True},
    )
    print(f"异步任务 ID: {job_id}")
    for _ in range(10):
        job = api.get_job_result(job_id)
        if job.status in ("DONE", "FAILED"):
            break
        time.sleep(0.5)
    print(f"异步任务状态: {job.status}")
    if job.result:
        print(f"异步任务结果最终等级: {job.result.table_result.final_level.value}")

    # 11. Zero-Knowledge 扫描
    print_section("Zero-Knowledge 扫描")
    zk_result = api.classify_field(
        "id_card",
        "110101199001011237",
        params={"returnFieldValues": False},
    )
    print(f"ZK 模式字段值: {zk_result.field_value}")

    # 12. 合规模板
    print_section("合规模板")
    tpl_result = api.classify_record(
        {"name": "张三", "id_card": "110101199001011237", "mobile": "13800138000"},
        params={"template": "gbt35273"},
    )
    print(f"模板模式最终等级: {tpl_result.final_level.value}")
    print(f"审计信息: {tpl_result.audit_info.model_dump(by_alias=True)}")

    # 13. 影子模式
    print_section("影子模式")
    shadow_result = api.classify_table(
        schema=["mobile"],
        rows=[{"mobile": "13800138000"}],
        params={
            "ruleSetVersion": "1.0.0",
            "shadowMode": True,
            "shadowVersion": "2.0.0",
        },
    )
    print(f"影子模式差异: {shadow_result.table_result.shadow_diff}")

    # 14. 人工复核与导出
    print_section("人工复核与导出")
    review_result = api.classify_table(
        schema=["gene_marker"],
        rows=[{"gene_marker": "BRCA1 c.5266dupC"}],
        params={"enableSmallNer": True},
    )
    review_entries = review_result.table_result.review_entries
    if review_entries:
        review_id = review_entries[0].review_id
        api.confirm_review(review_id, corrected_level="L5", reviewer="operator-1")
        print(f"已确认复核: {review_id}")
    jsonl_data = api.export_reviews(format="jsonl", mask_input=True)
    print(f"导出复核样本数: {len(jsonl_data.strip().split(chr(10))) if jsonl_data.strip() else 0}")

    # 15. 输出审计信息示例
    print_section("审计信息")
    full_result = api.classify_json({"id_card": "110101199001011237"})
    audit = full_result.audit_info
    print(f"原语版本: {audit.version}")
    print(f"参数版本: {audit.profile_version}")
    print(f"规则引擎版本: {audit.rule_engine_version}")
    print(f"参数来源: {audit.parameter_source}")
    print(f"时间戳: {audit.timestamp}")


if __name__ == "__main__":
    main()
