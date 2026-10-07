"""``cancel_and_wait``: a background task stopped without swallowing anyone else's cancel."""

from __future__ import annotations

import asyncio

import pytest

from satisfactory_mcp.interfaces.web.tasks import cancel_and_wait


async def _forever() -> None:
    await asyncio.Event().wait()


async def _fails_on_cancel() -> None:
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        raise RuntimeError("cleanup failed") from None


def test_a_cancelled_task_ends_quietly():
    async def scenario() -> asyncio.Task[None]:
        task = asyncio.create_task(_forever())
        await asyncio.sleep(0)
        await cancel_and_wait(task)
        return task

    assert asyncio.run(scenario()).cancelled()


def test_an_error_on_the_way_out_still_raises():
    async def scenario() -> None:
        task = asyncio.create_task(_fails_on_cancel())
        await asyncio.sleep(0)
        await cancel_and_wait(task)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        asyncio.run(scenario())


def test_the_callers_own_cancel_propagates():
    async def scenario() -> bool:
        stubborn = asyncio.create_task(_ignores_one_cancel())
        await asyncio.sleep(0)
        stopper = asyncio.create_task(cancel_and_wait(stubborn))
        await asyncio.sleep(0)
        stopper.cancel()
        try:
            await stopper
        except asyncio.CancelledError:
            stubborn.cancel()
            return True
        return False

    assert asyncio.run(scenario())


async def _ignores_one_cancel() -> None:
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await asyncio.Event().wait()
