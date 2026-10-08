"""The derive gate: a derived colour stands in for its screenshot target only when the two
agree in lightness, chroma and hue. docs/map/calibration.md section 31, "The derive gate"."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from mapgen.palette.painted.derive.camera import delta_e, lab_of_hex

__all__ = [
    "GATE_CHROMATIC_SHARE",
    "GATE_CHROMA_FLOOR",
    "GATE_DELTA_E",
    "GateVerdict",
    "gate",
    "gate_hex",
]

#: OKLab distance x100 a derived colour may lie from its screenshot target.
GATE_DELTA_E = 5.0
#: Its chroma and hue difference together, as a share of the target's chroma.
GATE_CHROMATIC_SHARE = 2.0 / 3.0
#: The chroma below which a target counts as this grey, so a grey's allowance does not vanish.
GATE_CHROMA_FLOOR = 0.02


@dataclass(frozen=True)
class GateVerdict:
    """The distances, OKLab x100, and what failed: ``delta_e``, the lightness, chroma and
    hue parts, and the chroma and hue part's allowance."""

    delta_e: float
    delta_l: float
    delta_c: float
    delta_h: float
    allowance: float
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def chromatic(self) -> float:
        return float(np.hypot(self.delta_c, self.delta_h))


def _chroma(lab: NDArray[np.floating]) -> float:
    return float(np.hypot(lab[1], lab[2]))


def gate(derived: NDArray[np.floating], target: NDArray[np.floating]) -> GateVerdict:
    """``derived`` against ``target``, both OKLab: within ``GATE_DELTA_E``, and its chroma and
    hue difference within ``GATE_CHROMATIC_SHARE`` of the target's chroma."""
    d, t = np.asarray(derived, np.float64), np.asarray(target, np.float64)
    distance = delta_e(d, t)
    chroma_d, chroma_t = _chroma(d), _chroma(t)
    ab = 100.0 * float(np.hypot(d[1] - t[1], d[2] - t[2]))
    dc = 100.0 * (chroma_d - chroma_t)
    dh = float(np.sqrt(max(ab * ab - dc * dc, 0.0)))
    allowance = 100.0 * GATE_CHROMATIC_SHARE * max(chroma_t, GATE_CHROMA_FLOOR)
    failures: list[str] = []
    if distance > GATE_DELTA_E:
        failures.append(f"dE {distance:.2f} > {GATE_DELTA_E:g}")
    if ab > allowance:
        failures.append(f"chroma and hue {ab:.1f} (dC {dc:+.1f}, dH {dh:.1f}) > {allowance:.1f}")
    return GateVerdict(distance, 100.0 * (d[0] - t[0]), dc, dh, allowance, tuple(failures))


def gate_hex(derived: str, target: str) -> GateVerdict:
    """``gate`` on two display sRGB hex colours."""
    return gate(lab_of_hex(derived), lab_of_hex(target))
