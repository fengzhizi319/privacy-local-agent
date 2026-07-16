"""隐私参数解析与校验模块。

从 YAML 配置文件加载各隐私原语的默认参数，并支持请求级参数覆盖。
提供参数校验能力，确保 DP epsilon 为正、K-Anonymity k 不小于 2 等。

Privacy parameter resolver. Loads default parameters from a YAML profile and
allows request-level overrides, with built-in validation rules.
"""

import os
import threading
from typing import Any, Dict, Optional

import yaml


def default_params(primitive: str) -> Dict[str, Any]:
    """获取指定隐私原语的默认参数。

    Args:
        primitive: 隐私原语名称，如 "dp"、"k_anonymity"、"sanitization"、"qol"。

    Returns:
        对应原语的默认参数字典；若不存在则返回空字典。
    """
    return {
        "dp": {"epsilon": 1.0, "delta": 0.0, "mechanism": "laplace"},
        "k_anonymity": {"k": 5, "l": 2, "t": 0.2, "max_depth": 10},
        "sanitization": {"engine": "mask"},
        "qol": {
            "num_dummies": 3,
            "medical_pool": [
                "高血压患者的日常饮食建议",
                "糖尿病患者运动注意事项",
                "冠心病的早期症状有哪些",
                "流感疫苗接种人群建议",
                "儿童常见过敏反应处理",
            ],
            "generic_pool": [
                "天气预报查询",
                "附近医院挂号流程",
                "健康档案如何查询",
                "医保报销比例说明",
                "体检报告解读指南",
            ],
        },
        "classification": {
            "version": "1.0.0",
            "default_level": "L3",
            "enable_rule_engine": True,
            "enable_small_ner": False,
            "enable_llm": False,
            "icd10_l4_intervals": [
                {"start": "B20", "end": "B24"},
                {"start": "F20", "end": "F29"},
                {"start": "C00", "end": "C97"},
            ],
            "genomic_keywords": [
                "brca1",
                "brca2",
                "tp53",
                "rs",
                "snp",
                "cnv",
                "genome",
                "genomic",
                "gene",
                "mutation",
                "variant",
            ],
            "public_field_whitelist": ["public_report", "annual_summary", "科普"],
            "operational_field_patterns": ["turnover_rate", "device_usage", "inventory"],
            "manual_override": {},
        },
    }.get(primitive, {})


def validate(primitive: str, params: Dict[str, Any]):
    """校验隐私原语参数是否合法。

    Args:
        primitive: 隐私原语名称。
        params: 合并后的参数。

    Raises:
        ValueError: 当参数不满足业务规则时抛出。
            - DP epsilon 必须为正数。
            - K-Anonymity k 必须大于等于 2。
    """
    if primitive == "dp":
        eps = float(params.get("epsilon", 0))
        if eps <= 0:
            raise ValueError("DP epsilon must be positive")
    elif primitive == "k_anonymity":
        k = int(params.get("k", 0))
        if k < 2:
            raise ValueError("K-Anonymity k must be >= 2")


class ParameterResolver:
    """参数解析器。

    负责将配置文件中的默认参数、YAML profile 中的原语参数以及请求参数
    按优先级合并，并做合法性校验。

    Attributes:
        profile: 解析后的 YAML profile 字典。

    参数优先级（从高到低）：
        1. 请求级参数 request_params
        2. YAML profile 中 primitives.<primitive> 的配置
        3. 内置 default_params 默认值
    """

    def __init__(self, profile_path: Optional[str] = None):
        """初始化参数解析器。

        Args:
            profile_path: YAML 配置文件路径。若提供且文件存在，则加载其内容；
                否则 profile 为空字典。
        """
        self.profile: Dict[str, Any] = {}
        if profile_path and os.path.exists(profile_path):
            with open(profile_path, "r", encoding="utf-8") as f:
                self.profile = yaml.safe_load(f) or {}

    def resolve(
        self,
        primitive: str,
        request_params: Optional[Dict[str, Any]] = None,
        namespace: Optional[str] = None,
    ) -> Dict[str, Any]:
        """解析并合并指定隐私原语的参数。

        参数优先级（从高到低）：
            1. 请求级参数 request_params
            2. 个性化推荐保存的参数 (personalized-profiles.yaml)
            3. YAML profile 中的配置 (privacy-profile.yaml)
            4. 内置 default_params 默认值

        Args:
            primitive: 隐私原语名称。
            request_params: 请求级参数，优先级最高。
            namespace: 命名空间。若提供，则优先合并该命名空间下保存的推荐参数。

        Returns:
            合并并校验后的完整参数字典。

        Raises:
            ValueError: 当参数校验失败时抛出。
        """
        # 以内置默认值为基础
        params = dict(default_params(primitive))
        # 使用 YAML profile 中的原语配置进行覆盖
        primitives = self.profile.get("primitives") or {}
        if primitive in primitives:
            params.update(primitives[primitive])

        # 使用个性化推荐保存的参数进行覆盖（若指定了 namespace）
        if namespace:
            personalized_path = os.environ.get("PRIVACY_PERSONALIZED_PROFILE", "personalized-profiles.yaml")
            if os.path.exists(personalized_path):
                with _profile_lock:
                    try:
                        with open(personalized_path, "r", encoding="utf-8") as f:
                            personalized_data = yaml.safe_load(f) or {}
                        namespace_config = personalized_data.get(namespace, {})
                        primitive_config = namespace_config.get(primitive, {})
                        if primitive_config:
                            params.update(primitive_config)
                    except Exception as e:
                        print(f"[-] Warning: Failed to load personalized profile: {e}")

        # 使用请求参数做最终覆盖
        if request_params:
            params.update(request_params)
        validate(primitive, params)
        return params


_resolver_cache: Dict[str, "ParameterResolver"] = {}
_profile_lock = threading.Lock()


def get_resolver(profile_path: Optional[str] = None) -> "ParameterResolver":
    """获取参数解析器单例/缓存实例，避免重复解析 YAML 文件。"""
    path = profile_path or os.environ.get("PRIVACY_PROFILE", "privacy-profile.yaml")
    abs_path = os.path.abspath(path)
    if abs_path not in _resolver_cache:
        _resolver_cache[abs_path] = ParameterResolver(abs_path)
    return _resolver_cache[abs_path]


def save_personalized_params(namespace: str, primitive: str, params: Dict[str, Any]) -> None:
    """持久化保存推荐的个性化参数到 personalized-profiles.yaml。"""
    personalized_path = os.environ.get("PRIVACY_PERSONALIZED_PROFILE", "personalized-profiles.yaml")
    with _profile_lock:
        personalized_data: Dict[str, Any] = {}
        if os.path.exists(personalized_path):
            try:
                with open(personalized_path, "r", encoding="utf-8") as f:
                    personalized_data = yaml.safe_load(f) or {}
            except Exception as e:
                print(f"[-] Warning: Failed to read personalized profile for saving: {e}")

        if namespace not in personalized_data:
            personalized_data[namespace] = {}
        if primitive not in personalized_data[namespace]:
            personalized_data[namespace][primitive] = {}

        personalized_data[namespace][primitive].update(params)

        try:
            with open(personalized_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(personalized_data, f, allow_unicode=True)
        except Exception as e:
            print(f"[-] Error: Failed to save personalized profile: {e}")

