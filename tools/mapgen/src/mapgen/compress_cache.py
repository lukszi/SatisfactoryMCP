"""Convert kept raster caches from raw memory maps to the zstd band store.

``python -m mapgen compress-cache <dir> [--to <dir>]``. ``<dir>`` is one raster cache or a
folder holding them. Each plane is written, every band read back against a digest of the raw
band, the sidecar records the storage, and only then are the raw planes removed. ``--to``
must lie outside the source. docs/spatial-and-map.md section 39.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from functools import partial
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
    held_open,
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


def _real(path: Path) -> Path:
    return Path(os.path.normcase(os.path.realpath(path)))


def _clash(source: Path, target: Path) -> str | None:
    """How ``target`` falls on ``source`` once links, junctions and case are resolved."""
    src, dst = _real(source), _real(target)
    if dst == src:
        how = "is"
    elif dst in src.parents:
        how = "holds"
    elif src in dst.parents:
        how = "is inside"
    else:
        return None
    real = os.path.realpath(target)
    which = "" if real == str(target) else f" (which is {real})"
    return f"{target}{which} {how} the source {source}"


def _digest(band: np.ndarray) -> bytes:
    return hashlib.blake2b(np.ascontiguousarray(band).data, digest_size=16).digest()


def _raw_bands(src: Path, size: int, dtype: np.dtype):
    with open(src, "rb") as raw:
        for top in range(0, size, BAND_ROWS):
            rows = min(BAND_ROWS, size - top)
            yield top, np.fromfile(raw, dtype, rows * size).reshape(rows, size)


def _check(dst: Path, size: int, dtype: np.dtype, digests: list[bytes]) -> None:
    back = BandArray(dst, (size, size), dtype, keep=1)
    for k, top in enumerate(range(0, size, BAND_ROWS)):
        if _digest(back[top : top + BAND_ROWS]) != digests[k]:
            raise Refused(f"{dst.name}: band {k} does not match the raw plane")


def _convert_plane(src: Path, dst: Path, size: int, dtype: np.dtype) -> None:
    digests = []
    with BandWriter(dst, (size, size), dtype, BAND_ROWS) as out:
        for top, band in _raw_bands(src, size, dtype):
            digests.append(_digest(band))
            out.write(top, band)
    _check(dst, size, dtype, digests)


def _matches_its_bands(cache: Path, size: int, name: str) -> None:
    dtype, bands = PLANE_DTYPES[name], plane_file(cache, name, STORAGE_BANDS)
    if not bands.is_file():
        raise Refused(f"no {bands.name} beside it")
    if (cache / name).stat().st_size != size * size * dtype.itemsize:
        raise Refused(f"not a {size} px square of {dtype}")
    _check(bands, size, dtype, [_digest(b) for _top, b in _raw_bands(cache / name, size, dtype)])


def _drop_raw(cache: Path, names: list[str], verify=None) -> dict:
    removed, kept = [], {}
    for name in names:
        try:
            if verify is not None:
                verify(name)
            (cache / name).unlink()
        except (Refused, OSError, ValueError) as exc:
            kept[name] = str(exc)
        else:
            removed.append(name)
    return {"removed": removed, "kept": kept}


def _write_sidecar(path: Path, recorded: dict) -> None:
    staging = path.with_name(path.name + ".tmp")
    try:
        with open(staging, "w", encoding="utf-8") as f:
            f.write(json.dumps(recorded, indent=1))
            f.flush()
            os.fsync(f.fileno())
        os.replace(staging, path)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise


def compress(cache: Path, target: Path | None = None) -> dict:
    """Convert one cache in place, or into ``target`` leaving ``cache`` as it was."""
    if target is not None and (on := _clash(cache, target)):
        raise Refused(f"{on}; --to takes a folder outside it")
    try:
        recorded = json.loads((cache / SIDECAR).read_text(encoding="utf-8"))
        size = int(recorded["size"])
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise Refused(f"no readable {SIDECAR} naming a size: still being written, or not a "
                      f"raster cache ({exc})") from exc  # fmt: skip
    storage = recorded.get("storage", STORAGE_RAW)
    names = [name for name in PLANE_DTYPES if (cache / name).is_file()]
    if probed := [n for n in PLANE_DTYPES if (cache / (n + ".probe")).is_file()]:
        raise Refused(f"{probed[0]}.probe is a plane an interrupted open-file check renamed; "
                      f"rename it back to {probed[0]}")  # fmt: skip
    if storage == STORAGE_BANDS:
        if target is not None:
            raise Refused("already in the band store")
        verify = partial(_matches_its_bands, cache, size)
        return {"already": True, **_drop_raw(cache, names, verify)}
    if storage != STORAGE_RAW or not names:
        raise Refused(f"storage {storage!r} with planes {names}: nothing this command converts")
    for name in names:
        if (cache / name).stat().st_size != size * size * PLANE_DTYPES[name].itemsize:
            raise Refused(f"{name} is not a {size} px square of {PLANE_DTYPES[name]}")
    if target is None and (held := held_open([cache / n for n in names])):
        raise Refused(f"{held.name} is held open, by a render reading this cache")
    if target is not None and (raw := [n for n in PLANE_DTYPES if (target / n).is_file()]):
        raise Refused(f"{target} holds raw planes {raw}: not a band store this command wrote")
    out = cache if target is None else target
    out.mkdir(parents=True, exist_ok=True)
    if target is not None:
        (target / SIDECAR).unlink(missing_ok=True)
    clear_planes(out, [n for n in PLANE_DTYPES if target is not None or n not in names])
    started, written = time.time(), []
    try:
        for name in names:
            written.append(plane_file(out, name, STORAGE_BANDS))
            _convert_plane(cache / name, written[-1], size, PLANE_DTYPES[name])
        band_bytes = sum(p.stat().st_size for p in written)
        _write_sidecar(out / SIDECAR, {**recorded, "storage": STORAGE_BANDS})
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    done = {"planes": names, "in_place": target is None, "target": out, "band_bytes": band_bytes,
            "raw_bytes": sum(size * size * PLANE_DTYPES[n].itemsize for n in names),
            "seconds": round(time.time() - started, 1)}  # fmt: skip
    return {**done, **(_drop_raw(cache, names) if target is None else {})}


def _report(cache: Path, done: dict) -> str:
    if done.get("already"):
        line = f"{cache}: already in the band store"
        if done["removed"]:
            line += (f"; raw {', '.join(done['removed'])} left by an interrupted run removed, "
                     "each matched to its bands first")  # fmt: skip
    else:
        sizes = (f"{done['raw_bytes'] / 1e6:,.1f} MB raw to {done['band_bytes'] / 1e6:,.1f} MB "
                 f"in {done['seconds']} s, every band read back")  # fmt: skip
        if not done["in_place"]:
            line = f"{cache}: written to {done['target']}, {sizes}; the source untouched"
        elif not done["kept"]:
            line = f"{cache}: converted in place, {sizes}; the raw planes removed"
        else:
            line = f"{cache}: converted in place, {sizes}"
    for name, why in done.get("kept", {}).items():
        line += f"; raw {name} kept: {why}"
    return line


def _refusal(source: Path, to: Path, targets: dict) -> str | None:
    pairs = [(source, to)] + [(cache, target) for target in targets.values() for cache in targets]
    on = next(filter(None, (_clash(cache, target) for cache, target in pairs)), None)
    return on and (f"--to: {on}. Nothing converted: give --to a folder outside the source, or "
                   "leave it out to convert in place.")  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dir", type=Path, help="a raster cache, or a folder of them")
    parser.add_argument("--to", type=Path,
                        help="write the band store here, outside dir, and leave dir alone")  # fmt: skip
    args = parser.parse_args()
    caches = caches_under(args.dir)
    if not caches:
        print(f"no raster cache in {args.dir}")
        return REFUSED
    targets = {cache: args.to and (args.to if cache == args.dir else args.to / cache.name)
               for cache in caches}  # fmt: skip
    if args.to is not None and (refusal := _refusal(args.dir, args.to, targets)):
        print(refusal)
        return REFUSED
    status = 0
    for cache in caches:
        try:
            done = compress(cache, targets[cache])
        except (Refused, OSError, ValueError) as exc:
            kept = "the source" if args.to is not None else "its raw planes and sidecar"
            print(f"{cache}: not converted, {kept} untouched. {exc}")
            status = REFUSED
            continue
        print(_report(cache, done))
        status = REFUSED if done.get("kept") else status
    return status
