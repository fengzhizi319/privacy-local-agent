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

敏感度（Sensitivity）是连接差分隐私"定义"与"机制设计"的核心桥梁。它本身不出现在差分隐私的定义式中，但决定了要满足该定义需要注入多少噪声。

#### 3.2.1 差分隐私的定义回顾

随机算法 $M$ 满足 $(\varepsilon, \delta)$-差分隐私，当且仅当对任意相邻数据集 $D, D'$（仅差一条记录）和任意输出集合 $S$：

$$\Pr[M(D) \in S] \le e^{\varepsilon} \cdot \Pr[M(D') \in S] + \delta$$

注意这个定义只约束输出分布在相邻数据集上的相似程度，**并不直接规定怎么实现**。要让机制满足它，需要回答一个问题：一个人的数据变化，最多能让查询结果改变多少？这正是敏感度。

#### 3.2.2 敏感度的定义与作用

对查询函数 $f$，其全局 $L_1$ 敏感度为：

$$\Delta_1 f = \max_{D \sim D'} \|f(D) - f(D')\|_1$$

（相应地有 $L_2$ 敏感度 $\Delta_2 f$。）

它的作用体现在以下几个方面：

**（1）标定噪声尺度——隐私机制的"调音旋钮"**

- **拉普拉斯机制**（用于纯 $\varepsilon$-DP）：

$$M(D) = f(D) + \text{Lap}\left(\frac{\Delta_1 f}{\varepsilon}\right)$$

- **高斯机制**（用于 $(\varepsilon, \delta)$-DP）：

$$M(D) = f(D) + \mathcal{N}(0, \sigma^2), \quad \sigma \approx \frac{\Delta_2 f \sqrt{2\ln(1.25/\delta)}}{\varepsilon}$$

噪声尺度与敏感度成正比、与 $\varepsilon$ 成反比。敏感度高意味着一个人的数据可能大幅改变结果，为了"掩盖"这个人的痕迹，就必须加更多噪声。

**（2）把抽象定义转化为可操作的设计参数**

DP 定义描述的是"输出分布要多相似"，但没有说怎么做。敏感度提供了量化途径：只要噪声足以淹没单条记录引起的最大输出偏移 $\Delta f$，相邻数据集上两个输出分布就不可区分到 $\varepsilon$ 以内。可以说：

> **DP 定义给出目标，敏感度给出实现该目标所需的噪声"剂量"。**

**（3）决定隐私与效用的权衡**

给定隐私预算 $\varepsilon$，误差直接正比于敏感度。因此工程上的关键技巧很多都围绕降低敏感度：

- 机器学习中的梯度**裁剪**（clipping）：把每条样本的梯度范数截断到 $C$，将原本无界的敏感度控制为 $C$——这是 DP-SGD 的核心步骤；
- 对查询加边界（如限定计数范围、截断贡献）；
- 选择合适的函数形式，使其对单条记录不敏感。

#### 3.2.3 全局敏感度 vs 局部敏感度

- **全局敏感度**：在所有可能的相邻数据集对上取最大值，只依赖查询函数本身，与具体数据无关。优点是简单、可直接用于机制校准；缺点是对某些查询（如中位数、平滑度差的函数）过于保守，导致噪声过大。
- **局部敏感度**：固定当前数据集 $D$，只在它的邻域内取最大值：$\text{LS}_f(D) = \max_{D' \sim D}\|f(D)-f(D')\|$。它通常更小，但直接用它校准噪声会泄露 $D$ 的信息，所以出现了平滑敏感度（smooth sensitivity）、Sample-and-Aggregate、Propose-Test-Release 等间接利用方法。

#### 3.2.4 小结

| 角度 | 敏感度的角色 |
|---|---|
| 定义层面 | 不在 DP 定义式中，但定义要求相邻数据集输出相似，敏感度量化"相似需要到什么程度" |
| 机制层面 | 拉普拉斯/高斯噪声的标准差直接由 $\Delta f / \varepsilon$ 决定 |
| 效用层面 | 敏感度越大，精度损失越大，降低敏感度是优化 DP 算法的主线 |
| 工程层面 | 裁剪、边界设定等技术本质上都是在"人为控制敏感度" |

> **敏感度度量单个个体的最大影响，差分隐私通过注入与敏感度成比例的噪声来掩盖这种影响——它是定义与实现之间的量化纽带。**

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

若仅有均匀分布随机数生成器，可通过 **Box-Muller 变换**生成标准正态分布噪声 $Z \sim \mathcal{N}(0, 1)$，再乘以 $\sigma$,得到$X \sim \mathcal{N}(0, \sigma^2)$：

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

$$\sigma =\frac{\Delta_2 f \cdot \sqrt{2 \ln(1.25 / \delta)}}{\varepsilon} = \frac{1 \cdot \sqrt{2 \ln(1.25 / 10^{-6})}}{1} = \sqrt{2 \ln(1,250,000)} \approx \sqrt{2 \times 14.0386} = \sqrt{28.077} \approx 5.30$$

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

#### 为什么 mean 不能直接加噪

与 count、sum 不同，均值 $\bar{x} = \frac{1}{n}\sum_{i=1}^n x_i$ 的敏感度分析更为复杂。如果将 mean 视为一个查询函数 $f(D) = \frac{\sum x_i}{|D|}$，其敏感度同时受分子（sum）和分母（count）的影响：

- 增加/删除一条记录，分子变化最多 $\max(|x_{\min}|, |x_{\max}|)$
- 分母同时变化 1

这使得 mean 的敏感度不是简单的常数，而是与数据规模和取值范围都相关。直接为 mean 设计一个端到端的噪声机制在理论上可行但实现复杂。

#### 组合策略：拆分为 count + sum

本模块采用**组合实现**：将 mean 拆解为两个独立的 DP 查询，分别加噪后相除：

$$\text{noisy_mean} = \frac{\text{noisy_sum}}{\text{noisy_count}}$$

其中 noisy_sum 和 noisy_count 各自独立注入 Laplace 或 Gaussian 噪声。这种方法的优点是：

1. **复用已有机制**：count 的敏感度为 1，sum 的敏感度为 $\text{clip_upper} - \text{clip_lower}$，均可直接校准噪声。
2. **隐私预算可追踪**：两次查询的预算消耗通过组合定理累加，清晰透明。
3. **实现简单**：无需为 mean 单独推导敏感度公式。

#### 预算分配

总预算 $(\varepsilon, \delta)$ 平分为两份，分别分配给 count 和 sum：

- **Laplace**：$\text{mean} = \frac{\text{sum_with_noise}(\varepsilon/2)}{\text{count_with_noise}(\varepsilon/2)}$，总消耗 $(\varepsilon, 0)$。
- **Gaussian**：$\text{mean} = \frac{\text{sum_with_noise}(\varepsilon/2, \delta/2)}{\text{count_with_noise}(\varepsilon/2, \delta/2)}$，总消耗 $(\varepsilon, \delta)$。

均分策略是最简单的预算分配方式。更精细的方案（如按敏感度加权分配）可以进一步优化效用，但会增加 API 复杂度，当前版本未采用。

#### 隐私保证

由基本组合定理，两次查询分别满足 $(\varepsilon/2, \delta/2)$-DP，整体满足：

$$\left(\frac{\varepsilon}{2} + \frac{\varepsilon}{2},\; \frac{\delta}{2} + \frac{\delta}{2}\right) = (\varepsilon, \delta)\text{-DP}$$

因此组合实现不会超出声明的总隐私预算。

#### 分母接近零的发散问题

组合实现面临的核心风险是**分母发散**：当 noisy_count 接近 0 时，$\text{noisy_sum} / \text{noisy_count}$ 的取值可能趋向无穷大，产生极端异常值。这在数学上类似于 **Cauchy 分布的长尾特性**——两个独立正态（或 Laplace）变量之比的尾部极重。

具体来说：

- 当真实计数 $n$ 较大时，$\text{noisy_count} \approx n + \text{noise}$，噪声相对较小，除法结果稳定。
- 当真实计数 $n$ 很小（如 $n < 5$）时，噪声可能将 noisy_count 拉到 0 附近甚至为负，导致结果爆炸。

#### min_count 阈值保护

为防止发散，实现中引入 `min_count` 阈值（默认 5.0）：

```python
def mean(..., min_count: float = 5.0) -> float:
    noisy_count = count(...)
    if noisy_count < min_count or noisy_count <= 0.0:
        return 0.0  # 拒绝返回不稳定的均值
    return noisy_sum / noisy_count
```

当估计计数低于阈值时，接口返回 0.0 作为安全 fallback，表示"样本量不足，结果不可靠"。调用方可通过 `params.min_count` 自定义该阈值：

| 场景 | 推荐 `min_count` | 说明 |
|---|---|---|
| 保守（医疗/金融） | 10.0 | 严格要求统计显著性 |
| 默认 | 5.0（默认值） | 平衡可用性与稳定性 |
| 宽松（探索性分析） | 2.0 | 允许更大方差，减少 fallback 频率 |

#### 示例

假设数据集有 1000 条记录，值域 $[0, 100]$，$\varepsilon = 2, \delta = 10^{-6}$，使用 Gaussian 机制：

1. count 部分：$\varepsilon/2 = 1, \delta/2 = 5 \times 10^{-7}$，敏感度 = 1，$\sigma_{\text{count}} \approx 5.3$
2. sum 部分：$\varepsilon/2 = 1, \delta/2 = 5 \times 10^{-7}$，敏感度 = 100，$\sigma_{\text{sum}} \approx 530$

若真实 sum = 50,000，真实 count = 1000：
- noisy_count $\approx 1000 \pm 5.3$（相对误差 ~0.5%，稳定）
- noisy_sum $\approx 50{,}000 \pm 530$（相对误差 ~1%）
- noisy_mean $\approx 50{,}000 / 1000 = 50.0$（接近真实均值）

若数据集仅有 3 条记录：
- noisy_count $\approx 3 \pm 5.3$，可能为负或接近 0
- 触发 `min_count` 保护，返回 0.0

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

##### 二值随机响应的公式推导

**1. 概率 $p$ 的推导**

由 $\varepsilon$-LDP 的定义，对任意两个可能的输入 $b, b' \in \{0,1\}$ 和任意输出 $o$，需满足：

$$\frac{\Pr[M(b) = o]}{\Pr[M(b') = o]} \leq e^\varepsilon$$

考虑最紧的约束情形——输出等于真实值时概率最大（$p$），输出等于翻转值时概率最小（$1-p$），取比值：

$$\frac{p}{1 - p} = e^\varepsilon$$

解出 $p$：

$$p = e^\varepsilon (1 - p) \implies p + p \cdot e^\varepsilon = e^\varepsilon \implies p(1 + e^\varepsilon) = e^\varepsilon$$

$$\boxed{p = \frac{e^\varepsilon}{1 + e^\varepsilon}}$$

当 $\varepsilon \to 0$ 时 $p \to 1/2$（完全随机）；当 $\varepsilon \to \infty$ 时 $p \to 1$（不扰动），符合直觉。

**2. 纠偏估计量 $\hat{f}$ 的推导**

设 $n$ 个用户中真实值为 1 的比例为 $f$。扰动后报告为 1 的期望比例为：

$$\mathbb{E}[\hat{f}_{\text{reported}}] = f \cdot p + (1 - f) \cdot (1 - p)$$

展开并整理：

$$\mathbb{E}[\hat{f}_{\text{reported}}] = fp + 1 - p - f + fp = f(2p - 1) + (1 - p)$$

令观测值 $\hat{f}_{\text{reported}}$ 等于期望值，反解 $f$：

$$\hat{f}_{\text{reported}} = f(2p - 1) + (1 - p)$$

$$\boxed{\hat{f} = \frac{\hat{f}_{\text{reported}} - (1 - p)}{2p - 1}}$$

该估计量是无偏的：$\mathbb{E}[\hat{f}] = f$。

**3. 估计量的方差**

由于每个用户的报告是独立的 Bernoulli 试验，$\hat{f}_{\text{reported}}$ 的方差为：

$$\text{Var}(\hat{f}_{\text{reported}}) = \frac{\mathbb{E}[\hat{f}_{\text{reported}}](1 - \mathbb{E}[\hat{f}_{\text{reported}}])}{n}$$

因此纠偏估计量的方差为：

$$\text{Var}(\hat{f}) = \frac{\text{Var}(\hat{f}_{\text{reported}})}{(2p - 1)^2} = \frac{f(1-f)}{n(2p-1)^2}$$

其中 $2p - 1 = \frac{e^\varepsilon - 1}{e^\varepsilon + 1} = \tanh(\varepsilon/2)$，故：

$$\text{Var}(\hat{f}) = \frac{f(1-f)}{n \cdot \tanh^2(\varepsilon/2)}$$

这说明：$\varepsilon$ 越小（隐私保护越强），方差越大，需要更多样本才能获得准确的频率估计。

##### k-ary 随机响应

对于类别型输入 $v \in \{1, \dots, k\}$：

$$p = \frac{e^\varepsilon}{k - 1 + e^\varepsilon}$$

- 以概率 $p$ 保持原类别
- 以均匀概率 $(1-p)/(k-1)$ 返回其他每个类别

##### k-ary 随机响应的公式推导

**1. 概率 $p$ 的推导**

类似二值情形，由 $\varepsilon$-LDP 定义，输出为真实类别的概率 $p$ 与输出为任一其他特定类别的概率 $q$ 之比需满足：

$$\frac{p}{q} = e^\varepsilon$$

同时概率归一化条件要求：

$$p + (k-1) \cdot q = 1$$

将 $q = p \cdot e^{-\varepsilon}$ 代入：

$$p + (k-1) \cdot p \cdot e^{-\varepsilon} = 1 \implies p(1 + (k-1)e^{-\varepsilon}) = 1$$

分子分母同乘 $e^\varepsilon$：

$$\boxed{p = \frac{e^\varepsilon}{k - 1 + e^\varepsilon}}$$

从而：

$$q = \frac{1 - p}{k - 1} = \frac{1}{k - 1 + e^\varepsilon}$$

**2. 纠偏估计量 $\hat{f}_j$ 的推导**

设类别 $j$ 的真实频率为 $f_j$。扰动后报告为类别 $j$ 的期望比例为：

$$\mathbb{E}[\hat{f}_{j,\text{reported}}] = f_j \cdot p + (1 - f_j) \cdot q$$

其中 $(1 - f_j) \cdot q$ 表示所有非 $j$ 类别的用户以概率 $q$ 被错误报告为 $j$。

展开：

$$\mathbb{E}[\hat{f}_{j,\text{reported}}] = f_j \cdot p + q - f_j \cdot q = f_j(p - q) + q$$

令观测值等于期望值，反解 $f_j$：

$$\boxed{\hat{f}_j = \frac{\hat{f}_{j,\text{reported}} - q}{p - q}}$$

**3. 估计量的方差**

类似二值情形的推导，纠偏估计量的方差为：

$$\text{Var}(\hat{f}_j) = \frac{\text{Var}(\hat{f}_{j,\text{reported}})}{(p - q)^2} = \frac{f_j(1 - f_j)}{n(p - q)^2}$$

其中 $p - q = \frac{e^\varepsilon - 1}{k - 1 + e^\varepsilon}$。当 $k = 2$ 时退化为二值情形，$p - q = 2p - 1 = \tanh(\varepsilon/2)$。

##### k-ary 频率估计与属性相关性处理

标准 k-ary 随机响应假设各属性值之间相互独立，但实际数据中属性往往存在相关性（如"操作系统 = Android"与"浏览器 = Chrome"高度共现）。直接在每个属性上独立施加随机响应会破坏属性间的联合分布，导致纠偏后的联合频率估计出现偏差。

**问题形式化**

设用户持有 $d$ 个属性 $\mathbf{v} = (v_1, v_2, \dots, v_d)$，其中 $v_i \in \{1, \dots, k_i\}$。若对每个属性独立施加 k-ary 随机响应，则扰动后的联合报告为 $\mathbf{y} = (y_1, \dots, y_d)$。纠偏时需要知道联合转移概率矩阵 $P(\mathbf{y} | \mathbf{v})$。

由于各属性独立扰动：

$$P(\mathbf{y} | \mathbf{v}) = \prod_{i=1}^{d} P(y_i | v_i)$$

其中每个 $P(y_i | v_i)$ 为对应属性的 k-ary 随机响应转移矩阵。

**纠偏估计**

对联合类别 $\mathbf{j} = (j_1, \dots, j_d)$ 的真实频率 $f_{\mathbf{j}}$，设扰动后报告为 $\mathbf{j}$ 的比例为 $\hat{f}_{\mathbf{j},\text{reported}}$，则：

$$\mathbb{E}[\hat{f}_{\mathbf{j},\text{reported}}] = \sum_{\mathbf{v}} f_{\mathbf{v}} \cdot P(\mathbf{j} | \mathbf{v}) = \sum_{\mathbf{v}} f_{\mathbf{v}} \prod_{i=1}^{d} P(j_i | v_i)$$

当属性数 $d$ 较大时，直接求解上述线性方程组的复杂度为 $O(\prod_i k_i^2)$，不可行。实际采用以下近似策略：

**1. 属性分组（Attribute Grouping）**

将高维属性按业务相关性分为若干小组，每组内部使用完整的联合转移矩阵纠偏，组间假设独立：

$$\hat{f}_{(G_1, G_2)} = \hat{f}_{G_1} \cdot \hat{f}_{G_2}$$

其中每个组 $G$ 的 $k_G = \prod_{i \in G} k_i$ 维转移矩阵大小为 $k_G \times k_G$，复杂度降为 $O(\sum_g k_g^2)$。

**2. 基于采样的迭代纠偏（Iterative Demarginalization）**

当无法合理分组时，使用迭代方法：

1. 初始化：假设所有属性独立，$\hat{f}^{(0)}_{\mathbf{v}} = \prod_i \hat{f}^{(0)}_{v_i}$。
2. E 步：利用当前联合估计 $\hat{f}^{(t)}$ 和转移矩阵，计算每个属性的边际纠偏估计。
3. M 步：用边际估计更新联合分布 $\hat{f}^{(t+1)}$，保持与观测边际一致。
4. 收敛后输出 $\hat{f}^{(\infty)}$。

该方法本质是 EM 算法在离散联合分布估计上的特化，通常 5-10 轮迭代收敛。

**3. 隐私预算分配**

对 $d$ 个属性分别施加随机响应时，由顺序组合定理，总隐私预算为 $\sum_{i=1}^d \varepsilon_i$。若所有属性使用相同的 $\varepsilon_0$，则总消耗为 $d \cdot \varepsilon_0$。可通过以下方式优化：

- **并行组合**：若属性组 $G_1, G_2$ 的数据来自不同用户（非同一用户的不同属性），则总预算为 $\max(\varepsilon_{G_1}, \varepsilon_{G_2})$。
- **预算加权**：对重要性不同的属性分配不同的 $\varepsilon_i$，重要属性分配更多预算以获得更低的估计方差。

##### RAPPOR（Randomized Aggregatable Privacy-Preserving Ordinal Reporting）

RAPPOR 是 Google 提出的一种面向高基数类别型数据的本地 DP 机制，核心思想是使用 Bloom Filter 编码 + 随机响应，适合域名、URL、应用名等高基数集合成员关系查询。

**编码流程**

每个用户持有一个类别值 $v$（如一个 URL），编码过程如下：

1. **Bloom Filter 编码**：用 $h$ 个哈希函数将 $v$ 映射到位数组 $B$ 的 $h$ 个位置，置为 1。
   $$B[j] = \begin{cases} 1 & \text{若 } \exists i \in \{1,\dots,h\}, H_i(v) = j \\ 0 & \text{其他} \end{cases}$$

2. **永久随机响应（Permanent Randomized Response, PRR）**：对 Bloom Filter 的每一位 $B[j]$ 独立施加随机扰动，生成 $B'[j]$：
   $$B'[j] = \begin{cases} B[j] & \text{概率 } f \text{（保持）} \\ 1 & \text{概率 } \frac{1-f}{2} \\ 0 & \text{概率 } \frac{1-f}{2} \end{cases}$$
   其中 $f$ 为"保持概率"（blinding factor），$f$ 越小隐私保护越强。

3. **瞬时随机响应（Instantaneous Randomized Response, IRR）**：对 PRR 的每一位 $B'[j]$ 再施加一层随机响应，生成最终报告 $B''[j]$：
   $$B''[j] = \begin{cases} B'[j] & \text{概率 } p \\ 1 - B'[j] & \text{概率 } 1 - p \end{cases}$$
   其中 $p = \frac{e^{\varepsilon/2}}{1 + e^{\varepsilon/2}}$（二值随机响应，消耗 $\varepsilon/2$）。

**隐私保证**

RAPPOR 通过两层随机响应实现隐私保护：

- PRR 层：每位独立扰动，提供 $(h \cdot \ln \frac{1+f}{1-f})$-LDP（对 Bloom Filter 整体）。
- IRR 层：提供 $(\varepsilon/2)$-LDP。
- 由顺序组合定理，整体满足 $\varepsilon_{\text{total}}$-LDP，其中 $\varepsilon_{\text{total}} = h \cdot \ln \frac{1+f}{1-f} + \varepsilon/2$（保守估计）。

实际部署中，Google Chrome 使用 $m = 1024$ 位 Bloom Filter，$h = 2$ 个哈希函数，$f = 0.5$，$p = e/(1+e)$。

**服务器端频率估计**

服务器收集 $n$ 个用户的 $B''$，对每个候选值 $v^*$：

1. 计算 $v^*$ 的 Bloom Filter 编码 $B_{v^*}$。
2. 统计所有用户报告中 $B''$ 与 $B_{v^*}$ 逐位匹配的比例。
3. 利用已知的转移概率矩阵进行纠偏，得到 $v^*$ 的频率估计。

单 bit 的纠偏公式为：

$$\Pr[B''[j] = 1 | v] = \frac{1}{2} + \frac{2p-1}{2} \cdot \left(f \cdot B_{v}[j] + \frac{1-f}{2}\right)$$

通过最大似然估计或最小二乘法拟合频率分布。

**RAPPOR 的优势与局限**

| 维度 | 优势 | 局限 |
|---|---|---|
| 高基数 | Bloom Filter 将任意基数映射到固定长度位数组 | 哈希冲突引入假阳性，需纠偏 |
| 集合成员查询 | 天然支持"某值是否在集合中"的查询 | 不支持直接的范围查询 |
| 通信开销 | 固定 $m$ bit 传输，与原始值大小无关 | $m$ 需足够大以控制假阳性率 |
| 隐私 | 双层随机响应，隐私保证清晰 | 相同 $\varepsilon$ 下效用低于直接 k-ary RR |

#### 3.7.2 本地直方图

本地直方图通过让每个用户独立扰动自己的类别值，再由服务器聚合纠偏得到总体分布估计。适用于：

- 浏览器/移动设备 telemetry 统计
- 用户偏好分布调查
- 不需要精确个体值的群体趋势分析

##### 基于随机响应的直方图估计

对于 $k$ 个类别的直方图，最直接的方式是对每个用户的类别值施加 k-ary 随机响应，服务器统计扰动后的频率向量 $\hat{\mathbf{f}}_{\text{reported}}$，再利用转移矩阵纠偏：

$$\hat{f}_j = \frac{\hat{f}_{j,\text{reported}} - q}{p - q}, \quad j = 1, \dots, k$$

该方法的方差为 $O\left(\frac{k}{n(p-q)^2}\right)$，当 $k$ 较大时方差显著增大。

##### 基于 RAPPOR 的高基数直方图

当类别数 $k$ 很大（如数千个 URL、应用名）时，标准 k-ary RR 的 $p - q$ 极小，导致纠偏方差不可接受。此时使用 RAPPOR + Bloom Filter 方案：

1. 每个用户将类别值编码为 Bloom Filter，经双层随机响应后上报。
2. 服务器维护候选值集合（可通过先验知识或探索阶段获得）。
3. 对每个候选值计算 Bloom Filter 编码，与所有用户报告逐位比较，利用最大似然估计拟合频率。

频率估计的精度取决于：

- Bloom Filter 位数 $m$：$m$ 越大，哈希冲突越少，假阳性率越低。
- 哈希函数数 $h$：最优 $h \approx \frac{m}{k_{\text{set}}} \ln 2$，其中 $k_{\text{set}}$ 为集合中元素数。
- 隐私参数 $f, p$：$f$ 越大（PRR 保持概率高），信号越强但隐私越弱。

##### 直方图估计的优化技术

**1. 前缀树编码（Prefix Tree）**

对于字符串型类别（如 URL、文件路径），使用 Trie 树将高基数类别组织为层次结构。每个节点代表一个前缀，用户只在 Trie 路径上报告，将有效 $k$ 从原始基数降低为路径长度 $O(\log k)$。

**2. 哈希分组（Hash Grouping）**

将 $k$ 个类别随机哈希到 $g \ll k$ 个桶中，对桶施加随机响应，再通过哈希逆映射估计原始频率。该方法将方差从 $O(k/n)$ 降低为 $O(g/n)$，但引入哈希碰撞偏差。

**3. 自适应精度控制**

通过两阶段协议实现自适应精度：

1. **探索阶段**：使用较大的 $\varepsilon_1$（较少隐私预算）进行粗粒度估计，识别出频率较高的"重 hitters"。
2. **精化阶段**：将剩余预算 $\varepsilon_2 = \varepsilon - \varepsilon_1$ 集中在重 hitters 上进行精细估计。

由顺序组合定理，总预算消耗为 $\varepsilon_1 + \varepsilon_2 = \varepsilon$。该策略在总预算固定时显著提高了高频类别的估计精度。

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

#### 3.8.1 概念说明

**Sidecar（边车）**

在本项目中，sidecar 指以“边车模式”伴随主应用部署的隐私保护服务进程。它与主应用运行在同一节点（如同一 Pod、同一主机），通过本地 REST/gRPC 接口提供隐私原语能力。主应用无需集成任何隐私保护 SDK，只需将需要差分隐私处理的中间结果发送给 sidecar，由 sidecar 完成噪声注入、预算扣减并返回结果。这种架构使得隐私保护逻辑与业务逻辑解耦，便于独立升级和审计。

```
┌─────────────────────────────────────┐
│  主应用 (Java/Go/Node.js/...)        │
│  ┌─────────────┐                    │
│  │ 数据聚合引擎 │─── 聚合结果 ───┐    │
│  └─────────────┘                │    │
└─────────────────────────────────┼────┘
                                  │ REST/gRPC (本地回环)
                          ┌───────┴───────┐
                          │   Sidecar     │
                          │ ┌───────────┐ │
                          │ │ Noisify   │ │
                          │ │ 噪声注入  │ │
                          │ │ 预算扣减  │ │
                          │ └───────────┘ │
                          └───────────────┘
```

**Noisify 接口**

Noisify 接口是 sidecar 提供的一类特殊 REST/gRPC 端点，面向“外部引擎已完成聚合，sidecar 仅负责注入噪声与预算扣减”的工作模式。与标准 DP 接口（接收原始数据、内部完成聚合+加噪）不同，Noisify 接口接收的是**已由外部引擎计算好的中间聚合结果**（如 `true_count`、`true_sum`、`true_counts`），sidecar 不再接触原始记录。

| 对比维度 | 标准 DP 接口 | Noisify 接口 |
|---|---|---|
| 输入 | 原始数据（values 列表） | 聚合后的标量/向量 |
| 聚合位置 | sidecar 内部 | 外部引擎（Spark/SQL/...） |
| 加噪位置 | sidecar 内部 | sidecar 内部（相同） |
| 敏感度来源 | sidecar 从原始数据自动推断 | 调用方显式提供 |
| 适用场景 | 数据量可传入 sidecar 内存 | 数据量过大、已在分布式引擎中聚合 |

典型场景包括：

- Spark / Flink / DuckDB / SQL 在数据源侧完成 `COUNT` / `SUM` / 直方图分桶。
- 调用方将中间聚合结果（如 `true_sum`、`true_count`、`true_counts`）发送到 sidecar。
- sidecar 根据调用方提供的敏感度计算噪声，加入结果后返回，并扣减命名空间预算。

#### 3.8.2 为什么需要调用方提供敏感度

##### 敏感度的定义与作用

在差分隐私中，**敏感度**（sensitivity）衡量的是单条记录的加入或删除对查询结果的最大影响量。它是决定噪声尺度的核心参数：

- **Laplace 机制**：噪声尺度 $b = \Delta f / \varepsilon$，其中 $\Delta f$ 为 L1 敏感度。
- **Gaussian 机制**：噪声标准差 $\sigma = \Delta_2 f \cdot \sqrt{2 \ln(1.25/\delta)} / \varepsilon$，其中 $\Delta_2 f$ 为 L2 敏感度。

敏感度越大，需要注入的噪声越多，才能保证相同的隐私保证 $(\varepsilon, \delta)$。

##### Noisify 接口无法自行推断敏感度的原因

在标准 DP 接口（`dp_count`、`dp_sum`、`dp_mean`）中，sidecar 直接接收原始数据，可以：

1. 观察数据的值域范围，自动计算 clipping 边界。
2. 根据查询类型推断敏感度（如 `count` 的敏感度恒为 1，`sum` 的敏感度为 `clip_upper - clip_lower`）。

但在 Noisify 接口中，sidecar **只接收聚合后的标量结果**（如 `true_sum = 123456.78`），原始记录已不可见。此时 sidecar 面临以下信息缺失：

| 信息 | 标准接口 | Noisify 接口 |
|---|---|---|
| 原始记录数 $n$ | 可见 | 不可见 |
| 单条记录的值域 | 可见，可自动 clip | 不可见 |
| 查询函数的敏感度 | 可自动推断 | 无法推断 |
| 数据分布范围 | 可计算 | 仅知聚合结果 |

**具体示例**：假设调用方发送 `noisy_sum(true_sum=123456.78)`。sidecar 无法知道：

- 这个总和是由 10 条记录（每条约 12345）还是 100 万条记录（每条约 0.12）累加而成。
- 单条记录的值域是 $[0, 100]$ 还是 $[0, 100000]$。
- 因此无法确定 `sum` 的敏感度应为 100 还是 100000。

敏感度不同，噪声尺度差异巨大：若 $\varepsilon = 1.0$，敏感度 100 时 Laplace 噪声尺度 $b = 100$；敏感度 100000 时 $b = 100000$，相差 1000 倍。

##### 调用方提供敏感度的两种方式

**方式一：直接指定 `sensitivity`**

调用方已了解数据的敏感度，直接传入：

```json
{
  "true_sum": 123456.78,
  "sensitivity": 100.0,
  "epsilon": 1.0,
  "mechanism": "laplace"
}
```

sidecar 直接使用 $\Delta f = 100.0$ 计算噪声。

**方式二：提供 `clip_lower` + `clip_upper`**

调用方告知 sidecar 原始数据在聚合前使用的裁剪边界：

```json
{
  "true_sum": 123456.78,
  "clip_lower": 0.0,
  "clip_upper": 100.0,
  "epsilon": 1.0,
  "mechanism": "laplace"
}
```

sidecar 自动计算 $\Delta f = \text{clip_upper} - \text{clip_lower} = 100.0$。

这种方式更推荐，因为：

1. **语义清晰**：`clip_lower` / `clip_upper` 直接对应数据裁剪的业务含义。
2. **一致性保证**：确保 Noisify 接口与标准接口使用相同的裁剪边界，避免因敏感度估计不一致导致隐私保证减弱或效用损失。
3. **审计友好**：裁剪边界可记录在审计日志中，便于合规检查。

##### 各查询类型的敏感度规则

| 查询 | 敏感度 | 推导 |
|---|---|---|
| `noisy_count` | $\Delta f = 1$ | 增删一条记录，计数最多变化 1。无需调用方提供。 |
| `noisy_sum` | $\Delta f = \text{clip_upper} - \text{clip_lower}$ | 增删一条记录，总和最多变化一条记录的值。需先裁剪才能确定边界。 |
| `noisy_mean` | count 部分 $\Delta f = 1$；sum 部分 $\Delta f = \text{clip_upper} - \text{clip_lower}$ | mean 通过组合 noisy_count 与 noisy_sum 实现，分别扣减预算。 |
| `noisy_histogram` | $\Delta f = 1$（联合敏感度） | 各桶互斥，一条记录只影响一个桶，联合敏感度为 1。 |

##### 敏感度设置不当的风险

- **敏感度过高**：噪声过大，查询结果几乎不可用。例如将 `sum` 的敏感度设为数据实际值域的 10 倍，噪声也会放大 10 倍。
- **敏感度过低**：隐私保证不足。若实际数据中存在超出声称敏感度的异常值，差分隐私的数学保证将被破坏。
- **建议实践**：在数据预处理阶段统一进行 clipping，并将裁剪边界作为数据管道的元数据传递给 Noisify 接口，确保敏感度与裁剪一致。

#### 3.8.3 接口映射

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
- Gaussian 机制下 `delta` 必须大于 0，且必须显式提供 `clip_lower` / `clip_upper`。
- Laplace 机制下若未提供 `clip_lower` / `clip_upper`，系统会自动从输入数据中自适应推断 `[min, max]` 作为截断区间并触发 Warning 警告；生产环境强烈建议显式指定。
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
