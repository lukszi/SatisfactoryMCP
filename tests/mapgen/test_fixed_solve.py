"""The membranes' conjugate gradients sum in a fixed order, so they solve to the same bits on
any number of BLAS threads.

docs/map/renders.md section 40, "Fixed-order sums". Synthetic systems: no install, no field.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from mapgen.terrain import solve
from mapgen.terrain.solve import fixed_dot, fixed_sum, jacobi_cg


def _halves(values: list[float]) -> float:
    """``fixed_sum``'s order, one Python float at a time."""
    while len(values) > 1:
        half = len(values) // 2
        folded = [values[i] + values[half + i] for i in range(half)]
        values = folded + values[2 * half :]
    return values[0] if values else 0.0


@pytest.mark.parametrize("n", [0, 1, 2, 3, 7, 8, 9, 1000, 1001, 4097])
def test_the_fixed_sum_is_its_halving_order_to_the_bit(n):
    rng = np.random.default_rng(n)
    values = rng.normal(0.0, 1.0, n) * 10.0 ** rng.integers(-8, 8, n)
    assert fixed_sum(values.copy()) == _halves(values.tolist())
    assert fixed_sum(values.copy()) == pytest.approx(math.fsum(values), rel=1e-12, abs=1e-300)
    assert fixed_dot(values, values) == _halves((values * values).tolist())


def test_jacobi_cg_is_scipy_s_cg_but_for_the_last_bits():
    n = 40 * 40
    lap = sp.diags([4.0] * n) - sp.diags([1.0] * (n - 1), 1) - sp.diags([1.0] * (n - 1), -1)
    a = (lap + sp.diags(np.full(n, 0.05))).tocsr()
    b = np.random.default_rng(1).normal(size=n)
    x0 = np.full(n, 3.0)
    want = spla.cg(a, b, x0=x0, rtol=1e-6, M=sp.diags(1.0 / a.diagonal()))[0]
    got = jacobi_cg(a, b, x0, 1e-6)
    np.testing.assert_allclose(got, want, rtol=1e-9, atol=1e-12)
    assert np.linalg.norm(b - a @ got) < 1e-6 * np.linalg.norm(b)


#: Solves a membrane of 300 x 300 cells, past OpenBLAS's threaded dot products, and prints
#: the answer's digest. With scipy's ``cg`` it printed three digests at 1, 4 and 24 threads.
_SOLVE = """
import hashlib, sys
import numpy as np
from mapgen.palette.water.open_sea import membrane
rng = np.random.default_rng(4)
free = rng.random((300, 300)) > 0.02
values = np.where(free, 0.0, rng.uniform(0.0, 60.0, (300, 300)))
got = membrane(values, ~free, free, 40.0, 60.0)
print(hashlib.sha256(got.tobytes()).hexdigest())
"""


def test_the_membrane_is_the_same_bits_on_any_number_of_blas_threads():
    src = Path(solve.__file__).parents[2]
    digests = set()
    for threads in ("1", "4", None):
        env = {k: v for k, v in os.environ.items() if k != "OPENBLAS_NUM_THREADS"}
        if threads:
            env["OPENBLAS_NUM_THREADS"] = threads
        env["PYTHONPATH"] = os.pathsep.join([str(src), env.get("PYTHONPATH", "")])
        done = subprocess.run([sys.executable, "-c", _SOLVE], env=env, capture_output=True,
                              text=True, check=True)  # fmt: skip
        digests.add(done.stdout.strip())
    assert len(digests) == 1, digests
