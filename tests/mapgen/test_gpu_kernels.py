"""The CUDA kernels give the bits of the numpy reference, and ``--gpu`` sets the switch.

docs/map/renders.md section 43. Synthetic fixtures: no install, no field. The kernel tests
skip, saying why, on a machine without numba, CuPy or a CUDA device; the switch, the flag
and the light's worker count run everywhere.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable

import numpy as np
import pytest

from mapgen import jit
from mapgen.lighting import horizon as hz
from mapgen.lighting import stage
from tests.support.paths import REPO_ROOT
from tests.support.relief import octave_terrain

SP = 1.0
HALO = hz.horizon_reach_px(SP)

#: pytest puts its own filters before ``mapgen.jit``'s, which quiets this one in a render.
pytestmark = pytest.mark.filterwarnings("ignore:CUDA path could not be detected:UserWarning")


@pytest.fixture(scope="module")
def device() -> None:
    """The CUDA kernels' preconditions, checked once a worker: numba, CuPy, a device."""
    problem = jit.gpu_problem()
    if problem is not None:
        pytest.skip(problem)


def _same(monkeypatch: pytest.MonkeyPatch, call: Callable[[], np.ndarray]) -> np.ndarray:
    """``call`` under the reference and on the GPU: the same dtype, shape and bytes."""
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    reference = call()
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    assert jit.gpu_on()
    on_gpu = call()
    assert (on_gpu.dtype, on_gpu.shape) == (reference.dtype, reference.shape)
    assert on_gpu.tobytes() == reference.tobytes()
    return on_gpu


# ------------------------------------------------------------------------------- switch


@pytest.mark.parametrize(
    ("value", "gpu"), [(" CUDA ", True), ("cuda", True), ("numpy", False), ("", False)]
)
def test_the_switch_names_the_gpu(monkeypatch, value, gpu):
    monkeypatch.setattr(jit, "_numba", lambda: object())
    monkeypatch.setenv(jit.KERNEL_SWITCH, value)
    assert jit.gpu_on() is gpu
    assert jit.kernels_on() is (value != "numpy"), "numba's kernels run beside the GPU's"
    monkeypatch.setattr(jit, "_numba", lambda: None)
    assert not jit.gpu_on(), "without numba the reference runs"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    jit.add_gpu_flag(parser)
    return parser


def test_the_flag_sets_the_switch_for_the_run_and_its_processes(monkeypatch):
    monkeypatch.delenv(jit.KERNEL_SWITCH, raising=False)
    monkeypatch.setattr(jit, "gpu_problem", lambda: None)
    assert _parser().parse_args([]).gpu is False
    assert jit.KERNEL_SWITCH not in os.environ
    assert _parser().parse_args(["--gpu"]).gpu is True
    assert os.environ[jit.KERNEL_SWITCH] == jit.GPU


def test_the_flag_is_refused_where_the_kernels_cannot_run(monkeypatch, capsys):
    monkeypatch.setenv(jit.KERNEL_SWITCH, "")
    monkeypatch.setattr(jit, "gpu_problem", lambda: "no CUDA device")
    with pytest.raises(SystemExit) as refused:
        _parser().parse_args(["--gpu"])
    assert refused.value.code == 2
    assert "--gpu: no CUDA device" in capsys.readouterr().err
    assert os.environ[jit.KERNEL_SWITCH] == ""


def test_the_renders_command_takes_the_flag():
    from mapgen.commands.renders import build_parser

    action = build_parser()._option_string_actions["--gpu"]
    assert action.nargs == 0 and action.default is False


def test_only_the_gpu_switch_loads_cupy():
    code = (
        "import os, sys\n"
        "import numpy as np\n"
        "from mapgen.lighting import horizon as hz\n"
        "z = np.random.default_rng(1).random((90, 90), dtype=np.float32)\n"
        "for value in ('numpy', ''):\n"
        "    os.environ['MAPGEN_KERNELS'] = value\n"
        "    hz.march_horizon(z, 40, 30.0, 4.0); hz.sky_view(z, 12, 1.0)\n"
        "print('cupy' in sys.modules, 'mapgen.lighting.gpu' in sys.modules)\n"
    )
    paths = [str(REPO_ROOT / "src"), str(REPO_ROOT / "tools" / "mapgen" / "src")]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env, timeout=120, check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "False False"


def test_a_light_process_with_the_gpu_counts_its_cuda_memory(monkeypatch):
    monkeypatch.setattr(stage.os, "cpu_count", lambda: 64)
    each = stage.LIGHT_WORKER_BYTES + stage.LIGHT_GPU_BYTES
    monkeypatch.setattr(stage, "free_ram_bytes", lambda: 5 * each)
    monkeypatch.setattr(jit, "_numba", lambda: object())
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    assert stage.light_workers() == 5 * each // stage.LIGHT_WORKER_BYTES
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    assert stage.light_workers() == 5


# ---------------------------------------------------------------------------- the light


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize("az", [0.0, 33.75, 90.0, 191.25, 270.0])
def test_the_march_is_the_reference_bit_for_bit(monkeypatch, az):
    z = octave_terrain(2 * HALO + 70, seed=1)
    lo = np.where(z > 20, z - 4, np.nan).astype(np.float32)
    _same(monkeypatch, lambda: hz.march_horizon(z, HALO, az, SP))
    _same(monkeypatch, lambda: hz.march_horizon(z, HALO, az, SP, slabs=(z - 1, lo, lo + 9)))
    _same(monkeypatch, lambda: hz.crown_horizon(z + 3, HALO, az, SP, z))


@pytest.mark.usefixtures("device")
def test_the_march_with_crowns_is_the_reference_bit_for_bit(monkeypatch):
    z = octave_terrain(2 * HALO + 50, seed=3)
    occluder = np.where(z > 12, z + 9, np.nan).astype(np.float32)
    _same(monkeypatch, lambda: hz.march_horizon(z, HALO, 45.0, SP, occluder=occluder))


@pytest.mark.usefixtures("device")
def test_rows_and_columns_off_the_block_size_are_marched_whole(monkeypatch):
    """A row of ``ROW_THREADS`` and one more column: the last block's threads past it idle."""
    from mapgen.lighting import gpu

    z = octave_terrain(2 * HALO + gpu.ROW_THREADS + 1, seed=6)[:-3]
    _same(monkeypatch, lambda: hz.march_horizon(z, HALO, 300.0, SP))


@pytest.mark.usefixtures("device")
def test_float64_slabs_are_marched_by_numba(monkeypatch):
    from mapgen.lighting import gpu

    z = octave_terrain(2 * HALO + 40, seed=2)
    lo = np.where(z > 10, z - 2.5, np.nan).astype(np.float64)
    slabs = (z, lo, lo + 4.25)
    monkeypatch.setattr(gpu, "_march", _refused)
    _same(monkeypatch, lambda: hz.march_horizon(z, HALO, 123.75, SP, slabs=slabs))


@pytest.mark.usefixtures("device")
def test_a_march_the_device_has_no_memory_for_runs_numba(monkeypatch):
    from mapgen.lighting import gpu

    z = octave_terrain(2 * HALO + 30, seed=7)
    monkeypatch.setattr(gpu, "_march", _out_of_memory)
    monkeypatch.setattr(gpu, "_sky_view", _out_of_memory)
    _same(monkeypatch, lambda: hz.march_horizon(z, HALO, 210.0, SP))
    _same(monkeypatch, lambda: hz.sky_view(z, 12, SP))


def _refused(*_args: object) -> None:
    raise AssertionError("a float64 slab reached the GPU")


def _out_of_memory(*_args: object) -> None:
    raise MemoryError("out of device memory")


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize("spacing", [0.5, 2.0, 100.0])
def test_the_sky_view_is_the_reference_bit_for_bit(monkeypatch, spacing):
    halo = int(np.ceil(hz.SKY_RADIUS_M / spacing)) + 2
    z = octave_terrain(2 * halo + 90, seed=5)
    _same(monkeypatch, lambda: hz.sky_view(z, halo, spacing))
    _same(monkeypatch, lambda: hz.sky_view(z[3:-1, 2:-5], halo, spacing))  # a strided view
