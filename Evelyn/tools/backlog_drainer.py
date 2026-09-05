# backlog_drainer.py
# date created: 2026-09-05 17:35:00
# date modified: 2026-09-05 17:35:15
# tags: #queue, #drainer, #backlog, #batch, #cooperative_yielding, #task_manager

"""
backlog_drainer.py — Canonical Backlog & Queue Draining Engine for the Evelyn Ecosystem.

Provides standardized, safe, and starvation-resistant queue draining loops with:
- Cooperative preemption checks (should_yield, idle threshold gating).
- Per-item exception isolation (dead-letter and poison-pill containment).
- Deadline and time-budget enforcement.
- Automatic task lifecycle management (task_manager set_running / clear_running).
- Progress telemetry hooks for the DevUI dashboard.

Exports:
    DrainConfig             — Configuration dataclass for backlog drain runs.
    DrainResult             — Telemetry and execution outcome dataclass.
    drain_backlog           — Synchronous / threaded backlog drainer.
    drain_backlog_async     — Asynchronous coroutine backlog drainer.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from Evelyn.tools import task_manager

logger = logging.getLogger("evelyn.backlog_drainer")


@dataclass
class DrainConfig:
    """Configuration options for a backlog drain execution."""

    batch_size: int = 10
    max_batches: int = 0  # 0 = unlimited (drain until exhausted, deadline, or yield)
    delay_between_batches: float = 0.0
    deadline: float | None = None  # Epoch timestamp after which no new item is started
    yield_check_interval: int = 1  # Check should_yield every N items
    auto_re_enqueue: bool = True  # Re-enqueue task in task_manager when yielding
    manage_task_lifecycle: bool = True  # Call set_running / clear_running automatically


@dataclass
class DrainResult:
    """Execution telemetry and outcome summary of a backlog drain run."""

    items_processed: int = 0
    errors_count: int = 0
    batches_completed: int = 0
    yielded: bool = False
    exhausted: bool = False
    deadline_exceeded: bool = False
    duration_ms: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


def drain_backlog[T](
    task_name: str,
    fetch_batch_fn: Callable[[int], list[T]],
    process_item_fn: Callable[[T], Any],
    config: DrainConfig | None = None,
    error_handler: Callable[[T, Exception], None] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> DrainResult:
    """Synchronously drain a backlog queue in batches with cooperative preemption.

    Args:
        task_name: Identifier of the background task in task_manager.
        fetch_batch_fn: Callable accepting limit (int) and returning list of items.
        process_item_fn: Callable processing an individual item.
        config: Optional DrainConfig overrides.
        error_handler: Optional callback on per-item exceptions (item, exception).
        progress_callback: Optional callback reporting (items_processed, errors_count).

    Returns:
        DrainResult summary of execution.
    """
    cfg = config or DrainConfig()
    result = DrainResult()
    start_time = time.perf_counter()

    if cfg.manage_task_lifecycle:
        task_manager.set_running(task_name, phase="draining_backlog")

    try:
        batch_num = 0
        while True:
            # Check deadline before starting a new batch
            if cfg.deadline is not None and time.time() >= cfg.deadline:
                result.deadline_exceeded = True
                break

            # Check cooperative yield before fetching
            if task_manager.should_yield(task_name):
                result.yielded = True
                if cfg.auto_re_enqueue:
                    task_manager.enqueue_idle_task(task_name)
                break

            # Fetch next batch
            try:
                batch = fetch_batch_fn(cfg.batch_size)
            except Exception as e:  # noqa: BLE001
                logger.error(f"[{task_name}] Error fetching batch: {e}", exc_info=True)
                result.errors_count += 1
                result.errors.append({"stage": "fetch", "error": str(e)})
                break

            if not batch:
                result.exhausted = True
                break

            # Process items in the batch
            for idx, item in enumerate(batch):
                # Check deadline before each item
                if cfg.deadline is not None and time.time() >= cfg.deadline:
                    result.deadline_exceeded = True
                    break

                # Check cooperative yield per interval
                if (
                    cfg.yield_check_interval > 0
                    and (idx % cfg.yield_check_interval == 0)
                    and task_manager.should_yield(task_name)
                ):
                    result.yielded = True
                    if cfg.auto_re_enqueue:
                        task_manager.enqueue_idle_task(task_name)
                    break

                try:
                    process_item_fn(item)
                    result.items_processed += 1
                except Exception as item_err:  # noqa: BLE001
                    result.errors_count += 1
                    err_msg = str(item_err)
                    logger.warning(
                        f"[{task_name}] Error processing item {item}: {err_msg}",
                        exc_info=True,
                    )
                    result.errors.append({"item": str(item), "error": err_msg})
                    if error_handler:
                        try:
                            error_handler(item, item_err)
                        except Exception as eh_err:  # noqa: BLE001
                            logger.error(f"[{task_name}] Error in error_handler: {eh_err}")

                if progress_callback:
                    try:
                        progress_callback(result.items_processed, result.errors_count)
                    except Exception as pc_err:  # noqa: BLE001
                        logger.debug(f"[{task_name}] Progress callback error: {pc_err}")

            batch_num += 1
            result.batches_completed = batch_num

            # If loop was terminated inside the batch due to yield or deadline, break outer loop
            if result.yielded or result.deadline_exceeded:
                break

            # Check max_batches limit
            if cfg.max_batches > 0 and batch_num >= cfg.max_batches:
                break

            # Pause between batches if configured
            if cfg.delay_between_batches > 0:
                time.sleep(cfg.delay_between_batches)

    finally:
        result.duration_ms = int((time.perf_counter() - start_time) * 1000)
        if cfg.manage_task_lifecycle and task_manager.get_status(task_name) == "running":
            task_manager.clear_running(
                task_name,
                status="idle" if result.errors_count == 0 else "degraded",
                items_processed=result.items_processed,
            )

    return result


async def drain_backlog_async[T](
    task_name: str,
    fetch_batch_fn: Callable[[int], Awaitable[list[T]] | list[T]],
    process_item_fn: Callable[[T], Awaitable[Any] | Any],
    config: DrainConfig | None = None,
    error_handler: Callable[[T, Exception], Awaitable[None] | None] | None = None,
    progress_callback: Callable[[int, int], Awaitable[None] | None] | None = None,
) -> DrainResult:
    """Asynchronously drain a backlog queue with native coroutines and cooperative yield.

    Args:
        task_name: Identifier of the background task in task_manager.
        fetch_batch_fn: Async (or sync) callable accepting limit (int).
        process_item_fn: Async (or sync) callable processing an individual item.
        config: Optional DrainConfig overrides.
        error_handler: Optional callback on per-item exceptions.
        progress_callback: Optional callback reporting progress.

    Returns:
        DrainResult summary of execution.
    """
    cfg = config or DrainConfig()
    result = DrainResult()
    start_time = time.perf_counter()

    if cfg.manage_task_lifecycle:
        task_manager.set_running(task_name, phase="draining_backlog")

    try:
        batch_num = 0
        while True:
            if cfg.deadline is not None and time.time() >= cfg.deadline:
                result.deadline_exceeded = True
                break

            if task_manager.should_yield(task_name):
                result.yielded = True
                if cfg.auto_re_enqueue:
                    task_manager.enqueue_idle_task(task_name)
                break

            try:
                fetch_res = fetch_batch_fn(cfg.batch_size)
                batch: list[T] = await fetch_res if inspect.isawaitable(fetch_res) else fetch_res  # type: ignore[assignment]
            except Exception as e:  # noqa: BLE001
                logger.error(f"[{task_name}] Error fetching batch: {e}", exc_info=True)
                result.errors_count += 1
                result.errors.append({"stage": "fetch", "error": str(e)})
                break

            if not batch:
                result.exhausted = True
                break

            for idx, item in enumerate(batch):
                if cfg.deadline is not None and time.time() >= cfg.deadline:
                    result.deadline_exceeded = True
                    break

                if (
                    cfg.yield_check_interval > 0
                    and (idx % cfg.yield_check_interval == 0)
                    and task_manager.should_yield(task_name)
                ):
                    result.yielded = True
                    if cfg.auto_re_enqueue:
                        task_manager.enqueue_idle_task(task_name)
                    break

                try:
                    proc_res = process_item_fn(item)
                    if inspect.isawaitable(proc_res):
                        await proc_res
                    result.items_processed += 1
                except Exception as item_err:  # noqa: BLE001
                    result.errors_count += 1
                    err_msg = str(item_err)
                    logger.warning(
                        f"[{task_name}] Error processing item {item}: {err_msg}",
                        exc_info=True,
                    )
                    result.errors.append({"item": str(item), "error": err_msg})
                    if error_handler:
                        try:
                            eh_res = error_handler(item, item_err)
                            if inspect.isawaitable(eh_res):
                                await eh_res
                        except Exception as eh_err:  # noqa: BLE001
                            logger.error(f"[{task_name}] Error in error_handler: {eh_err}")

                if progress_callback:
                    try:
                        pc_res = progress_callback(result.items_processed, result.errors_count)
                        if inspect.isawaitable(pc_res):
                            await pc_res
                    except Exception as pc_err:  # noqa: BLE001
                        logger.debug(f"[{task_name}] Progress callback error: {pc_err}")

            batch_num += 1
            result.batches_completed = batch_num

            if result.yielded or result.deadline_exceeded:
                break

            if cfg.max_batches > 0 and batch_num >= cfg.max_batches:
                break

            if cfg.delay_between_batches > 0:
                await asyncio.sleep(cfg.delay_between_batches)

    finally:
        result.duration_ms = int((time.perf_counter() - start_time) * 1000)
        if cfg.manage_task_lifecycle and task_manager.get_status(task_name) == "running":
            task_manager.clear_running(
                task_name,
                status="idle" if result.errors_count == 0 else "degraded",
                items_processed=result.items_processed,
            )

    return result
