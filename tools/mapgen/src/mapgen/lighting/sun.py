"""Where the sun stands: the game's own path through the day, and the defaults the map uses.

The game turns its sun about one fixed tilted axis (``AFGSkySphere``: pitch ``30 + 15 h``),
with no seasons. The frontend's ``sun.ts`` is the same arithmetic; a test holds the two to
the same noon. docs/spatial-and-map.md section 28.
"""

from __future__ import annotations

import math

__all__ = [
    "DEFAULT_SUN",
    "MAP_NW_SUN",
    "NOON_HOUR",
    "game_sun",
    "sun_vector",
]

#: Game noon: the sun's highest point on its path, 11:52.
NOON_HOUR = 11.87

#: The default sun, game noon, and the cartographic north-west preset.
DEFAULT_SUN = (225.0, 62.25)
MAP_NW_SUN = (315.0, 45.0)

_AXIS = (0.0, 45.0, 25.0)
_BASE = (55.0, 190.0, 0.0)


def _rotator(pitch: float, yaw: float, roll: float) -> list[list[float]]:
    """Unreal's ``FRotationMatrix`` rows, angles in degrees."""
    sp, cp = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    sy, cy = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
    sr, cr = math.sin(math.radians(roll)), math.cos(math.radians(roll))
    return [
        [cp * cy, cp * sy, sp],
        [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
        [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp],
    ]


def _row_times(v, m):
    return [sum(v[i] * m[i][j] for i in range(3)) for j in range(3)]


def _times_col(m, v):
    return [sum(m[j][i] * v[i] for i in range(3)) for j in range(3)]


def game_sun(hour: float) -> tuple[float, float]:
    """The sun at a game hour: ``(azimuth, elevation)`` in degrees, azimuth from north."""
    axis = _rotator(*_AXIS)
    light = _times_col(axis, _rotator(*_BASE)[0])
    norm = math.sqrt(sum(c * c for c in light))
    light = [c / norm for c in light]
    turned = _row_times(_row_times(light, _rotator(-(30.0 + 15.0 * hour), 0.0, 0.0)), axis)
    sx, sy, sz = (-c for c in turned)
    azimuth = (math.degrees(math.atan2(sx, -sy)) + 360.0) % 360.0
    elevation = math.degrees(math.asin(sz / math.sqrt(sx * sx + sy * sy + sz * sz)))
    return azimuth, elevation


def sun_vector(azimuth: float, elevation: float) -> tuple[float, float, float]:
    """Toward the sun as (east, south, up), the axes of the stored normals."""
    az, el = math.radians(azimuth), math.radians(elevation)
    return (math.cos(el) * math.sin(az), -math.cos(el) * math.cos(az), math.sin(el))
