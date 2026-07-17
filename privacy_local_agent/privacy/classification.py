"""数据分类原语公共 API 入口。

提供 ClassificationAPI 以及向后兼容的公共符号导出。
具体实现已拆分到：
- classification_rule_engine.py（规则引擎）
- classification_utils.py（ZK 工具、合规模板、SecretFlow 适配）
- classification_composite.py（复合规则）
- classification_async.py（异步任务）
- classification_review.py（复核存储）
- classification_models.py（数据模型与抽象基类）
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .classification_composite import CompositeRuleEngine, apply_composite_tags
from .classification_models import (
    AuditInfo,
    ClassificationJob,
    ClassificationParams,
    ClassificationResult,
    CompositeRule,
    EngineLayer,
    FieldClassificationResult,
    LlmClassifier,
    NoOpLlmClassifier,
    NoOpSmallNerEngine,
    RecordClassificationResult,
    RuleEngineABC,
    SecurityTag,
    SensitivityLevel,
    ShadowDiff,
    SmallNerEngine,
    TableClassificationResult,
    max_level,
    parse_level,
)
from .classification_rule_engine import DefaultRuleEngine, RuleEngine, _unique_tags
from .classification_utils import classify_secretflow, get_template_params, redact
from .profile import ParameterResolver, get_resolver

_PRIMITIVE_VERSION = "1.0.0"
_RULE_ENGINE_VERSION = "1.0.0"

__all__ = [
    "ClassificationAPI",
    "RuleEngine",
    "RuleEngineABC",
    "DefaultRuleEngine",
    "SmallNerEngine",
    "NoOpSmallNerEngine",
    "LlmClassifier",
    "NoOpLlmClassifier",
]

# ---------------------------------------------------------------------------
# 参数治理 / Parameter governance
# ---------------------------------------------------------------------------

def _resolve_classification_params(
    resolver: Optional[ParameterResolver] = None,
    request_params: Optional[Dict[str, Any]] = None,
) -> Tuple[ClassificationParams, str]:
    """合并默认、合规模板、YAML profile、请求参数得到最终分类参数。

    Returns:
        (ClassificationParams, parameter_source) 元组。
    """
    params: Dict[str, Any] = {}
    source = "default"

    # 1. YAML profile
    if resolver is not None:
        profile_params = resolver.resolve("classification", request_params=None) or {}
        if profile_params:
            params.update(profile_params)
            source = "profile"

    # 2. 合规模板：根据 request/template 或 profile 中的 template 激活
    template_name = None
    if request_params and request_params.get("template"):
        template_name = request_params.get("template")
    elif params.get("template"):
        template_name = params.get("template")
    if template_name:
        template_defaults = get_template_params(template_name)
        # 模板默认值只在未设置时使用
        for key, value in template_defaults.items():
            if key not in params:
                params[key] = value

    # 3. 请求参数覆盖
    if request_params:
        params.update(request_params)
        source = "request"

    classification_params = ClassificationParams.model_validate(params)

    if classification_params.manual_override:
        source = "manual"

    return classification_params, source


# ---------------------------------------------------------------------------
# 分类 API / Classification API
# ---------------------------------------------------------------------------

class ClassificationAPI:
    """数据分类统一 API。

    支持字段、记录、表多级分类，内置默认规则引擎，可插拔 Small-NER 与 LLM，
    并提供 dict/JSON/DataFrame/Arrow/SQL 结果集等多种格式适配器。

    Args:
        profile_path: YAML 配置文件路径，用于覆盖默认参数。
        rule_engine: 规则引擎实例，默认 DefaultRuleEngine。
        small_ner: Small-NER 引擎实例，默认 NoOpSmallNerEngine。
        llm: LLM 分类器实例，默认 NoOpLlmClassifier。
        use_vectorized: 是否启用基于 pandas 的向量化规则引擎；未安装 pandas 时自动回退。
    """

    def __init__(
        self,
        profile_path: Optional[str] = None,
        rule_engine: Optional[RuleEngine] = None,
        small_ner: Optional[SmallNerEngine] = None,
        llm: Optional[LlmClassifier] = None,
        resolver: Optional[ParameterResolver] = None,
        composite_engine: Optional[CompositeRuleEngine] = None,
        async_manager: Optional[Any] = None,
        review_store: Optional[Any] = None,
        use_vectorized: bool = False,
    ):
        self.resolver = resolver or get_resolver(profile_path)
        if rule_engine is None:
            if use_vectorized:
                try:
                    from .classification_vectorized import VectorizedRuleEngine

                    self.rule_engine = VectorizedRuleEngine()
                except ImportError:
                    import logging

                    logging.getLogger("privacy.classification").warning(
                        "use_vectorized=True but pandas is not installed; "
                        "falling back to DefaultRuleEngine"
                    )
                    self.rule_engine = DefaultRuleEngine()
            else:
                self.rule_engine = DefaultRuleEngine()
        else:
            self.rule_engine = rule_engine
        self.composite_engine = composite_engine or CompositeRuleEngine()
        # 延迟导入可选模块，避免循环依赖
        if async_manager is None:
            from .classification_async import AsyncClassificationManager
            async_manager = AsyncClassificationManager()
        self.async_manager = async_manager
        if review_store is None:
            from .classification_review import ReviewStore
            review_store = ReviewStore()
        self.review_store = review_store
        if small_ner is None:
            # 自动选择最合适的本地 NER 引擎 (优先选择高性能 ONNX，其次选择 ModelScope 官方管道)
            import os
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(current_dir))
            onnx_path = os.path.join(project_root, ".models", "raner_cmeee.onnx")

            if os.path.exists(onnx_path):
                try:
                    from .classification_ner import ONNXSmallNerEngine
                    self.small_ner = ONNXSmallNerEngine()
                except ImportError:
                    self.small_ner = NoOpSmallNerEngine()
            else:
                try:
                    from .classification_ner import ModelScopeSmallNerEngine
                    self.small_ner = ModelScopeSmallNerEngine()
                except ImportError:
                    self.small_ner = NoOpSmallNerEngine()
        else:
            self.small_ner = small_ner
        if llm is None:
            try:
                from .classification_llm import Qwen2VLClassifier

                candidate = Qwen2VLClassifier()
                # 如果本地模型目录不存在，直接降级为 NoOp，避免 readiness 长期为 false
                if not os.path.isdir(candidate.model_path):
                    self.llm = NoOpLlmClassifier()
                else:
                    self.llm = candidate
            except ImportError:
                self.llm = NoOpLlmClassifier()
        else:
            self.llm = llm

    # ------------------------------------------------------------------
    # 核心方法 / Core methods
    # ------------------------------------------------------------------

    def classify_field(
        self,
        field_name: str,
        value: Any,
        params: Optional[Dict[str, Any]] = None,
    ) -> FieldClassificationResult:
        """对单个字段进行分类。

        Args:
            field_name: 字段名。
            value: 字段值。
            params: 请求级参数，可覆盖默认与 profile 配置。

        Returns:
            FieldClassificationResult。
        """
        return self._classify_field_internal(field_name, value, params)

    def _classify_field_internal(
        self,
        field_name: str,
        value: Any,
        params: Optional[Dict[str, Any]] = None,
        initial_tags: Optional[List[SecurityTag]] = None,
    ) -> FieldClassificationResult:
        """对单个字段进行分类，允许调用方预计算 Layer-1 规则标签。

        Args:
            field_name: 字段名。
            value: 字段值。
            params: 请求级参数。
            initial_tags: 若已批量计算好 Layer-1 标签则直接传入，否则按常规流程评估。

        Returns:
            FieldClassificationResult。
        """
        cp, source = _resolve_classification_params(self.resolver, params)

        tags: List[SecurityTag] = list(initial_tags) if initial_tags is not None else []
        engine_layer = EngineLayer.L1_RULE
        reasoning = ""

        if initial_tags is None and cp.enable_rule_engine:
            tags = self.rule_engine.evaluate(field_name, value, cp)

        final_level = max_level(*(t.level for t in tags)) if tags else cp.default_level
        confidence = 1.0 if tags else 0.0

        if tags:
            rule_ids = [t.rule_id for t in tags if t.rule_id]
            reasoning = "命中规则: " + ", ".join(rule_ids)

        # Layer 2: Small-NER
        if cp.enable_small_ner and (not tags or final_level.value <= SensitivityLevel.L3.value):
            ner_tags = self._run_small_ner(field_name, value)
            if ner_tags:
                tags.extend(ner_tags)
                final_level = max_level(final_level, *(t.level for t in ner_tags))
                confidence = max(confidence, max(t.confidence for t in ner_tags))
                engine_layer = EngineLayer.L2_SMALL_NER

        # Layer 3: LLM fallback
        if cp.enable_llm or (not cp.enable_llm and confidence < 0.6):
            llm_result = self.llm.classify(str(value), final_level, confidence)
            if llm_result:
                final_level = parse_level(llm_result.get("final_level", final_level))
                confidence = float(llm_result.get("confidence", confidence))
                reasoning = str(llm_result.get("reasoning", reasoning))
                engine_layer = EngineLayer.L3_LLM

        # 人工覆盖 / Manual override
        overridden_level = cp.apply_manual_override(field_name, final_level)
        if overridden_level != final_level:
            manual_tag = SecurityTag(
                level=overridden_level,
                category="MANUAL_OVERRIDE",
                confidence=1.0,
                source_engine="MANUAL",
                rule_id="MANUAL_001",
                needs_human_review=False,
            )
            tags = _unique_tags([manual_tag] + tags)
            final_level = overridden_level
            engine_layer = EngineLayer.L1_RULE
            reasoning = f"人工覆盖为 {final_level.value}"
            source = "manual"

        return FieldClassificationResult(
            field_name=field_name,
            field_value=str(value) if value is not None and cp.return_field_values else None,
            tags=tags,
            final_level=final_level,
            confidence=confidence,
            engine_layer=engine_layer,
            needs_human_review=any(t.needs_human_review for t in tags),
            reasoning=reasoning,
        )

    def _run_small_ner(self, field_name: str, value: Any) -> List[SecurityTag]:
        """执行 Small-NER 并转换为 SecurityTag。"""
        entities = self.small_ner.extract(str(value) if value is not None else "")
        tags: List[SecurityTag] = []

        # 敏感疾病关键字字典，用于判定是否升级至 L4
        sensitive_keywords = ["hiv", "精神分裂", "艾滋", "梅毒", "肿瘤", "癌症", "白血病", "抑郁症"]

        for ent in entities:
            label = ent.get("label", "")
            text = str(ent.get("text", "")).lower()
            conf = float(ent.get("confidence", 0.0))

            if label == "GENOMIC_HINT":
                tags.append(
                    SecurityTag(
                        level=SensitivityLevel.L5,
                        category="GENOMIC_HINT",
                        confidence=conf,
                        source_engine="SMALL_NER",
                        rule_id="NER_GENE_001",
                        needs_human_review=True,
                    )
                )
            elif label == "MEDICAL_DISEASE":
                # 检查是否命中敏感疾病关键字以确定是 L4 还是 L3
                if any(kw in text for kw in sensitive_keywords):
                    tags.append(
                        SecurityTag(
                            level=SensitivityLevel.L4,
                            category="MEDICAL_SENSITIVE_DISEASE",
                            confidence=conf,
                            source_engine="SMALL_NER",
                            rule_id="NER_DIS_SENSITIVE",
                        )
                    )
                else:
                    tags.append(
                        SecurityTag(
                            level=SensitivityLevel.L3,
                            category="MEDICAL_DISEASE",
                            confidence=conf,
                            source_engine="SMALL_NER",
                            rule_id="NER_DIS_NORMAL",
                        )
                    )
            elif label == "MEDICATION":
                tags.append(
                    SecurityTag(
                        level=SensitivityLevel.L3,
                        category="MEDICATION",
                        confidence=conf,
                        source_engine="SMALL_NER",
                        rule_id="NER_DRU_001",
                    )
                )
            elif label == "SURGERY":
                tags.append(
                    SecurityTag(
                        level=SensitivityLevel.L3,
                        category="SURGERY",
                        confidence=conf,
                        source_engine="SMALL_NER",
                        rule_id="NER_PRO_001",
                    )
                )
            elif label == "BODY_PART":
                tags.append(
                    SecurityTag(
                        level=SensitivityLevel.L3,
                        category="BODY_PART",
                        confidence=conf,
                        source_engine="SMALL_NER",
                        rule_id="NER_BOD_001",
                    )
                )
        return tags

    def classify_record(
        self,
        record: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None,
        record_index: int = 0,
    ) -> RecordClassificationResult:
        """对单条记录进行分类。

        Args:
            record: 字段名到字段值的映射。
            params: 请求级参数。
            record_index: 记录索引，用于输出。

        Returns:
            RecordClassificationResult。
        """
        field_results: Dict[str, FieldClassificationResult] = {}
        aggregated_tags: List[SecurityTag] = []

        for field_name, value in record.items():
            field_result = self.classify_field(field_name, value, params)
            field_results[field_name] = field_result
            aggregated_tags.extend(field_result.tags)

        aggregated_tags = _unique_tags(aggregated_tags)
        final_level = (
            max_level(*(fr.final_level for fr in field_results.values()))
            if field_results
            else SensitivityLevel.L1
        )
        confidence = (
            max(fr.confidence for fr in field_results.values())
            if field_results
            else 0.0
        )

        record_result = RecordClassificationResult(
            record_index=record_index,
            field_results=field_results,
            aggregated_tags=aggregated_tags,
            final_level=final_level,
            confidence=confidence,
            needs_human_review=any(fr.needs_human_review for fr in field_results.values()),
        )

        # 复合/上下文敏感规则后处理
        cp, _ = _resolve_classification_params(self.resolver, params)
        custom_rules = None
        if cp.composite_rules:
            custom_rules = [CompositeRule.model_validate(r) for r in cp.composite_rules]
        engine = (
            CompositeRuleEngine(custom_rules)
            if custom_rules
            else self.composite_engine
        )
        composite_tags = engine.evaluate(record, field_results)
        record_result = apply_composite_tags(record_result, composite_tags)
        return record_result

    def classify_table(
        self,
        schema: List[str],
        rows: List[Dict[str, Any]],
        params: Optional[Dict[str, Any]] = None,
    ) -> TableClassificationResult:
        """对整张表进行分类。

        Args:
            schema: 列名列表，决定输出顺序。
            rows: 记录列表，每条记录是字段名到字段值的字典。
            params: 请求级参数。

        Returns:
            TableClassificationResult。
        """
        cp, _ = _resolve_classification_params(self.resolver, params)
        if rows and hasattr(self.rule_engine, "evaluate_series"):
            return self._classify_table_vectorized(schema, rows, params, cp)
        record_results: List[RecordClassificationResult] = []
        aggregated_tags: List[SecurityTag] = []
        review_entries: List[Any] = []

        for idx, row in enumerate(rows):
            # 仅保留 schema 中存在的字段 / keep only columns present in schema
            filtered = {col: row.get(col) for col in schema if col in row}
            record_result = self.classify_record(filtered, params, record_index=idx)
            record_results.append(record_result)
            aggregated_tags.extend(record_result.aggregated_tags)
            if cp.enable_review:
                review_entries.extend(
                    self.review_store.add_from_record(record_result, original_record=row)
                )

        aggregated_tags = _unique_tags(aggregated_tags)
        final_level = (
            max_level(*(rr.final_level for rr in record_results))
            if record_results
            else SensitivityLevel.L1
        )
        confidence = (
            max(rr.confidence for rr in record_results) if record_results else 0.0
        )

        shadow_diff: List[ShadowDiff] = []
        if cp.shadow_mode and cp.shadow_version:
            shadow_keys = {
                "shadow_mode", "shadowMode", "shadow_version", "shadowVersion",
                "rule_set_version", "ruleSetVersion",
            }
            shadow_params = {k: v for k, v in (params or {}).items() if k not in shadow_keys}
            shadow_params["rule_set_version"] = cp.shadow_version
            shadow_params["shadow_mode"] = False
            shadow_result = self.classify_table(schema, rows, shadow_params)
            shadow_diff = self._compute_shadow_diff(record_results, shadow_result.record_results)

        return TableClassificationResult(
            schema=schema,
            record_results=record_results,
            aggregated_tags=aggregated_tags,
            final_level=final_level,
            confidence=confidence,
            needs_human_review=any(rr.needs_human_review for rr in record_results),
            review_entries=review_entries,
            shadow_diff=shadow_diff,
        )

    def _classify_table_vectorized(
        self,
        schema: List[str],
        rows: List[Dict[str, Any]],
        params: Optional[Dict[str, Any]],
        cp: ClassificationParams,
    ) -> TableClassificationResult:
        """使用向量化规则引擎对整张表进行分类。

        仅当 ``self.rule_engine`` 提供 ``evaluate_series`` 时由 ``classify_table`` 调用。
        规则引擎批量计算 Layer-1 标签，NER/LLM/复合规则/复核仍按记录处理。
        """
        import pandas as pd

        df = pd.DataFrame(rows, columns=schema)
        record_results: List[RecordClassificationResult] = []
        aggregated_tags: List[SecurityTag] = []
        review_entries: List[Any] = []

        # 批量计算每列的 Layer-1 标签
        column_tags: Dict[str, List[List[SecurityTag]]] = {}
        if cp.enable_rule_engine:
            for field_name in schema:
                column_tags[field_name] = self.rule_engine.evaluate_series(
                    field_name, df[field_name], cp
                )
        else:
            empty: List[List[SecurityTag]] = [[] for _ in range(len(rows))]
            for field_name in schema:
                column_tags[field_name] = empty

        for idx, row in enumerate(rows):
            field_results: Dict[str, FieldClassificationResult] = {}
            for field_name in schema:
                value = row.get(field_name)
                initial_tags = column_tags[field_name][idx]
                field_result = self._classify_field_internal(
                    field_name, value, params, initial_tags=initial_tags
                )
                field_results[field_name] = field_result

            aggregated = _unique_tags(
                [tag for fr in field_results.values() for tag in fr.tags]
            )
            final_level = (
                max_level(*(fr.final_level for fr in field_results.values()))
                if field_results
                else SensitivityLevel.L1
            )
            confidence = (
                max(fr.confidence for fr in field_results.values())
                if field_results
                else 0.0
            )

            record_result = RecordClassificationResult(
                record_index=idx,
                field_results=field_results,
                aggregated_tags=aggregated,
                final_level=final_level,
                confidence=confidence,
                needs_human_review=any(
                    fr.needs_human_review for fr in field_results.values()
                ),
            )

            # 复合/上下文敏感规则后处理
            cp2, _ = _resolve_classification_params(self.resolver, params)
            custom_rules = None
            if cp2.composite_rules:
                custom_rules = [
                    CompositeRule.model_validate(r) for r in cp2.composite_rules
                ]
            engine = (
                CompositeRuleEngine(custom_rules)
                if custom_rules
                else self.composite_engine
            )
            composite_tags = engine.evaluate(row, field_results)
            record_result = apply_composite_tags(record_result, composite_tags)

            record_results.append(record_result)
            aggregated_tags.extend(record_result.aggregated_tags)
            if cp.enable_review:
                review_entries.extend(
                    self.review_store.add_from_record(record_result, original_record=row)
                )

        aggregated_tags = _unique_tags(aggregated_tags)
        final_level = (
            max_level(*(rr.final_level for rr in record_results))
            if record_results
            else SensitivityLevel.L1
        )
        confidence = (
            max(rr.confidence for rr in record_results) if record_results else 0.0
        )

        shadow_diff: List[ShadowDiff] = []
        if cp.shadow_mode and cp.shadow_version:
            shadow_keys = {
                "shadow_mode",
                "shadowMode",
                "shadow_version",
                "shadowVersion",
                "rule_set_version",
                "ruleSetVersion",
            }
            shadow_params = {
                k: v for k, v in (params or {}).items() if k not in shadow_keys
            }
            shadow_params["rule_set_version"] = cp.shadow_version
            shadow_params["shadow_mode"] = False
            shadow_result = self.classify_table(schema, rows, shadow_params)
            shadow_diff = self._compute_shadow_diff(
                record_results, shadow_result.record_results
            )

        return TableClassificationResult(
            schema=schema,
            record_results=record_results,
            aggregated_tags=aggregated_tags,
            final_level=final_level,
            confidence=confidence,
            needs_human_review=any(rr.needs_human_review for rr in record_results),
            review_entries=review_entries,
            shadow_diff=shadow_diff,
        )

    def _compute_shadow_diff(
        self,
        current_results: List[RecordClassificationResult],
        shadow_results: List[RecordClassificationResult],
    ) -> List[ShadowDiff]:
        """计算当前结果与影子结果的差异。"""
        diffs: List[ShadowDiff] = []
        for cur, shw in zip(current_results, shadow_results):
            for field_name in set(cur.field_results.keys()) | set(shw.field_results.keys()):
                cur_field = cur.field_results.get(field_name)
                shw_field = shw.field_results.get(field_name)
                if not cur_field or not shw_field:
                    continue
                if cur_field.final_level != shw_field.final_level:
                    diffs.append(
                        ShadowDiff(
                            field_name=field_name,
                            record_index=cur.record_index,
                            current_level=cur_field.final_level,
                            shadow_level=shw_field.final_level,
                            current_tags=[str(tag) for tag in cur_field.tags],
                            shadow_tags=[str(tag) for tag in shw_field.tags],
                        )
                    )
        return diffs

    # ------------------------------------------------------------------
    # 多格式适配器 / Format adapters
    # ------------------------------------------------------------------

    def classify_json(
        self,
        json_input: Any,
        params: Optional[Dict[str, Any]] = None,
    ) -> ClassificationResult:
        """解析 JSON 字符串或字典并分类。

        若顶层为 dict 则按单条记录分类；若为 list 则按表分类（schema 取并集）。

        Args:
            json_input: JSON 字符串、字典或列表。
            params: 请求级参数。

        Returns:
            ClassificationResult。
        """
        if isinstance(json_input, str):
            data = json.loads(json_input)
        else:
            data = json_input

        cp, source = _resolve_classification_params(self.resolver, params)

        if isinstance(data, dict):
            record_result = self.classify_record(data, params)
            return ClassificationResult(
                record_result=record_result,
                audit_info=_build_audit_info(cp, source),
            )

        if isinstance(data, list) and data:
            schema = sorted({col for row in data for col in row.keys()})
            table_result = self.classify_table(schema, data, params)
            return ClassificationResult(
                table_result=table_result,
                audit_info=_build_audit_info(cp, source),
            )

        if isinstance(data, list) and not data:
            return ClassificationResult(
                table_result=TableClassificationResult(schema=[], final_level=SensitivityLevel.L1),
                audit_info=_build_audit_info(cp, source),
            )

        raise ValueError("JSON input must be a dict or a list of dicts")

    def classify_dataframe(
        self,
        df: Any,
        params: Optional[Dict[str, Any]] = None,
    ) -> ClassificationResult:
        """对 pandas DataFrame 进行分类（可选依赖，未安装时抛出 ImportError）。

        Args:
            df: pandas.DataFrame 实例。
            params: 请求级参数。

        Returns:
            ClassificationResult。
        """
        import pandas as pd

        if not isinstance(df, pd.DataFrame):
            raise TypeError("classify_dataframe expects a pandas.DataFrame")
        schema = list(df.columns)
        rows = df.to_dict(orient="records")
        table_result = self.classify_table(schema, rows, params)
        cp, source = _resolve_classification_params(self.resolver, params)
        return ClassificationResult(
            table_result=table_result,
            audit_info=_build_audit_info(cp, source),
        )

    def classify_arrow(
        self,
        table: Any,
        params: Optional[Dict[str, Any]] = None,
    ) -> ClassificationResult:
        """对 pyarrow Table 进行分类（可选依赖，未安装时抛出 ImportError）。

        Args:
            table: pyarrow.Table 实例。
            params: 请求级参数。

        Returns:
            ClassificationResult。
        """
        import pyarrow as pa

        if not isinstance(table, pa.Table):
            raise TypeError("classify_arrow expects a pyarrow.Table")
        schema = list(table.column_names)
        rows = table.to_pandas().to_dict(orient="records")
        table_result = self.classify_table(schema, rows, params)
        cp, source = _resolve_classification_params(self.resolver, params)
        return ClassificationResult(
            table_result=table_result,
            audit_info=_build_audit_info(cp, source),
        )

    def classify_sql_result(
        self,
        result_set: List[Dict[str, Any]],
        params: Optional[Dict[str, Any]] = None,
    ) -> ClassificationResult:
        """对 SQL 结果集（list[dict]）进行分类。

        Args:
            result_set: 查询结果列表，每条记录为字段名到字段值的字典。
            params: 请求级参数。

        Returns:
            ClassificationResult。
        """
        if not result_set:
            cp, source = _resolve_classification_params(self.resolver, params)
            return ClassificationResult(
                table_result=TableClassificationResult(
                    schema=[], final_level=SensitivityLevel.L1
                ),
                audit_info=_build_audit_info(cp, source),
            )
        schema = sorted({col for row in result_set for col in row.keys()})
        table_result = self.classify_table(schema, result_set, params)
        cp, source = _resolve_classification_params(self.resolver, params)
        return ClassificationResult(
            table_result=table_result,
            audit_info=_build_audit_info(cp, source),
        )

    def classify_secretflow(
        self,
        sf_data: Any,
        params: Optional[Dict[str, Any]] = None,
        party: Optional[str] = None,
    ) -> ClassificationResult:
        """对 SecretFlow 联邦数据结构进行分类。

        Args:
            sf_data: SecretFlow DataFrame / HDataFrame / VDataFrame / FedNdarray。
            params: 请求级分类参数。
            party: HDataFrame 参与方。

        Returns:
            ClassificationResult。
        """
        table_result = classify_secretflow(self, sf_data, params=params, party=party)
        cp, source = _resolve_classification_params(self.resolver, params)
        return ClassificationResult(
            table_result=table_result,
            audit_info=_build_audit_info(cp, source),
        )

    def submit_classify_table_async(
        self,
        schema: List[str],
        rows: List[Dict[str, Any]],
        params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """提交异步表分类任务。

        Args:
            schema: 列名列表。
            rows: 记录列表。
            params: 请求级参数。

        Returns:
            异步任务 ID。
        """
        return self.async_manager.submit(self.classify_table, schema, rows, params)

    def get_job_result(self, job_id: str) -> ClassificationJob:
        """查询异步分类任务结果。

        Args:
            job_id: 任务 ID。

        Returns:
            ClassificationJob。
        """
        return self.async_manager.get(job_id)

    def is_llm_ready(self) -> bool:
        """判断 LLM 分类器是否已预热就绪。

        - ``NoOpLlmClassifier`` 视为始终就绪（无需预热）。
        - ``Qwen2VLClassifier`` 根据其内部初始化状态返回。
        - 其他自定义 LLM 分类器默认视为就绪。

        Returns:
            True 表示 LLM 已就绪或无需预热。
        """
        if isinstance(self.llm, NoOpLlmClassifier):
            return True
        try:
            from .classification_llm import Qwen2VLClassifier
        except ImportError:
            return True
        if isinstance(self.llm, Qwen2VLClassifier):
            # 已初始化成功，或已尝试过但失败（会降级），均视为就绪
            return self.llm.is_ready or self.llm._init_error is not None
        return True

    async def warmup_async(self) -> bool:
        """异步预热 LLM 分类器。

        对真实本地模型（如 Qwen2-VL）在后台线程中加载权重，避免阻塞事件循环。
        对 ``NoOpLlmClassifier`` 不执行任何操作并直接返回 True。

        Returns:
            是否成功完成预热。
        """
        if isinstance(self.llm, NoOpLlmClassifier):
            return True
        try:
            from .classification_llm import Qwen2VLClassifier
        except ImportError:
            return True
        if not isinstance(self.llm, Qwen2VLClassifier):
            return True

        import asyncio

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self.llm.warmup)
        except Exception as exc:
            logger = logging.getLogger("privacy.classification")
            logger.warning(f"LLM warmup failed: {exc}")
            return False

    def confirm_review(
        self,
        review_id: str,
        corrected_level: str,
        reviewer: str = "",
        comment: str = "",
    ) -> Any:
        """确认或修正复核条目。

        Args:
            review_id: 复核条目 ID。
            corrected_level: 修正后的等级。
            reviewer: 复核人。
            comment: 说明。

        Returns:
            ReviewEntry。
        """
        return self.review_store.confirm(review_id, corrected_level, reviewer, comment)

    def export_reviews(self, format: str = "jsonl", mask_input: bool = False) -> str:
        """导出复核样本。

        Args:
            format: `jsonl` 或 `csv`。
            mask_input: 是否对 input 掩码。

        Returns:
            导出内容字符串。
        """
        return self.review_store.export(format=format, mask_input=mask_input)


def _build_audit_info(params: ClassificationParams, parameter_source: str) -> AuditInfo:
    """构建审计信息。"""
    return AuditInfo(
        version=_PRIMITIVE_VERSION,
        profile_version=params.version,
        timestamp=datetime.now(timezone.utc).isoformat(),
        rule_engine_version=_RULE_ENGINE_VERSION,
        rule_set_version=params.rule_set_version,
        parameter_source=parameter_source,
    )
