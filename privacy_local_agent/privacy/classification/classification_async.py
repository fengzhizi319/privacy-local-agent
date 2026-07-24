"""异步分类任务管理器 / Asynchronous Classification Job Manager.

中文说明：
提供基于进程内 ThreadPoolExecutor 的异步分类任务调度，避免 Layer 3 LLM
推理阻塞主链路。任务状态保存在内存中，支持 TTL 清理。

设计目标：
- 非阻塞提交：classify_table 等耗时操作可异步执行，REST/gRPC 接口立即返回 job_id
- 状态查询：客户端通过 job_id 轮询任务状态（PENDING → RUNNING → DONE/FAILED）
- 资源限制：max_jobs 限制并发任务数，防止内存溢出
- 自动清理：TTL 机制定期清除已完成/失败的过期任务，防止内存泄漏

任务生命周期：
PENDING → RUNNING → DONE（成功）
                  → FAILED（异常）

配置环境变量：
- PRIVACY_ASYNC_MAX_WORKERS：线程池最大工作线程数（默认 4）
- PRIVACY_ASYNC_JOB_TTL_SECONDS：已完成任务保留时间（默认 3600 秒）
- PRIVACY_ASYNC_MAX_JOBS：最大并发任务数（默认 1000）

English Description:
Provides async classification job scheduling based on in-process ThreadPoolExecutor,
avoiding Layer 3 LLM inference blocking the main request path. Job state is stored
in memory with TTL-based cleanup support.
"""

# 启用延迟注解求值，允许在类型提示中引用尚未定义的类名
from __future__ import annotations

# 导入操作系统接口，用于读取环境变量配置
import os
# 导入线程模块，用于创建互斥锁保护任务字典的并发访问
import threading
# 导入时间模块，用于任务创建/完成时间戳和 TTL 计算
import time
# 导入 UUID 模块，用于生成唯一的任务 ID
import uuid
# 导入线程池执行器，用于异步执行分类任务
from concurrent.futures import ThreadPoolExecutor
# 导入类型注解工具：TYPE_CHECKING 用于条件导入，Any 通用类型
from typing import TYPE_CHECKING, Any

# 导入结构化日志工厂函数
from ...observability.logging_config import get_logger
# 导入 Prometheus 指标：
# - CLASSIFICATION_JOBS_DURATION：异步任务执行耗时直方图（按状态标签）
# - CLASSIFICATION_JOBS_TOTAL：异步任务状态变更计数器（按状态标签）
from ...observability.metrics import CLASSIFICATION_JOBS_DURATION, CLASSIFICATION_JOBS_TOTAL
# 导入异步任务相关的数据模型：
# - ClassificationJob：任务状态与结果容器
# - ClassificationJobResult：任务执行结果包装
# - ClassificationJobStatus：任务状态枚举（PENDING/RUNNING/DONE/FAILED）
from .classification_models import (
    ClassificationJob,
    ClassificationJobResult,
    ClassificationJobStatus,
)

# 仅在类型检查时导入 Callable 类型（避免运行时开销）
if TYPE_CHECKING:
    from collections.abc import Callable

# 创建模块级结构化日志器，用于记录异步任务相关事件
logger = get_logger(__name__)


class AsyncClassificationManager:
    """异步分类任务管理器 / Async Classification Job Manager.

    中文说明：
    管理异步分类任务的生命周期，包括提交、执行、状态查询和 TTL 清理。
    内部使用 ThreadPoolExecutor 执行实际分类逻辑，任务状态存储在内存字典中。

    线程安全设计：
    - _lock 互斥锁保护 _jobs 字典的所有读写操作
    - 任务状态变更（_set_status/_set_done/_set_failed）均在锁内执行
    - TTL 清理也在锁内执行，避免与状态变更产生竞态

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

        参数优先级：显式传参 > 环境变量 > 默认值

        Args:
            max_workers: 线程池最大工作线程数 / Maximum thread pool workers.
            ttl_seconds: 任务保留时间（秒） / Job TTL in seconds.
            max_jobs: 最大并发任务数 / Maximum concurrent jobs.
        """
        # 线程池工作线程数：优先使用参数，其次环境变量，最后默认值 4
        self.max_workers = max_workers or int(os.environ.get("PRIVACY_ASYNC_MAX_WORKERS", "4"))
        # 任务 TTL（秒）：已完成/失败的任务超过此时间后被清理
        self.ttl_seconds = ttl_seconds or float(os.environ.get("PRIVACY_ASYNC_JOB_TTL_SECONDS", "3600"))
        # 最大并发任务数：超过此限制时 submit() 将拒绝新任务
        self.max_jobs = max_jobs or int(os.environ.get("PRIVACY_ASYNC_MAX_JOBS", "1000"))
        # 创建线程池执行器，线程名前缀 "cls-async-" 便于日志和调试识别
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="cls-async-")
        # 任务存储字典：job_id → ClassificationJob 实例
        self._jobs: dict[str, ClassificationJob] = {}
        # 互斥锁：保护 _jobs 字典的并发读写安全
        self._lock = threading.Lock()
        # TTL 清理间隔（秒）：每 60 秒最多执行一次清理（避免频繁清理的开销）
        self._cleanup_interval = 60.0
        # 上次清理时间戳（monotonic 单调时钟）
        self._last_cleanup = time.monotonic()
        # 记录管理器初始化成功的结构化日志
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
        1. 执行 TTL 清理过期任务（惰性清理，非定时器）。
           (Execute TTL cleanup for expired jobs - lazy cleanup, not timer-based)
        2. 检查并发任务数是否超过限制（RUNNING 状态的任务数）。
           (Check if concurrent job count exceeds limit)
        3. 创建任务记录并存入内存字典。
           (Create job record and store in memory dict)
        4. 定义包装函数并提交到线程池执行。
           (Define wrapper function and submit to thread pool)

        Args:
            fn: 需要异步执行的函数（如 classify_table） / Function to execute asynchronously.
            *args: 函数位置参数 / Function positional arguments.
            **kwargs: 函数关键字参数 / Function keyword arguments.

        Returns:
            任务 ID（格式：cls-xxxxxxxxxxxx） / Job ID.

        Raises:
            RuntimeError: 当并发任务数超过最大限制时 / When concurrent jobs exceed max limit.
        """
        # 惰性清理：检查是否有过期任务需要清除
        self._maybe_cleanup()
        # 获取锁保护任务字典的读写
        with self._lock:
            # 统计当前正在运行（RUNNING）的任务数
            active = sum(1 for j in self._jobs.values() if j.status == ClassificationJobStatus.RUNNING)
            # 如果活跃任务数已达上限，拒绝新任务
            if active >= self.max_jobs:
                logger.error(
                    "async_job_queue_full",
                    extra={"active_jobs": active, "max_jobs": self.max_jobs},
                )
                raise RuntimeError("async classification job queue is full")

            # 生成唯一任务 ID：前缀 "cls-" + 12 位十六进制 UUID
            job_id = f"cls-{uuid.uuid4().hex[:12]}"
            # 创建任务记录对象，初始状态为 PENDING
            job = ClassificationJob(
                job_id=job_id,
                status=ClassificationJobStatus.PENDING,
                # 记录创建时间（UTC ISO 格式）
                created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            # 将任务存入内存字典
            self._jobs[job_id] = job

        # 定义任务执行包装函数（在线程池工作线程中运行）
        def _run() -> None:
            # 记录任务开始时间（用于计算执行耗时）
            start = time.monotonic()
            try:
                # 更新任务状态为 RUNNING
                self._set_status(job_id, ClassificationJobStatus.RUNNING)
                # 执行实际的分类函数（如 classify_table）
                result = fn(*args, **kwargs)
                # 计算执行耗时
                duration = time.monotonic() - start
                # 标记任务完成并保存结果
                self._set_done(job_id, result, duration)
            except Exception as exc:
                # 任务执行异常：计算耗时并标记失败
                duration = time.monotonic() - start
                self._set_failed(job_id, str(exc), duration)

        # 将包装函数提交到线程池异步执行
        self._executor.submit(_run)
        # 递增 Prometheus 指标：PENDING 状态任务数 +1
        CLASSIFICATION_JOBS_TOTAL.labels(status=ClassificationJobStatus.PENDING.value).inc()
        # 记录任务提交成功的日志
        logger.info(
            "async_job_submitted",
            extra={"job_id": job_id},
        )
        # 返回任务 ID 供客户端后续轮询
        return job_id

    def get(self, job_id: str) -> ClassificationJob:
        """查询异步任务状态与结果 / Get Async Job Status and Result.

        客户端通过此接口轮询任务进度：
        - PENDING：任务已提交但尚未开始执行
        - RUNNING：任务正在执行中
        - DONE：任务已完成，result 字段包含分类结果
        - FAILED：任务执行失败，error 字段包含错误信息

        Args:
            job_id: 任务 ID / Job ID.

        Returns:
            ClassificationJob 实例（包含状态、结果、错误等信息）。

        Raises:
            KeyError: 任务不存在（可能已被 TTL 清理） / Job not found.
        """
        # 查询前执行惰性清理（可能清除过期任务）
        self._maybe_cleanup()
        # 获取锁保护字典读取
        with self._lock:
            # 检查任务是否存在
            if job_id not in self._jobs:
                raise KeyError(f"job not found: {job_id}")
            # 返回任务对象（调用方可读取 status/result/error 等字段）
            return self._jobs[job_id]

    def shutdown(self, wait: bool = True) -> None:
        """关闭线程池 / Shutdown Thread Pool.

        服务优雅关闭时调用，等待所有正在执行的任务完成。

        Args:
            wait: 是否等待正在执行的任务完成后再关闭（默认 True）。
                  False 表示立即关闭，不等待未完成任务。
        """
        # 调用 ThreadPoolExecutor 的 shutdown 方法
        self._executor.shutdown(wait=wait)

    def _set_status(self, job_id: str, status: ClassificationJobStatus) -> None:
        """更新任务状态（内部方法） / Update Job Status (Internal).

        线程安全：在锁内修改任务状态。

        Args:
            job_id: 任务 ID。
            status: 新状态。
        """
        # 获取锁保护字典写入
        with self._lock:
            # 查找任务对象
            job = self._jobs.get(job_id)
            if job is not None:
                # 更新状态字段
                job.status = status

    def _set_done(self, job_id: str, result: Any, duration: float) -> None:
        """标记任务完成并保存结果（内部方法） / Mark Job Done with Result.

        执行步骤：
        1. 在锁内更新任务状态为 DONE、保存结果和完成时间
        2. 在锁外递增 Prometheus 指标（避免锁内执行耗时的指标操作）

        Args:
            job_id: 任务 ID。
            result: 分类结果（通常是 TableClassificationResult）。
            duration: 执行耗时（秒）。
        """
        # 获取锁保护状态更新
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return  # 任务可能已被 TTL 清理
            # 更新状态为 DONE
            job.status = ClassificationJobStatus.DONE
            # 包装并保存执行结果
            job.result = ClassificationJobResult(result=result)
            # 记录完成时间（UTC ISO 格式）
            job.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # 递增 DONE 状态计数器（在锁外执行，减少锁持有时间）
        CLASSIFICATION_JOBS_TOTAL.labels(status=ClassificationJobStatus.DONE.value).inc()
        # 记录执行耗时到直方图指标
        CLASSIFICATION_JOBS_DURATION.labels(status=ClassificationJobStatus.DONE.value).observe(duration)

    def _set_failed(self, job_id: str, error: str, duration: float) -> None:
        """标记任务失败并保存错误信息（内部方法） / Mark Job Failed with Error.

        执行步骤：
        1. 在锁内更新任务状态为 FAILED、保存错误信息和完成时间
        2. 在锁外递增 Prometheus 指标

        Args:
            job_id: 任务 ID。
            error: 错误信息字符串。
            duration: 执行耗时（秒）。
        """
        # 获取锁保护状态更新
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return  # 任务可能已被 TTL 清理
            # 更新状态为 FAILED
            job.status = ClassificationJobStatus.FAILED
            # 保存错误信息
            job.error = error
            # 记录失败时间（UTC ISO 格式）
            job.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # 递增 FAILED 状态计数器
        CLASSIFICATION_JOBS_TOTAL.labels(status=ClassificationJobStatus.FAILED.value).inc()
        # 记录执行耗时到直方图指标
        CLASSIFICATION_JOBS_DURATION.labels(status=ClassificationJobStatus.FAILED.value).observe(duration)

    def _maybe_cleanup(self) -> None:
        """惰性 TTL 清理：定期移除过期的已完成/失败任务。

        清理策略：
        - 每 _cleanup_interval（60秒）最多执行一次（避免频繁清理的开销）
        - 仅清理已完成（有 finished_at）且超过 TTL 的任务
        - 正在运行（RUNNING）或等待（PENDING）的任务不会被清理

        触发时机：每次 submit() 或 get() 调用时检查（惰性清理，无后台定时器）。
        """
        # 获取当前单调时钟时间
        now = time.monotonic()
        # 检查距离上次清理是否已过清理间隔
        if now - self._last_cleanup < self._cleanup_interval:
            return  # 未到清理时间，直接返回
        # 更新上次清理时间
        self._last_cleanup = now
        # 计算过期截止时间：当前时间 - TTL 秒数
        cutoff = now - self.ttl_seconds
        # 获取锁保护字典的遍历和删除操作
        with self._lock:
            # 收集所有过期的任务 ID（已完成且完成时间早于截止时间）
            expired = [
                job_id
                for job_id, job in self._jobs.items()
                if (
                    job.finished_at is not None  # 仅清理已完成/失败的任务
                    # 将 ISO 格式时间转换为时间戳进行比较
                    and time.mktime(time.strptime(job.finished_at, "%Y-%m-%dT%H:%M:%SZ")) < cutoff
                )
            ]
            # 批量删除过期任务
            for job_id in expired:
                del self._jobs[job_id]
