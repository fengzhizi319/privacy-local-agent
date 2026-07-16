# Data Classification Testing / 数据分类测试文档

## 1. 测试目标 / Test Goals

- 验证规则引擎覆盖规范中的 20 个通用测试用例。
- 验证参数治理、YAML profile、人工覆盖。
- 验证多格式适配器（JSON、pandas、Arrow、SQL 结果集）。
- 验证 REST/gRPC 接口与本地 SDK 输出一致。
- 确保不破坏既有接口的测试。

## 2. 测试结构 / Test Structure

```text
tests/
├── test_classification.py         # 分类原语单元测试
├── test_classification_rest.py   # 数据分类 REST 接口测试
└── test_classification_grpc.py   # 数据分类 gRPC 接口测试
```

## 3. 运行测试 / Running Tests

```bash
cd /home/charles/code/sfwork/privacy-local-agent
source .venv/bin/activate
PYTHONPATH=. pytest tests -q
```

pandas / pyarrow 相关测试在未安装对应包时自动跳过。

## 4. 20 个通用测试用例 / Common Spec Cases

| # | 字段 | 值 | 期望等级 | 期望类别 |
|---|------|-----|----------|----------|
| 1 | id_card | 110101199001011237 | L3 | PII_ID_CARD |
| 2 | id_card | 110101199001011234 | 回退 | 无 PII_ID_CARD |
| 3 | mobile | 13800138000 | L3 | PII_MOBILE |
| 4 | mobile | 12800138000 | 回退 | 无 PII_MOBILE |
| 5 | medical_card | 123456789 | L3 | PII_MEDICAL_CARD |
| 6 | diagnosis | B21.1 | L4 | MEDICAL_ICD10_HIV |
| 7 | diagnosis | F25 | L4 | MEDICAL_ICD10_PSYCHIATRIC |
| 8 | diagnosis | C78.0 | L4 | MEDICAL_ICD10_CANCER |
| 9 | diagnosis | J18.9 | L3 | MEDICAL_ICD10_GENERAL |
| 10 | brca1_status | positive | L5 | GENOMIC_BRCA_TP53 |
| 11 | rs_number | rs12345 | L5 | GENOMIC_VARIANT |
| 12 | file_content | BAM\x01... | L5 | GENOMIC_BAM |
| 13 | file_content | ##fileformat=VCFv4.2 | L5 | GENOMIC_VCF |
| 14 | file_content | @SQ SN:chr1 LN:1000 | L5 | GENOMIC_BAM |
| 15 | sequence | ATCG... (>=50) | L5 | GENOMIC_SEQUENCE |
| 16 | public_report | 2023 annual summary | L1 | PUBLIC_REPORT |
| 17 | turnover_rate | 0.85 | L2 | OPERATIONAL_STAT |
| 18 | name | Alice | 回退 | 无高敏感标签 |
| 19 | record | id_card + mobile + B21.1 | L4 | 聚合 |
| 20 | table | id_card + brca1 + diagnosis | L5 | 聚合 |

> 注：规范用例 1 的原始值 `11010119900101123X` 校验和不通过；测试使用有效身份证 `110101199001011237`。

## 5. 扩展测试建议 / Future Tests

- 接入真实 Small-NER/LLM 后补充升级/置信度阈值测试。
- 大表性能测试（采样、并发）。
- SecretFlow 组件模式端到端测试。
