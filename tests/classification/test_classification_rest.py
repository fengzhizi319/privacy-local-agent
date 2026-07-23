"""数据分类 REST API 接口测试集。

使用 FastAPI TestClient 对数据分类端点进行集成测试，
覆盖字段级、记录级、表级分类接口。

Integration tests for data classification REST endpoints using FastAPI TestClient.
Covers field, record and table classification endpoints.
"""

from fastapi.testclient import TestClient

from privacy_local_agent.main import app

# 复用同一个 TestClient 实例，避免重复创建应用
client = TestClient(app)


def test_classify_field():
    """测试单字段分类接口，验证身份证号命中 PII_ID_CARD 且等级为 L3。"""
    response = client.post(
        "/v1/privacy/classify/field",
        json={"field_name": "id_card", "value": "110101199001011237", "params": {}},
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["finalLevel"] == "L3"
    assert any(t["category"] == "PII_ID_CARD" for t in result["tags"])


def test_classify_record():
    """测试单条记录分类接口，验证聚合等级取最高。"""
    response = client.post(
        "/v1/privacy/classify/record",
        json={
            "record": {
                "id_card": "110101199001011237",
                "mobile": "13800138000",
                "diagnosis": "B21.1",
            },
            "params": {},
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["finalLevel"] == "L4"


def test_classify_table():
    """测试整张表分类接口，验证存在 L5 字段时表级最终等级为 L5。"""
    response = client.post(
        "/v1/privacy/classify/table",
        json={
            "schema": ["id_card", "brca1_status", "diagnosis"],
            "rows": [
                {
                    "id_card": "110101199001011237",
                    "brca1_status": "positive",
                    "diagnosis": "C78.0",
                }
            ],
            "params": {},
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["finalLevel"] == "L5"
    assert result["schema"] == ["id_card", "brca1_status", "diagnosis"]
