"""数据分类原语单元测试。

覆盖规范中 20 个通用测试用例、参数覆盖、YAML profile、人工覆盖、
pandas/Arrow 适配器、表聚合以及 JSON 输入适配。

Unit tests for the data classification primitive. Covers the 20 common spec
cases, parameter governance, YAML profile, manual override, pandas/Arrow
adapters, table aggregation and JSON input adaptation.
"""

import os
import tempfile

import pytest
import yaml

from privacy_local_agent.privacy.classification import ClassificationAPI
from privacy_local_agent.privacy.classification_models import (
    EngineLayer,
    FieldClassificationResult,
    RecordClassificationResult,
    SensitivityLevel,
    TableClassificationResult,
)


@pytest.fixture
def api():
    """默认 ClassificationAPI 实例。"""
    return ClassificationAPI()


# ---------------------------------------------------------------------------
# 通用测试用例 / Common spec cases
# ---------------------------------------------------------------------------


def assert_has_category(result: FieldClassificationResult, category: str, level: SensitivityLevel = None):
    """断言字段结果包含指定 category 的标签，并可选择校验等级。"""
    categories = [t.category for t in result.tags]
    assert category in categories, f"expected category {category} in {categories}"
    if level is not None:
        assert result.final_level == level, f"expected level {level}, got {result.final_level}"


def test_case_01_id_card_valid(api):
    """中国大陆身份证号命中 PII_ID_CARD，等级 L3。"""
    result = api.classify_field("id_card", "110101199001011237")
    assert_has_category(result, "PII_ID_CARD", SensitivityLevel.L3)


def test_case_02_id_card_invalid_checksum(api):
    """身份证校验和失败时不应命中 PII_ID_CARD，回退到 default_level。"""
    result = api.classify_field("id_card", "110101199001011234")
    assert "PII_ID_CARD" not in [t.category for t in result.tags]


def test_case_03_mobile_valid(api):
    """中国大陆手机号命中 PII_MOBILE，等级 L3。"""
    result = api.classify_field("mobile", "13800138000")
    assert_has_category(result, "PII_MOBILE", SensitivityLevel.L3)


def test_case_04_mobile_invalid(api):
    """非法手机号不应命中 PII_MOBILE。"""
    result = api.classify_field("mobile", "12800138000")
    assert "PII_MOBILE" not in [t.category for t in result.tags]


def test_case_05_shanghai_medical_card(api):
    """上海医保卡号命中 PII_MEDICAL_CARD，等级 L3。"""
    result = api.classify_field("medical_card", "123456789")
    assert_has_category(result, "PII_MEDICAL_CARD", SensitivityLevel.L3)


def test_case_06_icd10_hiv(api):
    """ICD-10 B21.1 命中 HIV，等级 L4。"""
    result = api.classify_field("diagnosis", "B21.1")
    assert_has_category(result, "MEDICAL_ICD10_HIV", SensitivityLevel.L4)


def test_case_07_icd10_psychiatric(api):
    """ICD-10 F25 命中精神疾病，等级 L4。"""
    result = api.classify_field("diagnosis", "F25")
    assert_has_category(result, "MEDICAL_ICD10_PSYCHIATRIC", SensitivityLevel.L4)


def test_case_08_icd10_cancer(api):
    """ICD-10 C78.0 命中癌症，等级 L4。"""
    result = api.classify_field("diagnosis", "C78.0")
    assert_has_category(result, "MEDICAL_ICD10_CANCER", SensitivityLevel.L4)


def test_case_09_icd10_general(api):
    """ICD-10 J18.9 命中普通疾病，等级 L3。"""
    result = api.classify_field("diagnosis", "J18.9")
    assert_has_category(result, "MEDICAL_ICD10_GENERAL", SensitivityLevel.L3)


def test_case_10_genomic_brca_tp53(api):
    """字段名含 brca1 命中 GENOMIC_BRCA_TP53，等级 L5。"""
    result = api.classify_field("brca1_status", "positive")
    assert_has_category(result, "GENOMIC_BRCA_TP53", SensitivityLevel.L5)


def test_case_11_genomic_variant(api):
    """rs 编号命中 GENOMIC_VARIANT，等级 L5。"""
    result = api.classify_field("rs_number", "rs12345")
    assert_has_category(result, "GENOMIC_VARIANT", SensitivityLevel.L5)


def test_case_12_genomic_bam_magic(api):
    """BAM 魔数命中 GENOMIC_BAM，等级 L5。"""
    result = api.classify_field("file_content", "BAM\x01header")
    assert_has_category(result, "GENOMIC_BAM", SensitivityLevel.L5)


def test_case_13_genomic_vcf(api):
    """VCF 文件头命中 GENOMIC_VCF，等级 L5。"""
    result = api.classify_field("file_content", "##fileformat=VCFv4.2")
    assert_has_category(result, "GENOMIC_VCF", SensitivityLevel.L5)


def test_case_14_genomic_bam_header(api):
    """SAM/BAM @SQ 头部命中 GENOMIC_BAM，等级 L5。"""
    result = api.classify_field("file_content", "@SQ SN:chr1 LN:1000")
    assert_has_category(result, "GENOMIC_BAM", SensitivityLevel.L5)


def test_case_15_genomic_sequence(api):
    """长基因序列命中 GENOMIC_SEQUENCE，等级 L5。"""
    result = api.classify_field("sequence", "ATCG" * 20)
    assert_has_category(result, "GENOMIC_SEQUENCE", SensitivityLevel.L5)


def test_case_16_public_report(api):
    """公开报表字段命中 PUBLIC_REPORT，等级 L1。"""
    result = api.classify_field("public_report", "2023 annual summary")
    assert_has_category(result, "PUBLIC_REPORT", SensitivityLevel.L1)


def test_case_17_operational_stat(api):
    """运营统计字段命中 OPERATIONAL_STAT，等级 L2。"""
    result = api.classify_field("turnover_rate", "0.85")
    assert_has_category(result, "OPERATIONAL_STAT", SensitivityLevel.L2)


def test_case_18_name_fallback(api):
    """普通姓名字段不应命中高敏感规则。"""
    result = api.classify_field("name", "Alice", params={"default_level": "L1"})
    high_categories = {"PII_ID_CARD", "PII_MOBILE", "PII_MEDICAL_CARD", "MEDICAL_ICD10_HIV",
                       "MEDICAL_ICD10_PSYCHIATRIC", "MEDICAL_ICD10_CANCER", "GENOMIC_BRCA_TP53",
                       "GENOMIC_VARIANT", "GENOMIC_BAM", "GENOMIC_VCF", "GENOMIC_FASTQ",
                       "GENOMIC_SEQUENCE"}
    found = {t.category for t in result.tags}
    assert not found & high_categories, f"unexpected high-sensitivity tags: {found & high_categories}"


def test_case_19_record_aggregation(api):
    """记录中同时存在 L3 与 L4 字段时聚合为 L4。"""
    record = {
        "id_card": "110101199001011237",
        "mobile": "13800138000",
        "diagnosis": "B21.1",
    }
    result = api.classify_record(record)
    assert result.final_level == SensitivityLevel.L4
    assert any(t.category == "MEDICAL_ICD10_HIV" for t in result.aggregated_tags)


def test_case_20_table_aggregation(api):
    """表中存在 L3/L5/L4 字段时聚合为 L5。"""
    schema = ["id_card", "brca1_status", "diagnosis"]
    rows = [
        {"id_card": "110101199001011237", "brca1_status": "positive", "diagnosis": "C78.0"},
    ]
    result = api.classify_table(schema, rows)
    assert result.final_level == SensitivityLevel.L5
    categories = {t.category for t in result.aggregated_tags}
    assert "PII_ID_CARD" in categories
    assert "GENOMIC_BRCA_TP53" in categories
    assert "MEDICAL_ICD10_CANCER" in categories


# ---------------------------------------------------------------------------
# 参数治理与覆盖 / Parameter governance and overrides
# ---------------------------------------------------------------------------


def test_parameter_source_request(api):
    """请求参数覆盖 default_level。"""
    result = api.classify_field("foo", "bar", params={"default_level": "L2"})
    assert result.final_level == SensitivityLevel.L2


def test_yaml_profile_override():
    """YAML profile 中的配置可覆盖默认值。"""
    profile = {
        "primitives": {
            "classification": {
                "default_level": "L2",
                "public_field_whitelist": ["open_data"],
            }
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        yaml.safe_dump(profile, f)
        path = f.name
    try:
        api = ClassificationAPI(profile_path=path)
        result = api.classify_field("unknown", "value")
        assert result.final_level == SensitivityLevel.L2

        result2 = api.classify_field("open_data", "x")
        assert any(t.category == "PUBLIC_REPORT" for t in result2.tags)
    finally:
        os.unlink(path)


def test_manual_override():
    """manual_override 可强制字段等级。"""
    api = ClassificationAPI()
    result = api.classify_field(
        "id_card",
        "110101199001011237",
        params={"manual_override": {"id_card": "L1"}},
    )
    assert result.final_level == SensitivityLevel.L1


# ---------------------------------------------------------------------------
# 多格式适配器 / Format adapters
# ---------------------------------------------------------------------------


def test_classify_json_record(api):
    """classify_json 解析字典并按记录分类。"""
    result = api.classify_json('{"id_card": "110101199001011237", "mobile": "13800138000"}')
    assert result.record_result is not None
    assert result.record_result.final_level == SensitivityLevel.L3


def test_classify_json_table(api):
    """classify_json 解析列表并按表分类。"""
    data = [{"id_card": "110101199001011237"}, {"brca1_status": "positive"}]
    result = api.classify_json(data)
    assert result.table_result is not None
    assert result.table_result.final_level == SensitivityLevel.L5


def test_classify_sql_result(api):
    """classify_sql_result 对 SQL 结果集分类。"""
    result_set = [
        {"id_card": "110101199001011237"},
        {"diagnosis": "B21.1"},
    ]
    result = api.classify_sql_result(result_set)
    assert result.table_result is not None
    assert result.table_result.final_level == SensitivityLevel.L4


def test_classify_dataframe(api):
    """classify_dataframe 对 pandas DataFrame 分类（可选依赖）。"""
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"id_card": ["110101199001011237"], "brca1_status": ["positive"]})
    result = api.classify_dataframe(df)
    assert result.table_result is not None
    assert result.table_result.final_level == SensitivityLevel.L5


def test_classify_arrow(api):
    """classify_arrow 对 pyarrow Table 分类（可选依赖）。"""
    pa = pytest.importorskip("pyarrow")
    table = pa.table({"id_card": ["110101199001011237"], "diagnosis": ["B21.1"]})
    result = api.classify_arrow(table)
    assert result.table_result is not None
    assert result.table_result.final_level == SensitivityLevel.L4


# ---------------------------------------------------------------------------
# 输出结构与审计 / Output structure and audit
# ---------------------------------------------------------------------------


def test_field_result_structure(api):
    """字段结果包含规范要求的字段且 confidence 在 [0,1]。"""
    result = api.classify_field("mobile", "13800138000")
    assert result.field_name == "mobile"
    assert result.final_level == SensitivityLevel.L3
    assert 0.0 <= result.confidence <= 1.0
    assert result.engine_layer == EngineLayer.L1_RULE


def test_audit_info(api):
    """ClassificationResult 携带审计信息。"""
    result = api.classify_json('{"mobile": "13800138000"}')
    assert result.audit_info.version == "1.0.0"
    assert result.audit_info.parameter_source in {"default", "profile", "request", "manual"}
    assert result.audit_info.timestamp
