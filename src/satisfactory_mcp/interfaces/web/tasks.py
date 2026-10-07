"""Stopping the server's background tasks."""

from __future__ import annotations

import asyncio

__all__ = ["cancel_and_wait"]


async def cancel_and_wait(task: asyncio.Task[None]) -> None:
    """Cancel ``task`` and wait until it has finished.

    The task's own cancellation is the expected end and is not raised; any other error it
    ended with is. A cancellation of the caller while it waits propagates as usual.
    """
    task.cancel()
    await asyncio.wait((task,))
    if not task.cancelled() and (error := task.exception()) is not None:
        raise error
