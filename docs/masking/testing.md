# 数据脱敏模块测试文档

## 1. 概述

本文档定义 `privacy_local_agent/privacy/masking.py` 的测试策略、测试范围与可执行示例。

## 2. 测试目标

- 验证各字段类型脱敏规则正确。
- 验证整记录脱敏不修改原始记录。
- 验证批量字段脱敏长度校验。
- 验证 DataFrame 脱敏列选择与默认行为。
- 验证 HMAC 哈希与截断行为。
- 验证 `privacy_masking_operations_total` 指标递增。
- 验证 REST/gRPC 接口参数透传。

## 3. 单元测试策略

### 3.1 字段脱敏测试

```python
from privacy_local_agent.privacy.masking import mask_value


def test_mask_mobile():
    assert mask_value("mobile", "13812345678") == "138****5678"


def test_mask_id_card():
    assert mask_value("id_card", "110101199001011234") == "110101********1234"


def test_mask_name():
    assert mask_value("name", "张三丰") == "张**丰"
```

### 3.2 整记录脱敏测试

```python
from privacy_local_agent.privacy.masking import mask_record


def test_mask_record():
    record = {"mobile": "13812345678", "age": 30}
    result = mask_record(record)
    assert result["mobile"] == "138****5678"
    assert record["mobile"] == "13812345678"  # 原记录不变
```

### 3.3 DataFrame 脱敏测试

```python
import pandas as pd
from privacy_local_agent.privacy.masking import mask_dataframe


def test_mask_dataframe():
    df = pd.DataFrame({"mobile": ["13812345678"], "name": ["张三"]})
    result = mask_dataframe(df)
    assert result["mobile"].tolist() == ["138****5678"]
    assert result["name"].tolist() == ["张*"]
```

### 3.4 指标测试

```python
from prometheus_client import REGISTRY
from privacy_local_agent.privacy.masking import mask_value


def test_masking_metric():
    before = REGISTRY.get_sample_value(
        "privacy_masking_operations_total", {"operation": "mask_value"}
    ) or 0.0
    mask_value("mobile", "13812345678")
    after = REGISTRY.get_sample_value(
        "privacy_masking_operations_total", {"operation": "mask_value"}
    )
    assert after == before + 1
```

## 4. 测试执行命令

```bash
PYTHONPATH=. pytest tests/test_masking.py -v
```

## 5. 验收检查清单

- [x] 各字段类型脱敏规则测试通过。
- [x] 整记录脱敏不修改原记录。
- [x] 批量字段脱敏长度校验正确。
- [x] DataFrame 脱敏测试通过。
- [x] HMAC 哈希与截断测试通过。
- [x] `privacy_masking_operations_total` 指标测试通过。
- [x] REST/gRPC 接口测试通过。
