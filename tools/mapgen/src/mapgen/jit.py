"""The kernel switch: loops numba compiles, CUDA kernels, or the numpy each one equals bit for bit.

``MAPGEN_KERNELS=numpy`` runs the numpy reference; ``cuda`` (``renders --gpu``) the CUDA
kernels where there are some and numba's elsewhere; unset, or any other value, numba's
wherever it imports. A module of kernels is imported only once its switch says so, so the
reference never loads numba or CuPy. docs/map/renders.md section 41, "On the GPU".
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import multiprocessing
import os
import threading
import warnings
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from importlib import resources
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, TypeVar, cast

if TYPE_CHECKING:
    from cupy import RawKernel, RawModule

__all__ = [
    "CUDA_OPTIONS",
    "GPU",
    "KERNEL_SWITCH",
    "NO_GPU",
    "ON_NUMBA",
    "REFERENCE",
    "add_gpu_flag",
    "cuda_kernel",
    "gpu_on",
    "gpu_problem",
    "helper",
    "kernel",
    "kernels_on",
    "keyed_cache_files",
]

KERNEL_SWITCH = "MAPGEN_KERNELS"
#: The switch's value that selects the numpy reference.
REFERENCE = "numpy"
#: The switch's value that selects the CUDA kernels.
GPU = "cuda"
#: Where a CUDA kernel's call is counted when the device had no memory for it and numba ran it.
ON_NUMBA = "numba"
#: Exit code of a run whose ``--gpu`` cannot run here: not argparse's 2.
NO_GPU = 12

#: NVRTC's options for every CUDA kernel: no fused multiply-add, and division, square roots
#: and subnormals as IEEE has them, so each operation rounds as numpy's does.
CUDA_OPTIONS = ("--fmad=false", "--prec-div=true", "--prec-sqrt=true", "--ftz=false")

# CuPy with NVRTC from its wheel warns that it found no CUDA toolkit, which it does not need.
warnings.filterwarnings("ignore", "CUDA path could not be detected", UserWarning)

_Loop = TypeVar("_Loop", bound=Callable[..., object])

_compiling = threading.Lock()


@functools.cache
def _numba() -> ModuleType | None:
    try:
        import numba
    except ImportError:
        return None
    return numba


def _chosen() -> str:
    return os.environ.get(KERNEL_SWITCH, "").strip().lower()


def kernels_on() -> bool:
    """True unless the switch names the reference or numba does not import."""
    return _chosen() != REFERENCE and _numba() is not None


def gpu_on() -> bool:
    """True when the switch names the CUDA kernels and numba's run beside them."""
    return _chosen() == GPU and kernels_on()


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
    cache = getattr(compiled, "_cache", None)  # none under NUMBA_DISABLE_JIT
    files = getattr(cache, "_cache_file", None)  # none where numba caches nothing
    if cache is not None and files is not None:
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
        if TYPE_CHECKING:  # numba's own methods, unannotated, as this class calls them
            _index_name: str

            def _load_index(self) -> dict[object, str]: ...
            def _save_index(self, overloads: dict[object, str]) -> None: ...
            def _save_data(self, name: str, data: object) -> None: ...

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


def cuda_kernel(package: str, source: str, name: str) -> RawKernel:
    """The kernel ``name`` of the CUDA file ``source`` in ``package``, compiled with
    ``CUDA_OPTIONS`` once and kept on disk (``_cubin``)."""
    with _compiling:
        return _cuda_module(package, source).get_function(name)


@functools.cache
def _cuda_module(package: str, source: str) -> RawModule:
    import cupy

    code = resources.files(package).joinpath(source).read_text(encoding="utf-8")
    return cupy.RawModule(path=str(_cubin(code)))


def _cubin(code: str) -> Path:
    """``code`` compiled by NVRTC with ``CUDA_OPTIONS`` alone, kept where CuPy keeps its own
    code. CuPy's own compile adds ``-ftz=true`` after them, which flushes subnormals."""
    import cupy
    from cupy.cuda import compiler

    device = cupy.cuda.Device().compute_capability
    key = repr((code, CUDA_OPTIONS, device, cupy.cuda.nvrtc.getVersion())).encode("utf-8")
    folder = Path(os.environ.get("CUPY_CACHE_DIR") or Path.home() / ".cupy" / "kernel_cache")
    path = folder / f"mapgen-{hashlib.sha256(key).hexdigest()[:32]}.cubin"
    if not path.is_file():
        binary, _names = compiler.compile_using_nvrtc(code, CUDA_OPTIONS)
        folder.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(f".{os.getpid()}.part")
        part.write_bytes(binary if isinstance(binary, bytes) else binary.encode("utf-8"))
        try:
            os.replace(part, path)
        except OSError:  # another process wrote the same file and holds it open
            part.unlink(missing_ok=True)
            if not path.is_file():
                raise
    return path


def gpu_problem() -> str | None:
    """Why the CUDA kernels cannot run here, or None: numba, the gpu extra, a device, and a
    kernel compiled and loaded on it."""
    if _numba() is None:
        return "the CUDA kernels run beside numba's, which is the gen extra"
    try:
        import cupy
    except ImportError as exc:
        return f"CuPy does not import ({exc}): install the gpu extra"
    try:
        if not cupy.cuda.runtime.getDeviceCount():
            return "no CUDA device"
        probe = 'extern "C" __global__ void probe() {}'
        cupy.RawModule(code=probe, options=CUDA_OPTIONS).get_function("probe")
    except Exception as exc:  # whatever stops CuPy: no driver, no device, no NVRTC
        return f"CuPy cannot run a kernel here: {exc}"
    return None


def _gpu_problem_apart() -> str | None:
    """``gpu_problem`` in a process of its own: this one opens no CUDA context it never uses."""
    spawn = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=spawn) as probe:
        return probe.submit(gpu_problem).result()


class _SelectGpu(argparse.Action):
    """``--gpu``: the switch set to ``GPU`` for this process and the light's processes, which
    inherit it; refused at once with ``NO_GPU`` and the reason on stdout where the kernels
    cannot run."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        problem = _gpu_problem_apart()
        if problem is not None:
            print(f"--gpu: {problem}", flush=True)
            raise SystemExit(NO_GPU)
        os.environ[KERNEL_SWITCH] = GPU
        setattr(namespace, self.dest, True)


def add_gpu_flag(parser: argparse.ArgumentParser) -> None:
    """``--gpu``: the CUDA kernels where there are some."""
    parser.add_argument(
        "--gpu",
        action=_SelectGpu,
        nargs=0,
        default=False,
        help=(
            "run the light's horizon march and sky view as CUDA kernels on the GPU: the gpu "
            f"extra and an NVIDIA driver (the same as {KERNEL_SWITCH}={GPU}). The tiles are the "
            "same bytes"
        ),
    )
