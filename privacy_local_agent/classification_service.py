"""数据分类服务编排层。

ClassificationService 作为 REST/gRPC 两种协议的统一分类业务入口，
将上层请求路由到数据分类原语实现（ClassificationAPI），并负责参数解析。

Service orchestration layer for data classification. ClassificationService is the
shared business entry point for both REST and gRPC interfaces, routing requests
to the underlying ClassificationAPI primitive and resolving parameters.
"""

from typing import Any, Dict, List

from .observability.metrics import CLASSIFICATION_TOTAL
from .privacy.classification import ClassificationAPI
from .privacy.profile import ParameterResolver


class ClassificationService:
    """数据分类统一服务类。

    持有 ClassificationAPI 实例，所有 REST/gRPC 分类 handler 均委托给本类，
    便于复用与单元测试。

    Attributes:
        classification_api: 数据分类 API 实例。
    """

    def __init__(self, profile_path: str = None, resolver: ParameterResolver = None):
        """初始化 ClassificationService。

        Args:
            profile_path: YAML 配置文件路径，可覆盖默认参数。
            resolver: 共享的参数解析器。
        """
        self.classification_api = ClassificationAPI(profile_path=profile_path, resolver=resolver)

    def _record_metric(self, result: Dict[str, Any]) -> None:
        """Record a classification result metric if final_level is present."""
        final_level = result.get("finalLevel")
        if not final_level:
            return
        layer = result.get("engineLayer", "AGGREGATED")
        CLASSIFICATION_TOTAL.labels(final_level=str(final_level), layer=str(layer)).inc()

    def classify_field(
        self, field_name: str, value: Any, params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """对单个字段进行分类。

        Args:
            field_name: 字段名。
            value: 字段值。
            params: 请求级分类参数，可覆盖默认与 profile 配置。

        Returns:
            字段分类结果字典。
        """
        result = self.classification_api.classify_field(
            field_name, value, params
        ).model_dump(by_alias=True)
        self._record_metric(result)
        return result

    def classify_record(
        self, record: Dict[str, Any], params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """对单条记录进行分类。

        Args:
            record: 字段名到字段值的映射。
            params: 请求级分类参数。

        Returns:
            记录分类结果字典。
        """
        result = self.classification_api.classify_record(record, params).model_dump(
            by_alias=True
        )
        self._record_metric(result)
        return result

    def classify_table(
        self,
        schema: List[str],
        rows: List[Dict[str, Any]],
        params: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """对整张表进行分类。

        Args:
            schema: 列名列表。
            rows: 记录列表。
            params: 请求级分类参数。

        Returns:
            表分类结果字典。
        """
        result = self.classification_api.classify_table(schema, rows, params).model_dump(
            by_alias=True
        )
        self._record_metric(result)
        return result
