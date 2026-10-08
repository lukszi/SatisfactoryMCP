"""The shape of the ``mapgen`` package, read off the AST. Standard library only.

No ``pytest`` import on purpose: ``tests/architecture/test_import_direction.py`` walks every
file under ``tools/`` and allows only the stdlib, the ``gen`` extra, ``core`` and ``mapgen``
there.
"""

from __future__ import annotations

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "tools"
MAPGEN_SRC = TOOLS / "mapgen" / "src"
PKG = MAPGEN_SRC / "mapgen"
VERSIONS_PY = REPO / "src" / "satisfactory_mcp" / "core" / "gameassets" / "versions.py"
AXES_PY = REPO / "src" / "satisfactory_mcp" / "domain" / "maps" / "axes.py"
PRESETS_PY = REPO / "src" / "satisfactory_mcp" / "domain" / "maps" / "presets.py"

#: Who may import whom inside ``mapgen``. A unit is a subpackage or a top-level module, and
#: each command module is a unit of its own (``commands.renders``).
#: gamedata <- terrain <- lighting <- palette <- render <- commands <- cli, ``sprites`` (the
#: crown sprites, built from gamedata) under terrain, whose crown stamps draw them, with
#: ``tiles`` (cutting and describing a finished sheet) under render and ``common``,
#: ``bandstore``, ``cache`` and ``colour`` as leaves under all of them, ``pools`` (free
#: memory, a worker's BLAS threads) under the units that start pools, and ``jit`` (the kernel
#: switch) under the units with kernels and ``render``, whose light flags set it. ``cli``
#: reaches its commands through ``importlib`` by name, so it statically imports nothing here.
ALLOWED: dict[str, frozenset[str]] = {
    "common": frozenset(),
    "bandstore": frozenset(),
    "pools": frozenset(),
    "colour": frozenset(),
    "jit": frozenset(),
    "cache": frozenset({"common", "bandstore"}),
    "gamedata": frozenset({"common", "colour", "gamedata"}),
    "sprites": frozenset({"common", "cache", "jit", "gamedata", "sprites"}),
    "terrain": frozenset({"common", "cache", "jit", "gamedata", "sprites", "terrain"}),
    "lighting": frozenset({"common", "colour", "pools", "jit", "gamedata", "terrain", "lighting"}),
    "palette": frozenset(
        {
            "common",
            "colour",
            "pools",
            "cache",
            "jit",
            "gamedata",
            "sprites",
            "terrain",
            "lighting",
            "palette",
        }
    ),
    "tiles": frozenset({"common", "pools", "gamedata", "lighting", "tiles"}),
    "render": frozenset(
        {
            "common",
            "colour",
            "pools",
            "jit",
            "cache",
            "gamedata",
            "sprites",
            "terrain",
            "lighting",
            "palette",
            "tiles",
            "render",
        }
    ),
    "enhance": frozenset({"common", "gamedata", "tiles", "enhance"}),
    "commands.renders": frozenset(
        {
            "common",
            "pools",
            "cache",
            "gamedata",
            "terrain",
            "lighting",
            "palette",
            "tiles",
            "render",
        }
    ),
    "commands.heightmap": frozenset(
        {"common", "gamedata", "terrain", "commands.caves", "commands.rocks"}
    ),
    "commands.caves": frozenset({"common", "gamedata"}),
    "commands.rocks": frozenset({"common", "gamedata"}),
    "commands.paint": frozenset({"common", "gamedata"}),
    "commands.crown_sprites": frozenset({"common", "jit", "gamedata", "sprites", "render"}),
    "commands.calibrate": frozenset({"common", "gamedata", "palette"}),
    "commands.artwork": frozenset({"common", "gamedata", "tiles", "enhance"}),
    "commands.check_fill": frozenset({"common", "cache", "gamedata", "terrain"}),
    "commands.compress_cache": frozenset({"common", "bandstore", "cache"}),
    "cli": frozenset(),
    "__main__": frozenset({"cli"}),
}

#: The cap on any module in the package, in physical lines.
MODULE_MAX_LINES = 600

#: Modules with their own ceiling at their measured size: over the cap, or a command held
#: thin under it so the stages stay in their modules. Shrink-only: a ceiling may be lowered,
#: never raised, and one more than ``CEILING_SLACK`` above the file is stale.
MODULE_CEILINGS: dict[str, int] = {
    "commands/renders.py": 336,
    "commands/heightmap.py": 306,
    "commands/artwork.py": 296,
}
CEILING_SLACK = 25

#: The cap on one function or method, and the ones over it. Shrink-only, same slack.
FUNCTION_MAX_LINES = 150
FUNCTION_CEILINGS: dict[str, int] = {}

#: The entry scripts that became shims, and the ``mapgen`` command each one runs.
SHIMS: dict[str, str] = {
    "gen_map_renders.py": "renders",
    "gen_world_heightmap.py": "heightmap",
    "gen_map_image.py": "artwork",
    "gen_paint_layers.py": "paint",
    "check_map_fill.py": "check-fill",
}
SHIM_MAX_LINES = 40

#: The style row with no palette: the artwork is cut from the game's own sheet.
ARTWORK_LAYER = "map"

#: The in-game map square's x extents, in metres and in centimetres. The y extents
#: (+-3750 m) are too common a number to search for.
FRAME_LITERALS = frozenset({3247.0, 4253.0, 324700.0, 425300.0})
FRAME_HOME = "src/satisfactory_mcp/domain/spatial/geo.py"

#: Copies of the frame allowed besides ``FRAME_HOME``, a ratchet: none are left, every
#: other file reads ``geo.MAP_SQUARE_M``, and a stale entry fails.
FRAME_COPIES: frozenset[str] = frozenset()

#: The only modules that may locate files relative to themselves.
SELF_LOCATING = frozenset({"mapgen.common", "mapgen.palette.styles"})


# ------------------------------------------------------------------------ helpers


def _sources(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _module_name(path: Path) -> str:
    parts = path.relative_to(MAPGEN_SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _rel(path: Path) -> str:
    return path.relative_to(PKG).as_posix()


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_mapgen_module(dotted: str) -> bool:
    base = MAPGEN_SRC.joinpath(*dotted.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _targets(node: ast.Import | ast.ImportFrom) -> list[str]:
    """Absolute targets only; relative imports are refused by their own test."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level or not node.module:
        return []
    found = [node.module]
    found.extend(
        f"{node.module}.{a.name}"
        for a in node.names
        if node.module.startswith("mapgen") and _is_mapgen_module(f"{node.module}.{a.name}")
    )
    return found


def _is_main_guard(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and any(
            isinstance(c, ast.Constant) and c.value == "__main__" for c in node.test.comparators
        )
    )


def _imports(node: ast.AST, deferred: bool = False):
    """(import node, deferred) for every import; deferred = inside a def or a main guard."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            yield child, deferred
        inner = deferred or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        yield from _imports(child, inner or _is_main_guard(child))


def _unit(module: str) -> str | None:
    """``mapgen.terrain.fill`` -> ``terrain``; a command is its own unit, ``commands.paint``."""
    parts = module.split(".")
    if parts[0] != "mapgen" or len(parts) < 2:
        return None
    return ".".join(parts[1:3]) if parts[1] == "commands" and len(parts) > 2 else parts[1]


def _mapgen_edges() -> set[tuple[str, str]]:
    edges = set()
    for path in _sources(PKG):
        for node, _ in _imports(_tree(path)):
            edges.update((_module_name(path), t) for t in _targets(node))
    return edges


def _literal(path: Path, name: str):
    for node in _tree(path).body:
        targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path.name} no longer assigns {name} as a literal")


def _docstring_ids(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def _code_strings(path: Path) -> list[str]:
    tree = _tree(path)
    prose = _docstring_ids(tree)
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in prose
    ]


def _command_modules() -> dict[str, str]:
    """``cli.COMMANDS`` read by AST: command -> dotted module."""
    table = _literal(PKG / "cli.py", "COMMANDS")
    return {command: module for command, (module, _prefix) in table.items()}


def _module_path(dotted: str) -> Path:
    return MAPGEN_SRC.joinpath(*dotted.split(".")).with_suffix(".py")


def _first_doc_line(path: Path) -> str | None:
    doc = ast.get_docstring(_tree(path), clean=False)
    return doc.splitlines()[0] if doc else None


# -------------------------------------------------------------------------- tests


def test_mapgen_imports_point_down():
    """gamedata <- terrain <- lighting <- palette <- render <- commands <- cli."""
    bad = []
    for importer, target in sorted(_mapgen_edges()):
        source, dest = _unit(importer), _unit(target)
        if dest is None or source == dest:
            continue
        if source not in ALLOWED:
            bad.append(f"  {importer}: unit {source!r} is not in ALLOWED")
        elif dest not in ALLOWED[source]:
            bad.append(f"  {importer} -> {target}")
    assert not bad, (
        "an import inside mapgen points up or sideways -- move the shared name down to the "
        "lowest unit that needs it, or add the unit to ALLOWED with a reason:\n" + "\n".join(bad)
    )


def test_mapgen_has_no_import_cycle():
    """Units may import themselves, so the per-unit table cannot see a module cycle."""
    graph: dict[str, set[str]] = {}
    for importer, target in _mapgen_edges():
        if _unit(target) and importer != target and _is_mapgen_module(target):
            graph.setdefault(importer, set()).add(target)
    done: set[str] = set()

    def visit(node: str, stack: list[str]) -> list[str] | None:
        if node in stack:
            return [*stack[stack.index(node) :], node]
        if node in done:
            return None
        for nxt in sorted(graph.get(node, ())):
            cycle = visit(nxt, [*stack, node])
            if cycle:
                return cycle
        done.add(node)
        return None

    for start in sorted(graph):
        cycle = visit(start, [])
        assert not cycle, "import cycle in mapgen: " + " -> ".join(cycle)


def test_mapgen_imports_are_absolute():
    """``from mapgen.x import y`` only: the tools-wide walker cannot resolve relative ones."""
    relative = [
        f"  {_rel(path)}:{node.lineno}"
        for path in _sources(PKG)
        for node, _ in _imports(_tree(path))
        if isinstance(node, ast.ImportFrom) and node.level
    ]
    assert not relative, "relative import in mapgen, spell it absolute:\n" + "\n".join(relative)


def test_mapgen_never_imports_tools():
    """The package is installable on its own; ``tools.*`` is the old flat layout."""
    reaching = sorted(
        f"  {importer} -> {target}"
        for importer, target in _mapgen_edges()
        if target == "tools" or target.startswith("tools.")
    )
    assert not reaching, "mapgen imports tools/ -- import from mapgen instead:\n" + "\n".join(
        reaching
    )


def test_nothing_imports_an_entry_script():
    """No tool or mapgen module imports a ``gen_*`` script, and no test imports a shim."""
    code = [*TOOLS.glob("*.py"), *_sources(PKG)]
    tests = [*_sources(REPO / "tests"), *_sources(TOOLS / "mapgen" / "tests")]
    found = []
    for path in code + tests:
        for node, _ in _imports(_tree(path)):
            names = _targets(node)
            if isinstance(node, ast.ImportFrom) and node.module == "tools":
                names += [f"tools.{a.name}" for a in node.names]
            for name in names:
                stem = name.rsplit(".", 1)[-1]
                if f"{stem}.py" in SHIMS or (path in code and stem.startswith("gen_")):
                    found.append(f"  {path.relative_to(REPO).as_posix()}:{node.lineno} -> {name}")
    assert not found, (
        "these import an entry script -- import the name from its mapgen home (MOVEMAP) "
        "instead:\n" + "\n".join(sorted(found))
    )


def test_mapgen_modules_stay_under_the_line_cap():
    over, stale = [], []
    for path in _sources(PKG):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        ceiling = MODULE_CEILINGS.get(_rel(path), MODULE_MAX_LINES)
        if lines > ceiling:
            over.append(f"  {_rel(path)}: {lines} > {ceiling}")
        elif _rel(path) in MODULE_CEILINGS and ceiling - lines > CEILING_SLACK:
            stale.append(f"  {_rel(path)}: {lines}, ceiling {ceiling} -- lower it")
    missing = [
        f"  {name}: no such module" for name in MODULE_CEILINGS if not (PKG / name).is_file()
    ]
    assert not over, f"over the {MODULE_MAX_LINES}-line cap -- split by concern:\n" + "\n".join(
        over
    )
    assert not stale + missing, "stale MODULE_CEILINGS entries:\n" + "\n".join(stale + missing)


def _literal_table_ends(tree: ast.Module) -> set[int]:
    """The last line of every module-level assignment of a literal: a table."""
    ends = set()
    for node in tree.body:
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if value is None:
            continue
        try:
            ast.literal_eval(value)
        except ValueError:
            continue
        ends.add(node.end_lineno)
    return ends


def test_formatting_is_skipped_only_on_a_literal_table():
    """``# fmt: skip`` and ``# fmt: off`` keep code packed past what ruff writes, so the line
    caps would measure unformatted text; only a module-level literal table keeps its layout."""
    found = []
    for path in _sources(PKG):
        source = path.read_text(encoding="utf-8")
        tables = _literal_table_ends(_tree(path))
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.COMMENT:
                continue
            text = token.string.lstrip("#").strip()
            if text in ("fmt: off", "fmt: on") or (
                text == "fmt: skip" and token.start[0] not in tables
            ):
                found.append(f"  {_rel(path)}:{token.start[0]}: {token.string}")
    assert not found, (
        "formatting skipped outside a literal table -- let ruff format it, and split the "
        "module if it then passes its cap:\n" + "\n".join(found)
    )


def test_no_mapgen_function_grows_past_its_cap():
    over, seen = [], {}
    for path in _sources(PKG):
        for node in ast.walk(_tree(path)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                key = f"{_rel(path)}::{node.name}"
                lines = node.end_lineno - node.lineno + 1
                ceiling = FUNCTION_CEILINGS.get(key, FUNCTION_MAX_LINES)
                seen[key] = max(seen.get(key, 0), lines)
                if lines > ceiling:
                    over.append(f"  {key}: {lines} > {ceiling}")
    stale = [
        f"  {key}: {seen.get(key, 'gone')}, ceiling {ceiling}"
        for key, ceiling in FUNCTION_CEILINGS.items()
        if key not in seen or ceiling - seen[key] > CEILING_SLACK
    ]
    assert not over, "a function grew -- split it rather than raise its ceiling:\n" + "\n".join(
        over
    )
    assert not stale, "stale FUNCTION_CEILINGS entries, lower or delete:\n" + "\n".join(stale)


def test_the_shims_are_thin():
    """Docstring, stdlib path setup, and ``mapgen.cli.main`` behind the main guard."""
    problems = []
    for name, command in SHIMS.items():
        path = TOOLS / name
        tree = _tree(path)
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > SHIM_MAX_LINES:
            problems.append(f"  {name}: {lines} lines > {SHIM_MAX_LINES}")
        defs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
        if defs:
            problems.append(f"  {name}: defines {len(defs)} functions/classes, e.g. {defs[0].name}")
        for node, deferred in _imports(tree):
            for target in _targets(node):
                root = target.split(".", 1)[0]
                if root in sys.stdlib_module_names or root == "__future__":
                    continue
                if not (deferred and target == "mapgen.cli"):
                    problems.append(f"  {name}:{node.lineno} imports {target}")
        guard = [n for n in tree.body if _is_main_guard(n)]
        if not guard or command not in _code_strings_of(guard[0]):
            problems.append(f"  {name}: no main guard running `mapgen {command}`")
    assert not problems, "an entry script is more than a shim:\n" + "\n".join(problems)


def _code_strings_of(node: ast.AST) -> set[str]:
    return {
        n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def test_each_shim_and_its_command_share_a_first_docstring_line():
    """``main`` builds ``--help`` from ``__doc__.splitlines()[0]``; the shim keeps that line."""
    commands = _command_modules()
    wrong = []
    for name, command in SHIMS.items():
        if command not in commands:
            wrong.append(f"  {name}: cli.COMMANDS has no {command!r}")
            continue
        target = _module_path(commands[command])
        shim_line = _first_doc_line(TOOLS / name)
        module_line = _first_doc_line(target) if target.is_file() else None
        if not shim_line or shim_line != module_line:
            wrong.append(f"  {name}: {shim_line!r}\n  {_rel(target)}: {module_line!r}")
    assert not wrong, "shim and command module disagree:\n" + "\n".join(wrong)


def test_a_shim_loaded_as_a_worker_imports_no_numpy():
    """A spawned worker re-runs its parent's ``__main__`` as ``__mp_main__``: 1516 MB vs 12 MB."""
    heavy = []
    for name in SHIMS:
        code = (
            "import runpy, sys\n"
            f"runpy.run_path({str(TOOLS / name)!r}, run_name='__mp_main__')\n"
            "print(sorted(m for m in ('numpy', 'scipy', 'PIL') if m in sys.modules))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if out.returncode or out.stdout.strip() != "[]":
            heavy.append(f"  {name}: rc={out.returncode} {out.stdout.strip()} {out.stderr[-300:]}")
    assert not heavy, "a shim imported as __mp_main__ loads heavy modules:\n" + "\n".join(heavy)


def test_the_cli_entry_imports_only_the_stdlib_at_module_scope():
    """``cli``, ``__main__`` and every package ``__init__`` stay light; the command loads later."""
    light = [PKG / "cli.py", PKG / "__main__.py", *PKG.rglob("__init__.py")]
    found = []
    for path in light:
        for node, deferred in _imports(_tree(path)):
            for target in _targets(node):
                root = target.split(".", 1)[0]
                in_init = path.name == "__init__.py" and root != "__future__"
                heavy = not deferred and root not in sys.stdlib_module_names | {"__future__"}
                if in_init or heavy:
                    found.append(f"  {_rel(path)}:{node.lineno} imports {target}")
    assert not found, (
        "the entry path imports something heavy at module scope (every package __init__ "
        "is on it) -- import inside the function that needs it:\n" + "\n".join(found)
    )


def test_the_style_tables_agree_with_versions_styles():
    """``versions.STYLES`` is the one style table; every copy of it must match it. The axes
    also name the retired styles, whose maps are still listed."""
    styles = _literal(VERSIONS_PY, "STYLES")
    retired = _literal(VERSIONS_PY, "RETIRED_STYLES")
    rendered = {sid: row for sid, row in styles.items() if row["layer"] != ARTWORK_LAYER}
    layer_styles = {row["layer"]: sid for sid, row in rendered.items()}
    layers = [row["layer"] for row in rendered.values()]

    assert _literal(PKG / "palette" / "styles.py", "LAYER_STYLES") == layer_styles
    assert list(_literal(PKG / "commands" / "renders.py", "LAYERS")) == layers
    assert list(_literal(PRESETS_PY, "RENDER_LAYERS")) == layers
    named = {row["layer"]: sid for sid, row in {**styles, **retired}.items()}
    assert _literal(AXES_PY, "LAYER_STYLE") == named

    palettes = {p.stem for p in (PKG / "palette" / "palettes").glob("*.json")}
    assert palettes == set(rendered), f"palette files {sorted(palettes)} vs {sorted(rendered)}"
    old = sorted(p.name for p in (TOOLS / "palettes").glob("*.json"))
    assert not old, f"tools/palettes still holds {old} -- the palettes live in mapgen now"


def test_style_ids_are_spelled_once_in_mapgen():
    """Only ``palette/styles.py`` names a render style id; the rest reads ``LAYER_STYLES``.

    The artwork's id is ``"artwork"``, which is also a command name, so it is left out.
    """
    styles = _literal(VERSIONS_PY, "STYLES")
    ids = {sid for sid, row in styles.items() if row["layer"] != ARTWORK_LAYER}
    home = PKG / "palette" / "styles.py"
    spelled = sorted(
        f"  {_rel(path)}: {value!r}"
        for path in _sources(PKG)
        if path != home
        for value in _code_strings(path)
        if value in ids
    )
    assert not spelled, "a style id is spelled outside palette/styles.py:\n" + "\n".join(spelled)


def test_the_map_frame_is_defined_once():
    """The square lives in ``geo.MAP_SQUARE_M``; ``FRAME_COPIES`` is a shrink-only ratchet."""
    holders = set()
    for root in (REPO / "src", TOOLS):
        for path in _sources(root):
            if "node_modules" in path.parts or path.parent.name == "tests":
                continue
            for node in ast.walk(_tree(path)):
                if (
                    isinstance(node, ast.Constant)
                    and type(node.value) in (int, float)
                    and abs(node.value) in FRAME_LITERALS
                ):
                    holders.add(path.relative_to(REPO).as_posix())
                    break
    new = sorted(holders - FRAME_COPIES - {FRAME_HOME})
    gone = sorted(FRAME_COPIES - holders)
    assert FRAME_HOME in holders, f"{FRAME_HOME} no longer defines the frame"
    assert not new, "a new copy of the map frame -- read geo.MAP_SQUARE_M:\n  " + "\n  ".join(new)
    assert not gone, "stale FRAME_COPIES entries, delete them:\n  " + "\n  ".join(gone)


def test_only_common_and_styles_locate_themselves():
    """No ``sys.path`` edits and no ``Path(__file__)`` roots in the package (MOVEMAP rule 3)."""
    found = []
    for path in _sources(PKG):
        name = _module_name(path)
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Name) and node.id == "__file__" and name not in SELF_LOCATING:
                found.append(f"  {name}:{node.lineno} uses __file__ -- use mapgen.common.ROOT")
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "path"
                and isinstance(node.value, ast.Name)
                and node.value.id == "sys"
            ):
                found.append(f"  {name}:{node.lineno} touches sys.path")
    assert not found, "\n".join(found)


def test_every_script_mapgen_names_exists():
    """``"tools/gen_*.py"`` strings are provenance the registry matches; each must be a file.

    Only the generator paths: older prose in a sidecar may name a module that has moved.
    """
    missing = sorted(
        f"  {_rel(path)}: {script}"
        for path in _sources(PKG)
        for value in _code_strings(path)
        for script in value.split()
        if script.startswith("tools/gen_")
        and script.rstrip(",.;").endswith(".py")
        and not (REPO / script.rstrip(",.;")).is_file()
    )
    assert not missing, "mapgen names a script that does not exist:\n" + "\n".join(missing)
