"""Tests for asynchronous classification jobs."""

import time

import pytest

from privacy_local_agent.privacy.classification import ClassificationAPI
from privacy_local_agent.privacy.classification_async import AsyncClassificationManager
from privacy_local_agent.privacy.classification_models import ClassificationJobStatus


@pytest.fixture
def api():
    return ClassificationAPI()


def test_async_job_lifecycle(api):
    job_id = api.submit_classify_table_async(
        schema=["id_card", "mobile"],
        rows=[{"id_card": "110101199001011237", "mobile": "13800138000"}],
        params={"enable_rule_engine": True},
    )
    assert job_id.startswith("cls-")

    for _ in range(20):
        job = api.get_job_result(job_id)
        if job.status in (ClassificationJobStatus.DONE, ClassificationJobStatus.FAILED):
            break
        time.sleep(0.2)

    assert job.status == ClassificationJobStatus.DONE
    assert job.result is not None
    assert job.result.result.final_level.value == "L3"


def test_async_job_not_found(api):
    with pytest.raises(KeyError):
        api.get_job_result("cls-nonexistent")


def test_async_manager_queue_full():
    manager = AsyncClassificationManager(max_workers=1, max_jobs=1, ttl_seconds=60.0)
    manager.submit(lambda: time.sleep(10))
    with pytest.raises(RuntimeError, match="full"):
        manager.submit(lambda: time.sleep(10))
    manager.shutdown(wait=False)
