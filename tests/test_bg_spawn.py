"""Unit tests for the fire-and-forget background-task helper (src/bg.py).

spawn() must (a) drop its strong reference once the task is done so `_bg_tasks`
doesn't grow without bound, (b) log a warning naming the label when the coro
raises, and (c) stay silent when the task is merely cancelled (a normal shutdown
signal, not a failure).
"""
import asyncio
import logging

import pytest

from src import bg  # noqa: E402


async def test_spawn_discards_ref_and_logs_failure(caplog):
    async def _boom():
        raise ValueError("kaboom")

    with caplog.at_level(logging.WARNING, logger="src.bg"):
        task = bg.spawn(_boom(), label="boomer")
        with pytest.raises(ValueError):
            await task
        await asyncio.sleep(0)  # let the done-callback run

    # (a) the strong ref is dropped once the task finishes
    assert task not in bg._bg_tasks
    # (b) the failure is logged as a warning naming the label
    assert any(
        r.levelno == logging.WARNING and "boomer" in r.getMessage()
        for r in caplog.records
    )


async def test_spawn_cancelled_task_logs_nothing(caplog):
    async def _sleep_forever():
        await asyncio.sleep(3600)

    with caplog.at_level(logging.WARNING, logger="src.bg"):
        task = bg.spawn(_sleep_forever(), label="sleeper")
        await asyncio.sleep(0)  # let the task start
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)  # let the done-callback run

    # (a) ref dropped, and (c) a cancellation is silent — no warning logged
    assert task not in bg._bg_tasks
    assert not any(r.name == "src.bg" for r in caplog.records)
