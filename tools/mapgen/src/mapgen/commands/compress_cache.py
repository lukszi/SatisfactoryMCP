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
from collections.abc import Callable, Iterable, Iterator
from functools import partial
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

import numpy as np
from numpy.typing import NDArray

from mapgen.bandstore import BAND_ROWS, BandArray, BandWriter
from mapgen.cache import (
    BAND_STORE_DIRS,
    CACHE_SIDECAR_NAME,
    PLANE_DTYPES,
    STORAGE_BANDS,
    STORAGE_RAW,
    clear_planes,
    held_open,
    plane_file,
    write_sidecar,
)
from mapgen.common import Refusal, require_gen
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue, as_int, require_object

__all__ = ["REFUSED", "AlreadyBanded", "Converted", "caches_under", "compress", "main"]

REFUSED = 1


class Dropped(TypedDict):
    """The raw planes removed after their bands were checked, and the ones kept, with why."""

    removed: list[str]
    kept: dict[str, str]


class AlreadyBanded(Dropped):
    """A cache already in the band store: only raw planes an interrupted run left are dropped."""

    already: Literal[True]


class Converted(TypedDict):
    """A cache converted in place, or into ``target`` with the source left as it was."""

    planes: list[str]
    in_place: bool
    target: Path
    band_bytes: int
    raw_bytes: int
    seconds: float
    removed: NotRequired[list[str]]
    kept: NotRequired[dict[str, str]]


def caches_under(root: Path) -> list[Path]:
    """``root`` itself when it is a cache, else the raster caches directly under it."""
    if (root / CACHE_SIDECAR_NAME).is_file() or root.name in BAND_STORE_DIRS:
        return [root]
    return [root / name for name in BAND_STORE_DIRS if (root / name).is_dir()]


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


def _digest(band: NDArray[np.generic]) -> bytes:
    return hashlib.blake2b(np.ascontiguousarray(band).data, digest_size=16).digest()


def _raw_bands(
    src: Path, size: int, dtype: np.dtype[np.generic]
) -> Iterator[tuple[int, NDArray[np.generic]]]:
    with open(src, "rb") as raw:
        for top in range(0, size, BAND_ROWS):
            rows = min(BAND_ROWS, size - top)
            yield top, np.fromfile(raw, dtype, rows * size).reshape(rows, size)


def _check(dst: Path, size: int, dtype: np.dtype[np.generic], digests: list[bytes]) -> None:
    back = BandArray(dst, (size, size), dtype, keep=1)
    for k, top in enumerate(range(0, size, BAND_ROWS)):
        if _digest(back[top : top + BAND_ROWS]) != digests[k]:
            raise Refusal(REFUSED, f"{dst.name}: band {k} does not match the raw plane")


def _convert_plane(src: Path, dst: Path, size: int, dtype: np.dtype[np.generic]) -> None:
    digests: list[bytes] = []
    with BandWriter(dst, (size, size), dtype, BAND_ROWS) as out:
        for top, band in _raw_bands(src, size, dtype):
            digests.append(_digest(band))
            out.write(top, band)
    _check(dst, size, dtype, digests)


def _matches_its_bands(cache: Path, size: int, name: str) -> None:
    dtype, bands = PLANE_DTYPES[name], plane_file(cache, name, STORAGE_BANDS)
    if not bands.is_file():
        raise Refusal(REFUSED, f"no {bands.name} beside it")
    if (cache / name).stat().st_size != size * size * dtype.itemsize:
        raise Refusal(REFUSED, f"not a {size} px square of {dtype}")
    _check(bands, size, dtype, [_digest(b) for _top, b in _raw_bands(cache / name, size, dtype)])


def _drop_raw(
    cache: Path, names: list[str], verify: Callable[[str], None] | None = None
) -> Dropped:
    removed: list[str] = []
    kept: dict[str, str] = {}
    for name in names:
        try:
            if verify is not None:
                verify(name)
            (cache / name).unlink()
        except (Refusal, OSError, ValueError) as exc:
            kept[name] = str(exc)
        else:
            removed.append(name)
    return {"removed": removed, "kept": kept}


def _recorded_size(cache: Path) -> tuple[JsonObject, int]:
    """The cache's sidecar and the plane size it names, or a refusal saying why not."""
    try:
        loaded: JsonValue = json.loads((cache / CACHE_SIDECAR_NAME).read_text(encoding="utf-8"))
        recorded = require_object(loaded)
        size = as_int(recorded["size"])
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise Refusal(REFUSED, f"no readable {CACHE_SIDECAR_NAME} naming a size: still being "
                               f"written, or not a raster cache ({exc})") from exc  # fmt: skip
    return recorded, size


def _check_raw(cache: Path, target: Path | None, size: int, storage: object,
               names: list[str]) -> None:  # fmt: skip
    """Refuse a raw cache this command cannot convert, or one a render holds open."""
    if storage != STORAGE_RAW or not names:
        raise Refusal(
            REFUSED, f"storage {storage!r} with planes {names}: nothing this command converts"
        )
    for name in names:
        if (cache / name).stat().st_size != size * size * PLANE_DTYPES[name].itemsize:
            raise Refusal(REFUSED, f"{name} is not a {size} px square of {PLANE_DTYPES[name]}")
    if target is None and (held := held_open([cache / n for n in names])):
        raise Refusal(REFUSED, f"{held.name} is held open, by a render reading this cache")
    if target is not None and (raw := [n for n in PLANE_DTYPES if (target / n).is_file()]):
        raise Refusal(
            REFUSED, f"{target} holds raw planes {raw}: not a band store this command wrote"
        )


def compress(cache: Path, target: Path | None = None) -> AlreadyBanded | Converted:
    """Convert one cache in place, or into ``target`` leaving ``cache`` as it was."""
    if target is not None and (on := _clash(cache, target)):
        raise Refusal(REFUSED, f"{on}; --to takes a folder outside it")
    recorded, size = _recorded_size(cache)
    storage = recorded.get("storage", STORAGE_RAW)
    names = [name for name in PLANE_DTYPES if (cache / name).is_file()]
    if probed := [n for n in PLANE_DTYPES if (cache / (n + ".probe")).is_file()]:
        raise Refusal(REFUSED, f"{probed[0]}.probe is a plane an interrupted open-file check "
                               f"renamed; rename it back to {probed[0]}")  # fmt: skip
    if storage == STORAGE_BANDS:
        if target is not None:
            raise Refusal(REFUSED, "already in the band store")
        verify = partial(_matches_its_bands, cache, size)
        return {"already": True, **_drop_raw(cache, names, verify)}
    _check_raw(cache, target, size, storage, names)
    out = cache if target is None else target
    out.mkdir(parents=True, exist_ok=True)
    if target is not None:
        (target / CACHE_SIDECAR_NAME).unlink(missing_ok=True)
    clear_planes(out, [n for n in PLANE_DTYPES if target is not None or n not in names])
    started = time.time()
    written: list[Path] = []
    try:
        for name in names:
            written.append(plane_file(out, name, STORAGE_BANDS))
            _convert_plane(cache / name, written[-1], size, PLANE_DTYPES[name])
        band_bytes = sum(p.stat().st_size for p in written)
        write_sidecar(out / CACHE_SIDECAR_NAME, {**recorded, "storage": STORAGE_BANDS})
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    done: Converted = {
        "planes": names, "in_place": target is None, "target": out, "band_bytes": band_bytes,
        "raw_bytes": sum(size * size * PLANE_DTYPES[n].itemsize for n in names),
        "seconds": round(time.time() - started, 1),
    }  # fmt: skip
    if target is None:
        dropped = _drop_raw(cache, names)
        done["removed"], done["kept"] = dropped["removed"], dropped["kept"]
    return done


def _report(cache: Path, done: AlreadyBanded | Converted) -> str:
    if "already" in done:
        line = f"{cache}: already in the band store"
        if done["removed"]:
            line += (f"; raw {', '.join(done['removed'])} left by an interrupted run removed, "
                     "each matched to its bands first")  # fmt: skip
    else:
        sizes = (f"{done['raw_bytes'] / 1e6:,.1f} MB raw to {done['band_bytes'] / 1e6:,.1f} MB "
                 f"in {done['seconds']} s, every band read back")  # fmt: skip
        if not done["in_place"]:
            line = f"{cache}: written to {done['target']}, {sizes}; the source untouched"
        elif not done.get("kept"):
            line = f"{cache}: converted in place, {sizes}; the raw planes removed"
        else:
            line = f"{cache}: converted in place, {sizes}"
    for name, why in done.get("kept", {}).items():
        line += f"; raw {name} kept: {why}"
    return line


def _refusal(source: Path, to: Path, caches: Iterable[Path], targets: Iterable[Path]) -> str | None:
    pairs = [(source, to)] + [(cache, target) for target in targets for cache in caches]
    on = next(filter(None, (_clash(cache, target) for cache, target in pairs)), None)
    return on and (f"--to: {on}. Nothing converted: give --to a folder outside the source, or "
                   "leave it out to convert in place.")  # fmt: skip


def _targets(caches: list[Path], source: Path, to: Path | None) -> dict[Path, Path | None]:
    """Where each cache's band store goes: in place, or ``to`` for the source, else under it."""
    if to is None:
        return dict.fromkeys(caches)
    return {cache: to if cache == source else to / cache.name for cache in caches}


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    parser.add_argument("dir", type=Path, help="a raster cache, or a folder of them")
    parser.add_argument("--to", type=Path,
                        help="write the band store here, outside dir, and leave dir alone")  # fmt: skip
    args = parser.parse_args()
    source: Path = args.dir
    to: Path | None = args.to
    require_gen("zstandard")
    caches = caches_under(source)
    if not caches:
        print(f"no raster cache in {source}")
        return REFUSED
    targets = _targets(caches, source, to)
    named = [target for target in targets.values() if target is not None]
    if to is not None and (refusal := _refusal(source, to, caches, named)):
        print(refusal)
        return REFUSED
    status = 0
    for cache in caches:
        try:
            done = compress(cache, targets[cache])
        except (Refusal, OSError, ValueError) as exc:
            kept = "the source" if to is not None else "its raw planes and sidecar"
            print(f"{cache}: not converted, {kept} untouched. {exc}")
            status = REFUSED
            continue
        print(_report(cache, done))
        status = REFUSED if done.get("kept") else status
    return status
