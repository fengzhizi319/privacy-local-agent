"""隐私计算服务编排层。

PrivacyService 作为 REST/gRPC 两种协议的统一业务入口，将上层请求路由到
具体的隐私原语实现（脱敏、差分隐私、K-匿名、查询混淆等），并负责参数解析。

Service orchestration layer. PrivacyService is the shared business entry point
for both REST and gRPC interfaces, routing requests to underlying privacy
primitives (masking, differential privacy, K-anonymity, query obfuscation) and
resolving parameters from profile/config.
"""

from typing import Any, Dict, List, Optional

from .privacy.budget import BudgetAccountant
from .privacy.classification import ClassificationAPI
from .privacy.dp import DPApi, LocalDPApi
from .privacy.kano import BUILTIN_HIERARCHIES, anonymize_record
from .privacy.kano_table import k_anonymize_table
from .privacy.masking import hash_value, mask_record, mask_value, truncate
from .privacy.profile import ParameterResolver, get_resolver
from .privacy.qol import obfuscate_query


class PrivacyService:
    """隐私计算统一服务类。

    持有 ParameterResolver（用于解析各原语参数）、DPApi（差分隐私预算管理）与 ClassificationAPI。
    所有 REST/gRPC handler 均委托给本类的实例方法，便于复用与单元测试。

    Attributes:
        resolver: 参数解析器，从 profile 与请求中解析并校验参数。
        namespace: 当前隐私命名空间，用于隔离预算。
        dp_api: 差分隐私 API 实例。
        classification_api: 数据分类 API 实例。
    """

    def __init__(self, profile_path: str = None, namespace: str = "default"):
        """初始化 PrivacyService。

        Args:
            profile_path: YAML 配置文件路径，可覆盖默认参数。
            namespace: 隐私命名空间，用于隔离隐私预算。
        """
        self.resolver = get_resolver(profile_path)
        self.namespace = namespace
        self.dp_api = DPApi(namespace)
        self.classification_api = ClassificationAPI(resolver=self.resolver)
        self.local_dp_api = LocalDPApi()

    def mask(self, field_name: str, value: str, context: str = "") -> str:
        """对单个字段值进行脱敏。

        Args:
            field_name: 字段名，用于推断敏感类型。
            value: 原始字段值。
            context: 上下文信息，透传给底层 masking 模块。

        Returns:
            脱敏后的字符串。
        """
        return mask_value(field_name, value, context)

    def mask_record(self, record: Dict[str, str], context: str = "") -> Dict[str, str]:
        """对整条记录进行脱敏。

        Args:
            record: 原始记录字典。
            context: 上下文信息，透传给底层 masking 模块。

        Returns:
            脱敏后的记录字典。
        """
        return mask_record(record, context)

    def hash(self, value: str, salt: str) -> str:
        """对字符串进行 HMAC 哈希。

        Args:
            value: 待哈希原始值。
            salt: 哈希盐值。

        Returns:
            16 字符长度的 base64 摘要。
        """
        return hash_value(value, salt)

    def truncate(self, value: str, keep_prefix: int) -> str:
        """截断字符串，保留指定前缀。

        Args:
            value: 原始字符串。
            keep_prefix: 保留的前缀长度。

        Returns:
            截断后的字符串。
        """
        return truncate(value, keep_prefix)

    def dp_count(self, values: List[float], params: Dict[str, Any] = None) -> float:
        """差分隐私计数。

        Args:
            values: 输入数值列表。
            params: 请求级 DP 参数，例如 {"epsilon": 1.0, "mechanism": "laplace"}。

        Returns:
            带噪声的计数值。
        """
        p = self.resolver.resolve("dp", params, namespace=self.namespace)
        return self.dp_api.count(
            values,
            float(p["epsilon"]),
            float(p.get("delta", 0.0)),
            str(p.get("mechanism", "laplace")),
        )

    def dp_sum(self, values: List[float], params: Dict[str, Any] = None) -> float:
        """差分隐私求和。

        Args:
            values: 输入数值列表。
            params: 请求级 DP 参数，可包含 clip_lower/clip_upper。

        Returns:
            带噪声的求和结果。
        """
        p = self.resolver.resolve("dp", params, namespace=self.namespace)
        return self.dp_api.sum(
            values,
            float(p["epsilon"]),
            float(p.get("delta", 0.0)),
            str(p.get("mechanism", "laplace")),
            clip_lower=p.get("clip_lower"),
            clip_upper=p.get("clip_upper"),
        )

    def dp_mean(self, values: List[float], params: Dict[str, Any] = None) -> float:
        """差分隐私均值。

        Args:
            values: 输入数值列表。
            params: 请求级 DP 参数，可包含 clip_lower/clip_upper、min_count。

        Returns:
            带噪声的均值。
        """
        p = self.resolver.resolve("dp", params, namespace=self.namespace)
        return self.dp_api.mean(
            values,
            float(p["epsilon"]),
            float(p.get("delta", 0.0)),
            str(p.get("mechanism", "laplace")),
            clip_lower=p.get("clip_lower"),
            clip_upper=p.get("clip_upper"),
            min_count=float(p.get("min_count", 5.0)),
        )

    def dp_histogram(
        self, values: List[Any], categories: List[Any], params: Dict[str, Any] = None
    ) -> Dict[Any, float]:
        """差分隐私直方图计数（使用联合敏感度为 1）。

        Args:
            values: 输入类别列表。
            categories: 目标类别集合。
            params: 请求级 DP 参数。

        Returns:
            分桶名到带噪计数的字典。
        """
        p = self.resolver.resolve("dp", params, namespace=self.namespace)
        return self.dp_api.histogram(
            values,
            categories,
            float(p["epsilon"]),
            float(p.get("delta", 0.0)),
            str(p.get("mechanism", "laplace")),
        )


    def perturb_binary_batch(self, values: List[int], epsilon: float) -> List[int]:
        """批量对二值数据进行本地 DP 扰动。"""
        return self.local_dp_api.perturb_binary_batch(values, epsilon)

    def perturb_categorical_batch(
        self, values: List[Any], categories: List[Any], epsilon: float
    ) -> List[Any]:
        """批量对类别型数据进行本地 DP 扰动。"""
        return self.local_dp_api.perturb_categorical_batch(values, categories, epsilon)

    def estimate_binary_frequency(self, reported_values: List[int], epsilon: float) -> float:
        """根据扰动后的二值样本估计真实比例为 1 的频率。"""
        return self.local_dp_api.estimate_binary_frequency(reported_values, epsilon)

    def estimate_categorical_histogram(
        self, reported_values: List[Any], categories: List[Any], epsilon: float
    ) -> Dict[Any, float]:
        """根据扰动后的类别样本估计各类别的真实频率。"""
        return self.local_dp_api.estimate_categorical_histogram(
            reported_values, categories, epsilon
        )

    def k_anonymize_record(
        self,
        record: Dict[str, Any],
        qi_cols: List[str],
        k: int = 5,
        hierarchies: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """对单条记录执行 K-匿名泛化。

        Args:
            record: 原始记录字典。
            qi_cols: 准标识符列名列表。
            k: K-匿名参数，决定泛化层级。
            hierarchies: 自定义泛化层次结构，当前 MVP 仅使用内置层次。

        Returns:
            泛化后的记录字典。
        """
        params = self.resolver.resolve("k_anonymity", {"k": k}, namespace=self.namespace)
        resolved_k = params.get("k", k)
        hiers = dict(BUILTIN_HIERARCHIES)
        if hierarchies:
            pass
        return anonymize_record(record, qi_cols, hiers, resolved_k)

    def k_anonymize_table(
        self,
        rows: List[Dict[str, Any]],
        qi_cols: List[str],
        k: int = 5,
        max_depth: int = 10,
    ) -> List[Dict[str, Any]]:
        """对整张表执行 K-匿名泛化。

        Args:
            rows: 原始记录列表。
            qi_cols: 准标识符列名列表.
            k: K-匿名阈值。
            max_depth: Mondrian 最大递归深度。

        Returns:
            泛化后的记录列表。
        """
        params = self.resolver.resolve(
            "k_anonymity",
            {"k": k, "max_depth": max_depth},
            namespace=self.namespace,
        )
        resolved_k = params.get("k", k)
        resolved_max_depth = params.get("max_depth", max_depth)
        return k_anonymize_table(rows, qi_cols, resolved_k, resolved_max_depth)

    def obfuscate_query(
        self,
        query: str,
        num_dummies: int = 3,
        domain: str = "medical",
        medical_pool: Optional[List[str]] = None,
        generic_pool: Optional[List[str]] = None,
    ) -> List[str]:
        """对查询进行混淆。

        Args:
            query: 真实查询字符串。
            num_dummies: 虚假查询数量。
            domain: 查询领域，影响 dummy 查询池选择。
            medical_pool: 自定义医疗 dummy 池。
            generic_pool: 自定义通用 dummy 池。

        Returns:
            混淆后的查询列表。
        """
        request_params = {"num_dummies": num_dummies}
        if medical_pool is not None:
            request_params["medical_pool"] = medical_pool
        if generic_pool is not None:
            request_params["generic_pool"] = generic_pool

        params = self.resolver.resolve("qol", request_params)
        resolved_num_dummies = params.get("num_dummies", num_dummies)
        resolved_medical_pool = params.get("medical_pool")
        resolved_generic_pool = params.get("generic_pool")
        return obfuscate_query(
            query,
            resolved_num_dummies,
            domain,
            medical_pool=resolved_medical_pool,
            generic_pool=resolved_generic_pool,
        )

    def recommend_and_save_params(
        self,
        values: List[float] = None,
        rows: List[Dict[str, Any]] = None,
        qi_cols: List[str] = None,
    ) -> Dict[str, Any]:
        """根据数据特点，自动推荐 DP 和 K-Anonymity 隐私参数并保存起来。"""
        from .privacy.profile import save_personalized_params
        recommendations = {}

        # 1. 推荐差分隐私（DP）参数
        if values:
            n = len(values)
            # 计算分位数推荐截断上下界
            sorted_vals = sorted(float(v) for v in values)
            p5_idx = int(n * 0.05)
            p95_idx = int(n * 0.95)
            clip_lower = sorted_vals[p5_idx] if n > 0 else 0.0
            clip_upper = sorted_vals[min(p95_idx, n - 1)] if n > 0 else 10.0
            if clip_lower == clip_upper:
                clip_lower -= 1.0
                clip_upper += 1.0

            # 推荐 delta = 1 / (10 * n^2) 并限制在 1e-5 以内
            recommended_delta = min(1e-5, 1.0 / (10.0 * (n ** 2))) if n > 0 else 1e-5

            dp_params = {
                "epsilon": 1.0,
                "delta": recommended_delta,
                "mechanism": "laplace",
                "clip_lower": clip_lower,
                "clip_upper": clip_upper,
            }
            save_personalized_params(self.namespace, "dp", dp_params)
            recommendations["dp"] = dp_params

        # 2. 推荐 K-Anonymity 参数
        if rows:
            n = len(rows)
            # 根据数据行数推荐 k 值为行数的 10%，在 [2, 10] 之间
            recommended_k = max(2, min(10, n // 10))
            kano_params = {
                "k": recommended_k,
                "max_depth": 10,
            }
            save_personalized_params(self.namespace, "k_anonymity", kano_params)
            recommendations["k_anonymity"] = kano_params

        return recommendations

    def budget_remaining(self) -> Dict[str, float]:
        """查询当前命名空间下剩余隐私预算。

        Returns:
            当前命名空间下 epsilon 与 delta 的剩余量字典。
        """
        return BudgetAccountant(self.namespace).remaining()

    def classify_field(
        self, field_name: str, value: Any, params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """对单个字段进行分类。"""
        return self.classification_api.classify_field(
            field_name, value, params
        ).model_dump(by_alias=True)

    def classify_record(
        self, record: Dict[str, Any], params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """对单条记录进行分类。"""
        return self.classification_api.classify_record(record, params).model_dump(
            by_alias=True
        )

    def classify_table(
        self,
        schema: List[str],
        rows: List[Dict[str, Any]],
        params: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """对整张表进行分类。"""
        return self.classification_api.classify_table(schema, rows, params).model_dump(
            by_alias=True
        )

    def classify_json(
        self, json_input: Any, params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """解析 JSON 字符串或字典并分类。"""
        return self.classification_api.classify_json(json_input, params).model_dump(
            by_alias=True
        )

    def classify_dataframe(
        self, df: Any, params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """对 pandas DataFrame 进行分类。"""
        return self.classification_api.classify_dataframe(df, params).model_dump(
            by_alias=True
        )

    def classify_arrow(
        self, table: Any, params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """对 pyarrow Table 进行分类。"""
        return self.classification_api.classify_arrow(table, params).model_dump(
            by_alias=True
        )

    def classify_sql_result(
        self, result_set: List[Dict[str, Any]], params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """对 SQL 结果集进行分类。"""
        return self.classification_api.classify_sql_result(result_set, params).model_dump(
            by_alias=True
        )
