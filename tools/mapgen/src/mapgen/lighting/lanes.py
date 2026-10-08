"""The GPU's lanes: how many light processes keep a block on the device at once.

With ``--gpu`` each light process opens its own CUDA context, and a block keeps its planes on
the device while its horizons and sky views are made. ``LightBake`` hands its processes one
semaphore of ``DEVICE_LANES`` (``join``); a block takes a lane for that while (``device_lane``)
and waits while every lane is taken, so the light's device memory stays bounded however many
processes it runs. docs/map/renders.md section 41, "On the GPU".
"""

from __future__ import annotations

import multiprocessing
from collections.abc import Generator
from contextlib import contextmanager
from multiprocessing.synchronize import Semaphore

__all__ = ["DEVICE_LANES", "device_lane", "join", "lanes"]

#: Blocks on the device at once (section 41, "Memory").
DEVICE_LANES = 3

_lanes: Semaphore | None = None


def lanes() -> Semaphore:
    """A new set of ``DEVICE_LANES`` lanes for a pool's processes."""
    return multiprocessing.get_context("spawn").Semaphore(DEVICE_LANES)


def join(shared: Semaphore) -> None:
    """A light process's initializer: its blocks take their lanes from ``shared``."""
    global _lanes
    _lanes = shared


@contextmanager
def device_lane() -> Generator[None, None, None]:
    """A lane held for the block's time on the device; a process that joined none (one
    baking alone, a test) holds nothing."""
    if _lanes is None:
        yield
        return
    with _lanes:
        yield
