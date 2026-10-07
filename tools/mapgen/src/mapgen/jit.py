"""The kernel switch: loops numba compiles, or the numpy code each one reproduces bit for bit.

``MAPGEN_KERNELS=numpy`` runs the numpy reference; unset, or any other value, runs the kernels
wherever numba imports. A module of kernels is imported only once ``kernels_on()`` says so,
so the reference never loads numba. docs/map/renders.md section 41.
"""

from __future__ import annotations

import functools
import hashlib
import os
from collections.abc import Callable
from types import ModuleType
from typing import TypeVar, cast

__all__ = ["KERNEL_SWITCH", "REFERENCE", "helper", "kernel", "kernels_on", "keyed_cache_files"]

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
    every operation stays the IEEE float32 one numpy does. Each signature's compiled code is
    kept in a file named by the signature (``keyed_cache_files``).
    """
    compiled = _compiler().njit(cache=True, nogil=True, error_model="numpy")(loop)
    cache = compiled._cache
    files = cache._cache_file
    base = files._index_name.removesuffix(".nbi")
    cache._cache_file = keyed_cache_files()(files._cache_path, base, files._source_stamp)
    return cast(_Loop, compiled)


@functools.cache
def keyed_cache_files() -> type:
    """numba's cache files with each signature's code named by a digest of its key.

    numba numbers a kernel's code files in the order its signatures are compiled. Two
    processes compiling different signatures at once can take the same number, and the index
    then hands one signature the other's code (docs/map/renders.md section 41). Named by key,
    they never share a file; a process that saves its index over another's only drops that
    one's entry, which is compiled again.
    """
    from numba.core.caching import IndexDataCacheFile

    class KeyedCacheFiles(IndexDataCacheFile):
        def save(self, key: object, data: object) -> None:
            digest = hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:24]
            name = self._index_name.removesuffix(".nbi") + f".{digest}.nbc"
            overloads = self._load_index()
            if overloads.get(key) != name:
                overloads[key] = name
                self._save_index(overloads)
            self._save_data(name, data)

    return KeyedCacheFiles


def helper(loop: _Loop) -> _Loop:
    """A scalar function the kernels call, compiled into each caller."""
    return cast(_Loop, _compiler().njit(inline="always", error_model="numpy")(loop))
