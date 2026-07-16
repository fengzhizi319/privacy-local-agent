"""查询混淆（Query Obfuscation）模块。

通过向真实查询中混入若干条虚假查询（dummy queries），降低查询日志被分析时
泄露用户真实意图的风险。当前内置医疗领域与通用领域的 dummy 查询模板。

Query obfuscation primitive. Mixes a real user query with dummy queries to
mitigate inference attacks against query logs.
"""

import random
from typing import List


# 医疗领域虚假查询池
MEDICAL_DUMMY = [
    "高血压患者的日常饮食建议",
    "糖尿病患者运动注意事项",
    "冠心病的早期症状有哪些",
    "流感疫苗接种人群建议",
    "儿童常见过敏反应处理",
]

# 通用领域虚假查询池
GENERIC_DUMMY = [
    "天气预报查询",
    "附近医院挂号流程",
    "健康档案如何查询",
    "医保报销比例说明",
    "体检报告解读指南",
]


def obfuscate_query(
    query: str,
    num_dummies: int = 3,
    domain: str = "medical",
    medical_pool: List[str] = None,
    generic_pool: List[str] = None,
) -> List[str]:
    """对单个查询进行混淆。

    根据 domain 选择对应的 dummy 查询池，随机抽取 num_dummies 条虚假查询，
    并将真实 query 随机插入到列表中的某个位置。

    Args:
        query: 用户真实查询字符串。
        num_dummies: 生成的虚假查询数量，默认 3。
        domain: 查询所属领域，"medical" 使用医疗 dummy 池，其他使用通用池。
        medical_pool: 自定义医疗 dummy 池，若指定则覆盖内置词库。
        generic_pool: 自定义通用 dummy 池，若指定则覆盖内置词库。

    Returns:
        混淆后的查询列表，长度为 num_dummies + 1，真实查询必在列表中。

    Note:
        当前使用非确定性随机数生成器，每次调用结果可能不同；
        若需要可重复性，可考虑传入固定随机种子。
    """
    # 根据领域选择 dummy 池（大小写不敏感）
    if domain.lower() == "medical":
        pool = medical_pool if medical_pool is not None else MEDICAL_DUMMY
    else:
        pool = generic_pool if generic_pool is not None else GENERIC_DUMMY
    rng = random.Random()
    # 随机抽取 num_dummies 条虚假查询（允许重复）
    dummies = [rng.choice(pool) for _ in range(num_dummies)]
    # 随机选择真实查询插入位置，范围 [0, len(dummies)]
    pos = rng.randint(0, len(dummies))
    dummies.insert(pos, query)
    return dummies
