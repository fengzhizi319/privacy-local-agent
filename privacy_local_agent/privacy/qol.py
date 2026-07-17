"""查询混淆（Query Obfuscation）模块。

通过向真实查询中混入若干条虚假查询（dummy queries），降低查询日志被分析时
泄露用户真实意图的风险。当前内置医疗领域与通用领域的 dummy 查询模板。

Query obfuscation primitive. Mixes a real user query with dummy queries to
mitigate inference attacks against query logs.
"""

import random
from typing import List, Optional

from ..observability.metrics import QOL_OPERATIONS_TOTAL


# 扩展后的内置医疗领域虚假查询词库（共 20 个）
MEDICAL_DUMMY = [
    "高血压患者的日常饮食建议",
    "糖尿病患者运动注意事项",
    "冠心病的早期症状有哪些",
    "流感疫苗接种人群建议",
    "儿童常见过敏反应处理",
    "胃溃疡患者吃什么食物好",
    "哮喘发作时的紧急处理方法",
    "慢性支气管炎的预防措施",
    "抑郁症自我调理与心理疏导",
    "长期失眠的危害及改善建议",
    "脂肪肝患者运动处方",
    "痛风患者避免食用的食物清单",
    "过敏性鼻炎的日常防治手段",
    "颈椎病康复训练操指南",
    "偏头痛的诱发因素与缓解方式",
    "脑梗塞前兆表现及预防建议",
    "骨质疏防摔倒安全提示",
    "带状疱疹的临床表现及治疗",
    "过敏性皮炎日常注意事项",
    "甲状腺结节患者饮食禁忌",
]

# 扩展后的内置通用领域虚假查询词库（共 20 个）
GENERIC_DUMMY = [
    "天气预报查询",
    "附近医院挂号流程",
    "健康档案如何查询",
    "医保报销比例说明",
    "体检报告解读指南",
    "公积金提取线上办理步骤",
    "个人所得税申报操作引导",
    "社保卡丢失如何在线补办",
    "市民卡网点营业时间查询",
    "生活垃圾分类最新标准",
    "最近的公共图书馆开放时间",
    "电动自行车上牌申领流程",
    "常用快递运费价格对比",
    "附近免费公共停车场推荐",
    "燃气费线上缴费使用指南",
    "自来水水质检测结果公告",
    "本地博物馆门票预约入口",
    "公交线路首末班车时间查询",
    "居住证积分申请材料清单",
    "数字证书在线更新流程",
]

# 语义实体词库，用于槽位替换
DISEASES = [
    "高血压", "糖尿病", "冠心病", "流感", "胃溃疡", 
    "哮喘", "脑梗塞", "痛风", "失眠", "抑郁症", "脂肪肝",
    "肺炎", "甲状腺结节", "过敏性鼻炎", "颈椎病"
]

ENTITIES = [
    "社保卡", "医保", "公积金", "健康档案", "体检报告", 
    "居住证", "天气预报", "市民卡", "数字证书", "身份证"
]


def obfuscate_query(
    query: str,
    num_dummies: int = 3,
    domain: str = "medical",
    medical_pool: List[str] = None,
    generic_pool: List[str] = None,
    seed: Optional[int] = None,
) -> List[str]:
    """对单个查询进行混淆。

    根据 domain 选择对应的 dummy 查询池，或在无自定义池时使用语义模板替换生成虚假查询，
    并将真实 query 随机插入到列表中的某个位置。

    Args:
        query: 用户真实查询字符串。
        num_dummies: 生成的虚假查询数量，默认 3。
        domain: 查询所属领域，"medical" 使用医疗 dummy 池，其他使用通用池。
        medical_pool: 自定义医疗 dummy 池，若指定则覆盖内置词库。
        generic_pool: 自定义通用 dummy 池，若指定则覆盖内置词库。
        seed: 可选随机种子，用于可复现测试。

    Returns:
        混淆后的查询列表，长度为 num_dummies + 1，真实查询必在列表中。
    """
    QOL_OPERATIONS_TOTAL.labels(domain=domain.lower()).inc()

    # 1. 确定基准池
    is_medical = domain.lower() == "medical"
    pool = (medical_pool if is_medical else generic_pool)
    if pool is None:
        pool = (MEDICAL_DUMMY if is_medical else GENERIC_DUMMY)

    dummies: List[str] = []
    rng = random.Random(seed)

    # 2. 如果使用内置池，尝试进行语义槽位（Slot-Filling）模板替换生成
    if (is_medical and medical_pool is None) or (not is_medical and generic_pool is None):
        matched_term = None
        terms_list = DISEASES if is_medical else ENTITIES
        placeholder = "{disease}" if is_medical else "{entity}"

        # 寻找 Query 中包含的已知实体词
        for t in terms_list:
            if t in query:
                matched_term = t
                break

        if matched_term:
            template = query.replace(matched_term, placeholder)
            # 过滤掉当前已命中的实体词
            choices = [t for t in terms_list if t != matched_term]
            if len(choices) >= num_dummies:
                # 随机抽取不重复的实体填入模板中
                selected_terms = rng.sample(choices, num_dummies)
                for st in selected_terms:
                    dummies.append(template.replace(placeholder, st))

    # 3. 兜底/补齐机制：若未命中模板，或者生成数量不够，从池中根据长度相近原则补齐
    needed = num_dummies - len(dummies)
    if needed > 0:
        # 排除掉与真实查询相同的条目
        filtered_pool = [p for p in pool if p != query]
        if not filtered_pool:
            filtered_pool = list(pool)

        # 尝试筛选与真实查询长度相差在 6 个字符以内的候选集以防御长度分析
        query_len = len(query)
        close_candidates = [p for p in filtered_pool if abs(len(p) - query_len) <= 6]
        if not close_candidates:
            # 扩展筛选到 12 字符
            close_candidates = [p for p in filtered_pool if abs(len(p) - query_len) <= 12]
        if not close_candidates:
            close_candidates = filtered_pool

        # 补齐所需数量
        while len(dummies) < num_dummies:
            dummies.append(rng.choice(close_candidates))

    # 4. 将真实查询插入随机位置
    pos = rng.randint(0, len(dummies))
    result = list(dummies)
    result.insert(pos, query)
    return result


def obfuscate_query_batch(
    queries: List[str],
    num_dummies: int = 3,
    domain: str = "medical",
    medical_pool: List[str] = None,
    generic_pool: List[str] = None,
    seed: Optional[int] = None,
) -> List[List[str]]:
    """批量对查询进行混淆。"""
    return [
        obfuscate_query(
            query,
            num_dummies=num_dummies,
            domain=domain,
            medical_pool=medical_pool,
            generic_pool=generic_pool,
            seed=seed,
        )
        for query in queries
    ]
