# 数据集级 K-匿名（K-Anonymity）产品设计 PRD

## 1. 概述

本文档定义 `privacy-local-agent` 数据集级 K-匿名模块的产品需求与验收标准。该模块通过泛化准标识符（QI）降低数据重识别风险，确保发布或共享的数据集中每个等价组至少包含 `k` 条记录。

## 2. 设计目标

- 提供 REST `/v1/privacy/k_anonymize/table` 与 gRPC `KAnonymizeTable` 接口。
- 对输入表按准标识符进行 Mondrian 多维分区，确保每个等价组大小 ≥ k。
- 对数值型 QI 输出区间泛化，对分类型 QI 输出取值集合。
- 保留非 QI 敏感字段不变，便于下游分析。
- 同时保留单记录启发式泛化接口，用于轻量场景。

## 3. 功能需求

| ID | 需求 |
|---|---|
| KANO-TABLE-1 | 输入为 `rows`（记录列表）、`qi_cols`（QI 列名）、`k`（匿名阈值）。 |
| KANO-TABLE-2 | 支持数值型 QI 与分类型 QI 的混合。 |
| KANO-TABLE-3 | 算法采用 Mondrian：递归按最大跨度维度中位数分区，直到每组大小 < 2k 或无法再分。 |
| KANO-TABLE-4 | 输出等价组内 QI 泛化结果，当组内某一 QI 取值全部相同时保持原值；非 QI 字段原样保留。 |
| KANO-TABLE-5 | 提供 `max_depth` 参数限制递归深度，防止过泛化。 |
| KANO-TABLE-6 | 当输入记录数 < k 时返回错误，避免输出不满足 K-匿名条件的数据集。 |
| KANO-RECORD-1 | 保留单记录启发式泛化接口，用于轻量场景。 |

## 4. 接口定义

### 4.1 REST

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

### 4.2 gRPC

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
