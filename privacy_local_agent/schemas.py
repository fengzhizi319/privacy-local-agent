"""REST 请求模型集合（Pydantic）。

按域分组集中定义各端点的请求体模型，供 ``routers/*`` 子路由导入。
这些模型与 ``main.py`` 拆分前的定义保持完全一致，确保接口契约不变。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel

# --------------------------------------------------------------------------- #
# 脱敏 / 哈希
# --------------------------------------------------------------------------- #


class MaskRequest(BaseModel):
    """单字段脱敏请求模型。"""

    field_name: str
    value: str
    context: str = ""


class MaskRecordRequest(BaseModel):
    """整记录脱敏请求模型。"""

    record: Dict[str, str]
    context: str = ""


class MaskBatchRequest(BaseModel):
    """批量字段脱敏请求模型。"""

    field_names: List[str]
    values: List[str]
    context: str = ""


class MaskDataFrameRequest(BaseModel):
    """DataFrame 脱敏请求模型。

    data 为 records 列表（可来自 pandas/SecretFlow DataFrame 的转换）。
    columns 指定需要脱敏的列；未指定则对所有字符串列脱敏。
    """

    data: List[Dict[str, Any]]
    columns: Optional[List[str]] = None
    context: str = ""


class HashRequest(BaseModel):
    """HMAC 哈希请求模型。"""

    value: str
    salt: str


# --------------------------------------------------------------------------- #
# 差分隐私（DP）
# --------------------------------------------------------------------------- #


class DPRequest(BaseModel):
    """差分隐私聚合请求模型。

    values 为输入数据列表；params 为可选参数，用于覆盖默认或 profile 中的配置。
    """

    values: List[float]
    params: Dict[str, object] = {}


class DPHistogramRequest(BaseModel):
    """差分隐私直方图请求模型。"""

    values: List[str]
    categories: List[str]
    params: Dict[str, object] = {}


class DPNoisyCountRequest(BaseModel):
    """对已聚合计数进行 DP 加噪的请求模型。"""

    true_count: float
    params: Dict[str, object] = {}


class DPNoisySumRequest(BaseModel):
    """对已聚合求和进行 DP 加噪的请求模型。

    params 中需提供 sensitivity，或同时提供 clip_lower 与 clip_upper。
    """

    true_sum: float
    params: Dict[str, object] = {}


class DPNoisyMeanRequest(BaseModel):
    """对已聚合 sum/count 进行 DP 加噪得到均值的请求模型。"""

    true_sum: float
    true_count: float
    params: Dict[str, object] = {}


class DPNoisyHistogramRequest(BaseModel):
    """对已聚合直方图计数进行 DP 加噪的请求模型。"""

    true_counts: Dict[str, float]
    params: Dict[str, object] = {}


class DPChunkedCountRequest(BaseModel):
    """分块流式 DP 计数请求模型。"""

    chunks: List[List[float]]
    params: Dict[str, object] = {}


class DPChunkedSumRequest(BaseModel):
    """分块流式 DP 求和请求模型。"""

    chunks: List[List[float]]
    params: Dict[str, object] = {}


class DPChunkedMeanRequest(BaseModel):
    """分块流式 DP 均值请求模型。"""

    chunks: List[List[float]]
    params: Dict[str, object] = {}


class DPAggregateRequest(BaseModel):
    """表格级原位 DP 聚合请求模型。"""

    rows: List[Dict[str, Any]]
    specs: Dict[str, Any]
    params: Dict[str, object] = {}


class DPVectorSumRequest(BaseModel):
    """高维向量 / 梯度 $L_2$ 范数截断加噪请求模型。"""

    vectors: List[List[float]]
    params: Dict[str, object] = {}


class DPAdaptiveClipRequest(BaseModel):
    """差分隐私自适应二分搜索估计上下界请求模型。"""

    values: List[float]
    params: Dict[str, object] = {}


class DPGroupByRequest(BaseModel):
    """Tau-Thresholding 差分隐私 SQL Group-By 请求模型。"""

    rows: List[Dict[str, Any]]
    group_col: str
    target_col: str
    agg: str
    params: Dict[str, object] = {}


class DPChunkedHistogramRequest(BaseModel):
    """分块流式 DP 直方图请求模型。"""

    chunks: List[List[str]]
    categories: List[str]
    params: Dict[str, object] = {}


# --------------------------------------------------------------------------- #
# K-匿名
# --------------------------------------------------------------------------- #


class KAnonRequest(BaseModel):
    """K-匿名单条记录请求模型。"""

    record: Dict[str, object]
    qi_cols: List[str]
    k: int = 5


class KAnonTableRequest(BaseModel):
    """K-匿名整张表请求模型。"""

    rows: List[Dict[str, object]]
    qi_cols: List[str]
    k: int = 5
    max_depth: int = 10


class KAnonDataFrameRequest(BaseModel):
    """K-匿名 DataFrame 请求模型。

    data 为 records 列表（可来自 pandas/SecretFlow DataFrame）。
    """

    data: List[Dict[str, Any]]
    qi_cols: List[str]
    k: int = 5
    max_depth: int = 10


# --------------------------------------------------------------------------- #
# 查询混淆（QoL）
# --------------------------------------------------------------------------- #


class QolRequest(BaseModel):
    """查询混淆请求模型。"""

    query: str
    num_dummies: int = 3
    domain: str = "medical"
    medical_pool: Optional[List[str]] = None
    generic_pool: Optional[List[str]] = None
    seed: Optional[int] = None


class QolBatchRequest(BaseModel):
    """批量查询混淆请求模型。"""

    queries: List[str]
    num_dummies: int = 3
    domain: str = "medical"
    medical_pool: Optional[List[str]] = None
    generic_pool: Optional[List[str]] = None
    seed: Optional[int] = None


# --------------------------------------------------------------------------- #
# 本地差分隐私（LDP）
# --------------------------------------------------------------------------- #


class LdpPerturbBinaryRequest(BaseModel):
    """二值本地 DP 扰动请求模型。"""

    values: List[int]
    epsilon: float


class LdpPerturbCategoricalRequest(BaseModel):
    """类别型本地 DP 扰动请求模型。"""

    values: List[str]
    categories: List[str]
    epsilon: float


class LdpEstimateBinaryRequest(BaseModel):
    """二值本地 DP 估计请求模型。"""

    reported_values: List[int]
    epsilon: float


class LdpEstimateCategoricalRequest(BaseModel):
    """类别型本地 DP 估计请求模型。"""

    reported_values: List[str]
    categories: List[str]
    epsilon: float


# --------------------------------------------------------------------------- #
# 隐私参数推荐
# --------------------------------------------------------------------------- #


class RecommendRequest(BaseModel):
    """隐私参数推荐请求模型。"""

    namespace: str
    values: Optional[List[float]] = None
    rows: Optional[List[Dict[str, object]]] = None
    qi_cols: Optional[List[str]] = None
