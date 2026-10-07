"""Named numpy array types, so a signature says the dtype instead of a bare ``ndarray``.

A grid is any array of that dtype; the shape is the docstring's business, since numpy's
types do not carry it (docs/DEVELOPING.md, "Types").
"""

from __future__ import annotations

from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

__all__ = [
    "BoolMask",
    "F16Grid",
    "F32Grid",
    "F64Grid",
    "FloatGrid",
    "I8Grid",
    "I16Grid",
    "I32Grid",
    "I64Grid",
    "U8Grid",
    "U16Grid",
    "U32Grid",
    "U64Grid",
]

BoolMask: TypeAlias = NDArray[np.bool_]
F16Grid: TypeAlias = NDArray[np.float16]
F32Grid: TypeAlias = NDArray[np.float32]
F64Grid: TypeAlias = NDArray[np.float64]
I8Grid: TypeAlias = NDArray[np.int8]
I16Grid: TypeAlias = NDArray[np.int16]
I32Grid: TypeAlias = NDArray[np.int32]
I64Grid: TypeAlias = NDArray[np.int64]
U8Grid: TypeAlias = NDArray[np.uint8]
U16Grid: TypeAlias = NDArray[np.uint16]
U32Grid: TypeAlias = NDArray[np.uint32]
U64Grid: TypeAlias = NDArray[np.uint64]
#: A float plane of either width: numpy's stubs widen float32 arithmetic with a Python float
#: to float64, so a plane computed that way is typed by kind rather than by width.
FloatGrid: TypeAlias = NDArray[np.floating]
