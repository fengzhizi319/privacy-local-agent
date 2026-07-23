"""数据分类子包。

将原 privacy/classification_*.py 系列模块组织为独立子包，
对外通过本 __init__.py 保持 ``from privacy_local_agent.privacy.classification import X``
的导入路径兼容。

内部模块：
- classification.py：ClassificationAPI 主入口（三层分类漏斗）
- classification_models.py：数据模型与枚举（SensitivityLevel 等）
- classification_rule_engine.py：规则引擎层
- classification_vectorized.py：向量化规则引擎
- classification_composite.py：组合规则引擎
- classification_ner.py：小型 NER 引擎层
- classification_llm.py：本地 LLM/VLM 分类层
- classification_async.py：异步分类任务管理
- classification_review.py：人工复核存储
- classification_utils.py：工具函数
"""

from .classification import ClassificationAPI  # noqa: F401
from .classification_models import (  # noqa: F401
    ClassificationParams,
    EngineLayer,
    SensitivityLevel,
)
