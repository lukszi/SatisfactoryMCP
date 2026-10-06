"""The map type registry: ``data/local/maps/manifest.json``, which only the server writes.

A type is a pyramid directory under ``data/local`` plus what the manifest says about it. A
``dir`` is read from the manifest and checked to resolve inside ``data/local`` before use, so
a request's id is a dictionary key and never a path. Folders that predate the registry are
adopted where they lie. docs/maps_contract.md §2 is the specification.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import NamedTuple

from ... import config
from ...core import atomic, filelock, schema
from . import axes as ax
from . import titles

__all__ = [
    "LEGACY",
    "MapsError",
    "MapsRefused",
    "MapsStale",
    "MapsUnknown",
    "adopt_existing",
    "delete",
    "directory",
    "finish",
    "local_dir",
    "manifest_path",
    "read",
    "register",
    "set_default",
    "unregistered",
    "update",
    "view",
]

SCHEMA = 1
MAPS_DIR_NAME = "maps"
MANIFEST_NAME = "manifest.json"
TRASH_DIR_NAME = "_trash"
CACHE_DIR_NAME = "_cache"
JOBS_DIR_NAME = "jobs"
PLAIN = "plain"
HISTORY_KEEP = 20

#: Ids a route segment could mistake for something else.
RESERVED = frozenset({"default", "jobs", "cache", "adopt", "estimate", PLAIN})

#: The ids the page used before the registry, kept so old ``#mode=`` links still open:
#: id -> (dir under data/local, sidecar, kind, layer).
LEGACY = {
    "map": (".", "map.json", "artwork", "map"),
    "terrain": ("renders/terrain", "meta.json", "render", "terrain"),
    "satellite": ("renders/satellite", "meta.json", "render", "satellite"),
}

SKIPPED_SUFFIXES = (".incoming", ".retired")
STATUSES = ("building", "ready", "failed")


class MapsError(ValueError):
    """A registry change that cannot be honoured, worded for the player."""


class MapsUnknown(MapsError):
    pass


class MapsRefused(MapsError):
    pass


class MapsStale(MapsError):
    def __init__(self, asked: int, current: int) -> None:
        super().__init__(f"the map list changed elsewhere since version {asked}")
        self.current = current


def local_dir() -> Path:
    return config.data_dir() / "local"


def maps_dir() -> Path:
    return local_dir() / MAPS_DIR_NAME


def manifest_path() -> Path:
    return maps_dir() / MANIFEST_NAME


def _inside(local: Path, rel: str) -> Path | None:
    target = local / rel
    try:
        if not target.resolve().is_relative_to(local.resolve()):
            return None
    except OSError:
        return None
    return target


def has_pyramid(directory: Path) -> bool:
    return (directory / "tiles" / "0" / "0_0.png").is_file()


def _pyramid_present(local: Path, entry: dict) -> bool:
    target = _inside(local, entry["dir"])
    if target is None:
        return False
    return has_pyramid(target) or (entry["dir"] == "." and (target / "map.png").is_file())


def _tile_stats(sidecar: dict | None) -> dict:
    """Bytes on disk and pyramid depth of both trees, as the sidecar records them."""
    one = ax.dict_at(sidecar, "_meta", "tiles")
    two = ax.dict_at(sidecar, "_meta", "tiles_2x")
    return {
        "bytes": (ax.int_or_none(one.get("bytes")) or 0) + (ax.int_or_none(two.get("bytes")) or 0),
        "max_z": ax.int_or_none(one.get("max_z")),
        "max_2x_z": ax.int_or_none(two.get("max_z")),
    }


def describe_pyramid(local: Path, rel: str, sidecar_name: str, kind: str) -> dict:
    """What a pyramid directory's sidecar says: axes, size on disk, depth, when written."""
    target = local / rel
    sidecar = ax.read_json(target / sidecar_name)
    try:
        created = (target / sidecar_name).stat().st_mtime
    except OSError:
        created = None
    return {"axes": ax.axes_from_sidecar(sidecar, kind), **_tile_stats(sidecar), "created": created}


def new_entry(ident: str, kind: str, layer: str, rel: str, sidecar: str, origin: str) -> dict:
    return {
        "label": None,
        "kind": kind,
        "layer": layer,
        "generator": ax.ARTWORK_GENERATOR if kind == "artwork" else ax.RENDERS_GENERATOR,
        "dir": rel,
        "sidecar": sidecar,
        "axes": {},
        "bytes": 0,
        "max_z": None,
        "max_2x_z": None,
        "created": None,
        "status": "ready",
        "origin": origin,
        "job": None,
        "replaces": None,
        "in_switcher": True,
    }


def _resolved(local: Path, rel: str) -> str:
    try:
        return str((local / rel).resolve()).casefold()
    except OSError:
        return str(local / rel).casefold()


class PyramidCandidate(NamedTuple):
    """A pyramid lying under ``data/local``, and the legacy id it answers to, if any."""

    legacy: str | None
    rel: str
    sidecar: str
    kind: str
    layer: str


def _candidates(local: Path) -> list[PyramidCandidate]:
    """Every pyramid lying under ``local``."""
    found: list[PyramidCandidate] = []
    if has_pyramid(local) or (local / "map.png").is_file():
        found.append(PyramidCandidate("map", ".", "map.json", "artwork", "map"))
    for ident in ("terrain", "satellite"):
        rel = LEGACY[ident][0]
        if has_pyramid(local / rel):
            found.append(PyramidCandidate(ident, rel, "meta.json", "render", ident))
    roots = sorted(p for p in local.glob("renders*") if p.is_dir() and p.name != "renders")
    maps = local / MAPS_DIR_NAME
    if maps.is_dir():
        roots += sorted(
            p for p in maps.iterdir() if p.is_dir() and not p.name.startswith(("_", "jobs"))
        )
    for root in roots:
        if root.name.endswith(SKIPPED_SUFFIXES):
            continue
        if has_pyramid(root):
            rel = root.relative_to(local).as_posix()
            found.append(PyramidCandidate(None, rel, "map.json", "artwork", "map"))
            continue
        for layer_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            if layer_dir.name.endswith(SKIPPED_SUFFIXES) or not has_pyramid(layer_dir):
                continue
            block = ax.dict_at(ax.read_json(layer_dir / "meta.json"), "_meta")
            if block.get("generator") != ax.RENDERS_GENERATOR:
                continue
            layer = str(block.get("layer") or layer_dir.name)
            rel = layer_dir.relative_to(local).as_posix()
            found.append(PyramidCandidate(None, rel, "meta.json", "render", layer))
    return found


def _adopt_into(data: dict, local: Path) -> list[str]:
    types = data["types"]
    seen = {_resolved(local, entry["dir"]) for entry in types.values()}
    added: list[str] = []
    for legacy, rel, sidecar, kind, layer in _candidates(local):
        where = _resolved(local, rel)
        if where in seen:
            continue
        seen.add(where)
        described = describe_pyramid(local, rel, sidecar, kind)
        if legacy is not None and legacy not in types:
            ident = legacy
        else:
            axes = described["axes"]
            ident = ax.derive_id(
                ax.style_label(axes),
                axes.get("renderer", {}).get("recipe"),
                ax.data_changelist(axes),
                set(types) | RESERVED,
            )
        entry = new_entry(ident, kind, layer, rel, sidecar, "adopted")
        entry.update(described)
        types[ident] = entry
        added.append(ident)
    if added and data.get("default") is None and "map" in types:
        data["default"] = "map"
    return added


def _fresh() -> dict:
    return {"schema": SCHEMA, "version": 0, "default": None, "types": {}, "history": []}


def _load_raw(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raw = {}
    schema.check(raw, SCHEMA, path)
    data = _fresh()
    if isinstance(raw, dict):
        data["version"] = int(raw.get("version") or 0)
        data["default"] = raw.get("default") if isinstance(raw.get("default"), str) else None
        types = raw.get("types") if isinstance(raw.get("types"), dict) else {}
        for ident, entry in types.items():
            if isinstance(entry, dict) and isinstance(entry.get("dir"), str):
                data["types"][ident] = {**new_entry(ident, "render", "", "", "", ""), **entry}
        history = raw.get("history")
        data["history"] = history if isinstance(history, list) else []
    return data


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps(data, ensure_ascii=False, indent=1))


_cache: dict[str, tuple[object, dict]] = {}


def read() -> dict:
    """The manifest, cached on its stamp. Absent, what ``ensure`` would adopt, unwritten."""
    path = manifest_path()
    try:
        stat = path.stat()
        stamp: object = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        try:
            stamp = ("absent", local_dir().stat().st_mtime_ns)
        except OSError:
            stamp = ("absent", None)
    held = _cache.get(str(path))
    if held is not None and held[0] == stamp:
        return held[1]
    data = _load_raw(path) if stamp[0] != "absent" else None
    if data is None:
        data = _fresh()
        _adopt_into(data, local_dir())
    _cache[str(path)] = (stamp, data)
    return data


def ensure() -> list[str]:
    """Write the manifest when it is absent and there is something to adopt; the server's start."""
    path = manifest_path()
    if path.exists():
        return []
    data = _fresh()
    added = _adopt_into(data, local_dir())
    if added:
        with filelock.held(path):
            if _load_raw(path) is None:
                data["version"] = 1
                _write(path, data)
        _cache.pop(str(path), None)
    return added


def _mutate(change, version: int | None = None) -> dict:
    """Apply ``change(data) -> bool`` under the lock; a moved ``version`` raises ``MapsStale``."""
    path = manifest_path()
    with filelock.held(path):
        data = _load_raw(path)
        if data is None:
            data = _fresh()
            _adopt_into(data, local_dir())
        if version is not None and version != data["version"]:
            raise MapsStale(version, data["version"])
        if change(data):
            data["version"] += 1
            _write(path, data)
    _cache.pop(str(path), None)
    return data


def lookup(ident: str) -> tuple[dict | None, Path | None]:
    """A type's manifest entry and its directory; a legacy id answers before it is registered."""
    local = local_dir()
    entry = read()["types"].get(ident)
    if entry is None:
        if ident not in LEGACY:
            return None, None
        rel, sidecar, kind, layer = LEGACY[ident]
        entry = new_entry(ident, kind, layer, rel, sidecar, "adopted")
    return entry, _inside(local, entry["dir"])


def directory(ident: str) -> Path | None:
    """Where a servable type's tiles live: ``None`` for an unknown id or one not ready."""
    entry, target = lookup(ident)
    if entry is None or entry.get("status") != "ready":
        return None
    return target


def known_ids() -> list[str]:
    return sorted(set(read()["types"]) | set(LEGACY))


_axes_cache: dict[str, tuple[tuple[int, int], dict]] = {}


def _live_axes(local: Path, entry: dict) -> dict:
    """The axes the sidecar says NOW, so a retargeted junction is described as what it shows."""
    target = _inside(local, entry["dir"])
    if target is None:
        return entry.get("axes") or {}
    path = target / entry["sidecar"]
    try:
        stat = path.stat()
    except OSError:
        return entry.get("axes") or {}
    stamp = (stat.st_mtime_ns, stat.st_size)
    held = _axes_cache.get(str(path))
    if held is None or held[0] != stamp:
        held = (stamp, ax.axes_from_sidecar(ax.read_json(path), entry["kind"]))
        _axes_cache[str(path)] = held
    return held[1]


def installed_changelist() -> int | None:
    from ...core.gameassets.provenance import InstallNotFound, changelist, installed_build

    try:
        _pin, raw = installed_build(config.game_root())
    except (FileNotFoundError, InstallNotFound, OSError, ValueError):
        return None
    return changelist(raw)


def view(current: dict | None = None) -> dict:
    """Every type with its computed status, freshness, name and title, in display order."""
    data = read()
    local = local_dir()
    current = current if current is not None else ax.current_state(local, installed_changelist())
    rows = []
    for ident, entry in data["types"].items():
        axes = _live_axes(local, entry)
        status = entry.get("status") if entry.get("status") in STATUSES else "ready"
        if status == "ready" and not _pyramid_present(local, entry):
            status = "missing"
        rows.append(
            {
                "id": ident,
                "entry": entry,
                "axes": axes,
                "status": status,
                "freshness": ax.freshness(axes, current),
            }
        )
    groups: dict[str, int] = {}
    for row in rows:
        short = ax.display_name(row["axes"])
        groups[short] = groups.get(short, 0) + 1
    named = titles.titles(rows, data["default"])
    for row in rows:
        row["name"] = ax.display_name(row["axes"], groups[ax.display_name(row["axes"])] > 1)
        row["title"] = named[row["id"]]
    rows.sort(key=lambda row: ax.sort_key(row["axes"], row["id"]))
    return {
        "version": data["version"],
        "default": data["default"],
        "types": rows,
        "history": data["history"],
        "current": current,
    }


def unregistered() -> list[str]:
    """Dirs holding a pyramid the manifest does not list: what ``adopt_existing`` would add."""
    probe = {**_fresh(), "types": {k: dict(v) for k, v in read()["types"].items()}}
    before = set(probe["types"])
    _adopt_into(probe, local_dir())
    return [probe["types"][k]["dir"] for k in probe["types"] if k not in before]


def adopt_existing() -> list[str]:
    added: list[str] = []

    def change(data: dict) -> bool:
        added.extend(_adopt_into(data, local_dir()))
        return bool(added)

    _mutate(change)
    return added


def _require_entry(data: dict, ident: str) -> dict:
    entry = data["types"].get(ident)
    if entry is None:
        raise MapsUnknown(f"no map type “{ident}”")
    return entry


def update(
    ident: str, label: str | None = None, in_switcher: bool | None = None, version=None
) -> dict:
    """Rename a type (``""`` clears the label) or show or hide it in the switcher."""
    if label is not None and len(label) > 80:
        raise MapsError("a label is at most 80 characters")

    def change(data: dict) -> bool:
        entry = _require_entry(data, ident)
        dirty = False
        if label is not None:
            new = label.strip() or None
            dirty |= entry.get("label") != new
            entry["label"] = new
        if in_switcher is not None:
            dirty |= entry.get("in_switcher") != in_switcher
            entry["in_switcher"] = bool(in_switcher)
        return dirty

    return _mutate(change, version)


def set_default(ident: str, version: int | None = None) -> dict:
    """The type a fresh page opens on, for every browser; ``plain`` is no imagery."""

    def change(data: dict) -> bool:
        if ident != PLAIN:
            entry = _require_entry(data, ident)
            if entry.get("status") != "ready":
                raise MapsRefused(f"{ident} is {entry.get('status')}, not ready to be the default")
        if data["default"] == ident:
            return False
        data["default"] = ident
        return True

    return _mutate(change, version)


def _trash_target(ident: str) -> Path:
    return maps_dir() / TRASH_DIR_NAME / f"{ident}-{int(time.time() * 1000)}"


def _move_to_trash(local: Path, entry: dict, ident: str) -> None:
    source = _inside(local, entry["dir"])
    if source is None or not source.exists():
        return
    target = _trash_target(ident)
    target.parent.mkdir(parents=True, exist_ok=True)
    if entry["dir"] != ".":
        source.rename(target)
        parent = source.parent
        rel = parent.relative_to(local).as_posix()
        linked = _resolved(local, rel) != str(local.resolve() / rel).casefold()
        if parent not in (local, maps_dir()) and not linked and not any(parent.iterdir()):
            parent.rmdir()
        return
    target.mkdir()
    moved: list[tuple[Path, Path]] = []
    try:
        for name in ("tiles", "tiles@2x", "map.png", "map.json"):
            if (source / name).exists():
                (source / name).rename(target / name)
                moved.append((source / name, target / name))
    except OSError:
        for back, there in reversed(moved):
            there.rename(back)
        raise


def delete(ident: str, version: int | None = None, busy: frozenset[str] = frozenset()) -> int:
    """Move a type's files to ``maps/_trash`` and forget it; returns the bytes it held.

    Refuses the default and any type a queued or running job names in ``busy``.
    """
    freed = 0

    def change(data: dict) -> bool:
        nonlocal freed
        entry = _require_entry(data, ident)
        if data["default"] == ident:
            raise MapsRefused("pick another default first")
        if ident in busy:
            raise MapsRefused(f"a generation job still uses {ident}; cancel it first")
        try:
            _move_to_trash(local_dir(), entry, ident)
        except OSError as exc:
            raise MapsRefused(
                f"{ident} is in use and was not deleted ({exc.strerror or exc}); "
                "close what holds it and try again"
            ) from None
        freed = int(entry.get("bytes") or 0)
        del data["types"][ident]
        return True

    _mutate(change, version)
    return freed


def discard(ident: str) -> None:
    """Forget a type a job did not finish, moving whatever it wrote to the trash."""

    def change(data: dict) -> bool:
        entry = data["types"].pop(ident, None)
        if entry is None:
            return False
        try:
            _move_to_trash(local_dir(), entry, ident)
        except OSError:
            pass
        if data["default"] == ident:
            data["default"] = None
        return True

    _mutate(change)


def purge_trash() -> None:
    """Delete what earlier deletes moved aside; whatever Windows still holds waits for next time."""
    trash = maps_dir() / TRASH_DIR_NAME
    if not trash.is_dir():
        return
    for child in trash.iterdir():
        shutil.rmtree(child, ignore_errors=True)


def register(entries: dict[str, dict]) -> dict:
    """Add types a job is about to write, as ``building``; ids must be new."""

    def change(data: dict) -> bool:
        for ident, entry in entries.items():
            if ident in data["types"]:
                raise MapsRefused(f"map type {ident} already exists")
            data["types"][ident] = {**new_entry(ident, "render", "", "", "", "generated"), **entry}
        return bool(entries)

    return _mutate(change)


def finish(ident: str, job: str) -> bool:
    """Mark a job's type ready from what its sidecar now says; False if its pyramid is absent."""
    ok = False

    def change(data: dict) -> bool:
        nonlocal ok
        entry = data["types"].get(ident)
        if entry is None:
            return False
        local = local_dir()
        ok = _pyramid_present(local, entry)
        entry.update(describe_pyramid(local, entry["dir"], entry["sidecar"], entry["kind"]))
        entry["status"] = "ready" if ok else "failed"
        entry["job"] = job
        if data["default"] is None and ok:
            data["default"] = ident
        return True

    _mutate(change)
    return ok


def record_history(row: dict) -> None:
    def change(data: dict) -> bool:
        data["history"] = (data["history"] + [row])[-HISTORY_KEEP:]
        return True

    _mutate(change)


def taken_ids() -> set[str]:
    return set(read()["types"]) | set(LEGACY) | RESERVED


def clear_cache() -> int:
    """Delete the kept render rasters; returns the bytes freed."""
    cache = maps_dir() / CACHE_DIR_NAME
    freed = cache_bytes()
    shutil.rmtree(cache, ignore_errors=True)
    return freed


def cache_bytes() -> int:
    cache = maps_dir() / CACHE_DIR_NAME
    if not cache.is_dir():
        return 0
    return sum(p.stat().st_size for p in cache.rglob("*") if p.is_file())
