"""The kernel switch: loops numba compiles, or the numpy code each one reproduces bit for bit.

``MAPGEN_KERNELS=numpy`` runs the numpy reference; unset, or any other value, runs the kernels
wherever numba imports. A module of kernels is imported only once ``kernels_on()`` says so,
so the reference never loads numba. docs/map/renders.md section 41.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from types import ModuleType
from typing import TypeVar, cast

__all__ = ["KERNEL_SWITCH", "REFERENCE", "helper", "kernel", "kernels_on"]

KERNEL_SWITCH = "MAPGEN_KERNELS"
#: The switch's value that selects the numpy reference.
REFERENCE = "numpy"

_Loop = TypeVar("_Loop", bound=Callable[..., object])


@functools.cache
def _numba() -> ModuleType | None:
    try:
        import numba
    except ImportError:
        return None
    return numba


def kernels_on() -> bool:
    """True unless the switch names the reference or numba does not import."""
    chosen = os.environ.get(KERNEL_SWITCH, "").strip().lower()
    return chosen != REFERENCE and _numba() is not None


def _compiler() -> ModuleType:
    numba = _numba()
    if numba is None:
        raise ImportError(
            "the kernels need numba (the gen extra); MAPGEN_KERNELS=numpy runs without"
        )
    return numba


def kernel(loop: _Loop) -> _Loop:
    """``loop`` compiled on its first call.

    ``nogil`` so draw threads run it side by side, ``cache`` so a light process loads it from
    disk, and numpy's error model so a division by zero is inf as it is in numpy. No fastmath:
    every operation stays the IEEE float32 one numpy does.
    """
    return cast(_Loop, _compiler().njit(cache=True, nogil=True, error_model="numpy")(loop))


def helper(loop: _Loop) -> _Loop:
    """A scalar function the kernels call, compiled into each caller."""
    return cast(_Loop, _compiler().njit(inline="always", error_model="numpy")(loop))
