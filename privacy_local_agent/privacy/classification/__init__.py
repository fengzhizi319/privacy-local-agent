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

# 从主分类模块导入 ClassificationAPI，这是三层分类漏斗的唯一对外入口类。
# 外部代码通过 `from privacy_local_agent.privacy.classification import ClassificationAPI` 使用。
from .classification import ClassificationAPI  # noqa: F401

# 从数据模型模块导入核心公共类型：
# - ClassificationParams：分类请求参数模型（含规则开关、阈值、模板等配置）
# - EngineLayer：分类引擎层级枚举（L1_RULE / L2_SMALL_NER / L3_LLM）
# - SensitivityLevel：敏感度等级枚举（L1~L5，从公开到极敏感）
from .classification_models import (  # noqa: F401
    ClassificationParams,
    EngineLayer,
    SensitivityLevel,
)
