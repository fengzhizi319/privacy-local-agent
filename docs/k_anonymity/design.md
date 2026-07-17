# 数据集级 K-匿名设计文档

## 1. 概述

本文档定义 `privacy-local-agent` 数据集级 K-匿名模块的算法原理、技术架构与实现细节。该模块通过泛化准标识符（QI）降低数据重识别风险，确保发布或共享的数据集中每个等价组至少包含 `k` 条记录。

## 2. 设计目标

- 提供 REST `/v1/privacy/k_anonymize/table` 与 gRPC `KAnonymizeTable` 接口。
- 采用 Mondrian 多维分区算法对输入表按 QI 进行分区。
- 对数值型 QI 输出区间泛化，对分类型 QI 输出取值集合。
- 保留非 QI 敏感字段不变，便于下游分析。
- 同时保留单记录启发式泛化接口，用于轻量场景。
- 支持 pandas / SecretFlow DataFrame 输入，输出本地 pandas 副本。
- 暴露 `privacy_kano_operations_total` 模块级指标。

## 3. 算法原理

### 3.1 K-匿名定义

给定数据集 `D` 与准标识符集合 `QI`，若 `D` 中任意一条记录在 `QI` 属性上的取值组合都至少与 `k-1` 条其他记录相同，则称 `D` 满足 K-匿名。等价组是具有相同 QI 取值组合的记录集合。

### 3.2 准标识符与敏感属性

- **准标识符（QI）**：可与其他数据源结合用于识别个体的属性，如年龄、邮编、性别。
- **敏感属性**：如疾病，通常不作为 QI 泛化，而是保留原值用于分析。

### 3.3 Mondrian 多维分区算法

Mondrian 把整张表看作多维空间，每条记录是一个点，每个 QI 是一个维度：

1. 计算每个 QI 维度在当前分区内的跨度（数值型为 `max - min`，分类型为不同取值数减 1）。
2. 选择跨度最大的维度作为划分维度。
3. 按该维度排序记录，并在尽量接近中位数的位置切分。
4. 切分时必须保证左右两侧记录数都不少于 `k`，否则切分无效。
5. 当无法继续安全切分，或递归深度达到上限时，对当前分区做统一泛化。

Mondrian 的本质是“先切分、后泛化”：通过尽可能细的分区缩小每个等价组范围，在满足 K-匿名的前提下降低信息损失。

### 3.4 泛化规则

| QI 类型 | 泛化方式 | 示例 |
|---|---|---|
| 数值型 | 区间泛化 | `age=25` → `age=[25-30]` |
| 分类型 | 取值集合 | `gender=M` → `gender={M,F}` |

当等价组内某一 QI 取值全部相同时，保持原值以减少信息损失。非 QI 字段原样保留。

### 3.5 信息损失与深度控制

泛化会引入信息损失。`max_depth` 参数限制递归深度，防止对高度偏斜数据过度泛化。业务方可根据数据用途平衡隐私强度与数据可用性。

## 4. 算法流程

```text
mondrian(records, qi_cols, k, depth):
    if len(records) < 2*k or depth <= 0:
        return generalize(records, qi_cols)

    dim = choose_dimension(records, qi_cols)
    split_idx = find_median_split(records, dim, k)

    if split_idx is None:
        return generalize(records, qi_cols)

    left = records[:split_idx]
    right = records[split_idx:]
    return mondrian(left, ...) + mondrian(right, ...)
```

## 5. 模块设计

`privacy_local_agent/privacy/kano_table.py`：

| 函数 | 作用 |
|---|---|
| `k_anonymize_table(rows, qi_cols, k, max_depth)` | 记录列表入口函数 |
| `k_anonymize_dataframe(df, qi_cols, k, max_depth)` | DataFrame 入口函数 |
| `_choose_dimension(records, qi_cols)` | 选择分割维度 |
| `_median_split(records, dim, k)` | 中位数分割 |
| `_generalize(records, qi_cols)` | 等价组泛化 |

`privacy_local_agent/privacy/data_adapters.py` 提供 `to_records` / `from_records`，将 pandas / SecretFlow DataFrame 与记录列表互转。

`PrivacyService` 新增 `k_anonymize_table` / `k_anonymize_dataframe` 方法；`main.py` / `grpc_server.py` 暴露 `KAnonymizeTable` / `KAnonymizeDataFrame` 接口。

### 5.1 指标

`privacy_kano_operations_total{operation}` 在以下入口递增：

| `operation` | 触发接口 |
|---|---|
| `record` | `anonymize_record` |
| `table` | `k_anonymize_table` |
| `dataframe` | `k_anonymize_dataframe` |

## 6. 复杂度分析

每次排序 `O(n log n)`，递归深度 `O(log(n/k))`，总复杂度约 `O(n log² n)`，适合中小规模数据集。

## 7. 接口定义

### 7.1 REST

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

### 7.2 gRPC

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

## 8. 测试策略

- Mondrian 实现单元测试，覆盖数值/分类 QI、等价组大小 ≥ k、敏感字段不变。
- DataFrame 输入/输出测试。
- `privacy_kano_operations_total` 指标测试。
- REST/gRPC 接口测试（含 `KAnonymizeDataFrame`）。
- 边界条件测试：记录数 < k、单值 QI、max_depth=0。
