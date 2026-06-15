"""Fire-and-forget background-task helper.

`asyncio.create_task(coro)` whose result is dropped has two hazards: the loop
keeps only a weak reference, so the task can be garbage-collected mid-flight
under load, and any exception it raises surfaces only as an "exception was never
retrieved" warning at GC time (easy to miss, wrong timestamp). `spawn()` keeps a
strong reference until the task finishes and logs failures explicitly.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Strong refs to in-flight fire-and-forget tasks so they aren't GC'd early.
_bg_tasks: set = set()


def spawn(coro, label: str = ""):
    """Schedule `coro` as a background task that won't be GC'd early and whose
    exceptions are logged instead of silently lost. Returns the task."""
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)

    def _done(t: asyncio.Task):
        _bg_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.warning("background task %s failed: %r", label or "<anon>", exc)

    task.add_done_callback(_done)
    return task
