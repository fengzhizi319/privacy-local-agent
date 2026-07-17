"""数据分类服务编排层。

ClassificationService 作为 REST/gRPC 两种协议的统一分类业务入口，
将上层请求路由到数据分类原语实现（ClassificationAPI），并负责参数解析。
"""

from typing import Any, Dict, List, Optional

from .observability.metrics import CLASSIFICATION_TOTAL, CLASSIFICATION_TEMPLATES_TOTAL
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

    def _record_template_metric(self, params: Optional[Dict[str, Any]]) -> None:
        """Record compliance template usage metric."""
        template = (params or {}).get("template")
        if template:
            CLASSIFICATION_TEMPLATES_TOTAL.labels(template=str(template)).inc()

    def classify_field(
        self, field_name: str, value: Any, params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """对单个字段进行分类。"""
        self._record_template_metric(params)
        result = self.classification_api.classify_field(
            field_name, value, params
        ).model_dump(by_alias=True)
        self._record_metric(result)
        return result

    def classify_record(
        self, record: Dict[str, Any], params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """对单条记录进行分类。"""
        self._record_template_metric(params)
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
        """对整张表进行分类。"""
        self._record_template_metric(params)
        result = self.classification_api.classify_table(schema, rows, params).model_dump(
            by_alias=True
        )
        self._record_metric(result)
        return result

    def classify_secretflow(
        self,
        sf_data: Any,
        params: Dict[str, Any] = None,
        party: Optional[str] = None,
    ) -> Dict[str, Any]:
        """对 SecretFlow 数据结构进行分类。"""
        self._record_template_metric(params)
        result = self.classification_api.classify_secretflow(
            sf_data, params=params, party=party
        ).model_dump(by_alias=True)
        if result.get("tableResult"):
            self._record_metric(result["tableResult"])
        return result

    def submit_classify_table_async(
        self,
        schema: List[str],
        rows: List[Dict[str, Any]],
        params: Dict[str, Any] = None,
    ) -> str:
        """提交异步表分类任务，返回 job_id。"""
        self._record_template_metric(params)
        return self.classification_api.submit_classify_table_async(schema, rows, params)

    def get_job_result(self, job_id: str) -> Dict[str, Any]:
        """查询异步任务结果。"""
        job = self.classification_api.get_job_result(job_id)
        return job.model_dump(by_alias=True)

    def confirm_review(
        self,
        review_id: str,
        corrected_level: str,
        reviewer: str = "",
        comment: str = "",
    ) -> Dict[str, Any]:
        """确认或修正复核条目。"""
        entry = self.classification_api.confirm_review(
            review_id, corrected_level, reviewer, comment
        )
        return entry.model_dump(by_alias=True)

    def export_reviews(
        self, format: str = "jsonl", mask_input: bool = False
    ) -> str:
        """导出复核样本。"""
        return self.classification_api.export_reviews(format=format, mask_input=mask_input)
