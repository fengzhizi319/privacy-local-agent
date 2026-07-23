"""异步分类任务单元测试 / Asynchronous Classification Job Unit Tests.

中文说明：
验证异步分类任务管理器的完整生命周期：
- 任务提交、执行、状态查询、结果获取。
- 任务不存在时的异常处理。
- 并发任务队列满时的拒绝策略。

English Description:
Tests for the async classification job manager lifecycle:
- Job submission, execution, status polling, and result retrieval.
- Error handling for non-existent jobs.
- Rejection policy when concurrent job queue is full.
"""

import time

import pytest

from privacy_local_agent.privacy.classification import ClassificationAPI
from privacy_local_agent.privacy.classification_async import AsyncClassificationManager
from privacy_local_agent.privacy.classification_models import ClassificationJobStatus


@pytest.fixture
def api():
    """创建默认 ClassificationAPI 实例。"""
    return ClassificationAPI()


def test_async_job_lifecycle(api):
    """测试异步分类任务的完整生命周期：提交 → 轮询 → 完成。

    Test the full lifecycle of an async classification job:
    submit → poll status → done with result.
    """
    # 提交异步表分类任务
    job_id = api.submit_classify_table_async(
        schema=["id_card", "mobile"],
        rows=[{"id_card": "110101199001011237", "mobile": "13800138000"}],
        params={"enable_rule_engine": True},
    )
    # 任务 ID 应以 "cls-" 前缀开头
    assert job_id.startswith("cls-")

    # 轮询任务状态，最多等待 4 秒 (20 * 0.2s)
    for _ in range(20):
        job = api.get_job_result(job_id)
        if job.status in (ClassificationJobStatus.DONE, ClassificationJobStatus.FAILED):
            break
        time.sleep(0.2)

    # 任务应成功完成，结果等级为 L3（身份证+手机号）
    assert job.status == ClassificationJobStatus.DONE
    assert job.result is not None
    assert job.result.result.final_level.value == "L3"


def test_async_job_not_found(api):
    """测试查询不存在的任务 ID 时抛出 KeyError。

    Test that querying a non-existent job ID raises KeyError.
    """
    with pytest.raises(KeyError):
        api.get_job_result("cls-nonexistent")


def test_async_manager_queue_full():
    """测试并发任务队列满时的拒绝策略。

    Test that submitting a job when the queue is full raises RuntimeError.
    """
    # 创建最大并发数为 1 的管理器
    manager = AsyncClassificationManager(max_workers=1, max_jobs=1, ttl_seconds=60.0)
    # 第一个任务占用唯一槽位
    manager.submit(lambda: time.sleep(10))
    # 第二个任务应被拒绝
    with pytest.raises(RuntimeError, match="full"):
        manager.submit(lambda: time.sleep(10))
    manager.shutdown(wait=False)
