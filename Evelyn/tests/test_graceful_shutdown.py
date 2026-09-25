# test_graceful_shutdown.py
# date created: 2026-09-25
# date modified: 2026-09-25 07:20:52
# tags: #services, #shutdown, #chroma, #testing

"""The shutdown path must stay bounded, and must stay reachable.

`clean_shutdown_all_tasks()` drains the Chroma write queue before the process exits. The
custodian holds the store's single-writer lease for its whole life, so a SIGKILL before that
drain ends the lease mid-write.

It had never run. Measured 2026-09-25: uvicorn logs "Waiting for connections to close" and stops
there, because the chat UI holds a `text/event-stream` connection that never closes on its own —
`uvicorn.run()` passed no `timeout_graceful_shutdown`, so lifespan shutdown was unreachable and
systemd SIGKILLed the process at `TimeoutStopSec` on every restart with a browser tab open.

Two waits ahead of the drain were unbounded, and each is checked here:

1. uvicorn's wait for in-flight connections.
2. `asyncio.gather` over the cancelled lifespan tasks — `cancel()` raises at the next await
   point, which a task inside `asyncio.to_thread` or a blocking Ollama call does not reach
   while that call runs.

These are source-level assertions on purpose. The failure mode is a *missing* argument and an
*unwrapped* await; both read as perfectly working code, and both are invisible to a call-graph
check because every function involved is called. That is the whole reason the defect survived.
"""

import ast
import pathlib

import evelyn_config as cfg

SERVER = pathlib.Path(__file__).resolve().parents[2] / "evelyn_server.py"

# Matches the unit's TimeoutStopSec (see SETUP_GUIDE.md and the systemd override).
TIMEOUT_STOP_SEC = 30
# clean_shutdown_all_tasks: terminate_all_subprocesses(grace_period=3.0) + flush_sync_queue(5.0).
HANDLER_BUDGET_SECONDS = 8


def _uvicorn_run_call() -> ast.Call:
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "uvicorn"
        ):
            return node
    raise AssertionError("no uvicorn.run(...) call found in evelyn_server.py")


def test_uvicorn_caps_its_wait_for_connections() -> None:
    """Without this, an open SSE stream keeps lifespan shutdown from ever running."""
    kwargs = {kw.arg for kw in _uvicorn_run_call().keywords}
    assert "timeout_graceful_shutdown" in kwargs


def _shutdown_block() -> str:
    """The lifespan shutdown region: the cancellation gather through the Chroma drain.

    Anchored on the gather rather than on `except TimeoutError:`, which appears three times in
    this file — an earlier one belongs to an unrelated request path.
    """
    src = SERVER.read_text(encoding="utf-8")
    marker = "asyncio.gather(*_lifespan_tasks, return_exceptions=True)"
    assert marker in src, "the lifespan shutdown gather moved; re-check these bounds"
    start = src.index(marker)
    end = src.index("clean_shutdown_all_tasks()", start)
    return src[max(0, start - 600) : end + len("clean_shutdown_all_tasks()")]


def test_lifespan_task_cancellation_is_bounded() -> None:
    """A task that ignores cancellation must not consume the drain's budget."""
    block = _shutdown_block()
    assert "asyncio.wait_for" in block, "the lifespan gather is unbounded again"
    assert "SHUTDOWN_TASK_CANCEL_SECONDS" in block


def test_the_budget_fits_inside_the_systemd_stop_timeout() -> None:
    """Exceeding this means SIGKILL lands mid-drain, which is the bug in the first place."""
    worst_case = (
        cfg.SHUTDOWN_CONNECTION_DRAIN_SECONDS
        + cfg.SHUTDOWN_TASK_CANCEL_SECONDS
        + HANDLER_BUDGET_SECONDS
    )
    assert worst_case < TIMEOUT_STOP_SEC, (
        f"worst-case shutdown is {worst_case}s against TimeoutStopSec={TIMEOUT_STOP_SEC}s. "
        "Find what is slow rather than raising the timeout."
    )


def test_the_drain_runs_even_when_tasks_will_not_stop() -> None:
    """The timeout branch must fall through to the handler, not return early."""
    block = _shutdown_block()
    assert "except TimeoutError:" in block, "the cancellation timeout is not handled"

    after_timeout = block[block.index("except TimeoutError:") : block.index(
        "clean_shutdown_all_tasks()"
    )]
    assert "return" not in after_timeout, (
        "a return between the cancellation timeout and the drain would skip the drain"
    )
