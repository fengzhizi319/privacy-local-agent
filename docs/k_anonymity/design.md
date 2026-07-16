# 数据集级 K-匿名设计文档

## 1. 算法选择

采用 **Mondrian** 多维分区算法，原因：
- 实现简单，不需要预定义泛化层次。
- 对数值型 QI 可生成紧凑区间，信息损失可控。
- 对分类型 QI 可生成取值集合，易于解释。

## 2. 算法流程

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

### 2.1 维度选择

- 对数值型 QI：计算该列 `max - min`（跨度）。
- 对分类型 QI：计算该列不同取值数量减 1。
- 选择跨度最大的维度进行分割。

### 2.2 中位数分割

- 按选定维度排序记录。
- 找到中位数位置，确保左右两部分都至少包含 k 条记录。
- 若不存在合法分割点，返回当前组泛化结果。

### 2.3 泛化

- 数值型：若等价组内各记录对应列取值相同（即 `min == max`），则保持原值（保留数值类型）；否则泛化替换为 `[min-max]` 字符串。
- 分类型：若等价组内各记录对应列唯一值只有 1 个，则保持原值；否则泛化替换为 `{val1,val2,...}` 字符串。
- 非 QI 字段：保持不变。

## 3. 模块结构

新增 `privacy_local_agent/privacy/kano_table.py`：

| 函数 | 作用 |
|---|---|
| `k_anonymize_table(rows, qi_cols, k, max_depth)` | 入口函数 |
| `_choose_dimension(records, qi_cols)` | 选择分割维度 |
| `_median_split(records, dim, k)` | 中位数分割 |
| `_generalize(records, qi_cols)` | 等价组泛化 |

`PrivacyService` 新增 `k_anonymize_table` 方法；`main.py` / `grpc_server.py` 暴露接口。

## 4. 复杂度

- 每次排序 `O(n log n)`，递归深度 `O(log(n/k))`，总复杂度约 `O(n log^2 n)`，适合中小规模数据集。
