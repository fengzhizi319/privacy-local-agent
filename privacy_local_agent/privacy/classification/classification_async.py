"""异步分类任务管理器 / Asynchronous Classification Job Manager.

中文说明：
提供基于进程内 ThreadPoolExecutor 的异步分类任务调度，避免 Layer 3 LLM
推理阻塞主链路。任务状态保存在内存中，支持 TTL 清理。

English Description:
Provides async classification job scheduling based on in-process ThreadPoolExecutor,
avoiding Layer 3 LLM inference blocking the main request path. Job state is stored
in memory with TTL-based cleanup support.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from ...observability.logging_config import get_logger
from ...observability.metrics import CLASSIFICATION_JOBS_DURATION, CLASSIFICATION_JOBS_TOTAL
from .classification_models import (
    ClassificationJob,
    ClassificationJobResult,
    ClassificationJobStatus,
)

if TYPE_CHECKING:
    from collections.abc import Callable

# Module-level structured logger for async job events
logger = get_logger(__name__)


class AsyncClassificationManager:
    """异步分类任务管理器 / Async Classification Job Manager.

    中文说明：
    管理异步分类任务的生命周期，包括提交、执行、状态查询和 TTL 清理。

    English Description:
    Manages the lifecycle of async classification jobs including submission,
    execution, status querying, and TTL-based cleanup.

    Attributes:
        max_workers: 线程池最大工作线程数 / Maximum thread pool worker count.
        ttl_seconds: 任务保留时间（秒） / Job retention time in seconds.
        max_jobs: 最大并发任务数 / Maximum concurrent job count.
    """

    def __init__(
        self,
        max_workers: int | None = None,
        ttl_seconds: float | None = None,
        max_jobs: int | None = None,
    ):
        """初始化异步分类任务管理器 / Initialize Async Classification Job Manager.

        Args:
            max_workers: 线程池最大工作线程数 / Maximum thread pool workers.
            ttl_seconds: 任务保留时间（秒） / Job TTL in seconds.
            max_jobs: 最大并发任务数 / Maximum concurrent jobs.
        """
        self.max_workers = max_workers or int(os.environ.get("PRIVACY_ASYNC_MAX_WORKERS", "4"))
        self.ttl_seconds = ttl_seconds or float(os.environ.get("PRIVACY_ASYNC_JOB_TTL_SECONDS", "3600"))
        self.max_jobs = max_jobs or int(os.environ.get("PRIVACY_ASYNC_MAX_JOBS", "1000"))
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="cls-async-")
        self._jobs: dict[str, ClassificationJob] = {}
        self._lock = threading.Lock()
        self._cleanup_interval = 60.0
        self._last_cleanup = time.monotonic()
        logger.info(
            "async_classification_manager_initialized",
            extra={
                "max_workers": self.max_workers,
                "ttl_seconds": self.ttl_seconds,
                "max_jobs": self.max_jobs,
            },
        )

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
        """提交一个异步分类任务 / Submit an Async Classification Job.

        执行步骤 / Execution Steps:
        1. 执行 TTL 清理过期任务。
           (Execute TTL cleanup for expired jobs)
        2. 检查并发任务数是否超过限制。
           (Check if concurrent job count exceeds limit)
        3. 创建任务并提交到线程池执行。
           (Create job and submit to thread pool for execution)

        Args:
            fn: 需要异步执行的函数 / Function to execute asynchronously.
            *args: 函数位置参数 / Function positional arguments.
            **kwargs: 函数关键字参数 / Function keyword arguments.

        Returns:
            任务 ID / Job ID.

        Raises:
            RuntimeError: 当并发任务数超过最大限制时 / When concurrent jobs exceed max limit.
        """
        self._maybe_cleanup()
        with self._lock:
            active = sum(1 for j in self._jobs.values() if j.status == ClassificationJobStatus.RUNNING)
            if active >= self.max_jobs:
                logger.error(
                    "async_job_queue_full",
                    extra={"active_jobs": active, "max_jobs": self.max_jobs},
                )
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
            except Exception as exc:
                duration = time.monotonic() - start
                self._set_failed(job_id, str(exc), duration)

        self._executor.submit(_run)
        CLASSIFICATION_JOBS_TOTAL.labels(status=ClassificationJobStatus.PENDING.value).inc()
        logger.info(
            "async_job_submitted",
            extra={"job_id": job_id},
        )
        return job_id

    def get(self, job_id: str) -> ClassificationJob:
        """查询异步任务状态与结果 / Get Async Job Status and Result.

        Args:
            job_id: 任务 ID / Job ID.

        Returns:
            ClassificationJob 实例 / ClassificationJob instance.

        Raises:
            KeyError: 任务不存在 / Job not found.
        """
        self._maybe_cleanup()
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(f"job not found: {job_id}")
            return self._jobs[job_id]

    def shutdown(self, wait: bool = True) -> None:
        """关闭线程池 / Shutdown Thread Pool.

        Args:
            wait: 是否等待任务完成 / Whether to wait for jobs to complete.
        """
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
                if (
                    job.finished_at is not None
                    and time.mktime(time.strptime(job.finished_at, "%Y-%m-%dT%H:%M:%SZ")) < cutoff
                )
            ]
            for job_id in expired:
                del self._jobs[job_id]
