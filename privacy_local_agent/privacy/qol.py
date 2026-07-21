"""查询混淆（Query Obfuscation）模块 / Query Obfuscation Primitive API Implementation.

中文说明：
通过向真实查询中混入若干条虚假查询（dummy queries），降低查询日志被分析时
泄露用户真实意图的风险。当前内置医疗领域与通用领域的 dummy 查询模板。
支持语义槽位替换（Slot-Filling）与长度相近抽样两种混淆策略。
内置输入校验、结构化日志与 Prometheus 指标埋点。

English Description:
Query obfuscation primitive. Mixes a real user query with dummy queries to
mitigate inference attacks against query logs. Built-in medical and generic
domain dummy query pools with semantic slot-filling and length-similarity strategies.
Built-in input validation, structured logging, and Prometheus metrics instrumentation.

扩展能力 / Key Features:
- 枚举类型安全：ObfuscationDomain / ObfuscationStrategy 枚举避免裸字符串拼写错误。
- 语义槽位替换：基于实体词库生成近邻语义 Dummy 查询，提升混淆质量。
- 结构化日志：每次操作记录领域、虚假查询数、策略等上下文信息。
- 输入校验：统一的参数合法性检查，快速失败并给出清晰错误信息。
- 批量处理：支持批量查询混淆，保持接口一致性。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional, Union

from ..observability.logging_config import get_logger
from ..observability.metrics import QOL_OPERATIONS_TOTAL

# Module-level structured logger for query obfuscation operations
logger = get_logger(__name__)


class ObfuscationDomain(str, Enum):
    """查询混淆领域枚举 / Query Obfuscation Domain Enum.

    继承 str 保证与字符串的向后兼容性：ObfuscationDomain.MEDICAL == "medical" 为 True。
    IDE 自动补全 + 静态类型检查，避免裸字符串拼写错误。
    """

    MEDICAL = "medical"
    GENERIC = "generic"


class ObfuscationStrategy(str, Enum):
    """查询混淆策略枚举 / Query Obfuscation Strategy Enum.

    继承 str 保证与字符串的向后兼容性。
    用于标识不同的混淆方法（语义槽位替换、长度相近抽样、混合策略）。
    """

    SLOT_FILLING = "slot_filling"      # 语义槽位替换 / Semantic slot-filling
    LENGTH_SIMILARITY = "length_similarity"  # 长度相近抽样 / Length-similarity sampling
    HYBRID = "hybrid"                  # 混合策略 / Hybrid (slot-filling + length fallback)


def _validate_query(query: str) -> None:
    """校验查询参数有效性 / Validate query parameter.

    Args:
        query: 待校验的查询字符串。

    Raises:
        ValueError: 当查询为空或不是字符串时抛出。
    """
    if not isinstance(query, str):
        raise ValueError(f"query must be a string, got {type(query).__name__}")
    if not query.strip():
        raise ValueError("query must not be empty or whitespace-only")


def _validate_num_dummies(num_dummies: int) -> None:
    """校验虚假查询数量参数有效性 / Validate num_dummies parameter.

    Args:
        num_dummies: 待校验的虚假查询数量。

    Raises:
        ValueError: 当数量不是正整数时抛出。
    """
    if not isinstance(num_dummies, int) or isinstance(num_dummies, bool):
        raise ValueError(f"num_dummies must be an integer, got {type(num_dummies).__name__}")
    if num_dummies < 1:
        raise ValueError(f"num_dummies must be at least 1, got {num_dummies}")


def _validate_domain(domain: str) -> str:
    """校验并规范化领域参数 / Validate and normalize domain parameter.

    Args:
        domain: 待校验的领域字符串。

    Returns:
        规范化后的领域字符串（小写）/ Normalized domain string (lowercase).

    Raises:
        ValueError: 当领域不是支持的值时抛出。
    """
    normalized = domain.lower().strip()
    if normalized not in (ObfuscationDomain.MEDICAL.value, ObfuscationDomain.GENERIC.value):
        raise ValueError(
            f"domain must be '{ObfuscationDomain.MEDICAL.value}' or '{ObfuscationDomain.GENERIC.value}', "
            f"got '{domain}'"
        )
    return normalized


@dataclass
class QoLResult:
    """查询混淆结果及结构化元数据包装。

    Attributes:
        queries: 包含真实查询与虚假 dummy 查询在内的混淆文本列表。
        real_query_index: 真实查询在混淆列表中的索引下划位置。
        domain: 应用的混淆领域（"medical" / "generic"）。
        num_dummies: 生成的 Dummy 虚假查询数量。
    """

    queries: List[str]
    real_query_index: int
    domain: str
    num_dummies: int

    def to_arrow(self):
        """将 QoLResult 包装转换为附带 查询混淆 Metadata 的 PyArrow Table。

        执行步骤：
        1. 提取 QoLResult 的混淆元数据（真实查询索引、领域、虚假查询数量）构造 JSON。
        2. 将元数据编码存入 Schema Metadata Key `b"qol_metadata"`。
        3. 构建 `queries` 列并生成 PyArrow Table 导出。
        """
        import json
        import pyarrow as pa

        meta = {
            "real_query_index": str(self.real_query_index),
            "domain": str(self.domain),
            "num_dummies": str(self.num_dummies),
        }
        custom_metadata = {b"qol_metadata": json.dumps(meta).encode("utf-8")}

        arr = pa.array(self.queries)
        table = pa.Table.from_arrays([arr], names=["obfuscated_query"])

        existing_meta = table.schema.metadata or {}
        merged_meta = {**existing_meta, **custom_metadata}
        return table.replace_schema_metadata(merged_meta)


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
    return_details: bool = False,
) -> Union[List[str], QoLResult]:
    """对单个查询进行混淆 / Obfuscate a Single Query with Dummy Queries.

    执行步骤 / Execution Steps:
    1. 校验 query、num_dummies、domain 参数有效性。
       (Validate query, num_dummies, and domain parameters)
    2. 根据 domain 选择医疗（medical）或通用（generic）虚假查询池。
       (Select medical or generic dummy pool by domain)
    3. 优先使用语义实体槽位（Slot-Filling）匹配并生成近邻语义 Dummy 查询。
       (Prefer semantic slot-filling to generate near-neighbor dummies)
    4. 若未能匹配实体，基于长度相近原则从 Dummy 池中抽取补齐。
       (Fallback to length-similarity sampling if no entity match)
    5. 将真实 query 随机插入到混淆列表中的某个随机位置。
       (Insert real query at random position in obfuscated list)
    6. 记录结构化日志并返回混淆列表或 QoLResult。
       (Emit structured log and return obfuscated list or QoLResult)

    Args:
        query: 待混淆的真实查询 / Real query to obfuscate.
        num_dummies: 生成的虚假查询数量 / Number of dummy queries to generate.
        domain: 混淆领域 / Obfuscation domain ("medical" or "generic").
        medical_pool: 自定义医疗领域虚假查询池 / Custom medical dummy pool.
        generic_pool: 自定义通用领域虚假查询池 / Custom generic dummy pool.
        seed: 可选随机种子 / Optional random seed for reproducibility.
        return_details: 是否返回 QoLResult / Whether to return QoLResult.

    Returns:
        混淆后的查询列表或 QoLResult / Obfuscated query list or QoLResult.

    Raises:
        ValueError: 当参数无效时 / When parameters are invalid.
    """
    _validate_query(query)
    _validate_num_dummies(num_dummies)
    domain = _validate_domain(domain)

    QOL_OPERATIONS_TOTAL.labels(domain=domain).inc()

    is_medical = domain == ObfuscationDomain.MEDICAL.value
    pool = (medical_pool if is_medical else generic_pool)
    if pool is None:
        pool = (MEDICAL_DUMMY if is_medical else GENERIC_DUMMY)

    dummies: List[str] = []
    rng = random.Random(seed)
    strategy_used = ObfuscationStrategy.LENGTH_SIMILARITY.value

    if (is_medical and medical_pool is None) or (not is_medical and generic_pool is None):
        matched_term = None
        terms_list = DISEASES if is_medical else ENTITIES
        placeholder = "{disease}" if is_medical else "{entity}"

        for t in terms_list:
            if t in query:
                matched_term = t
                break

        if matched_term:
            template = query.replace(matched_term, placeholder)
            choices = [t for t in terms_list if t != matched_term]
            if len(choices) >= num_dummies:
                selected_terms = rng.sample(choices, num_dummies)
                for st in selected_terms:
                    dummies.append(template.replace(placeholder, st))
                strategy_used = ObfuscationStrategy.SLOT_FILLING.value

    needed = num_dummies - len(dummies)
    if needed > 0:
        if dummies:
            strategy_used = ObfuscationStrategy.HYBRID.value
        filtered_pool = [p for p in pool if p != query]
        if not filtered_pool:
            filtered_pool = list(pool)

        query_len = len(query)
        close_candidates = [p for p in filtered_pool if abs(len(p) - query_len) <= 6]
        if not close_candidates:
            close_candidates = [p for p in filtered_pool if abs(len(p) - query_len) <= 12]
        if not close_candidates:
            close_candidates = filtered_pool

        while len(dummies) < num_dummies:
            dummies.append(rng.choice(close_candidates))

    pos = rng.randint(0, len(dummies))
    result = list(dummies)
    result.insert(pos, query)

    logger.info(
        "qol_obfuscate_query_completed",
        extra={
            "domain": domain,
            "num_dummies": num_dummies,
            "strategy": strategy_used,
            "real_query_index": pos,
        },
    )

    if return_details:
        return QoLResult(
            queries=result,
            real_query_index=pos,
            domain=domain,
            num_dummies=num_dummies,
        )
    return result


def obfuscate_query_batch(
    queries: List[str],
    num_dummies: int = 3,
    domain: str = "medical",
    medical_pool: List[str] = None,
    generic_pool: List[str] = None,
    seed: Optional[int] = None,
    return_details: bool = False,
) -> Union[List[List[str]], List[QoLResult]]:
    """批量对查询进行混淆 / Batch Obfuscate Queries with Dummy Queries.

    执行步骤 / Execution Steps:
    1. 校验 queries 参数为非空列表。
       (Validate queries is a non-empty list)
    2. 遍历 `queries` 数组，对每个查询依次调用 `obfuscate_query`。
       (Iterate queries and apply obfuscate_query per query)
    3. 记录结构化日志并返回混淆结果列表。
       (Emit structured log and return obfuscated results)

    Args:
        queries: 待混淆的查询列表 / List of queries to obfuscate.
        num_dummies: 每个查询生成的虚假查询数量 / Number of dummies per query.
        domain: 混淆领域 / Obfuscation domain.
        medical_pool: 自定义医疗领域虚假查询池 / Custom medical dummy pool.
        generic_pool: 自定义通用领域虚假查询池 / Custom generic dummy pool.
        seed: 可选随机种子 / Optional random seed.
        return_details: 是否返回 QoLResult 列表 / Whether to return QoLResult list.

    Returns:
        混淆后的查询列表的列表或 QoLResult 列表 / List of obfuscated query lists or QoLResults.

    Raises:
        ValueError: 当 queries 为空时 / When queries is empty.
    """
    if not queries:
        raise ValueError("queries must not be empty")
    if not isinstance(queries, list):
        raise ValueError(f"queries must be a list, got {type(queries).__name__}")

    logger.info(
        "qol_obfuscate_query_batch_started",
        extra={"num_queries": len(queries), "domain": domain, "num_dummies": num_dummies},
    )

    results = [
        obfuscate_query(
            query,
            num_dummies=num_dummies,
            domain=domain,
            medical_pool=medical_pool,
            generic_pool=generic_pool,
            seed=seed,
            return_details=return_details,
        )
        for query in queries
    ]

    logger.info(
        "qol_obfuscate_query_batch_completed",
        extra={"num_queries": len(queries), "domain": domain},
    )

    return results
