"""查询混淆高级语义特性单元测试。"""

from __future__ import annotations

from privacy_local_agent.privacy.qol import DISEASES, ENTITIES, obfuscate_query


def test_obfuscate_query_semantic_slot_filling_medical() -> None:
    # 真实查询包含 "高血压" (属于 DISEASES) 且模式为 "如何治疗高血压"
    real_query = "如何治疗高血压"
    result = obfuscate_query(real_query, num_dummies=3, domain="medical", seed=42)

    assert real_query in result
    assert len(result) == 4

    # 虚假查询应当保持 "如何治疗{disease}" 的句式结构
    for item in result:
        if item != real_query:
            assert item.startswith("如何治疗")
            disease_part = item.replace("如何治疗", "")
            assert disease_part in DISEASES
            assert disease_part != "高血压"


def test_obfuscate_query_semantic_slot_filling_generic() -> None:
    # 真实查询包含 "公积金" (属于 ENTITIES) 且模式为 "公积金查询"
    real_query = "公积金查询"
    result = obfuscate_query(real_query, num_dummies=2, domain="generic", seed=42)

    assert real_query in result
    assert len(result) == 3

    for item in result:
        if item != real_query:
            assert item.endswith("查询")
            entity_part = item.replace("查询", "")
            assert entity_part in ENTITIES
            assert entity_part != "公积金"


def test_obfuscate_query_length_filtering_fallback() -> None:
    # 当无法匹配任何语义实体时，应该根据真实查询的长度过滤静态混淆池
    # 输入一个非常长且不包含已知实体的 Query
    long_query = "我想知道在本地办理跨省异地医保备案和报销需要的具体纸质材料有哪些"
    result = obfuscate_query(long_query, num_dummies=3, domain="generic", seed=42)

    assert long_query in result
    assert len(result) == 4

    # 检查生成的 dummy query 的长度，不应该比 long_query 短得太离谱
    # 原静态混淆池里有较长和较短的查询，长度分析机制应尽可能选长度接近的
    for item in result:
        if item != long_query:
            # 真实查询长度为 30 左右，过滤机制应使 dummy 长度至少在合理范围内（>10）
            assert len(item) > 10
