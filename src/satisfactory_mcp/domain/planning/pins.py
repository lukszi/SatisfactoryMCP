"""Pins: numbered handles on plans, processes, machines, factories, fields, nodes and points.

docs/planner-p3_contract.md §4 and §7 are the specification; docs/planner_p3.md says what was built.
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from ... import config
from ...core import atomic, filelock, schema
from ..spatial import geo
from ..spatial import nodes as nodes_mod
from .planlog import PlanLog, PlanLogError

__all__ = [
    "KINDS",
    "LABEL_MAX",
    "LOCATED",
    "MAX_LIVE",
    "SCHEMA",
    "ObjectMissing",
    "PinError",
    "PinMissing",
    "PinStale",
    "canonical",
    "canonical_args",
    "canonical_ops",
    "create",
    "drop",
    "get",
    "live",
    "match",
    "parse",
    "path_for",
    "place",
    "read",
    "rename",
    "row",
    "terms",
]

SCHEMA = 1
MAX_LIVE = 500
LABEL_MAX = 80
KINDS = ("plan", "process", "machine", "factory", "field", "node", "point")
LOCATED = ("point", "node", "field", "machine", "factory", "plan")
FIELD_LINK_M = 200.0
MAP_SQUARE_M = (-3247.0, -3750.0, 4253.0, 3750.0)
GRAMMARS = {
    "nodes": ("node", "field"),
    "machines": ("machine", "factory"),
    "plan": ("plan",),
    "recipes": ("process",),
}
_STANDS_FOR = {
    "nodes": "resource nodes",
    "machines": "machines",
    "plan": "a plan",
    "recipes": "a recipe",
}
_PIN = re.compile(r"\s*pin:(\d+)\s*", re.IGNORECASE)
_RECIPE_FIELDS = ("required", "banned", "exclude_recipes")


class PinError(ValueError):
    """A pin request that cannot be honoured, worded for the player."""


class ObjectMissing(PinError):
    """The object a new pin would point at is not in this world."""


class PinMissing(PinError, KeyError):
    def __init__(self, n: int, deleted: bool = False, top: int = 0) -> None:
        if deleted:
            text = f"pin:{n} was deleted"
        elif top:
            text = f"pin:{n} does not exist (pins run to pin:{top})"
        else:
            text = f"pin:{n} does not exist (no pins yet)"
        super().__init__(text)
        self.n = n
        self.deleted = deleted

    def __str__(self) -> str:
        return self.args[0]


class PinStale(PinError):
    def __init__(self, pin: dict) -> None:
        super().__init__(f"pin:{pin['n']} changed since you read it")
        self.pin = pin


def path_for(world_id: str) -> Path:
    safe = "".join(c for c in world_id if c.isalnum() or c in "-_") or "world"
    return config.pins_dir() / f"{safe}.json"


def _empty() -> dict:
    return {"schema": SCHEMA, "version": 0, "next": 1, "pins": []}


def read(world_id: str) -> dict:
    path = path_for(world_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    schema.check(raw, SCHEMA, path)
    if not isinstance(raw, dict) or not isinstance(raw.get("pins"), list):
        return _empty()
    out = _empty()
    out["version"] = int(raw.get("version") or 0)
    out["pins"] = [p for p in raw["pins"] if isinstance(p, dict) and isinstance(p.get("n"), int)]
    top = max((p["n"] for p in out["pins"]), default=0)
    out["next"] = max(int(raw.get("next") or 1), top + 1)
    return out


def _write(world_id: str, change):
    path = path_for(world_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with filelock.held(path):
        data = read(world_id)
        result, dirty = change(data)
        if dirty:
            data["version"] += 1
            atomic.write_text(path, json.dumps(data, ensure_ascii=False))
    return result


def parse(text) -> int | None:
    if not isinstance(text, str):
        return None
    hit = _PIN.fullmatch(text)
    return int(hit.group(1)) if hit else None


def _label(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PinError(f"a pin label is text, not {value!r}")
    text = value.strip()
    if len(text) > LABEL_MAX:
        raise PinError(f"a pin label is at most {LABEL_MAX} characters, not {len(text)}")
    return text


def _short(instance: str) -> str:
    return str(instance).strip().rsplit(".", 1)[-1]


def _text_ref(ref: dict, name: str) -> str:
    value = ref.get(name)
    if not isinstance(value, str) or not value.strip():
        raise PinError(f"this pin needs ref.{name}")
    return value.strip()


def _number(ref: dict, name: str) -> float:
    value = ref.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise PinError(f"a point needs a finite ref.{name}, not {value!r}")
    return float(value)


class _World:
    """What resolving pins reads from one world state, each part read once."""

    def __init__(self, st) -> None:
        self.st = st
        self._plans: dict | None = None
        self._machines: dict | None = None
        self._nodes: dict | None = None

    @property
    def game(self):
        return getattr(self.st, "game", None)

    def plans(self) -> dict:
        if self._plans is None:
            try:
                heads = PlanLog(self.st.world_id).heads(include_forgotten=True)
            except (PlanLogError, OSError):
                heads = []
            self._plans = {s.key: s for s in heads}
        return self._plans

    def plan(self, key: str):
        return self.plans().get(key)

    def machines(self) -> dict:
        if self._machines is None:
            out = {}
            projection = getattr(self.st, "projection", None) or {}
            for group in ("machines", "extractors", "generators"):
                for record in projection.get(group, ()):
                    out[_short(record.get("instance", ""))] = record
            self._machines = out
        return self._machines

    def nodes(self) -> dict:
        if self._nodes is None:
            out = {}
            for node in nodes_mod.load_nodes().nodes:
                out[_short(node["instance"])] = node
            self._nodes = out
        return self._nodes

    def node(self, text: str) -> dict | None:
        return self.nodes().get(_short(text.removeprefix("node:")))

    def label(self, name: str):
        store = getattr(self.st, "labels", None)
        wanted = name.strip().casefold()
        for label in getattr(store, "labels", None) or ():
            if label.name.casefold() == wanted:
                return label
        return None

    def item(self, cls: str) -> str:
        try:
            return self.game.item_name(cls) if cls in self.game.items else cls
        except AttributeError:
            return cls

    def recipe(self, rid: str) -> tuple[str, str]:
        recipe = self.game.recipes.get(rid) if self.game is not None else None
        if recipe is None:
            return rid, ""
        building = self.game.machine(recipe)
        return recipe.name, building.name if building else ""

    def building(self, cls: str) -> str:
        try:
            return self.game.building_name(cls) or cls
        except AttributeError:
            return cls


def _metres(pos) -> tuple[float, float]:
    return round(float(pos[0]) / 100.0, 1), round(float(pos[1]) / 100.0, 1)


def _field(world: _World, node: dict) -> tuple[list[str], tuple[float, float]]:
    same = [n for n in world.nodes().values() if n["resource"] == node["resource"]]
    for group in geo.cluster(same, link_m=FIELD_LINK_M):
        if any(m["instance"] == node["instance"] for m in group.members):
            cx, cy, _ = group.centroid
            return sorted(_short(m["instance"]) for m in group.members), (cx, cy)
    return [_short(node["instance"])], (node["x"], node["y"])


def _in_plan(st, state, rid: str) -> bool:
    if rid in state.args.required:
        return True
    from . import summary

    try:
        solved = summary.solve_summary(st.game, st, state.kwargs())
    except Exception:
        return False
    return any(r.get("recipe_id") == rid for r in solved.get("rows") or ())


def _normalise(world: _World, kind: str, ref: dict) -> tuple[dict, tuple[float, float] | None]:
    if not isinstance(ref, dict):
        raise PinError(f"ref must be an object, not {ref!r}")
    if kind in ("plan", "process"):
        wanted = _text_ref(ref, "plan")
        state = world.plan(wanted.lower())
        if state is None:
            try:
                state = PlanLog(world.st.world_id).find(wanted)
            except PlanLogError:
                state = None
        if state is None or state.forgotten:
            raise ObjectMissing(f"no plan “{wanted}” in this world")
        if kind == "plan":
            return {"plan": state.key}, None
        rid = _text_ref(ref, "recipe")
        if world.game is None or rid not in world.game.recipes:
            raise ObjectMissing(f"no recipe “{rid}”")
        if not _in_plan(world.st, state, rid):
            name = world.game.recipes[rid].name
            raise ObjectMissing(f"“{name}” is not in plan “{state.name}”")
        return {"plan": state.key, "recipe": rid}, None
    if kind == "factory":
        name = _text_ref(ref, "factory")
        label = world.label(name)
        if label is None:
            raise ObjectMissing(f"no factory named “{name}” in this save")
        from ..spatial import origin as origin_mod

        centre = origin_mod.label_centre(world.st, label)
        return {"factory": label.name}, _metres(centre) if centre is not None else None
    if kind == "machine":
        inst = _short(_text_ref(ref, "machine"))
        record = world.machines().get(inst)
        if record is None or not record.get("pos"):
            raise ObjectMissing(f"no machine “{inst}” in this save")
        return {"machine": inst}, _metres(record["pos"])
    if kind in ("node", "field"):
        wanted = _text_ref(ref, "node")
        node = world.node(wanted)
        if node is None:
            raise ObjectMissing(f"no resource node “{_short(wanted)}”")
        short = _short(node["instance"])
        if kind == "node":
            return {"node": short}, _metres((node["x"], node["y"]))
        members, centre = _field(world, node)
        return {"node": short, "resource": node["resource"], "nodes": members}, _metres(centre)
    x_m, y_m = _number(ref, "x_m"), _number(ref, "y_m")
    x0, y0, x1, y1 = MAP_SQUARE_M
    if not (x0 <= x_m <= x1 and y0 <= y_m <= y1):
        raise ObjectMissing(f"{x_m:g},{y_m:g} is outside the map")
    return {"x_m": round(x_m, 1), "y_m": round(y_m, 1)}, (round(x_m, 1), round(y_m, 1))


def _identity(kind: str, ref: dict) -> tuple:
    if kind == "plan":
        return (kind, ref["plan"])
    if kind == "process":
        return (kind, ref["plan"], ref["recipe"])
    if kind == "factory":
        return (kind, ref["factory"].casefold())
    if kind in ("machine", "node"):
        return (kind, ref[kind])
    if kind == "field":
        return (kind, ref["resource"], tuple(sorted(ref["nodes"])))
    return (kind, round(ref["x_m"], 1), round(ref["y_m"], 1))


def create(st, kind: str, ref: dict, label: str = "") -> tuple[dict, bool]:
    if kind not in KINDS:
        raise PinError(f"a pin kind is one of {', '.join(KINDS)}, not {kind!r}")
    text = _label(label)
    world = _World(st)
    stored, pos = _normalise(world, kind, ref)
    wanted = _identity(kind, stored)

    def change(data: dict):
        live_pins = [p for p in data["pins"] if not p.get("deleted")]
        for pin in live_pins:
            try:
                if pin.get("kind") == kind and _identity(kind, pin.get("ref") or {}) == wanted:
                    return (pin, True), False
            except (KeyError, TypeError):
                continue
        if len(live_pins) >= MAX_LIVE:
            raise PinError(f"this world already has {MAX_LIVE} pins; delete one first")
        pin = {
            "n": data["next"],
            "kind": kind,
            "ref": stored,
            "x_m": pos[0] if pos else None,
            "y_m": pos[1] if pos else None,
            "label": text,
            "rev": 1,
            "created": time.time(),
            "deleted": False,
        }
        data["next"] += 1
        data["pins"].append(pin)
        return (pin, False), True

    pin, existing = _write(st.world_id, change)
    return row(st, pin, world), existing


def _edit(world_id: str, n: int, rev: int, apply) -> dict:
    def change(data: dict):
        pin = next((p for p in data["pins"] if p["n"] == n), None)
        if pin is None:
            raise PinMissing(n, top=data["next"] - 1)
        if pin.get("deleted"):
            raise PinMissing(n, deleted=True)
        if isinstance(rev, bool) or rev != pin.get("rev"):
            raise PinStale(dict(pin))
        apply(pin)
        pin["rev"] = int(pin.get("rev") or 1) + 1
        return dict(pin), True

    return _write(world_id, change)


def rename(world_id: str, n: int, rev: int, label: str) -> dict:
    text = _label(label)

    def apply(pin: dict) -> None:
        pin["label"] = text

    return _edit(world_id, n, rev, apply)


def drop(world_id: str, n: int, rev: int) -> dict:
    def apply(pin: dict) -> None:
        pin["deleted"] = True

    return _edit(world_id, n, rev, apply)


def _describe(world: _World, pin: dict) -> dict:
    kind, ref = pin["kind"], pin.get("ref") or {}
    x_m, y_m = pin.get("x_m"), pin.get("y_m")
    gone_why, selector, text, what = "", "", kind, kind
    if kind in ("plan", "process"):
        state = world.plan(ref.get("plan", ""))
        name = state.name if state is not None else ref.get("plan", "")
        if state is None or state.forgotten:
            gone_why = "plan forgotten"
        if kind == "plan":
            selector, text, what = name, f"plan “{name}”", f"plan “{name}”"
            origin = (state.siting or {}).get("origin_m") if state is not None else None
            x_m, y_m = (None, None)
            if origin and len(origin) >= 2 and not gone_why:
                x_m, y_m = round(float(origin[0]), 1), round(float(origin[1]), 1)
        else:
            rname, building = world.recipe(ref.get("recipe", ""))
            shown = f"{building} · {rname}" if building else rname
            selector = ref.get("recipe", "")
            text = f"process {shown} in “{name}”"
            what = text
    elif kind == "factory":
        name = ref.get("factory", "")
        label = world.label(name)
        selector, text, what = f"label:{name}", f"factory “{name}”", f"factory “{name}”"
        if label is None:
            gone_why = f"no factory named “{name}” in this save"
        else:
            from ..spatial import origin as origin_mod

            x_m = y_m = None
            centre = origin_mod.label_centre(world.st, label)
            if centre is not None:
                x_m, y_m = _metres(centre)
    elif kind == "machine":
        inst = ref.get("machine", "")
        record = world.machines().get(inst)
        selector = f"machine:{inst}"
        building = world.building(record["cls"]) if record and record.get("cls") else "machine"
        text, what = f"machine {building}", building
        if record is None:
            gone_why = "machine not in this save"
            text = f"machine {inst}"
    elif kind == "node":
        node = world.node(ref.get("node", ""))
        selector = f"node:{ref.get('node', '')}"
        if node is not None:
            what = f"{world.item(node['resource'])}, {node['purity']}"
        text = f"node {what}"
    elif kind == "field":
        members = list(ref.get("nodes") or ())
        selector = ",".join(f"node:{m}" for m in members)
        resource = world.item(ref.get("resource", ""))
        text = f"field {resource} · {len(members)} node{'s' if len(members) != 1 else ''}"
        what = f"{resource} field · {len(members)} node{'s' if len(members) != 1 else ''}"
    else:
        selector = f"{x_m:g},{y_m:g}" if x_m is not None else ""
        text = f"point x {x_m:,.0f}, y {y_m:,.0f} m" if x_m is not None else "point"
        what = "point"
    return {
        "selector": selector,
        "text": text,
        "what": what,
        "x_m": x_m,
        "y_m": y_m,
        "gone_why": gone_why,
    }


def row(st, pin: dict, world: _World | None = None) -> dict:
    world = world or _World(st)
    info = _describe(world, pin)
    return {
        "n": pin["n"],
        "id": f"pin:{pin['n']}",
        "kind": pin["kind"],
        "ref": dict(pin.get("ref") or {}),
        "label": str(pin.get("label") or ""),
        "text": info["text"],
        "selector": info["selector"],
        "x_m": info["x_m"],
        "y_m": info["y_m"],
        "rev": int(pin.get("rev") or 1),
        "created": float(pin.get("created") or 0.0),
        "gone": bool(info["gone_why"]),
        "gone_why": info["gone_why"],
    }


def live(st) -> list[dict]:
    world = _World(st)
    data = read(st.world_id)
    return [row(st, p, world) for p in data["pins"] if not p.get("deleted")]


def get(st, n: int) -> dict:
    data = read(st.world_id)
    pin = next((p for p in data["pins"] if p["n"] == n), None)
    if pin is None:
        raise PinMissing(n, top=data["next"] - 1)
    if pin.get("deleted"):
        raise PinMissing(n, deleted=True)
    return row(st, pin)


def _echo(n: int, info: dict, label: str) -> str:
    shown = f"“{label}” " if label else ""
    return f"pin:{n} = {info['selector']} ({shown}{info['what']})".replace(" ()", "")


def _usable(st, n: int) -> tuple[dict, dict]:
    if st is None or not getattr(st, "world_id", None):
        raise PinError(f"pin:{n} needs a readable save to resolve")
    data = read(st.world_id)
    pin = next((p for p in data["pins"] if p["n"] == n), None)
    if pin is None:
        raise PinMissing(n, top=data["next"] - 1)
    if pin.get("deleted"):
        raise PinMissing(n, deleted=True)
    info = _describe(_World(st), pin)
    if info["gone_why"]:
        raise PinError(f"pin:{n} is gone: {info['gone_why']}")
    return pin, info


def place(st, n: int) -> tuple[tuple[float, float], str]:
    pin, info = _usable(st, n)
    kind = pin["kind"]
    if kind not in LOCATED:
        raise PinError(f"pin:{n} is a {kind}: it has no place on the map")
    if info["x_m"] is None:
        if kind == "plan":
            raise PinError(f"pin:{n} is a plan with no site")
        raise PinError(f"pin:{n} is a {kind} with no machines left to centre on")
    return (info["x_m"] * 100.0, info["y_m"] * 100.0), _echo(n, info, pin.get("label", ""))


def terms(st, n: int, grammar: str) -> tuple[list[str], str]:
    pin, info = _usable(st, n)
    kind, ref = pin["kind"], pin.get("ref") or {}
    if kind not in GRAMMARS[grammar]:
        if kind == "point" and grammar == "nodes":
            raise PinError(f"pin:{n} is a point: write near:pin:{n}@<radius_m>")
        raise PinError(f"pin:{n} is a {kind}: it cannot stand for {_STANDS_FOR[grammar]}")
    if kind == "node":
        out = [f"node:{ref['node']}"]
    elif kind == "field":
        out = [f"node:{m}" for m in ref.get("nodes") or ()]
    elif kind == "machine":
        out = [f"machine:{ref['machine']}"]
    elif kind == "factory":
        out = [f"label:{ref['factory']}"]
    elif kind == "plan":
        out = [ref["plan"]]
    else:
        out = [ref["recipe"]]
    return out, _echo(n, info, pin.get("label", ""))


def _near(st, member: str) -> tuple[str, str] | None:
    head, _, body = member.partition(":")
    if head.strip().casefold() != "near" or "@" not in body:
        return None
    place_text, _, radius = body.rpartition("@")
    n = parse(place_text)
    if n is None:
        return None
    (x, y), echo = place(st, n)
    return f"near:{x / 100.0:g},{y / 100.0:g}@{radius.strip()}", echo


def canonical(st, field: str, members: list) -> tuple[list, list[str]]:
    out: list = []
    echoes: list[str] = []
    for member in members or ():
        n = parse(member)
        if field == "sources":
            if n is not None:
                found, echo = terms(st, n, "nodes")
                out += found
                echoes.append(echo)
                continue
            near = _near(st, member) if isinstance(member, str) else None
            if near is not None:
                out.append(near[0])
                echoes.append(near[1])
                continue
        elif field in _RECIPE_FIELDS and n is not None:
            found, echo = terms(st, n, "recipes")
            out += found
            echoes.append(echo)
            continue
        out.append(member)
    return out, echoes


def canonical_args(st, args: dict) -> tuple[dict, list[str]]:
    out = dict(args or {})
    echoes: list[str] = []
    for name in ("sources", *_RECIPE_FIELDS):
        value = out.get(name)
        if isinstance(value, list | tuple) and value:
            out[name], said = canonical(st, name, list(value))
            echoes += said
    return out, echoes


def canonical_ops(st, ops: list) -> tuple[list, list[str]]:
    out: list = []
    echoes: list[str] = []
    for op in ops or ():
        name = op.get("field") if isinstance(op, dict) else None
        if isinstance(op, dict) and op.get("op") == "add" and name in ("sources", *_RECIPE_FIELDS):
            members, said = canonical(st, name, [op.get("member")])
            out += [{**op, "member": m} for m in members]
            echoes += said
            continue
        out.append(op)
    return out, echoes


def match(rows: list[dict], kind: str, ref: str, plan: str | None = None) -> dict | None:
    prefix = "label:" if kind == "factory" else kind + ":"
    if kind in ("factory", "machine", "node") and ref.casefold().startswith(prefix):
        ref = ref[len(prefix) :]
    for pin in rows:
        if pin["kind"] != kind or pin.get("gone"):
            continue
        stored = pin.get("ref") or {}
        if kind == "process":
            if stored.get("recipe") == ref and (plan is None or stored.get("plan") == plan):
                return pin
        elif kind == "plan":
            if stored.get("plan") == ref:
                return pin
        elif kind == "factory":
            if str(stored.get("factory", "")).casefold() == str(ref).casefold():
                return pin
        elif kind in ("machine", "node") and stored.get(kind) == _short(str(ref)):
            return pin
    return None
