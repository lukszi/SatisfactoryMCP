"""The CUDA kernels give the bits of the numpy reference, ``--gpu`` sets the switch, and a bake
logs where its calls ran.

docs/map/renders.md section 41, "On the GPU". Synthetic fixtures: no install, no field. The kernel tests
skip, saying why, on a machine without numba, CuPy or a CUDA device; the switch, the flag
and the light's worker count run everywhere.
"""

from __future__ import annotations

import argparse
import importlib
import os
import re
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
    monkeypatch.setenv(jit.KERNEL_SWITCH, "")  # recorded, so the flag's setting is undone
    monkeypatch.delenv(jit.KERNEL_SWITCH)
    monkeypatch.setattr(jit, "_gpu_problem_apart", lambda: None)
    assert _parser().parse_args([]).gpu is False
    assert jit.KERNEL_SWITCH not in os.environ
    assert _parser().parse_args(["--gpu"]).gpu is True
    assert os.environ[jit.KERNEL_SWITCH] == jit.GPU


def test_the_flag_is_refused_where_the_kernels_cannot_run(monkeypatch, capsys):
    from mapgen.commands.renders import build_parser

    monkeypatch.setenv(jit.KERNEL_SWITCH, "")
    monkeypatch.setattr(jit, "_gpu_problem_apart", lambda: "no CUDA device")
    for parser in (_parser(), build_parser()):
        with pytest.raises(SystemExit) as refused:
            parser.parse_args(["--gpu"])
        assert refused.value.code == jit.NO_GPU
        assert "--gpu: no CUDA device" in capsys.readouterr().out
        assert os.environ[jit.KERNEL_SWITCH] == ""


def test_the_refusal_table_holds_each_code_and_the_gpu_s_is_its_own():
    """docs/map/renders.md section 20, "Refusals": each constant it names has its row's code."""
    text = (REPO_ROOT / "docs" / "map" / "renders.md").read_text(encoding="utf-8")
    table = text.split("### Refusals", 1)[1].split("\n### ", 1)[0]
    codes = []
    for code, names in re.findall(r"^\| (\d+) \| ([^|]+) \|", table, re.MULTILINE):
        for module, name in re.findall(r"`([\w/]+)\.([A-Z_]+)`", names):
            found = importlib.import_module("mapgen." + module.replace("/", "."))
            assert getattr(found, name) == int(code), f"{module}.{name}"
            codes.append(int(code))
    assert codes.count(jit.NO_GPU) == 1 and len(codes) > 10
    assert jit.NO_GPU != 2, "argparse exits 2 on a usage error"


@pytest.mark.usefixtures("device")
def test_the_flag_probes_the_device_from_a_process_of_its_own():
    """The run's own process never opens a CUDA context: only its light's processes use one."""
    code = (
        "import sys\n"
        "from mapgen import jit\n"
        "print(jit._gpu_problem_apart(), 'cupy' in sys.modules)\n"
    )
    paths = [str(REPO_ROOT / "src"), str(REPO_ROOT / "tools" / "mapgen" / "src")]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env, timeout=300, check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "None False"


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
    _same(monkeypatch, lambda: hz.march_horizon(z, HALO, az, SP))
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
def test_a_march_the_device_has_no_memory_for_runs_numba(monkeypatch):
    from mapgen.lighting import gpu

    z = octave_terrain(2 * HALO + 30, seed=7)
    monkeypatch.setattr(gpu, "_march", _out_of_memory)
    monkeypatch.setattr(gpu, "_sky_view", _out_of_memory)
    _same(monkeypatch, lambda: hz.march_horizon(z, HALO, 210.0, SP))
    _same(monkeypatch, lambda: hz.sky_view(z, 12, SP))


@pytest.mark.usefixtures("device")
def test_each_call_is_counted_where_it_ran(monkeypatch):
    from mapgen.lighting import gpu

    z = octave_terrain(2 * HALO + 30, seed=8)
    gpu.ran()
    _same(monkeypatch, lambda: hz.sky_view(z, 12, SP))
    on_device = gpu.ran()
    assert on_device and jit.ON_NUMBA not in on_device
    assert gpu.ran() == {}, "read once"
    monkeypatch.setattr(gpu, "_sky_view", _out_of_memory)
    _same(monkeypatch, lambda: hz.sky_view(z, 12, SP))
    assert gpu.ran() == {jit.ON_NUMBA: sum(on_device.values())}


def test_a_gpu_bake_logs_where_its_calls_ran():
    from mapgen.lighting.bake import gpu_calls_line
    from mapgen.lighting.stage import BlockDone

    done = [BlockDone(1, 0, 0.0, {"RTX": 3}), BlockDone(1, 0, 0.0, {"RTX": 2, jit.ON_NUMBA: 1})]
    assert gpu_calls_line(done) == (
        "light: horizon and sky-view calls 5 on RTX; 1 ran on numba, the device out of memory"
    )
    assert "calls none on CUDA; 0 ran" in gpu_calls_line([BlockDone(1, 0, 0.0, {})])


def _out_of_memory(*_args: object) -> None:
    raise MemoryError("out of device memory")


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize("spacing", [0.5, 2.0, 100.0])
def test_the_sky_view_is_the_reference_bit_for_bit(monkeypatch, spacing):
    halo = int(np.ceil(hz.SKY_RADIUS_M / spacing)) + 2
    z = octave_terrain(2 * halo + 90, seed=5)
    _same(monkeypatch, lambda: hz.sky_view(z, halo, spacing))
    _same(monkeypatch, lambda: hz.sky_view(z[3:-1, 2:-5], halo, spacing))  # a strided view
