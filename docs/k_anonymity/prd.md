# 数据集级 K-匿名（K-Anonymity）PRD

## 1. 背景

当前 `/v1/privacy/k_anonymize/record` 仅支持单记录启发式泛化，无法保证整张表的 K-匿名性。攻击者仍可能通过多条记录的组合重识别个体。因此需要新增数据集级 K-匿名接口，采用 Mondrian 多维分区算法。

## 2. 目标

- 提供 REST `/v1/privacy/k_anonymize/table` 与 gRPC `KAnonymizeTable`。
- 对输入表按准标识符（QI）进行 Mondrian 分区，确保每个等价组大小 ≥ k。
- 对数值型 QI 输出区间泛化（如 `[25-30]`），对分类型 QI 输出取值集合。
- 保留非 QI 敏感字段不变，便于下游分析。

## 3. 功能需求

| ID | 需求 |
|---|---|
| KANO-TABLE-1 | 输入为 `rows`（记录列表）、`qi_cols`（QI 列名）、`k`（匿名阈值）。 |
| KANO-TABLE-2 | 支持数值型 QI 与分类型 QI 的混合。 |
| KANO-TABLE-3 | 算法采用 Mondrian：递归按最大跨度维度中位数分区，直到每组大小 < 2k 或无法再分。 |
| KANO-TABLE-4 | 输出等价组内 QI 泛化结果，当组内某一 QI 取值全部相同时，应保持原值以减少信息损失；非 QI 字段原样保留。 |
| KANO-TABLE-5 | 提供 `max_depth` 参数限制递归深度，防止极端数据导致过泛化。 |
| KANO-TABLE-6 | 当输入记录数 < k 时返回错误，避免无法 anonymize。 |

## 4. 接口定义

### REST

```http
POST /v1/privacy/k_anonymize/table
Content-Type: application/json

{
  "rows": [
    {"age": 25, "zipcode": "100001", "gender": "M", "disease": "A"},
    {"age": 26, "zipcode": "100002", "gender": "M", "disease": "B"}
  ],
  "qi_cols": ["age", "zipcode", "gender"],
  "k": 2,
  "max_depth": 10
}
```

### gRPC

```protobuf
rpc KAnonymizeTable (KAnonymizeTableRequest) returns (KAnonymizeTableResponse);

message KAnonymizeTableRequest {
  repeated RecordEntry rows = 1;
  repeated string qi_cols = 2;
  int32 k = 3;
  int32 max_depth = 4;
}

message KAnonymizeTableResponse {
  repeated RecordEntry rows = 1;
}
```

## 5. 验收标准

- [ ] Mondrian 实现通过单元测试，覆盖数值/分类 QI、等价组大小 ≥ k、敏感字段不变。
- [ ] REST/gRPC 接口测试通过。
- [ ] 文档（PRD/design/ops）与 `AGENTS.md` 已更新。
