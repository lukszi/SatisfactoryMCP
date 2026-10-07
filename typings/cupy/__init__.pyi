"""CuPy ships no types; this covers what mapgen's CUDA kernels use of it (``mapgen.jit``)."""

from typing import Generic, TypeVar

import numpy as np
from cupy import cuda as cuda
from numpy.typing import NDArray

_Scalar = TypeVar("_Scalar", bound=np.generic)

class ndarray(Generic[_Scalar]):
    @property
    def shape(self) -> tuple[int, ...]: ...
    def get(self) -> NDArray[_Scalar]: ...

def asarray(a: NDArray[_Scalar], /) -> ndarray[_Scalar]: ...
def empty(shape: tuple[int, ...], dtype: type[_Scalar]) -> ndarray[_Scalar]: ...

class RawKernel:
    def __call__(
        self, grid: tuple[int, ...], block: tuple[int, ...], args: tuple[object, ...], /
    ) -> None: ...

class RawModule:
    def __init__(self, *, code: str, options: tuple[str, ...] = ...) -> None: ...
    def get_function(self, name: str) -> RawKernel: ...

class MemoryPool:
    def free_all_blocks(self) -> None: ...

def get_default_memory_pool() -> MemoryPool: ...
