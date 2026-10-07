"""Conjugate gradients whose every sum is fixed, so a membrane solves to the same bits on any
number of BLAS threads. docs/map/renders.md section 40, "Fixed-order sums".
"""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp

from satisfactory_mcp.core.arrays import F64Grid

__all__ = ["fixed_dot", "fixed_sum", "jacobi_cg"]


def fixed_sum(values: F64Grid) -> float:
    """The sum of a 1-D array by halves: each level adds its second half onto its first,
    elementwise, an odd last term carried to the next level. Overwrites ``values``."""
    level = values
    while level.size > 1:
        half = level.size // 2
        np.add(level[:half], level[half : 2 * half], out=level[:half])
        if level.size % 2:
            level[half] = level[-1]
            half += 1
        level = level[:half]
    return float(level[0]) if level.size else 0.0


def fixed_dot(a: F64Grid, b: F64Grid, scratch: F64Grid | None = None) -> float:
    """``a . b`` as ``fixed_sum`` of the products, where ``np.dot`` hands it to BLAS.
    ``scratch``, as long as ``a``, holds the products when given."""
    return fixed_sum(np.multiply(a, b, out=scratch))


def jacobi_cg(a: sp.csr_matrix, b: F64Grid, x0: F64Grid, rtol: float) -> F64Grid:
    """scipy's Jacobi-preconditioned ``cg``, step for step, with ``fixed_dot`` for its dot
    products and norms. Stops at ``|r| < rtol |b|``, or after ``10 n`` steps."""
    inverse = 1.0 / a.diagonal()
    x = x0.astype(np.float64).copy()
    scratch, z = np.empty_like(x), np.empty_like(x)
    norm_b = np.sqrt(fixed_dot(b, b, scratch))
    if norm_b == 0:
        return b.copy()
    stop = rtol * norm_b
    r = b - a @ x if x.any() else b.copy()
    p = np.empty_like(r)
    rho_prev = 1.0
    for step in range(10 * len(b)):
        if np.sqrt(fixed_dot(r, r, scratch)) < stop:
            break
        np.multiply(inverse, r, out=z)
        rho = fixed_dot(r, z, scratch)
        if step:
            p *= rho / rho_prev
            p += z
        else:
            p[:] = z
        q = a @ p
        alpha = rho / fixed_dot(p, q, scratch)
        x += np.multiply(p, alpha, out=scratch)
        r -= np.multiply(q, alpha, out=scratch)
        rho_prev = rho
    return x
