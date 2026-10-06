"""Convert kept raster caches from raw memory maps to the zstd band store.

``python -m mapgen compress-cache <dir> [--to <dir>]``. ``<dir>`` is one raster cache or a
folder holding them. Each plane is written, every band read back against a digest of the raw
band, the sidecar records the storage, and only then are the raw planes removed.
docs/spatial-and-map.md section 39.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np

from mapgen.bandstore import BAND_ROWS, BandArray, BandWriter
from mapgen.cache import (
    DIRECT_CACHE_SIDECAR,
    PLANE_DTYPES,
    RASTER_DIRS,
    STORAGE_BANDS,
    STORAGE_RAW,
    clear_planes,
    plane_file,
)

__all__ = ["REFUSED", "Refused", "caches_under", "compress", "held_open", "main"]

REFUSED = 1
SIDECAR = DIRECT_CACHE_SIDECAR


class Refused(Exception):
    """A cache this command will not convert, with the reason."""


def caches_under(root: Path) -> list[Path]:
    """``root`` itself when it is a cache, else the raster caches directly under it."""
    if (root / SIDECAR).is_file() or root.name in RASTER_DIRS:
        return [root]
    return [root / name for name in RASTER_DIRS if (root / name).is_dir()]


def held_open(paths) -> Path | None:
    """The first file another process holds open, found by renaming it to itself and back.

    Windows refuses to rename an open file; elsewhere a rename always succeeds, and nothing is
    found.
    """
    for path in paths:
        probe = path.with_name(path.name + ".probe")
        try:
            os.replace(path, probe)
        except OSError:
            return path
        os.replace(probe, path)
    return None


def _digest(band: np.ndarray) -> bytes:
    return hashlib.blake2b(np.ascontiguousarray(band).data, digest_size=16).digest()


def _convert_plane(src: Path, dst: Path, size: int, dtype: np.dtype) -> None:
    digests = []
    with open(src, "rb") as raw, BandWriter(dst, (size, size), dtype, BAND_ROWS) as out:
        for top in range(0, size, BAND_ROWS):
            rows = min(BAND_ROWS, size - top)
            band = np.fromfile(raw, dtype, rows * size).reshape(rows, size)
            digests.append(_digest(band))
            out.write(top, band)
    back = BandArray(dst, (size, size), dtype, keep=1)
    for k, top in enumerate(range(0, size, BAND_ROWS)):
        if _digest(back[top : top + BAND_ROWS]) != digests[k]:
            raise Refused(f"{dst.name}: band {k} does not read back as it was written")


def _write_sidecar(path: Path, recorded: dict) -> None:
    staging = path.with_name(path.name + ".tmp")
    with open(staging, "w", encoding="utf-8") as f:
        f.write(json.dumps(recorded, indent=1))
        f.flush()
        os.fsync(f.fileno())
    os.replace(staging, path)


def compress(cache: Path, target: Path | None = None) -> dict:
    """Convert one cache in place, or into ``target`` leaving ``cache`` as it was."""
    try:
        recorded = json.loads((cache / SIDECAR).read_text(encoding="utf-8"))
        size = int(recorded["size"])
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise Refused(f"no readable {SIDECAR} naming a size: still being written, or not a "
                      f"raster cache ({exc})") from exc  # fmt: skip
    storage = recorded.get("storage", STORAGE_RAW)
    names = [name for name in PLANE_DTYPES if (cache / name).is_file()]
    if storage == STORAGE_BANDS:
        if target is not None:
            raise Refused("already in the band store")
        for name in names:
            if plane_file(cache, name, STORAGE_BANDS).is_file():
                (cache / name).unlink()
        return {"already": True}
    if storage != STORAGE_RAW or not names:
        raise Refused(f"storage {storage!r} with planes {names}: nothing this command converts")
    for name in names:
        if (cache / name).stat().st_size != size * size * PLANE_DTYPES[name].itemsize:
            raise Refused(f"{name} is not a {size} px square of {PLANE_DTYPES[name]}")
    if target is None and (held := held_open([cache / n for n in names])):
        raise Refused(f"{held.name} is held open, by a render reading this cache")
    out = cache if target is None else target
    out.mkdir(parents=True, exist_ok=True)
    if target is not None:
        (target / SIDECAR).unlink(missing_ok=True)
        clear_planes(target, names)
    started, written = time.time(), []
    try:
        for name in names:
            written.append(plane_file(out, name, STORAGE_BANDS))
            _convert_plane(cache / name, written[-1], size, PLANE_DTYPES[name])
        _write_sidecar(out / SIDECAR, {**recorded, "storage": STORAGE_BANDS})
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    raw = sum((cache / n).stat().st_size for n in names)
    if target is None:
        for name in names:
            (cache / name).unlink()
    return {"planes": names, "raw_bytes": raw, "band_bytes": sum(p.stat().st_size for p in written),
            "seconds": round(time.time() - started, 1)}  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dir", type=Path, help="a raster cache, or a folder of them")
    parser.add_argument("--to", type=Path, help="write the band store here and leave dir alone")
    args = parser.parse_args()
    caches = caches_under(args.dir)
    if not caches:
        print(f"no raster cache in {args.dir}")
        return REFUSED
    status = 0
    for cache in caches:
        target = args.to and (args.to if cache == args.dir else args.to / cache.name)
        try:
            done = compress(cache, target)
        except (Refused, OSError, ValueError) as exc:
            print(f"{cache}: left as it was. {exc}")
            status = REFUSED
            continue
        if done.get("already"):
            print(f"{cache}: already in the band store")
            continue
        print(f"{cache}: {done['raw_bytes'] / 1e6:,.1f} MB raw to {done['band_bytes'] / 1e6:,.1f} MB "
              f"in {done['seconds']} s, every band read back")  # fmt: skip
    return status
