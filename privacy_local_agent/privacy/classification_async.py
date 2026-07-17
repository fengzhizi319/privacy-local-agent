"""Asynchronous classification job manager.

提供基于进程内 ThreadPoolExecutor 的异步分类任务调度，避免 Layer 3 LLM
推理阻塞主链路。任务状态保存在内存中，支持 TTL 清理。
"""

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from ..observability.metrics import CLASSIFICATION_JOBS_DURATION, CLASSIFICATION_JOBS_TOTAL
from .classification_models import ClassificationJob, ClassificationJobResult, ClassificationJobStatus


class AsyncClassificationManager:
    """异步分类任务管理器。

    Attributes:
        max_workers: 线程池最大工作线程数。
        ttl_seconds: 任务保留时间（秒）。
        max_jobs: 最大并发任务数。
    """

    def __init__(
        self,
        max_workers: Optional[int] = None,
        ttl_seconds: Optional[float] = None,
        max_jobs: Optional[int] = None,
    ):
        self.max_workers = max_workers or int(os.environ.get("PRIVACY_ASYNC_MAX_WORKERS", "4"))
        self.ttl_seconds = ttl_seconds or float(os.environ.get("PRIVACY_ASYNC_JOB_TTL_SECONDS", "3600"))
        self.max_jobs = max_jobs or int(os.environ.get("PRIVACY_ASYNC_MAX_JOBS", "1000"))
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="cls-async-")
        self._jobs: Dict[str, ClassificationJob] = {}
        self._lock = threading.Lock()
        self._cleanup_interval = 60.0
        self._last_cleanup = time.monotonic()

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
        """提交一个异步分类任务。

        Args:
            fn: 需要异步执行的函数，通常为 ClassificationAPI.classify_table。
            *args: 函数位置参数。
            **kwargs: 函数关键字参数。

        Returns:
            任务 ID。

        Raises:
            RuntimeError: 当并发任务数超过最大限制时。
        """
        self._maybe_cleanup()
        with self._lock:
            active = sum(1 for j in self._jobs.values() if j.status == ClassificationJobStatus.RUNNING)
            if active >= self.max_jobs:
                raise RuntimeError("async classification job queue is full")

            job_id = f"cls-{uuid.uuid4().hex[:12]}"
            job = ClassificationJob(
                job_id=job_id,
                status=ClassificationJobStatus.PENDING,
                created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            self._jobs[job_id] = job

        def _run() -> None:
            start = time.monotonic()
            try:
                self._set_status(job_id, ClassificationJobStatus.RUNNING)
                result = fn(*args, **kwargs)
                duration = time.monotonic() - start
                self._set_done(job_id, result, duration)
            except Exception as exc:  # noqa: BLE001
                duration = time.monotonic() - start
                self._set_failed(job_id, str(exc), duration)

        self._executor.submit(_run)
        CLASSIFICATION_JOBS_TOTAL.labels(status=ClassificationJobStatus.PENDING.value).inc()
        return job_id

    def get(self, job_id: str) -> ClassificationJob:
        """查询异步任务状态与结果。

        Args:
            job_id: 任务 ID。

        Returns:
            ClassificationJob 实例。

        Raises:
            KeyError: 任务不存在。
        """
        self._maybe_cleanup()
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(f"job not found: {job_id}")
            return self._jobs[job_id]

    def shutdown(self, wait: bool = True) -> None:
        """关闭线程池。"""
        self._executor.shutdown(wait=wait)

    def _set_status(self, job_id: str, status: ClassificationJobStatus) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.status = status

    def _set_done(self, job_id: str, result: Any, duration: float) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = ClassificationJobStatus.DONE
            job.result = ClassificationJobResult(result=result)
            job.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        CLASSIFICATION_JOBS_TOTAL.labels(status=ClassificationJobStatus.DONE.value).inc()
        CLASSIFICATION_JOBS_DURATION.labels(status=ClassificationJobStatus.DONE.value).observe(duration)

    def _set_failed(self, job_id: str, error: str, duration: float) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = ClassificationJobStatus.FAILED
            job.error = error
            job.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        CLASSIFICATION_JOBS_TOTAL.labels(status=ClassificationJobStatus.FAILED.value).inc()
        CLASSIFICATION_JOBS_DURATION.labels(status=ClassificationJobStatus.FAILED.value).observe(duration)

    def _maybe_cleanup(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        cutoff = now - self.ttl_seconds
        with self._lock:
            expired = [
                job_id
                for job_id, job in self._jobs.items()
                if job.finished_at is not None and time.mktime(time.strptime(job.finished_at, "%Y-%m-%dT%H:%M:%SZ")) < cutoff
            ]
            for job_id in expired:
                del self._jobs[job_id]
