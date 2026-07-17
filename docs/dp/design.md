# 差分隐私算法设计文档

## 1. 概述

本文档定义 `privacy-local-agent` 差分隐私（DP）模块的算法原理、技术架构与实现细节。DP 模块为 count、sum、mean 等聚合查询提供校准噪声注入能力，并通过 BudgetAccountant 追踪累计隐私预算消耗。

## 2. 设计目标

- 实现 Laplace 机制，提供纯 $\varepsilon$-DP 保证。
- 实现 Gaussian 机制，提供 $(\varepsilon, \delta)$-DP 保证。
- 通过显式 clipping 控制 sum/mean 的敏感度，使敏感度在数据观测前即可确定。
- 提供 BudgetAccountant，支持内存与 SQLite 两种预算存储后端。
- 实现 NumPy 向量化 clipping，提升大规模数据裁剪效率。
- 提供 noisify 接口，对已由外部引擎聚合好的中间结果直接加噪。
- 提供 chunked 流式聚合接口，避免一次性加载全部数据到内存。
- 提供统一数据适配器，支持 pandas / NumPy / SecretFlow 等输入格式。
- 暴露 `privacy_traffic_bytes_total` 指标，用于监控 REST/gRPC 流量。
- 保证 REST/gRPC 接口参数语义一致，便于审计与集成。

## 3. 算法原理

### 3.1 差分隐私定义

对任意两个仅相差一条记录的相邻数据集 `D` 和 `D'`，机制 `M` 对任意输出集合 `S` 满足：

- **$(\varepsilon, \delta)$-DP**：$P[M(D) \in S] \leq e^\varepsilon \cdot P[M(D') \in S] + \delta$
- **$\varepsilon$-DP**（$\delta = 0$）：$P[M(D) \in S] \leq e^\varepsilon \cdot P[M(D') \in S]$

> **差分隐私一定是某个算法（运算、查询、机制）的属性，而不是数据本身的属性。** 没有运算，敏感度就无从定义，差分隐私的数学保证也就失去了锚点。

#### 3.1.1 为什么差分隐私必须绑定运算

##### 定义本身就需要一个机制 $M$

差分隐私的数学定义：

$$\Pr[M(D) \in S] \leq e^\varepsilon \cdot \Pr[M(D') \in S]$$

这里的 $M$ 就是一个**随机化算法**（randomized mechanism）。它接受数据集 $D$ 作为输入，输出某个结果。没有 $M$，这个不等式就没有主体。

##### 敏感度是"函数的敏感度"，不是"数据的敏感度"

敏感度 $\Delta f$ 的下标是 $f$，而 $f$ 是一个**确定性查询函数**：

$$\Delta f = \max_{D \sim D'} |f(D) - f(D')|$$

- 没有 $f$，就没有 $\Delta f$。
- 同一个数据集，不同的查询有不同的敏感度：
  - 查询"人数"：$\Delta f = 1$
  - 查询"总工资"（无截断）：$\Delta f = \infty$
  - 查询"平均工资"：需要通过总和和人数间接计算

**数据本身没有敏感度，是运算赋予了数据敏感度。**

#### 3.1.2 边界情况：单条数据加噪声算不算"运算"

如果我只是对单条数据加噪声然后发布，比如"年龄 25 + Lap(1) = 26.3"，这里没有复杂运算吧？

**实际上，这仍然是一个运算**——它是**恒等查询（Identity Query）**：

$$f(x) = x$$

然后对结果加噪声：

$$M(x) = x + \text{Lap}\left(\frac{\Delta f}{\varepsilon}\right)$$

恒等查询的敏感度取决于数据域：
- 如果年龄范围是 $[0, 150]$，则 $\Delta f = 150$
- 如果年龄范围是 $[20, 30]$，则 $\Delta f = 10$

**所以即使是最简单的"直接发布原始数据"，在差分隐私框架下也必须被建模为一个运算（恒等查询），并计算其敏感度。**

#### 3.1.3 没有运算的"噪声"只是噪声，不是差分隐私

| | 给数据加噪声 | 差分隐私 |
|--|------------|---------|
| **主体** | 可以是对原始数据的操作 | 是**算法**的数学属性 |
| **需要敏感度吗** | 不需要 | 必须需要 |
| **有隐私保证吗** | 没有数学保证 | 有严格的 $\varepsilon$-DP 保证 |
| **例子** | 把年龄 25 改成 26.3 | "恒等查询 + Laplace 噪声"满足 $\varepsilon$-DP |

**很多人误以为"给数据加噪声 = 差分隐私"，这是错误的。** 噪声只是实现差分隐私的一种技术手段，差分隐私是**算法满足的一个数学条件**。

#### 3.1.4 对比：数据层面的隐私 vs 算法层面的隐私

这也解释了差分隐私与其他隐私方法的根本区别：

| 方法 | 层面 | 是否依赖运算 |
|------|------|------------|
| **K-匿名** | 数据层面 | 否，通过泛化/抑制数据本身实现 |
| **数据脱敏** | 数据层面 | 否，直接替换/删除敏感字段 |
| **差分隐私** | **算法层面** | **是，必须绑定具体运算** |

- K-匿名：把年龄"25"泛化为"20-30"，数据本身被修改了，不需要考虑后续做什么查询。
- 差分隐私：数据可以保持原样，但**任何接触数据的算法**都必须满足 DP 条件。

#### 3.1.5 实际意义：为什么这个区分很重要

##### 同一个数据集，不同查询需要不同噪声

> 数据集：1000 人的医疗记录

| 查询 | 敏感度 | 噪声尺度（$\varepsilon=1$） |
|------|--------|----------------|
| "患者人数" | 1 | Lap(1) |
| "总医疗费"（截断到10万） | 100000 | Lap(100000) |
| "平均年龄" | 通过组合计算 | 更复杂 |

**你不能先给数据"统一加一层 DP 噪声"，然后做任何查询。** 噪声必须根据**具体查询的敏感度**来定制。

##### 隐私预算是针对"运算序列"的

如果你先发布"计数"（消耗 $\varepsilon=1$），再发布"求和"（消耗 $\varepsilon=1$），总消耗是 $\varepsilon=2$。这个预算管理是**围绕运算序列**展开的，不是围绕数据本身。

##### 本地差分隐私中，"运算"在用户端

本地 DP 中，每个用户的数据在**离开设备前**就经过一个随机化算法（如随机响应、RAPPOR）。这个算法就是**绑定在单条数据上的运算**，它有自己的敏感度定义（通常是基于输出域的）。

#### 3.1.6 一句话总结

> **差分隐私不是数据的"属性标签"，而是算法的"行为证书"。没有运算（查询、机制、算法），敏感度就失去了定义对象，差分隐私的数学不等式就没有了主体。给数据加噪声只是操作，只有当这个操作被证明满足 $\Pr[M(D) \in S] \leq e^\varepsilon \cdot \Pr[M(D') \in S]$ 时，它才成为差分隐私。**

#### 3.1.7 为什么不能直接给每条原始记录加噪声

一个常见误区是：既然差分隐私要加噪声，那我能不能把数据集中的**每条记录**都加上一点噪声，然后直接发布这条"脱敏"后的数据集？例如：

```text
patients        = [1, 0, 1, 1, 0, 1, 0, 0, 1, 1]
patients_dp ?   = [1.1, 0.2, 1.3, 1.05, -0.1, 0.9, 0.15, -0.05, 1.2, 0.8]
```

**答案是否定的**：在**中心式差分隐私（Central DP）**模型下，直接把噪声加到每条原始记录上，并不能提供差分隐私保证。

##### 原因一：敏感度分析对象错误

中心式 DP 的噪声是加在**查询结果**上的，噪声尺度由查询的敏感度决定：

- count 查询：敏感度 = 1，噪声尺度 = $1/\varepsilon$
- sum 查询（截断到 $[0, C]$）：敏感度 = $C$，噪声尺度 = $C/\varepsilon$

如果你给每条记录加噪声，那么发布的不再是"count 的结果"，而是"10 条带噪声的记录"。攻击者可以对同一个体进行多次观测、取平均，从而把噪声抵消掉，恢复原始值。此时你保护的不再是"查询结果"，而是"单条记录"，需要的噪声尺度会大得多。

##### 原因二：这不再是中心式 DP，而是另一种模型

给单条记录加噪声属于以下两种情形之一：

1. **本地差分隐私（Local DP）**：每个用户在**数据离开自己的设备前**就通过约定好的随机化算法（如随机响应、RAPPOR）对数据进行扰动。这是一种与中心式 DP 完全不同的信任模型，噪声通常远大于中心式 DP。
2. **启发式数据扰动**：只是"给数据加点噪声"，没有严格的敏感度分析和数学证明，不能给出 $(\varepsilon, \delta)$-DP 保证。

`DPApi` 实现的是**中心式 DP**，它的职责是保护**聚合查询结果**的发布过程，而不是生成带噪声的微观记录。

##### 原因三：噪声会摧毁单条记录的可用性

假设要对单条记录提供中心式 DP 级别的保护，需要把"增加/删除一个人"的影响隐藏起来。对于单条记录本身，这相当于要求：

$$\Pr[M(D_i) \in S] \leq e^\varepsilon \cdot \Pr[M(D_i') \in S]$$

其中 $D_i$ 和 $D_i'$ 是两条完全不同的记录（例如 0 和 1）。要让这两条记录无法区分，噪声尺度必须覆盖整个取值范围。对于二值数据，噪声尺度约为 $1/\varepsilon$；对于年龄 $[0, 150]$，噪声尺度约为 $150/\varepsilon$。这样加噪后的单条记录将完全失去意义。

##### 正确的做法

- **中心式 DP**：只发布带噪声的**聚合结果**（count、sum、mean、直方图等），如 `DPApi.count()` / `sum()` / `mean()` 所做。
- **需要微观数据时**：使用专门的**合成数据生成**算法（如 DP-GAN、PrivBayes），这些算法本身也是一个满足 DP 的"运算"，而不是简单给每条记录加噪声。
- **本地 DP 场景**：在数据采集端部署本地随机化机制，与 `DPApi` 的中心式模型区分开。

#### 3.1.8 数据表 / CSV 场景下的正确使用方式

实际业务中常见的输入是一张数据表（如 CSV、数据库表、Pandas DataFrame），其中包含多个字段、多条记录。需要明确的是：**`DPApi` 的 `values` 参数不是整张表，而是单个聚合查询所针对的某一列数据。**

##### 为什么不能直接传入整张表

中心式 DP 的噪声尺度由**具体查询的敏感度**决定，而敏感度取决于查询函数 $f$。整张表本身没有唯一的敏感度：

| 查询 | 输入 | 敏感度 |
|---|---|---|
| 员工人数 | `employee_id` 列 | 1 |
| 总工资 | `salary` 列（clip 到 $[0, C]$） | $C$ |
| 平均年龄 | `age` 列（clip 到 $[0, 150]$） | 通过 count + sum 组合 |
| 部门人数分布 | `department` 列 | 每个桶 1 |

如果允许一次性传入整张表而不指定查询，系统无法确定该为哪种查询加多少噪声，也就无法给出严格的 DP 保证。

##### CSV / 数据表的正确使用流程

1. 按业务需求确定要发布的聚合查询（count / sum / mean / histogram）。
2. 从数据表中提取该查询对应的**单列**作为 `values`。
3. 根据该列的业务取值范围设定 `clip_lower` / `clip_upper`（sum / mean 必填）。
4. 调用对应接口，消耗对应命名空间的隐私预算。

**示例**：对 `data.csv` 的 `salary` 列做差分隐私求和。

```python
import pandas as pd
from privacy_local_agent.privacy.dp import DPApi

df = pd.read_csv("data.csv")
api = DPApi(namespace="hr_dataset")

# 方式 1：手动提取单列
result = api.sum(
    values=df["salary"].tolist(),
    epsilon=1.0,
    delta=1e-6,
    mechanism="gaussian",
    clip_lower=0.0,
    clip_upper=100000.0,
)

# 方式 2：直接传入 DataFrame，使用 column 参数指定目标列
result = api.sum(
    values=df,
    column="salary",
    epsilon=1.0,
    delta=1e-6,
    mechanism="gaussian",
    clip_lower=0.0,
    clip_upper=100000.0,
)
```

SecretFlow 联邦 DataFrame 同样支持直接传入（需安装 secretflow）：

```python
from privacy_local_agent.privacy.dp import DPApi

api = DPApi(namespace="hr_dataset")

# VDataFrame：列分布在不同参与方，自动定位包含 salary 的 partition
result = api.sum(
    values=vdf,
    column="salary",
    epsilon=1.0,
    delta=1e-6,
    mechanism="gaussian",
    clip_lower=0.0,
    clip_upper=100000.0,
)

# HDataFrame：样本水平分割，需指定参与方
result = api.sum(
    values=hdf,
    column="salary",
    party="alice",
    epsilon=1.0,
    delta=1e-6,
    mechanism="gaussian",
    clip_lower=0.0,
    clip_upper=100000.0,
)
```

REST 侧同理：`values` 字段只包含目标列的样本值；如需指定列/参与方，可在 `params` 中传入 `column` 和 `party`。

##### 多字段 / 多列 / 分组分析怎么办

如果需要同时分析多个字段，应该拆分为多个独立的 DP 查询，并分别消耗隐私预算。例如：

- 先对 `salary` 列做 sum（消耗 $\varepsilon_1$）
- 再对 `age` 列做 mean（消耗 $\varepsilon_2$）
- 再对 `department` 列做 histogram（消耗 $\varepsilon_3$）

总隐私消耗按组合定理累加：$\varepsilon_{\text{total}} = \varepsilon_1 + \varepsilon_2 + \varepsilon_3$。

**分组聚合**（如按部门求平均工资）属于更复杂的查询，需要额外设计：

- 每个分组的计数值是否足够大（避免 noisy_count 接近 0 导致结果爆炸）
- 是否对分组数量做限制（防止输出维度泄漏信息）
- 如何为每个分组分配预算

这些超出了 `DPApi.count/sum/mean` 的职责范围，应作为独立功能（如 `dp_histogram`、`dp_groupby`）另行设计。

##### 如果需要发布“脱敏后的整张表”

这不是中心式 DP 聚合查询能解决的问题。应使用专门的**差分隐私合成数据生成**算法，例如：

- **DP-GAN**：在训练生成模型时注入 DP 噪声，生成统计特征相似的合成记录。
- **PrivBayes**：基于贝叶斯网络建模字段间关系，逐维度加噪声后采样合成数据。

这些算法本身也是满足 DP 的"运算"，需要独立的模块、接口和隐私预算管理，不能通过扩展 `DPApi` 的 `values` 参数来实现。

##### 一句话总结

> **对数据表做差分隐私，本质是对数据表上的某个具体聚合查询做差分隐私。`DPApi` 一次只处理一个查询对应的一列数据；多列、多查询、分组聚合、合成数据发布都是更高层的能力，需要单独设计，不能混为一谈。**

### 3.2 敏感度

敏感度 $\Delta f$ 是差分隐私中**决定噪声大小的唯一核心参数**。它回答了一个问题：

> **如果数据集中只改变一个人的记录（增加或删除），查询结果最多能变多少？**

这个"最多"的变化量，就是敏感度。噪声量 = 敏感度 / $\varepsilon$，所以 **敏感度越大，需要加的噪声就越大**。

#### 3.2.1 数学定义

对于两个**邻接数据集** $D$ 和 $D'$（只相差一条记录）：

$$\Delta f = \max_{D \sim D'} |f(D) - f(D')|$$

根据使用的机制不同，敏感度分为：

- **L1 敏感度**：$\Delta_1 f = \max_{D \sim D'} \|f(D) - f(D')\|_1$，用于 Laplace 机制。
- **L2 敏感度**：$\Delta_2 f = \max_{D \sim D'} \|f(D) - f(D')\|_2$，用于 Gaussian 机制。

#### 3.2.2 各查询敏感度

| 查询 | L1 敏感度 | L2 敏感度 |
|---|---|---|
| count | 1 | 1 |
| sum | $\text{clip\_upper} - \text{clip\_lower}$ | $\text{clip\_upper} - \text{clip\_lower}$ |
| mean | $\frac{\text{clip\_upper} - \text{clip\_lower}}{n}$ | $\frac{\text{clip\_upper} - \text{clip\_lower}}{n}$ |

sum/mean 在计算敏感度前，先将每条记录裁剪到 `[clip_lower, clip_upper]` 区间；敏感度由 clip 区间决定，不依赖于输入数据分布。

#### 3.2.3 不同查询的敏感度计算

##### 场景1：计数查询（Count）

**查询**：数据集中有多少人？

- 增加/删除一个人 → 计数变化 **1**
- **敏感度 $\Delta f = 1$**

**例子**：某学校有 3000 名学生，查询"戴眼镜的学生人数"。
- 无论哪个学生加入或离开，计数最多变化 1
- 若 $\varepsilon = 1$，噪声尺度 $b = 1/1 = 1$，发布结果 $=$ 真实值 $\pm$ 约 $1$

##### 场景2：求和查询（Sum）—— 无截断时

**查询**：所有学生的身高总和是多少？

- 如果一个人的身高可以是任意值（0 到 $\infty$），增加一个人，总和变化可能是**无穷大**
- **敏感度 $\Delta f = \infty$**

**这意味着什么**：不加处理的话，求和查询理论上无法用 Laplace 机制做差分隐私，因为需要加无限大的噪声。

##### 场景3：求和查询（Sum）—— 有截断（Clipping）时

**处理**：先对数据做**截断（Clipping）**，限制每个值的范围。

假设把身高限制在 $[150, 200]$ cm：
- 最大值 200，最小值 150
- 增加/删除一个人，总和最多变化 $200 - 150 = 50$？不对。

**正确计算**：增加一个人最多加 200，删除一个人最多减 150（或反过来）。实际上，对于**有界数据** $[a, b]$：
- **敏感度 $\Delta f = \max(|a|, |b|)$**，或者更严格地，$\Delta f = b - a$（取决于邻接定义）

**常用简化**：对于非负数据截断到 $[0, C]$，$\Delta f = C$。

**例子**：某医院计算患者总医疗费用，先把每个人的费用截断到 $[0, 100000]$ 元。
- 敏感度 $\Delta f = 100000$
- 若 $\varepsilon = 1$，噪声尺度 $b = 100000$，发布结果 $=$ 真实总和 $\pm$ 约 $10$ 万

**代价**：截断会引入**偏差**——超过 10 万的费用被压成 10 万，总和会偏低。需要在隐私和精度之间权衡。

##### 场景4：均值查询（Mean）

均值 = 总和 / 计数。不能直接对均值加噪声，因为均值查询的敏感度分析复杂。

**正确做法**：分别发布带噪声的**总和**和**计数**，然后让使用者自己相除。

- 噪声总和：$S_{noisy} = \sum x_i + \text{Lap}(\Delta_{sum}/\varepsilon_1)$
- 噪声计数：$N_{noisy} = n + \text{Lap}(1/\varepsilon_2)$
- 用户计算：$\text{Mean}_{noisy} = S_{noisy} / N_{noisy}$

**注意**：这不再满足纯 $\varepsilon$-DP，而是**组合隐私**（$\varepsilon_1 + \varepsilon_2$）。且当 $N_{noisy}$ 接近 0 时，结果会爆炸，需要做**后处理**（如丢弃计数过小的结果）。

##### 场景5：直方图查询（Histogram）

**查询**：把年龄分成区间，统计每个区间的人数。

- 增加/删除一个人，只会影响**一个区间**的计数（他所在的年龄组）
- 那个区间的计数变化 **1**，其他区间变化 0
- **每个区间的敏感度 $\Delta f = 1$**

**关键**：如果同时发布 10 个区间的计数，总敏感度是多少？

- **基本组合**：10 个区间各加噪声，每个 $\varepsilon = 1$，总隐私消耗 $= 10$
- **更优做法**：利用直方图的特殊结构。因为一个人只贡献给一个区间，10 个区间的**联合敏感度**（L1 敏感度）= 1（所有区间的变化量绝对值之和 = 1）
- 因此可以给整个直方图加一组噪声，总消耗可以控制在 $\varepsilon = 1$

**例子**：发布某城市人口年龄分布（0-10岁, 11-20岁, ..., 91-100岁，共 10 个区间）。
- 每个区间真实计数：{120, 150, 200, ...}
- 每个区间加噪声 $\text{Lap}(1/\varepsilon)$
- 发布带噪声的直方图

##### 场景6：最大值/最小值查询（Max/Min）

**查询**：数据集中的最高分数是多少？

- 增加一个人，他可能带来一个更高的分数
- 敏感度取决于数据域。如果分数范围是 $[0, 100]$：
  - 删除最高分的人，次高分变成新的 max，变化可能很小
  - 但差分隐私要求考虑**最坏情况**：增加一个人，他把 max 从 0 推到 100
  - **敏感度 $\Delta f = 100$**

**问题**：Max/Min 查询对噪声极其敏感。加 $\text{Lap}(100/\varepsilon)$ 的噪声后，结果几乎不可信。

**替代方案**：使用**指数机制（Exponential Mechanism）**而非 Laplace 机制，或者发布**分位数**而非精确最值。

##### 场景7：线性回归系数

**查询**：用身高预测体重，求回归系数 $w$。

- 增加/删除一个人，回归系数的变化量取决于数据分布
- 敏感度没有闭式解，通常需要**经验估计**或**使用 DP-SGD 等迭代算法**

**DP-SGD**（深度学习中常用的差分隐私优化）：
- 每次迭代，先计算梯度，然后对梯度做**裁剪**（限制 L2 范数上限为 C）
- 裁剪后的梯度敏感度 = C
- 加噪声：$\text{Gaussian}(C \cdot \sigma)$（这里用高斯机制，因为 DP-SGD 通常用 $(\varepsilon, \delta)$-DP）
- 通过**矩会计（Moments Accountant）**追踪累积隐私消耗

##### 场景8：发布整个数据集（合成数据）

**查询**：生成一个与原始数据集统计特征相似的合成数据集。

- 每个合成记录都可能"泄露"真实记录的信息
- 敏感度分析极其复杂，通常使用：
  - **PrivBayes**：基于贝叶斯网络，逐维度加噪声
  - **DP-GAN**：训练生成模型时注入 DP 噪声
- 隐私预算通常设得较高（$\varepsilon = 5 \sim 10$），因为任务本身极难

#### 3.2.4 敏感度控制的核心技巧

| 技巧 | 原理 | 代价 |
|---|---|---|
| **截断（Clipping）** | 把单个值限制在 $[a, b]$ | 引入偏差，极端值信息丢失 |
| **分桶（Binning）** | 把连续值分成区间 | 降低精度，但降低敏感度 |
| **查询分解** | 把复杂查询拆成多个简单查询 | 组合隐私预算消耗增加 |
| **平滑（Smoothing）** | 对结果做后处理，限制输出范围 | 不消耗隐私预算，但可能引入偏差 |

#### 3.2.5 一句话总结

> **敏感度 = "一个人的影响力"。计数查询天然优秀（$\Delta f=1$），求和查询必须截断，最值查询很难做，复杂模型靠梯度裁剪。敏感度控制是差分隐私工程中最关键的实操环节——它直接决定了你需要加多少噪声，以及结果还能不能用。**

### 3.3 Laplace 机制

Laplace 机制是**唯一**一种能提供**纯 $\varepsilon$-差分隐私**（即 $(\varepsilon, 0)$-DP，不含 $\delta$）的连续噪声机制。其核心是：向查询结果添加服从 **Laplace 分布**的随机噪声。

#### 3.3.1 数学原理

对于查询函数 $f$，其**敏感度**（Sensitivity）为 $\Delta f$：

$$\Delta f = \max_{D \sim D'} |f(D) - f(D')|$$

Laplace 机制 $M$ 定义为：

$$M(D) = f(D) + \text{Lap}\left(\frac{\Delta f}{\varepsilon}\right)$$

其中 $\text{Lap}(b)$ 是位置参数 $\mu = 0$、尺度参数 $b = \frac{\Delta f}{\varepsilon}$ 的 Laplace 分布，概率密度函数：

$$p(x) = \frac{1}{2b} \exp\left(-\frac{|x|}{b}\right) = \frac{\varepsilon}{2\Delta f} \exp\left(-\frac{\varepsilon |x|}{\Delta f}\right)$$

#### 3.3.2 Laplace 噪声生成（逆变换采样）

若仅有均匀分布随机数生成器，可通过**逆变换采样**生成 Laplace 噪声：

**步骤**：
1. 生成 $U \sim \text{Uniform}(0, 1)$
2. 令 $V = U - 0.5$（将范围移到 $[-0.5, 0.5]$）
3. 计算噪声：
   $$X = -\frac{\Delta f}{\varepsilon} \cdot \text{sign}(V) \cdot \ln(1 - 2|V|)$$

**等价写法**：
- 若 $U < 0.5$：$X = \frac{\Delta f}{\varepsilon} \cdot \ln(2U)$
- 若 $U \geq 0.5$：$X = -\frac{\Delta f}{\varepsilon} \cdot \ln(2(1-U))$

#### 3.3.3 示例：计数查询

**场景**：数据集有 1000 名高血压患者，发布计数结果，要求纯 $\varepsilon$-DP。

**参数**：
- 真实计数 $f(D) = 1000$
- 敏感度 $\Delta f = 1$（增加/删除一个人最多改变计数 1）
- 隐私预算 $\varepsilon = 1$

**噪声尺度**：$b = \frac{\Delta f}{\varepsilon} = 1$

**生成过程**（假设随机数 $U = 0.37$）：
1. $V = 0.37 - 0.5 = -0.13$
2. $|V| = 0.13$，$\text{sign}(V) = -1$
3. $X = -1 \cdot (-1) \cdot \ln(1 - 2 \times 0.13) = \ln(0.74) \approx -0.302$
4. **发布结果**：$1000 + (-0.302) = 999.698$

#### 3.3.4 Python 代码示例

```python
import math
import random

def laplace_noise(sensitivity, epsilon):
    """
    生成纯 ε-DP 的 Laplace 噪声
    sensitivity: 查询敏感度 Δf
    epsilon: 隐私预算 ε
    """
    b = sensitivity / epsilon  # 尺度参数
    U = random.random()  # Uniform(0,1)

    # 逆变换采样
    if U < 0.5:
        noise = b * math.log(2 * U)
    else:
        noise = -b * math.log(2 * (1 - U))

    return noise

# 例子：计数查询
true_count = 1000
sensitivity = 1
epsilon = 1

noise = laplace_noise(sensitivity, epsilon)
released_result = true_count + noise

print(f"真实计数: {true_count}")
print(f"Laplace 噪声: {noise:.4f}")
print(f"发布结果: {released_result:.4f}")
```

#### 3.3.5 示例：求和查询

**场景**：发布 1000 名患者的总医疗费用，要求纯 $\varepsilon$-DP。

**参数**：
- 真实总和 $f(D) = 5,000,000$
- 每人费用上限 100,000（截断后）
- 敏感度 $\Delta f = 100,000$
- 隐私预算 $\varepsilon = 2$

**噪声尺度**：$b = \frac{100,000}{2} = 50,000$

**生成噪声**：
- 若 $U = 0.15$：$X = 50,000 \cdot \ln(0.3) \approx -60,208$
- **发布结果**：$5,000,000 + (-60,208) = 4,939,792$

虽然噪声绝对值较大，但相对于 500 万总和，相对误差仅约 1%。这体现了**敏感度小则噪声小**的原则；若单条数据即占全部（敏感度=500万），噪声会大到完全不可用，因此 sum 必须先 clipping。

### 3.4 Gaussian 机制

Gaussian 机制向查询结果添加服从 **Gaussian（正态）分布**的随机噪声，提供 **$(\varepsilon, \delta)$-差分隐私**。与 Laplace 机制不同，Gaussian 机制允许一个极小的失败概率 $\delta$，因此在高维数据、多次组合查询或需要更紧致组合分析的场景中更为常用。

#### 3.4.1 数学原理

对于查询函数 $f$，使用 **L2 敏感度** $\Delta_2 f$：

$$\Delta_2 f = \max_{D \sim D'} \|f(D) - f(D')\|_2$$

Gaussian 机制 $M$ 定义为：

$$M(D) = f(D) + \mathcal{N}(0, \sigma^2)$$

标准 Gaussian 机制取（Dwork & Roth 附录 A）：

$$\sigma = \frac{\Delta_2 f \cdot \sqrt{2 \ln(1.25 / \delta)}}{\varepsilon}$$

该参数满足 $(\varepsilon, \delta)$-DP，但要求 $\varepsilon \le 1$ 且给出的噪声界较松散。

本模块默认采用 **Balle & Wang (2018) 提出的解析高斯机制（Analytic Gaussian Mechanism）**。该算法对任意 $\varepsilon > 0$、$\delta > 0$ 直接数值求解满足 $(\varepsilon, \delta)$-DP 的最小 $\sigma$，在相同隐私参数下噪声通常小于经典公式，且不受 $\varepsilon \le 1$ 的限制。实现位于 `privacy_local_agent.privacy.dp.calibrate_analytic_gaussian()`。

**与 Laplace 的核心区别**：

| 特性 | Laplace | Gaussian |
|---|---|---|
| 隐私保证 | 纯 $\varepsilon$-DP（$\delta = 0$） | $(\varepsilon, \delta)$-DP（$\delta > 0$） |
| 敏感度 | L1 敏感度 | L2 敏感度 |
| 噪声分布 | 拉普拉斯分布 | 正态分布 |
| 适用场景 | 简单查询、纯 $\varepsilon$ 保证 | 高维、多次组合、需要紧致界 |

#### 3.4.2 Gaussian 噪声生成（Box-Muller 变换）

若仅有均匀分布随机数生成器，可通过 **Box-Muller 变换**生成标准正态分布噪声 $Z \sim \mathcal{N}(0, 1)$，再乘以 $\sigma$：

**步骤**：
1. 生成 $U_1, U_2 \sim \text{Uniform}(0, 1)$
2. 计算：
   $$Z = \sqrt{-2 \ln U_1} \cdot \cos(2\pi U_2)$$
3. Gaussian 噪声：$X = \sigma \cdot Z$

> 也可使用 `numpy.random.normal` 或 `random.gauss` 直接生成，但 Box-Muller 更便于理解其数学原理。

#### 3.4.3 示例：计数查询

**场景**：数据集有 1000 名高血压患者，发布计数结果，要求 $(\varepsilon, \delta)$-DP。

**参数**：
- 真实计数 $f(D) = 1000$
- L2 敏感度 $\Delta_2 f = 1$
- 隐私预算 $\varepsilon = 1$，$\delta = 10^{-6}$

**噪声尺度**：

$$\sigma = \frac{1 \cdot \sqrt{2 \ln(1.25 / 10^{-6})}}{1} = \sqrt{2 \ln(1,250,000)} \approx \sqrt{2 \times 14.04} \approx 5.30$$

**生成过程**（假设 $U_1 = 0.37, U_2 = 0.82$）：
1. $Z = \sqrt{-2 \ln(0.37)} \cdot \cos(2\pi \times 0.82)$
2. $\ln(0.37) \approx -0.994$，$\sqrt{-2 \times (-0.994)} = \sqrt{1.988} \approx 1.410$
3. $\cos(2\pi \times 0.82) = \cos(5.154) \approx 0.426$
4. $Z \approx 1.410 \times 0.426 \approx 0.601$
5. $X = 5.30 \times 0.601 \approx 3.19$
6. **发布结果**：$1000 + 3.19 = 1003.19$

可见在相同 $\varepsilon=1$ 下，Gaussian 机制由于引入了 $\delta$，噪声尺度（约 5.3）明显大于 Laplace 机制（尺度为 1）。这是换取 $(\varepsilon, \delta)$-DP 的代价。

#### 3.4.4 Python 代码示例

```python
import math
import random

def gaussian_noise(sensitivity, epsilon, delta):
    """
    生成 (ε, δ)-DP 的 Gaussian 噪声
    sensitivity: L2 敏感度 Δ₂f
    epsilon: 隐私预算 ε
    delta: 失败概率 δ
    """
    sigma = sensitivity * math.sqrt(2 * math.log(1.25 / delta)) / epsilon

    # Box-Muller 变换生成标准正态分布
    U1 = random.random()
    U2 = random.random()
    Z = math.sqrt(-2 * math.log(U1)) * math.cos(2 * math.pi * U2)

    return sigma * Z

# 例子：计数查询
true_count = 1000
sensitivity = 1  # L2 敏感度
epsilon = 1
delta = 1e-6

noise = gaussian_noise(sensitivity, epsilon, delta)
released_result = true_count + noise

print(f"真实计数: {true_count}")
print(f"Gaussian 噪声: {noise:.4f}")
print(f"发布结果: {released_result:.4f}")
```

#### 3.4.5 示例：求和查询

**场景**：发布 1000 名患者的总医疗费用，要求 $(\varepsilon, \delta)$-DP。

**参数**：
- 真实总和 $f(D) = 5,000,000$
- 每人费用上限 100,000（截断后）
- L2 敏感度 $\Delta_2 f = 100,000$
- 隐私预算 $\varepsilon = 2$，$\delta = 10^{-6}$

**噪声尺度**：

$$\sigma = \frac{100,000 \cdot \sqrt{2 \ln(1.25 / 10^{-6})}}{2} \approx \frac{100,000 \times 5.30}{2} = 265,000$$

**生成噪声**：
- 若 $Z \approx -0.5$：$X = 265,000 \times (-0.5) = -132,500$
- **发布结果**：$5,000,000 + (-132,500) = 4,867,500$

相对误差约 2.65%。若将 $\varepsilon$ 提高到 4，噪声尺度减半，相对误差约 1.3%。

#### 3.4.6 何时选择 Gaussian 机制

- **高维查询**：当查询输出是向量时，Gaussian 机制在高维下的组合界通常更紧致。
- **多次查询**：需要利用高级组合定理（Advanced Composition）时，Gaussian 机制的分析更自然。
- **允许 $\delta$ 的场景**：如发布统计报告、训练差分隐私模型等，可接受极小失败概率。

> **注意**：Gaussian 机制必须要求 $\delta > 0$。若业务场景要求纯 $\varepsilon$-DP，应使用 Laplace 机制。

### 3.5 mean 的组合实现

mean 通过组合 count 与 sum 实现：

- Laplace：$\text{mean} = \frac{\text{sum\_with\_noise}(\varepsilon/2)}{\text{count\_with\_noise}(\varepsilon/2)}$，总消耗 $(\varepsilon, 0)$。
- Gaussian：$\text{mean} = \frac{\text{sum\_with\_noise}(\varepsilon/2, \delta/2)}{\text{count\_with\_noise}(\varepsilon/2, \delta/2)}$，总消耗 $(\varepsilon, \delta)$。

为防止噪声计数接近 0 导致均值结果发散（Cauchy 型长尾），实现中引入 `min_count` 阈值：

```python
def mean(..., min_count: float = 5.0) -> float:
    noisy_count = count(...)
    if noisy_count < min_count or noisy_count <= 0.0:
        return 0.0  # 拒绝返回不稳定的均值
    return noisy_sum / noisy_count
```

当估计计数低于阈值时，接口返回 0.0 作为安全 fallback。调用方可通过 `params.min_count` 自定义该阈值。

### 3.6 组合定理

多次 DP 查询会累计消耗隐私预算。基本组合定理：若 $k$ 个机制分别满足 $(\varepsilon_i, \delta_i)$-DP，则整体满足 $(\sum \varepsilon_i, \sum \delta_i)$-DP。BudgetAccountant 据此拒绝会导致总预算超支的查询。

### 3.7 本地差分隐私（Local DP）

本地差分隐私与中心式 DP 的信任模型不同：

- **中心式 DP**：用户把原始数据交给可信的数据管理者，管理者在聚合结果上加噪声。
- **本地 DP**：每个用户在数据离开自己的设备前，先对数据进行随机化扰动；服务器只收到扰动后的值，无法反推单个用户的真实值。

本地 DP 提供更强的隐私保证（无需信任服务器），但相同 $\varepsilon$ 下统计效用通常低于中心式 DP。

#### 3.7.1 随机响应（Randomized Response）

随机响应是最经典的本地 DP 机制。

##### 二值随机响应

对于输入 $b \in \{0, 1\}$，以概率 $p$ 保持原值，以概率 $1-p$ 翻转：

$$p = \frac{e^\varepsilon}{1 + e^\varepsilon}$$

$$M(b) = \begin{cases} b & \text{概率 } p \\ 1-b & \text{概率 } 1-p \end{cases}$$

该机制满足 $\varepsilon$-LDP。

对于 $n$ 个用户，设扰动后报告为 1 的比例为 $\hat{f}_{\text{reported}}$，真实比例为 $f$ 的纠偏估计为：

$$\hat{f} = \frac{\hat{f}_{\text{reported}} - (1 - p)}{2p - 1}$$

##### k-ary 随机响应

对于类别型输入 $v \in \{1, \dots, k\}$：

$$p = \frac{e^\varepsilon}{k - 1 + e^\varepsilon}$$

- 以概率 $p$ 保持原类别
- 以均匀概率 $(1-p)/(k-1)$ 返回其他每个类别

对类别 $j$ 的真实频率纠偏估计：

$$\hat{f}_j = \frac{\hat{f}_{j,\text{reported}} - q}{p - q}, \quad q = \frac{1 - p}{k - 1}$$

#### 3.7.2 本地直方图

本地直方图通过让每个用户独立扰动自己的类别值，再由服务器聚合纠偏得到总体分布估计。适用于：

- 浏览器/移动设备 telemetry 统计
- 用户偏好分布调查
- 不需要精确个体值的群体趋势分析

#### 3.7.3 与中心式 DP 的对比

| 维度 | 中心式 DP | 本地 DP |
|---|---|---|
| 信任模型 | 信任数据管理者 | 不信任任何中心方 |
| 噪声位置 | 聚合结果 | 每条用户记录 |
| 典型噪声 | 较小 | 较大 |
| 代表机制 | Laplace/Gaussian | Randomized Response / RAPPOR |
| 适用场景 | 企业内部分析、可信平台 | 浏览器 telemetry、移动设备 |

#### 3.7.4 使用限制

- 本地 DP 的噪声通常远大于中心式 DP，因此只适合**大样本下的频率/分布估计**。
- 不能用于需要精确个体值或复杂聚合（如 sum/mean）的场景；这些场景应使用中心式 DP 或安全聚合（Secure Aggregation）。

### 3.8 Noisify 接口设计

Noisify 接口面向"外部引擎已完成聚合，sidecar 仅负责注入噪声与预算扣减"的工作模式。典型场景包括：

- Spark / Flink / DuckDB / SQL 在数据源侧完成 `COUNT` / `SUM` / 直方图分桶。
- 调用方将中间聚合结果（如 `true_sum`、`true_count`、`true_counts`）发送到 sidecar。
- sidecar 根据调用方提供的敏感度计算噪声，加入结果后返回，并扣减命名空间预算。

#### 为什么需要调用方提供敏感度

中心式 DP 的噪声尺度由查询敏感度决定。Noisify 接口不再接触原始记录，因此无法自行推断 `sum` 的 clip 区间或 `count` 的邻接变化量。调用方必须提供以下二者之一：

- `sensitivity`：直接给出 L1/L2 敏感度。
- `clip_lower` + `clip_upper`：sidecar 计算 `sensitivity = clip_upper - clip_lower`。

#### 接口映射

| 接口 | 输入 | 输出 | 敏感度 |
|---|---|---|---|
| `noisy_count` | `true_count` | 带噪计数 | 1 |
| `noisy_sum` | `true_sum` | 带噪求和 | `sensitivity`（或 clip 区间长度） |
| `noisy_mean` | `true_sum`, `true_count` | 带噪均值 | count 部分为 1；sum 部分为 `sensitivity` |
| `noisy_histogram` | `true_counts` | 带噪直方图 | 1（联合敏感度） |

`noisy_mean` 同样使用组合定理：将 `(epsilon, delta)` 平分为两份，分别用于 `noisy_count` 与 `noisy_sum`。

### 3.9 Chunked 流式聚合

Chunked 接口允许调用方以多个 chunk（生成器/迭代器/列表）分批传入数据，sidecar 在内部完成增量聚合，最终只注入一次噪声、消耗一次隐私预算。

#### 适用场景

- 数据量超过单台机器内存，无法一次性构造 `values` 列表。
- 数据从流式源（Kafka、文件流）逐批读取。
- 希望降低网络单次传输的峰值负载。

#### 实现要点

1. **单遍遍历**：每个 chunk 只被遍历一次，边读边累加真实计数/求和/直方图。
2. **统一 clip**：`chunked_sum` / `chunked_mean` 必须显式提供全局 `clip_lower` / `clip_upper`，所有 chunk 使用同一边界裁剪。
3. **单次预算**：真实聚合完成后，只调用一次 `BudgetAccountant.spend(epsilon, delta)`，然后注入噪声。
4. **数据格式**：每个 chunk 支持 list/tuple/ndarray/Series/DataFrame/SecretFlow 格式，通过 `data_adapters.extract_values` 统一转换。

#### 与 noisify 的协作

对于真正海量（亿级）数据，推荐模式是：

1. 在分布式引擎（Spark/SQL）中完成预聚合，得到 `true_sum` / `true_count`。
2. 调用 `noisy_sum` / `noisy_mean` 对中间结果加噪。

Chunked 接口适合"数据能放进单台 sidecar 内存分批处理，但不想一次性全量传输"的场景。

### 3.10 数据适配器

`privacy_local_agent/privacy/data_adapters.py` 为 DP 原语提供统一输入适配，将多种数据格式转换为 Python `List[float]`。

#### 支持格式

| 类型 | 说明 | 所需参数 |
|---|---|---|
| `list` / `tuple` | 原生序列 | 无 |
| `np.ndarray` | NumPy 数组 | 无 |
| `pd.Series` | pandas Series | 无 |
| `pd.DataFrame` | pandas DataFrame | `column` |
| `sf.data.DataFrame` | SecretFlow 本地 DataFrame | `column` |
| `HDataFrame` | 水平分割联邦数据 | `column`，可选 `party` |
| `VDataFrame` | 垂直分割联邦数据 | `column` |
| `MixDataFrame` | 混合联邦数据 | 不支持直接提取，需先转换 |
| `FedNdarray` | 联邦 ndarray | 按 H/V 方式处理 |

#### SecretFlow 支持

SecretFlow 为可选依赖。未安装时，适配器跳过 SecretFlow 分支，仅处理 list/NumPy/pandas；已安装时自动识别联邦数据结构。

- **VDataFrame**：列分布在不同参与方，系统自动遍历 partitions 找到包含 `column` 的 partition。
- **HDataFrame**：样本水平分割，单 partition 时自动提取；多 partition 时必须通过 `party` 指定参与方。
- **MixDataFrame**：结构复杂，直接抛出错误，要求调用方先转换为 H/V DataFrame 或手动提取。

#### 使用方式

Python SDK 中 `column` / `party` 作为 `DPApi.count/sum/mean/histogram` 的命名参数传入；REST/gRPC 中通过 `params.column` / `params.party` 透传。

### 3.11 流量监控指标

为便于运维审计与容量规划，REST 中间件与 gRPC 拦截器均接入了 `privacy_traffic_bytes_total` Counter。

#### 指标定义

```text
privacy_traffic_bytes_total{method, path, direction}
```

- `method`：HTTP 方法（如 `POST`）或 `gRPC`。
- `path`：HTTP 路径（如 `/v1/privacy/dp/sum`）或 gRPC 完整方法名（如 `/privacy.local.PrivacyService/DPSum`）。
- `direction`：`request` 或 `response`。

#### 实现位置

- REST：`privacy_local_agent/observability/middleware.py` 中的 `ObservabilityMiddleware`，读取请求体长度与响应内容长度。
- gRPC：`GrpcObservabilityInterceptor` 中对 unary 调用使用 `protobuf.Message.ByteSize()` 估算请求/响应字节数；stream 调用因消息流不可预知，request/response 字节数计为 0。

## 4. 模块设计

### 4.1 `privacy_local_agent/privacy/dp.py`

- `DPApi.count(...)`：count 查询入口。
- `DPApi.sum(...)`：sum 查询入口，先 clipping 再计算。
- `DPApi.mean(...)`：mean 查询入口，组合 count 与 sum，支持 `min_count` 低频保护。
- `DPApi.histogram(...)`：直方图查询入口，利用互斥划分的联合敏感度为 1，仅消耗一次预算。
- `DPApi.noisy_count/noisy_sum/noisy_mean/noisy_histogram(...)`：对已由外部引擎聚合好的中间结果加噪。
- `DPApi.chunked_count/chunked_sum/chunked_mean/chunked_histogram(...)`：分块流式聚合。
- `DPApi._clip_values(...)`：NumPy 向量化 clip，失败回退纯 Python。
- `LocalDPApi.perturb_binary/perturb_categorical(...)`：二值/类别型本地 DP 扰动。
- `LocalDPApi.estimate_binary_frequency/estimate_categorical_histogram(...)`：本地 DP 频率/直方图纠偏估计。
- `calibrate_analytic_gaussian(...)`：解析高斯机制噪声校准。
- `_sample_laplace(scale)` / `_sample_gaussian(sigma)`：噪声采样。
- `mechanism` 校验为 `laplace` 或 `gaussian`。

### 4.2 `privacy_local_agent/service.py`

- `dp_count/dp_sum/dp_mean` 从解析后的参数中传递 `delta`、`clip_lower`、`clip_upper`。
- 负责参数解析、profile 合并与错误处理。

### 4.3 proto / REST / gRPC

- `DPRequest` 包含 `epsilon`、`delta`、`mechanism`、`clip_lower`、`clip_upper`。
- REST `DPRequest.params` 透传上述字段，mean 查询额外支持 `min_count`；表格型输入额外支持 `column` / `party`。
- 新增 `DPHistogramRequest` / `POST /v1/privacy/dp/histogram`：差分隐私直方图。
- 新增 noisify REST 接口：
  - `POST /v1/privacy/dp/noisy_count`
  - `POST /v1/privacy/dp/noisy_sum`
  - `POST /v1/privacy/dp/noisy_mean`
  - `POST /v1/privacy/dp/noisy_histogram`
- 新增 chunked REST 接口：
  - `POST /v1/privacy/dp/chunked_count`
  - `POST /v1/privacy/dp/chunked_sum`
  - `POST /v1/privacy/dp/chunked_mean`
  - `POST /v1/privacy/dp/chunked_histogram`
- 新增本地 DP REST 接口：
  - `POST /v1/privacy/ldp/perturb/binary`
  - `POST /v1/privacy/ldp/perturb/categorical`
  - `POST /v1/privacy/ldp/estimate/binary`
  - `POST /v1/privacy/ldp/estimate/categorical`
- 对应 gRPC 方法：`DPHistogram`、`DPNoisyCount`、`DPNoisySum`、`DPNoisyMean`、`DPNoisyHistogram`、`DPChunkedCount`、`DPChunkedSum`、`DPChunkedMean`、`DPChunkedHistogram`、`PerturbBinaryBatch`、`PerturbCategoricalBatch`、`EstimateBinaryFrequency`、`EstimateCategoricalHistogram`。

## 5. BudgetAccountant 设计

### 5.1 预算消耗规则

- Laplace：$\text{spend}(\varepsilon, 0.0)$
- Gaussian：$\text{spend}(\varepsilon, \delta)$
- mean 组合：分别对 count 与 sum 调用，总消耗为 $(\varepsilon, \delta)$。
- 直方图：利用联合敏感度为 1，仅调用一次 `spend(\varepsilon, \delta)`。

### 5.1.1 时间窗口重置

为避免 Sidecar 长期运行后预算永久耗尽，BudgetAccountant 支持按时间窗口自动重置已消耗预算：

- 通过构造函数 `window_seconds` 或环境变量 `PRIVACY_BUDGET_WINDOW_SECONDS` 配置窗口长度。
- 每个 namespace 独立维护一个窗口开始时间 `_window_start`。
- 在 `spend()` 或 `remaining()` 时，若当前时间超过 `_window_start + window_seconds`，则自动将 `epsilon_spent` 与 `delta_spent` 清零，并将 `_window_start` 更新为当前时间。
- 在 SQLite 持久化模式下，窗口开始时间也存储在数据库中，确保多实例共享一致的时间边界。

示例：设置 `window_seconds=86400`（1 天），则每个 namespace 每天 0 点（从首次消费开始计）后预算自动恢复为 `epsilon_total`。

### 5.2 存储后端

| 模式 | 实现 | 适用场景 |
|---|---|---|
| 内存模式 | 单例 + 线程锁 | 单进程、高吞吐 |
| SQLite 模式 | `BEGIN IMMEDIATE` 独占事务 | 多实例共享预算 |

### 5.3 超支处理

当累计消耗超过 `total_epsilon` / `total_delta` 时，拒绝新查询并返回明确错误。预算一旦记录即不可回退，但会在配置的窗口到期后自动清零。

## 6. 数据集级隐私预算分配

### 6.1 确定数据集总预算

为某个数据集设定总预算时，需综合考虑数据敏感度、合规要求与分析用途：

| 数据类型 | 推荐 `total_epsilon` | 推荐 `total_delta` |
|---|---|---|
| 医疗/基因数据 | 0.1 ~ 2.0 | $< 10^{-6}$ |
| 金融/个人征信 | 0.5 ~ 3.0 | $< 10^{-6}$ |
| 一般用户行为 | 1.0 ~ 5.0 | $< 10^{-5}$ |
| 公开/聚合统计 | 3.0 ~ 10.0 | $< 10^{-5}$ |

delta 通常设为远小于 $1/n$，$n$ 为数据集大小。例如 $n = 100,000$ 时，可取 $\delta = 10^{-6}$。

### 6.2 查询级预算分配

总预算确定后，按计划在数据集上执行的查询次数进行拆分。常见策略包括：

1. **均分策略**：若计划执行 $k$ 次同类型查询，每次分配 $\varepsilon/k$，$\delta/k$。
2. **加权策略**：核心查询分配更多预算，辅助查询分配较少预算。
3. **按需审批策略**：总预算由治理平台托管，每次查询单独申请并记录。

### 6.3 组合定理

#### 基本组合

若在同一数据集上执行 $k$ 个机制，分别满足 $(\varepsilon_i, \delta_i)$-DP，则整体满足 $(\sum \varepsilon_i, \sum \delta_i)$-DP。这是 BudgetAccountant 默认采用的累加方式。

#### 高级组合（Advanced Composition）

当 $k$ 个机制均满足 $(\varepsilon, \delta)$-DP 且相互独立时，对任意 $\delta' > 0$，整体满足 $(\varepsilon', k\delta + \delta')$-DP，其中：

$$\varepsilon' = \sqrt{2k \cdot \ln(1/\delta')} \cdot \varepsilon + k \cdot \varepsilon \cdot (e^\varepsilon - 1)$$

当 $\varepsilon$ 较小时，$e^\varepsilon - 1 \approx \varepsilon$，可近似为：

$$\varepsilon' \approx \sqrt{2k \cdot \ln(1/\delta')} \cdot \varepsilon + k \cdot \varepsilon^2$$

高级组合在 $k$ 较大时通常比基本组合更紧致，可将总 $\varepsilon$ 从 $k \cdot \varepsilon$ 降低到约 $\sqrt{k} \cdot \varepsilon$ 量级。

> **当前实现状态**：BudgetAccountant 仅支持基本组合（直接相加）。高级组合与 Renyi DP（RDP）虽然能显著改善多次查询下的预算估计，但会改变预算语义，要求调用方预先声明查询序列长度或维护 RDP 阶数状态，增加了 API 复杂度与审计难度。在 POC/MVP 阶段，为保持接口简单和可解释性，未实现高级组合/RDP；若未来需要支持高频查询场景，可在 BudgetAccountant 中增加可选的组合模式。

### 6.4 预算分配示例

假设某数据集 $n = 50,000$，设定 $\text{total\_epsilon} = 4.0$、$\text{total\_delta} = 10^{-6}$，计划执行 10 次 Gaussian 机制的 sum 查询。

#### 均分基本组合

每次查询分配：

$$\varepsilon_{\text{per\_query}} = \frac{4.0}{10} = 0.4$$

$$\delta_{\text{per\_query}} = \frac{10^{-6}}{10} = 10^{-7}$$

10 次查询后总消耗恰好为 $(4.0, 10^{-6})$。

#### 高级组合

取 $\delta' = 10^{-7}$，每次查询 $\varepsilon = 0.4$、$\delta = 10^{-7}$，则 10 次查询后：

$$\varepsilon' \approx \sqrt{2 \times 10 \times \ln(1/10^{-7})} \times 0.4 + 10 \times 0.4^2$$

$$\approx \sqrt{20 \times 16.12} \times 0.4 + 1.6$$

$$\approx 7.2 \times 0.4 + 1.6$$

$$\approx 4.48$$

总 $\delta = 10 \times 10^{-7} + 10^{-7} = 1.1 \times 10^{-6}$，略超总预算。此时可降低单次 $\varepsilon$ 至约 0.36，使总 $\varepsilon' \approx 4.0$。

### 6.5 设计建议

- 在数据集创建或导入阶段即设定 `total_epsilon` 与 `total_delta`，写入 BudgetAccountant 的 namespace 配置。
- 对高频分析场景，优先使用 Gaussian 机制 + 高级组合，以降低累计噪声。
- 对仅需要纯 $\varepsilon$-DP 保证的场景，使用 Laplace 机制 + 基本组合。
- 记录每次查询的 $(\varepsilon, \delta)$ 消耗、查询类型与调用者身份，便于审计与后续预算调整。

## 7. 接口定义

### 7.1 REST 请求示例（标准聚合）

```json
{
  "values": [1.0, 2.0, 3.0],
  "params": {
    "epsilon": 1.0,
    "delta": 1e-6,
    "mechanism": "gaussian",
    "clip_lower": 0.0,
    "clip_upper": 10.0
  }
}
```

表格型输入可指定 `column`（以及 SecretFlow HDataFrame 所需的 `party`）：

```json
{
  "values": [[1.0, 2.0], [3.0, 4.0]],
  "params": {
    "column": "salary",
    "epsilon": 1.0,
    "clip_lower": 0.0,
    "clip_upper": 100000.0
  }
}
```

### 7.2 REST 请求示例（Noisify）

```json
{
  "true_sum": 5000000.0,
  "params": {
    "epsilon": 1.0,
    "delta": 1e-6,
    "mechanism": "gaussian",
    "sensitivity": 100000.0
  }
}
```

### 7.3 REST 请求示例（Chunked）

```json
{
  "chunks": [
    [1.0, 2.0, 3.0],
    [4.0, 5.0, 6.0]
  ],
  "params": {
    "epsilon": 1.0,
    "delta": 1e-6,
    "mechanism": "gaussian",
    "clip_lower": 0.0,
    "clip_upper": 10.0
  }
}
```

### 7.4 gRPC 字段

`DPRequest` 包含 `epsilon`、`delta`、`mechanism`、`clip_lower`、`clip_upper`，与 REST 参数语义一致。

新增消息：`DPNoisyCountRequest`、`DPNoisySumRequest`、`DPNoisyMeanRequest`、`DPNoisyHistogramRequest`、`DPChunkedCountRequest`、`DPChunkedSumRequest`、`DPChunkedMeanRequest`、`DPChunkedHistogramRequest`，以及 `DoubleChunk`、`StringChunk`。

## 8. 安全与兼容性设计

- 默认 `mechanism=laplace`，$\delta = 0$。
- sum/mean 必须提供 `clip_lower` / `clip_upper`；未提供时返回明确错误。
- Gaussian 机制下 `delta` 必须大于 0。
- 默认 `mechanism=laplace`，$δ = 0$。
- sum/mean 必须提供 `clip_lower` / `clip_upper`；未提供时返回明确错误。
- Gaussian 机制下 `delta` 必须大于 0。
- **NumPy 依赖**：clip 操作优先使用 NumPy（`numpy>=1.24.0` 为核心依赖）；若 NumPy 不可用或转换失败，自动回退到纯 Python，保证核心功能可用。
- **SecretFlow 可选依赖**：SecretFlow 相关数据适配为可选能力，未安装时不影响 list/NumPy/pandas 输入。
- **浮点数与随机数安全**：当前采样噪声已升级为密码学安全的随机数生成器（CSRPNG，通过 `SecureRandom` 包装 `secrets.SystemRandom`），防止攻击者通过多次查询收集的样本还原生成器状态；同时保留了测试模式下 `.seed()` 调用的确定性兼容。针对浮点数表示缺陷引起的物理泄漏风险（Mironov, 2012），可考虑后续集成 discrete Laplace 或 snapping 机制。

## 9. 测试策略

- Laplace/Gaussian 机制单元测试（含解析高斯机制）。
- NumPy 向量化 clip 与纯 Python 回退测试。
- clipping 参数校验与敏感度计算测试。
- delta 预算正确消耗、超支拒绝与时间窗口重置测试。
- mean `min_count` 低频保护测试。
- histogram 联合敏感度测试。
- noisify 接口（count/sum/mean/histogram）单元测试与 REST/gRPC 测试。
- chunked 接口（count/sum/mean/histogram）单元测试与 REST/gRPC 测试。
- 数据适配器对 list/NumPy/pandas/SecretFlow 的测试。
- REST/gRPC 接口参数透传测试。
- 本地 DP REST/gRPC 接口测试。
- `privacy_traffic_bytes_total` 指标接入测试。
