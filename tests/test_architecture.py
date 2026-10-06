"""The layering rules, enforced by reading the source rather than trusting review.

The refactor's whole point is a one-way import graph: core knows nothing, domain
knows core, presenters know domain, interfaces know everything. Nothing in that
list is checkable at runtime -- a lazy ``import`` three frames deep inside a
method body loads fine and violates the architecture silently. So this test
parses every module with ``ast`` and looks at *every* import node at *any*
depth, which is why ``domain/world/state.py``'s deliberate lazy imports of the
factories and planning packages are visible here instead of hiding.

It runs with no game install and no save file: stdlib only.

``WHITELIST`` is a ratchet. It held the violations that existed at the start,
each one dying in a named phase, and ``test_whitelist_is_not_stale`` fails once a
fix lands and the entry is left behind. Entries only ever got deleted, and the
last one is gone: the refactor finished with an empty whitelist, which
``test_whitelist_is_empty`` now states outright.

The second ratchet is ``FORBIDDEN_PATHS``, and it has inverted. For one commit the
eight pre-refactor import paths survived as alias shims so the test suite could be
moved over gradually; they are gone, every caller spells the layered home, and the
rule is now that those names must never come back. A folder whose only job is to
forward an import is a second place to look for the same code, and it re-attracts
logic the moment someone is in a hurry -- so ``test_the_old_paths_stay_deleted``
asserts absence rather than shape.

The fourth is the frontend, and it is a different shape of rule because it is a different
language. ``interfaces/web/frontend`` is an npm project and ``interfaces/web/static`` is
what ``npm run build`` writes; the built files are NOT committed -- ``static/`` is
gitignored, because a dependency's compiled code (Leaflet, minified into ``app.js``) does
not belong in the repository's tree. Three things have to stay true for that arrangement
to be honest: nothing under ``static/`` may ever be tracked again (the gitignore rule and
``git ls-files`` are both asserted), the served files must be recognisable as OUTPUT when
they exist (they carry a banner the build injects, and a hand edit to them is lost on the
next build, silently, which is the failure this catches), and no Python may reach into the
sources next door -- the seam is the built directory. All of it is read off the filesystem
and the index here, with no Node executed: pytest must keep running on a machine that has
none, including a fresh clone where ``static/`` does not exist at all.

The fifth is the ``gen`` extra, and it is the ``_SDK_ROOTS`` rule again with different
names. ``ooz``, ``texture2ddecoder`` and Pillow are what ``core/gameassets`` needs to read
the installed game's container, they are an optional extra, and optional has to mean
optional *at import time*: a clone with none of them installed imports every module, runs
this suite and serves the map, and only ``tools/gen_*.py`` ever finds out they are missing.
That is checkable by AST only while nothing reaches for them dynamically, which is why the
second half of the rule -- no ``importlib``, no ``__import__``, no ``sys.path`` mutation
inside that package -- is not a separate preference but the thing that makes the first half
mean anything.

The sixth is ``tools/``, which until now no ratchet walked at all -- it is outside ``src``,
outside the wheel, and outside every loop in this file. That is precisely why it needed one:
the generators are the only code in the repository that reads the installed game, and the
cheapest way to make the server depend on a game install is to let an import drift the wrong
way through a file nothing checks. It is treated as a pseudo-layer, like the SDK: it may see
``core``, the ``gen`` extra, the standard library and itself, plus four things measured to be
there already and whitelisted by name below. And nothing under ``src`` may see IT, which falls
out of the layer table for free.

The seventh is the web adapter's own routers, and it exists because ``interfaces/web/api.py``
was 2,174 lines of every endpoint the surface has and is now thirteen modules under
``routers/``. Nothing about that arrangement holds itself up: a router importing another
router puts the file boundary back where the coupling is not, a module left out of
``ALL_ROUTERS`` is a 404 that nothing reports, a file called ``api.py`` re-created for the
handler that "does not fit anywhere" collects the next one too, and a router quietly past a
thousand lines is the original file under a new name. Four rules, all read off the AST and
the filesystem, none of them needing FastAPI installed.

The eighth is the page's fetch registry, and it is the only rule here whose failure mode is
a feature that quietly stops existing. ``load.ts`` used to import every drawing module on the
page and call each one by name; the modules now declare what they want fetched, and
``load.ts`` runs the list knowing none of the names. That inversion has a hole in it that no
compiler can see: a module nothing imports is a module Rollup leaves out of the bundle, and a
registration that never ran is a layer that is simply never fetched -- no error, no blank
space, just an absence. So the two directions are checked as a pair. Every module that calls
``registerFetch`` is named in ``main.ts``'s FEATURES block, and nothing in that block fails to
register; and the mechanism side -- ``load.ts``, ``registry.ts``, ``layers.ts`` and
``layercontrol.ts`` -- imports none of them, which is what makes the FEATURES block the only
thing holding them in. Those same four are held to a second, wider rule: none may import a
module that imports it. The narrow rule is about the bundle; this one is about rings, and
``layers.ts`` is why it exists -- every drawing module reaches it, so anything it reached back
would be evaluated before all of them, and ``regions.ts`` (which draws and fetches nothing)
is exactly the import the narrow rule would wave through.

The ninth is the API's own description of itself, and it is the last one the web refactor
installs. Every GET handler under ``routers/`` must pass a ``response_model``, or return a
``Response`` subclass and say so in its annotation, or be named in ``RESPONSE_MODEL_EXEMPT``
with the reason. Without it, ``/openapi.json`` types an endpoint's ``200`` as ``unknown``,
the generated ``api-schema.d.ts`` does too, and the page has no choice but to declare the
payload itself -- from observed bytes, which is a claim about one save rather than about the
API. That file existed; it reached 438 lines and got three nullability notes wrong before it
was deleted. The failure this catches is silent in the usual way: the new endpoint compiles,
serves and is correct, and nothing says anything until somebody needs its type. The
exemptions are held to ``WHITELIST``'s discipline, so one that stops being needed fails
rather than lingering.

The third ratchet is the parser. ``src/pioneersav`` is a standalone library that
happens to live in this repository, and the subprocess boundary in front of it is
load-bearing for reasons that have nothing to do with layering -- crash isolation,
memory return, and a projection small enough to commit as the suite's fixture. Two
tests keep that true by import graph rather than by intention: the parser may not
know the application exists, and inside the application exactly one module may name
the parser, because everything else reaches it through the subprocess.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
PKG = SRC / "satisfactory_mcp"

#: The generators. Outside ``src`` and outside the wheel -- ``pyproject.toml`` ships two
#: packages and this is not one of them -- because they read the reader's own game install.
TOOLS = REPO / "tools"

#: The parser package, and the one module in the application allowed to import it.
PARSER = "pioneersav"
PARSER_PKG = SRC / PARSER
PARSER_IMPORTER = "satisfactory_mcp.core.saveio.extract"

#: Longest-prefix-first layer map, applied to importer *and* target alike.
#:
#: Through the migration this also carried the pre-refactor paths, each classified
#: as the layer it was moving *to*, so the rules bit while things were still in
#: motion. Those entries are gone with the paths they described: an entry for a
#: name that no longer exists can never match an edge, so it says nothing and
#: quietly invites the next reader to believe the old layout is still around.
_LAYERS: tuple[tuple[str, str], ...] = (
    ("satisfactory_mcp.core", "core"),
    ("satisfactory_mcp.domain", "domain"),
    ("satisfactory_mcp.presenters", "presenters"),
    ("satisfactory_mcp.interfaces", "interfaces"),
    ("satisfactory_mcp.config", "core"),
    ("satisfactory_mcp.server", "interfaces"),
    # The generators, as a layer above everything: they may read the application, and no
    # part of the application may read them. Safe beside the prefixes above because the
    # match is exact-or-dotted -- ``satisfactory_mcp.interfaces.mcp.tools`` neither equals
    # ``tools`` nor starts with ``tools.``, and is covered by its own longer prefix anyway.
    ("tools", "tools"),
)

#: Third-party packages that belong to the outside world. They form a pseudo-layer
#: so the rule "only the interface layer may see the SDK" is checkable the same way
#: as every other rule.
_SDK_ROOTS = frozenset({"mcp", "pydantic", "fastapi", "uvicorn", "starlette"})

#: The ``gen`` extra: what the ``tools/gen_*.py`` generators need to read the installed
#: game's own container, pinned exactly in ``pyproject.toml`` because these decide the BYTES
#: an artifact is cut with. ``ooz`` (from pyooz) decompresses a container block,
#: ``texture2ddecoder`` unpacks a BC1 block and Pillow writes the PNG.
#:
#: The property below is that ``optional`` means optional AT IMPORT TIME -- the same posture
#: ``_SDK_ROOTS`` has, and checked the same way, because a machine with none of these
#: installed still has to import the whole package, run this suite and serve the map.
_GEN_EXTRA_ROOTS = frozenset({"ooz", "pyooz", "texture2ddecoder", "PIL"})

#: The one package allowed to name them at all, and only inside a function body.
GAMEASSETS = "satisfactory_mcp.core.gameassets"

#: What ``core/gameassets`` may import at module scope besides the standard library and
#: ``core`` itself. **One entry, and it is not an extra**: ``numpy`` is in
#: ``[project] dependencies``, so a clone that installs this project has it, and naming it
#: here does not weaken the rule next door -- the rule is that an *optional* dependency
#: must not become a dependency of the server by the back door, and numpy was never
#: optional. It is here because ``nanite.py`` and ``staticmesh.py`` hand back triangle
#: arrays, and an array reader that returns lists of tuples to avoid an import the project
#: already makes would be a worse file for no gain.
_GAMEASSETS_HARD_ROOTS = frozenset({"numpy"})

#: Who may import whom. A layer always may import itself.
#:
#: ``tools`` is the one entry that is not part of the shipped package, and the interesting
#: half of its row is what is NOT in any other row: no layer lists ``tools``, so the moment
#: anything under ``src`` imports a generator the edge walker calls it a violation. That is
#: the direction that matters -- a generator reading the application is the arrangement, and
#: the application reading a generator would make a game install a runtime dependency.
ALLOWED: dict[str, frozenset[str]] = {
    "core": frozenset({"core"}),
    "domain": frozenset({"domain", "core"}),
    "presenters": frozenset({"presenters", "domain", "core"}),
    "interfaces": frozenset({"interfaces", "presenters", "domain", "core", "sdk"}),
    "tools": frozenset({"tools", "core", "domain"}),
}

#: Empty, and it stays that way. It held five render leakages at the start of the
#: refactor -- the four planning modules the sore-point list named, plus
#: ``docs.search``, which reading the AST found -- and phase 1 moved every one of
#: them behind ``presenters.text``. The ratchet is kept rather than deleted: a new
#: violation now has to be argued for by adding a line here, which is the point.
WHITELIST: frozenset[tuple[str, str]] = frozenset()

#: The pre-refactor import paths, deleted, with where each one went.
#:
#: They lived one commit longer than the code did, as alias shims, so that ~28 test
#: files could be moved over without a 2,000-line diff landing in the same change as
#: the moves. That job is done. Re-creating any of them -- as a package, as a module,
#: or as a directory with anything at all in it -- means the tree has two names for
#: one thing again, and the second name is always the one that goes stale.
FORBIDDEN_PATHS: dict[str, str] = {
    "docs": "core.gamedata",
    "save": "core.saveio + domain.world.state",
    "graph": "domain.factories",
    "spatial": "domain.spatial",
    "planning": "domain.planning",
    "tools": "interfaces.mcp.tools",
    "app": "interfaces.mcp.app",
    "render": "presenters.text.primitives",
}

#: The homes the refactor moved things to, asserted to exist by literal name.
#:
#: Cheap, and it catches the one failure mode the edge walker cannot: a package
#: renamed or folded away leaves no violating edge behind, it just leaves the
#: architecture undescribed. ``core.text`` is a module rather than a package on
#: purpose -- it holds ``num`` and ``plural`` and nothing else, because those two
#: are the only formatting helpers the domain layer is allowed to reach.
LAYERED_HOMES: tuple[str, ...] = (
    "satisfactory_mcp.core.gamedata",
    "satisfactory_mcp.core.gameassets",
    "satisfactory_mcp.core.saveio",
    "satisfactory_mcp.core.text",
    "satisfactory_mcp.domain.world",
    "satisfactory_mcp.domain.progression",
    "satisfactory_mcp.domain.power",
    "satisfactory_mcp.domain.factories",
    "satisfactory_mcp.domain.spatial",
    "satisfactory_mcp.domain.collectibles",
    "satisfactory_mcp.domain.planning",
    "satisfactory_mcp.domain.advice",
    "satisfactory_mcp.presenters.text",
    "satisfactory_mcp.interfaces.mcp",
    "satisfactory_mcp.interfaces.web",
)

#: Everything the package root is allowed to contain: the four layers, the two
#: real modules that stay at the top by decision (``server.py`` because the
#: console script names it, ``config.py`` because it is read from every layer),
#: and the package marker. Seven names, and a new one means someone started a
#: fifth top-level home instead of picking a layer.
ROOT_ENTRIES: frozenset[str] = frozenset(
    {
        "__init__.py",
        "config.py",
        "server.py",
        "core",
        "domain",
        "presenters",
        "interfaces",
    }
)

#: The presenter layer, by literal name rather than by layer lookup.
_PRESENTER_ROOTS = ("satisfactory_mcp.presenters",)

#: The frontend's two halves: the npm project, and the directory its build writes -- which
#: is also the directory ``app.py`` mounts at ``/``.
WEB = PKG / "interfaces" / "web"
FRONTEND = WEB / "frontend"
BUILT = WEB / "static"

#: The string ``vite.config.ts`` injects into every file it emits. Asserting the marker
#: rather than the bytes is what keeps this test cheap and stable: the bundle is minified
#: output whose shape is the toolchain's business, and the only claim worth pinning is that
#: it came OUT of the toolchain rather than off someone's keyboard.
BUILD_MARKER = "GENERATED by Vite"

#: Every file the build is responsible for, and the whole contents of ``static/`` when a
#: build has run: a name here that stops being generated, or a file there that no build
#: produced, both mean the served directory has grown a hand-maintained corner again. The
#: licence is copied out of ``node_modules/leaflet/LICENSE`` by the ``leafletLicence``
#: plugin in ``vite.config.ts`` -- the npm package is the source of truth, not a vendored
#: copy in this tree.
BUILT_FILES: tuple[str, ...] = ("index.html", "app.js", "app.css", "vendor/LEAFLET-LICENSE")

#: The one file in ``static/`` the marker cannot be asserted on. It is Leaflet's licence,
#: copied through verbatim by the build, and stamping a banner into a licence text would be
#: modifying the notice it exists to reproduce.
COPIED_VERBATIM = "vendor/LEAFLET-LICENSE"

#: The gitignore line that keeps the build output out of the tree, exactly as the root
#: ``.gitignore`` spells it. ``static/`` holds a dependency's compiled code (Leaflet,
#: minified into ``app.js``), and the repository does not redistribute that: a clone builds
#: its own page. Asserted verbatim so that a reworded ignore file cannot silently stop
#: covering the directory while this test keeps passing.
STATIC_IGNORE_LINE = "src/satisfactory_mcp/interfaces/web/static/"


#: The page's TypeScript, and the files the registry rules are about.
FRONTEND_SRC = FRONTEND / "src"
FRONTEND_MAIN_TS = FRONTEND_SRC / "main.ts"

#: The MECHANISM side of the page: five modules that hold a list and run it, and know none of
#: the names in it.
#:
#: ``load.ts`` runs the two waves, ``registry.ts`` holds what is in them, ``layers.ts`` hands
#: out the named groups, ``layercontrol.ts`` is the widget listing them, and ``palette.ts``
#: records what colour each feature chose and checks the choices against each other. Every
#: feature on the page reaches at least one of these; none of the five may reach a feature.
#: That is what makes each of them a seam rather than a habit -- see the two rules below for
#: the two different things that sentence has to mean.
FRONTEND_MECHANISM = (
    FRONTEND_SRC / "load.ts",
    FRONTEND_SRC / "registry.ts",
    FRONTEND_SRC / "layers.ts",
    FRONTEND_SRC / "layercontrol.ts",
    FRONTEND_SRC / "palette.ts",
)
FRONTEND_REGISTRY_TS = FRONTEND_SRC / "registry.ts"
FRONTEND_PALETTE_TS = FRONTEND_SRC / "palette.ts"

#: A colour VALUE, in any of the four CSS hex forms, anywhere in ``palette.ts`` -- code or
#: comment, because the file's old header quoted the values it was arguing about and quoting
#: one is the same mistake as declaring one: it is a colour the audit cannot see.
#:
#: Deliberately loose about length. ``#abc``, ``#abcd``, ``#abcdef`` and ``#abcdef12`` are all
#: colours, and the two lengths CSS does not accept are worth catching anyway -- a five-digit
#: one is a six-digit one somebody mistyped, and it would reach ``lab()`` and come back out as
#: a colour nobody chose.
HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b")

#: A registration, which is a call at MODULE SCOPE and has to be: it runs when the module is
#: evaluated, which is the whole point of it. Anchoring on the margin is what tells the three
#: other appearances of the name apart from a real one -- the declaration in ``registry.ts``
#: (``export function …``), the prose in ``load.ts``, and the paragraph in ``main.ts``
#: explaining what the block of bare imports below it is for.
REGISTER_CALL = re.compile(r"^registerFetch[<(]", re.MULTILINE)

#: A bare side-effect import of a sibling module: ``import "./markers";`` and nothing else.
#: Stylesheets are excluded by the pattern itself -- ``"./style.css"`` carries a dot.
BARE_IMPORT = re.compile(r'^import "\./([A-Za-z0-9_-]+)";$', re.MULTILINE)

#: The tail of any other import of a sibling, which is matched rather than the whole
#: statement because several of them span lines.
FROM_IMPORT = re.compile(r'from "\./([A-Za-z0-9_-]+)"')

# ------------------------------------------------------------------- the routers

#: The JSON surface: one module per concern, and the tuple that mounts them.
#:
#: ``api.py`` was 2,174 lines of every endpoint in one file. It is gone, and the three rules
#: below are what stop it reassembling itself somewhere else -- because each of the three
#: failures they catch is silent. A router importing a router compiles and serves; a router
#: never appended to ``ALL_ROUTERS`` compiles and 404s; a file growing back past a thousand
#: lines compiles and reads fine right up to the point where nobody can find anything in it.
WEB_ROUTERS = WEB / "routers"
WEB_APP_PY = WEB / "app.py"

#: What a router module may import, beyond the standard library. Deliberately short.
#:
#: ``fastapi``/``starlette`` because it is one; ``config``/``core``/``domain`` because that
#: is the whole point of the layer -- parse a query, call a domain service, serialise; and
#: the web package's own two shared homes, ``serial`` (the metre/error vocabulary) and
#: ``terrain`` (the heightfield seam), which exist precisely so that routers need nothing
#: else from each other.
ROUTER_ALLOWED_ROOTS = frozenset({"fastapi", "starlette"})

#: The two packages a relative import TRAVERSES, allowed exactly and never as a prefix.
#:
#: ``from .. import terrain`` and ``from .... import config`` are two edges each -- the
#: package walked through, and the module bound -- and the first is unavoidable. Neither
#: name is a layer: ``_layer`` already says the top-level ``__init__`` is a package marker
#: and importing it claims nothing. Allowing them as PREFIXES instead would wave through
#: ``from .. import app`` in the same breath, which is the import that would make the mount
#: order a cycle; spelled exactly, that one still fails on its second edge.
ROUTER_ALLOWED_EXACT = frozenset({"satisfactory_mcp", "satisfactory_mcp.interfaces.web"})

ROUTER_ALLOWED_PREFIXES: tuple[str, ...] = (
    "satisfactory_mcp.config",
    "satisfactory_mcp.core",
    "satisfactory_mcp.domain",
    "satisfactory_mcp.interfaces.web.serial",
    "satisfactory_mcp.interfaces.web.terrain",
)

#: The one measured exception, and it is one module reaching one module.
#:
#: ``routers/bridge/events.py`` streams what ``watch.SaveWatcher`` publishes. It reads the
#: watcher off ``request.app.state`` and imports only the event names and record, which live
#: in ``watch_events``. Named as a pair rather than as a prefix: any OTHER router importing
#: the watcher's modules is the failure this is shaped to still catch.
ROUTER_EXTRA_EDGES: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "satisfactory_mcp.interfaces.web.routers.bridge.events",
            "satisfactory_mcp.interfaces.web.watch_events",
        )
    }
)

#: The hard cap, in lines, on any one router module.
#:
#: Not a style preference: it is the number that makes "one module per concern" checkable.
#: What it stops is the drift back: a second concern lands in a router, then a third, and
#: nothing says so until the file is api.py again under a different name. The largest router
#: measured 624 lines (plans/planlog.py) when the cap was set, so a breach is a real change.
ROUTER_MAX_LINES = 650

#: The classes FastAPI treats as "this handler answers for itself".
#:
#: A handler annotated with one of these is returning a ``Response`` rather than a body to be
#: serialised, so there is nothing for a ``response_model`` to describe and FastAPI skips
#: inference for it. Matched on the terminal NAME of the annotation, because these are spelled
#: both ways in this package -- ``StreamingResponse`` imported, and ``fastapi.responses.X``
#: reached through the module -- and the AST sees an ``ast.Name`` in one case and an
#: ``ast.Attribute`` in the other.
RESPONSE_CLASSES = frozenset(
    {
        "Response",
        "JSONResponse",
        "StreamingResponse",
        "FileResponse",
        "HTMLResponse",
        "PlainTextResponse",
        "RedirectResponse",
        "ORJSONResponse",
        "UJSONResponse",
    }
)

#: The GET handlers allowed to publish no response schema, by function name, each with the
#: reason. Held to the same discipline as ``WHITELIST``: an entry that stops being needed
#: fails ``test_no_stale_response_model_exemption``, so this list can only shrink by accident.
#:
#: All four send BYTES. ``mapimage``, ``maptiles``, ``maptiles_layer`` and ``icon`` answer
#: with a picture, a 204 or a 404 that names the generator, and they are ``-> Any`` rather
#: than ``-> FileResponse`` because each one really can return either -- annotating them
#: into the clause above would be narrowing a signature to satisfy a test.
#:
#: There used to be a fifth, and it was the reason this rule is stated with exemptions
#: instead of as "every handler": ``/api/worlds`` was DEFERRED while a ``response_model``
#: for it meant deleting eight keys from every save row it forwarded -- a body change, not
#: a typing one. That body change has been made, on purpose and in its own commit, so the
#: entry is gone and every JSON endpoint on the surface now says what it sends. The comment
#: above ``worlds()`` in ``routers/world.py`` records which keys died and why it was safe.
#:
#: ``events`` is deliberately NOT here: it is annotated ``-> StreamingResponse`` and the
#: clause above carries it, which is the arrangement worth having -- an exemption should be
#: needed only where the signature cannot say so itself.
RESPONSE_MODEL_EXEMPT: dict[str, str] = {
    "mapimage": "serves a PNG, a 204 or a 404 -- there is no JSON body to describe",
    "maptiles": "serves a tile, a 204 or a 404 -- there is no JSON body to describe",
    "maptiles_layer": "serves a tile, a 204 or a 404 -- there is no JSON body to describe",
    "icon": "serves a PNG, a 204 or a 404 -- there is no JSON body to describe",
}

# --------------------------------------------------------------------- the generators

#: What ``tools/`` may import, beyond the standard library, the ``gen`` extra and itself.
#:
#: ``core`` is the point of the arrangement: ``core/gameassets`` exists to be read by these
#: generators, and ``core`` may import only ``core``, so the whole reachable set stays small.
#: ``mapgen`` is tools/mapgen/src/mapgen, the generators' own package: itself, by import name.
TOOLS_ALLOWED_PREFIXES: tuple[str, ...] = ("tools", "mapgen", "satisfactory_mcp.core")

#: MEASURED, not assumed. Walking every import in ``tools/`` found exactly four things
#: outside the set above, and each is here by name with the reason it is allowed to stay:
#:
#: * ``numpy``, ``scipy`` and ``platformdirs`` are not optional at all -- they are in
#:   ``[project] dependencies``, so every install of this package already has them and a
#:   generator naming one adds nothing to what a clone must have. (They are NOT in the ``gen``
#:   extra, which is why they do not come in through ``_GEN_EXTRA_ROOTS``.)
#: * ``pioneersav`` is the parser, and ``gen_world_collectibles.py`` reads a ``.sav`` with it
#:   directly. The subprocess boundary the application keeps in front of it is not being
#:   broken here: that boundary exists for crash isolation, for handing a 2.9 MB parse's
#:   memory back to the OS, and for a projection small enough to commit -- three properties of
#:   a long-lived SERVER process. A generator is a one-shot CLI that exits when it is done, so
#:   it is the caller the boundary was built to protect, not one it applies to.
#:
#: Anything else is a real failure: it either makes the server depend on a generation-time
#: package, or points a generator at a layer above ``core``.
TOOLS_EXTRA_ROOTS = frozenset({"numpy", "scipy", "platformdirs", "pioneersav"})

#: The fifth measured exception, and a prefix rather than a root because it is one package of
#: one layer. ``gen_map_renders.py`` and ``gen_world_heightmap.py`` import
#: ``domain.spatial.heightfield`` -- the generator that WRITES the field, reading the module
#: that reads it, so that the two cannot disagree about the format. Kept narrow on purpose: a
#: generator has no business in ``domain.planning`` or ``domain.factories``, and widening this
#: to ``satisfactory_mcp.domain`` would let it have one without anybody noticing.
TOOLS_EXTRA_PREFIXES: tuple[str, ...] = ("satisfactory_mcp.domain.spatial",)


def _layer(module: str) -> str | None:
    """The layer a dotted module belongs to, or None if the rules do not cover it.

    Bare ``satisfactory_mcp`` is uncovered on purpose: the top-level ``__init__``
    is a package marker, not a layer, and importing it says nothing.
    """
    if module.split(".", 1)[0] in _SDK_ROOTS:
        return "sdk"
    best: tuple[int, str] | None = None
    for prefix, layer in _LAYERS:
        covers = module == prefix or module.startswith(prefix + ".")
        if covers and (best is None or len(prefix) > best[0]):
            best = (len(prefix), layer)
    return None if best is None else best[1]


def _module_name(path: Path) -> str:
    """The dotted name a file under ``src/`` is imported as."""
    parts = path.relative_to(SRC).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _package_of(path: Path) -> str:
    """The package an import inside ``path`` is relative to."""
    name = _module_name(path)
    return name if path.name == "__init__.py" else name.rsplit(".", 1)[0]


def _is_module(dotted: str) -> bool:
    """True when the dotted name names a file on disk rather than a symbol.

    ``from .. import config, render`` cannot be told from ``from .. import clamp``
    by the syntax alone; only the filesystem knows. This is what turns the former
    into two module edges and drops the latter as a symbol.
    """
    base = SRC.joinpath(*dotted.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _targets(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    """Every module this import node reaches, absolute and dotted."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]

    if node.level:
        parts = package.split(".")
        parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
        base = ".".join(parts)
        resolved = f"{base}.{node.module}" if node.module else base
    else:
        resolved = node.module or ""
    if not resolved:
        return []

    # The module imported from is itself an edge; each imported name is an edge
    # too, but only if it is a submodule rather than a symbol.
    found = [resolved]
    found.extend(
        f"{resolved}.{alias.name}"
        for alias in node.names
        if alias.name != "*" and _is_module(f"{resolved}.{alias.name}")
    )
    return found


def _sources(root: Path) -> list[Path]:
    """Every Python file under ``root`` that is this project's own.

    ``node_modules`` is skipped rather than trusted to hold no Python: it is an installed
    dependency tree, nothing here chose its contents, and a package that ships a ``.py``
    would otherwise be walked as if it were a module of this application.
    """
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts and "node_modules" not in path.parts
    )


def _edges(root: Path = PKG) -> set[tuple[str, str]]:
    """Every (importer, target) module pair in the package, lazy imports included."""
    edges: set[tuple[str, str]] = set()
    for path in _sources(root):
        importer = _module_name(path)
        package = _package_of(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in _targets(node, package):
                    edges.add((importer, target))
    return edges


def _tools_edges() -> set[tuple[str, str]]:
    """Every (importer, target) pair in ``tools/``, named as the package it imports as.

    A walker of its own rather than ``_edges(TOOLS)``, because ``_module_name`` derives a
    dotted name by relative path from ``SRC`` and these files live outside it. ``tools`` is a
    real package -- it has an ``__init__.py`` and ``conftest.py`` puts the repository root on
    ``sys.path`` so the suite can ``from tools import gen_map_image`` -- so the names built
    here are the names Python uses.
    """
    edges: set[tuple[str, str]] = set()
    for path in _sources(TOOLS):
        importer = "tools" if path.name == "__init__.py" else f"tools.{path.stem}"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in _targets(node, "tools"):
                    edges.add((importer, target))
    return edges


def _covers(target: str, prefixes: tuple[str, ...]) -> bool:
    return any(target == prefix or target.startswith(prefix + ".") for prefix in prefixes)


def _violations() -> set[tuple[str, str]]:
    """Edges that cross a layer boundary the wrong way."""
    bad: set[tuple[str, str]] = set()
    for importer, target in _edges():
        source_layer = _layer(importer)
        target_layer = _layer(target)
        if source_layer is None or target_layer is None:
            continue
        if target_layer not in ALLOWED[source_layer]:
            bad.add((importer, target))
    return bad


def _describe(pairs: set[tuple[str, str]]) -> str:
    return "\n".join(
        f"  {importer} ({_layer(importer)}) -> {target} ({_layer(target)})"
        for importer, target in sorted(pairs)
    )


def test_no_new_violations():
    """No module may reach across a layer boundary that is not already known."""
    new = _violations() - WHITELIST
    assert not new, (
        "import layering violated -- domain and core must not know presenters, "
        f"interfaces or the SDK:\n{_describe(new)}"
    )


def test_whitelist_is_not_stale():
    """A fixed violation must lose its whitelist entry, or the ratchet slips."""
    gone = WHITELIST - _violations()
    assert not gone, (
        f"stale whitelist entry -- the violation is gone, delete the entry:\n{_describe(gone)}"
    )


def test_whitelist_is_empty():
    """The refactor ended with nothing exempted, and that is the resting state.

    Neither adding the shims nor deleting them needed an entry: a shim's only edges
    pointed at the package it forwarded to, and removing it removed both ends at
    once. Adding a line back here is allowed, but it now has to be argued for in a
    diff that touches this file, which is exactly the friction it is meant to have.
    """
    assert WHITELIST == frozenset(), (
        "the whitelist is meant to stay empty -- a new exemption needs a reason "
        f"written next to it:\n{_describe(set(WHITELIST))}"
    )


def test_domain_and_core_never_import_a_presenter():
    """The seam phase 1 built, named outright instead of implied by the table.

    ``test_no_new_violations`` already covers this, but only through ``_LAYERS``:
    re-pointing ``presenters`` at the domain layer in that table would silence it
    while the leak came straight back. This test spells the presenter package
    literally, so the one rule the refactor existed for cannot be dissolved by
    editing a mapping.
    """
    leaks = {
        (importer, target)
        for importer, target in _edges()
        if _layer(importer) in {"core", "domain"}
        and any(target == root or target.startswith(root + ".") for root in _PRESENTER_ROOTS)
    }
    assert not leaks, (
        "domain and core must return data, never formatted text -- move the "
        f"formatting into presenters/text instead:\n{_describe(leaks)}"
    )


def test_the_layered_homes_exist():
    """Every destination the refactor names is a real package on disk."""
    missing = [name for name in LAYERED_HOMES if not _is_module(name)]
    assert not missing, (
        "the layered tree is incomplete -- these are the agreed homes and they "
        "have to exist:\n" + "\n".join(f"  {name}" for name in missing)
    )


def test_the_package_root_holds_only_the_layers():
    """No fifth top-level home. Pick a layer, or say why in ``ROOT_ENTRIES``."""
    found = {p.name for p in PKG.iterdir() if p.name != "__pycache__"}
    assert found == ROOT_ENTRIES, (
        "the package root drifted -- everything belongs in core, domain, "
        "presenters or interfaces:\n"
        f"  unexpected: {sorted(found - ROOT_ENTRIES)}\n"
        f"  missing:    {sorted(ROOT_ENTRIES - found)}"
    )


def test_the_parser_knows_nothing_about_the_application():
    """``pioneersav`` is a library, not a layer of this project.

    It reads a file format. Anything it learned about factories, plans or the MCP surface
    would be an import that has to be untangled again before it can be published or reused,
    so the rule is the strongest one available: zero edges pointing back this way.
    """
    leaks = {
        (importer, target)
        for importer, target in _edges(PARSER_PKG)
        if target == "satisfactory_mcp" or target.startswith("satisfactory_mcp.")
    }
    assert not leaks, (
        f"{PARSER} is a standalone library and must not import this application -- move "
        "whatever it needs into the caller:\n" + _describe(leaks)
    )


def test_only_the_extractor_imports_the_parser():
    """The subprocess boundary, stated as an import rule.

    The seam exists for crash isolation, for getting a 2.9 MB parse's memory back from the
    OS, and for a projection small enough to commit as the test fixture. None of that
    survives a convenience import: the first ``from pioneersav import ...`` anywhere else
    loads the parser into the server process and quietly deletes all three properties, with
    no test failing to say so. Exactly one module names it, and it is the one that runs in
    the child.
    """
    wrong = {
        (importer, target)
        for importer, target in _edges()
        if (target == PARSER or target.startswith(PARSER + ".")) and importer != PARSER_IMPORTER
    }
    assert not wrong, (
        f"the parser lives behind a subprocess -- only {PARSER_IMPORTER} may import "
        f"{PARSER}, everything else goes through core.saveio.projection:\n" + _describe(wrong)
    )


def test_the_old_paths_stay_deleted():
    """The inverted ratchet: a forwarding path is a defect, not a courtesy.

    While the shims stood, this test pinned their *shape* -- one alias ``__init__``,
    no def, no class -- because the failure to catch was logic creeping back into a
    path the architecture called dead. The paths are gone now, so the check gets to
    be the blunt one: neither ``docs/`` nor ``docs.py`` may exist at the package root
    under any of the eight old names. Re-adding one to spare a caller an import edit
    puts the tree back to having two names for one module, and the forwarding one is
    the one that stops being updated.
    """
    for name, moved_to in sorted(FORBIDDEN_PATHS.items()):
        for candidate in (PKG / name, PKG / f"{name}.py"):
            assert not candidate.exists(), (
                f"satisfactory_mcp.{name} is back -- it moved to {moved_to} and the old "
                "path is not a place code may live again; fix the caller's import instead"
            )


def _gameassets_sources() -> list[Path]:
    """Every module of the one package allowed to name the ``gen`` extra."""
    return _sources(PKG / "core" / "gameassets")


def _import_nodes(node: ast.AST, in_function: bool = False):
    """Every import in the tree, paired with whether a function body encloses it.

    ``ast.walk`` cannot answer that -- it flattens the tree -- and the whole distinction
    this file draws about the ``gen`` extra is between an import that runs at import time
    and one that runs when a generator calls the function.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            yield child, in_function
        deeper = in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        yield from _import_nodes(child, deeper)


def _root(dotted: str) -> str:
    return dotted.split(".", 1)[0]


def test_the_gen_extra_is_optional_at_import_time():
    """``ooz``, ``texture2ddecoder`` and Pillow: named in one package, and only lazily.

    Two halves of one rule. Outside ``core/gameassets`` nothing in the application or the
    parser may name them at all -- that is what makes ``uv run satisfactory-mcp`` work on a
    machine that has never installed the extra. Inside it, they may be named only from a
    function body, because a module-scope ``import ooz`` turns a missing OPTIONAL dependency
    into an ``ImportError`` at collection time for every test that so much as touches the
    package, which is the failure this exists to prevent and not a theoretical one.
    """
    outside = {
        (importer, target)
        for importer, target in _edges() | _edges(PARSER_PKG)
        if _root(target) in _GEN_EXTRA_ROOTS
        and importer != GAMEASSETS
        and not importer.startswith(GAMEASSETS + ".")
    }
    assert not outside, (
        "the `gen` extra is generation-time only -- these modules would stop importing on a "
        f"machine that has not installed it, and only {GAMEASSETS} may name it:\n"
        + _describe(outside)
    )

    eager = []
    for path in _gameassets_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node, in_function in _import_nodes(tree):
            if in_function:
                continue
            for target in _targets(node, _package_of(path)):
                if _root(target) in _GEN_EXTRA_ROOTS:
                    eager.append(f"  {_module_name(path)}:{node.lineno} imports {target}")
    assert not eager, (
        "these imports of the `gen` extra run at import time -- move them inside the "
        "function that needs them, the way `iostore.oodle_decompress` does:\n"
        + "\n".join(sorted(eager))
    )


def test_gameassets_never_imports_dynamically():
    """No ``importlib``, no ``__import__``, no ``sys.path`` -- so the rule above is readable.

    This is not a second preference dressed up as a test. ``test_the_gen_extra_is_optional``
    proves its point by parsing import statements, and every dynamic import is a hole in
    that proof: ``importlib.import_module("ooz")`` is invisible to it, and a ``sys.path``
    insert is how the decoders used to be reached -- out of a throwaway venv, at runtime,
    with the import graph saying nothing about it. Keeping all three out is what lets the
    AST be believed.
    """
    found = []
    for path in _gameassets_sources():
        name = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node, _in_function in _import_nodes(tree):
            for target in _targets(node, _package_of(path)):
                if _root(target) == "importlib":
                    found.append(f"  {name}:{node.lineno} imports {target}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "__import__":
                found.append(f"  {name}:{node.lineno} calls __import__")
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "path"
                and isinstance(node.value, ast.Name)
                and node.value.id == "sys"
            ):
                found.append(f"  {name}:{node.lineno} touches sys.path")
    assert not found, (
        "this package reaches its decoders through a parameter and its own imports, and "
        "nothing else -- a dynamic import here makes the layering unreadable rather than "
        "merely unusual:\n" + "\n".join(sorted(found))
    )


def test_gameassets_imports_nothing_but_the_stdlib_and_core():
    """The allowlist, stated positively: stdlib, ``core``/``config``, numpy, the extra lazily.

    ``core`` may already import only ``core``, so most of this is implied -- but only most.
    The package exists to be read by ``tools/gen_*.py``, and the tempting import is the one
    that goes the other way: a generator's helper, a third-party format library pulled in
    because it is already installed for something else. Either would quietly make a
    *generation-time* dependency into a dependency of the server, which is exactly the shape
    of thing the ``gen`` extra was created to stop.

    ``numpy`` is on the list and is not an exception to that rule -- it is a hard dependency
    of the project, so nothing about a clone changes by naming it. See
    ``_GAMEASSETS_HARD_ROOTS``. Anything genuinely optional still has to be imported inside
    a function body, and ``test_gameassets_never_imports_dynamically`` is what stops that
    rule being routed around.
    """
    stray = []
    for path in _gameassets_sources():
        name = _module_name(path)
        for node, _in_function in _import_nodes(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        ):
            for target in _targets(node, _package_of(path)):
                root = _root(target)
                allowed = (
                    root in sys.stdlib_module_names
                    or root in _GEN_EXTRA_ROOTS
                    or root in _GAMEASSETS_HARD_ROOTS
                    or target == "satisfactory_mcp.config"
                    or target == "satisfactory_mcp.core"
                    or target.startswith("satisfactory_mcp.core.")
                )
                if not allowed:
                    stray.append(f"  {name}:{node.lineno} imports {target}")
    assert not stray, (
        "core/gameassets may import the standard library, satisfactory_mcp.core (and "
        "config), numpy, and the `gen` extra from inside a function -- nothing else, or "
        "reading the game's assets stops being something the server can be built without:\n"
        + "\n".join(sorted(stray))
    )


def test_the_built_page_stays_out_of_git():
    """``static/`` is untracked build output, and the ignore rule keeping it so is pinned.

    The bundle used to be committed so a fresh clone served the map with no Node. That
    arrangement ended when the repository went public: ``app.js`` is a dependency's
    compiled code -- Leaflet, minified -- and redistributing it from this tree was the one
    thing the licence posture existed to manage. Now nothing under ``static/`` may be
    tracked, ever: the licence obligation attaches to whoever distributes a BUILD, and the
    repository distributes none. Both halves are asserted -- the ignore line, so the rule
    is visible where a contributor looks for it, and the index itself, because a file
    ``git add -f``-ed past the ignore is tracked regardless of what ``.gitignore`` says.
    """
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert STATIC_IGNORE_LINE in ignore, (
        f".gitignore no longer carries `{STATIC_IGNORE_LINE}` -- the built page (which "
        "embeds minified Leaflet) must never be committed; a clone builds its own"
    )

    try:
        tracked = subprocess.run(
            ["git", "ls-files", "--", "src/satisfactory_mcp/interfaces/web/static"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout, or no git on PATH -- nothing to be tracked in")
    assert not tracked, (
        "build output is tracked in git again -- `git rm -r --cached` it; static/ is "
        "generated by `npm run build` and holds a dependency's compiled code:\n"
        + "\n".join(f"  {name}" for name in tracked)
    )


def test_the_served_page_is_build_output_and_nothing_else():
    """``static/`` is written by ``npm run build``, in full, every time -- when it exists.

    The directory is untracked (the rule above), so a fresh clone simply does not have it
    and this test has nothing to say there. But wherever a build HAS run, the old hazard
    is unchanged: build output is exactly the kind of file someone edits in place, because
    it is right there and the change appears to work. It does work, once: the next
    ``npm run build`` empties this directory and the edit is gone with no error anywhere.
    So every file here has to be one the build produces, the set has to be complete -- a
    partial ``static/`` serves a page that half-loads, which is worse to diagnose than the
    503 an absent one gets -- and every file the build produces says so in its first line.
    """
    if not BUILT.is_dir():
        pytest.skip("static/ has not been built here -- untracked output, absent on a clone")

    missing = [name for name in BUILT_FILES if not (BUILT / name).is_file()]
    assert not missing, (
        "the built bundle is incomplete -- run `npm ci && npm run build` in "
        f"{FRONTEND.name}/:\n" + "\n".join(f"  static/{name}" for name in missing)
    )

    unmarked = [
        name
        for name in BUILT_FILES
        if name != COPIED_VERBATIM
        and BUILD_MARKER not in (BUILT / name).read_text(encoding="utf-8")[:400]
    ]
    assert not unmarked, (
        "these served files carry no build banner, so they were either hand-edited or "
        "written by something that is not the build -- the source is "
        f"{FRONTEND.name}/src:\n" + "\n".join(f"  static/{name}" for name in unmarked)
    )

    strays = {
        str(path.relative_to(BUILT)).replace("\\", "/")
        for path in BUILT.rglob("*")
        if path.is_file()
    } - set(BUILT_FILES)
    assert not strays, (
        "static/ is build output and holds nothing else -- put the source in "
        f"{FRONTEND.name}/ and let the build emit it, or add it to BUILT_FILES with a "
        "reason:\n" + "\n".join(f"  static/{name}" for name in sorted(strays))
    )


def _docstring_ids(tree: ast.AST) -> set[int]:
    """The identity of every docstring node, so prose can be told from a value.

    A docstring is a string literal in the AST and nothing else marks it, so the only way to
    exclude one is to find it where it is allowed to be: first statement of a module, a class
    or a function.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.add(id(value))
    return found


def _names_in_code(path: Path, needle: str) -> bool:
    """Whether ``needle`` appears anywhere this module could USE it.

    Every string constant that is not a docstring, and every import target. Deliberately not
    the file's raw text: a ``#`` comment and a docstring cannot become a path, cannot be
    passed to ``open`` and cannot be imported, so a grep over the whole file makes it
    impossible to EXPLAIN the rule in the module the rule is about -- which is where the
    explanation is most useful and where the next reader will look for it.

    An f-string is covered: its literal halves are ``Constant`` nodes inside a ``JoinedStr``,
    which ``ast.walk`` reaches, so ``f"{root}/frontend/src"`` is caught exactly as the plain
    string would be. What this cannot see is a name assembled at runtime out of pieces --
    which is the same limit every other rule in this file has, and the reason
    ``test_gameassets_never_imports_dynamically`` exists next door.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    prose = _docstring_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any(needle in target for target in _targets(node, _package_of(path))):
                return True
            continue
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) not in prose and needle in node.value:
            return True
    return False


def test_the_frontend_sources_are_not_reachable_from_python():
    """The seam is the built directory, and it is the only seam.

    ``app.py`` mounts ``static/`` and that is the whole contract: whatever is inside came
    out of a build, and the Python has no opinion about how. A module that read a path
    inside ``frontend/`` -- to serve a ``.ts`` file, to parse ``package.json`` for a version,
    to find ``src/main.ts`` -- would make the npm project a runtime dependency of the server
    and put a machine with no ``node_modules`` one import away from a 500.

    Read off the AST rather than off the raw text, which is what this used to do. Grepping the
    bytes makes the rule bite its own documentation: ``app.py`` cannot say "the frontend
    sources are next door and are not served from here" in the comment above the mount without
    failing the test that enforces exactly that. A rule nobody may explain in place is a rule
    the next reader has to rediscover, so what is checked is what a module could USE -- string
    values and import targets -- and prose is left alone.

    One module carries a measured allowance: ``app.py`` holds the message ``/`` answers with
    when ``static/`` has not been built, and that message tells a HUMAN where to run
    ``npm run build`` -- which cannot be said without naming ``frontend/``. It is response
    text, not a path the server opens: nothing else in the module may name the directory,
    which is what keeping the allowance to exactly one string constant enforces.
    """
    allowed = PKG / "interfaces" / "web" / "app.py"
    tree = ast.parse(allowed.read_text(encoding="utf-8"), filename=str(allowed))
    prose = _docstring_ids(tree)
    naming = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
        and "frontend" in node.value
    ]
    assert len(naming) == 1 and "npm ci && npm run build" in naming[0].value, (
        "app.py may name frontend/ exactly once, in the not-built instruction served at / "
        "-- anything more is the server reaching past the built directory"
    )

    named = sorted(
        _module_name(path)
        for path in _sources(PKG)
        if path != allowed and _names_in_code(path, "frontend")
    )
    assert not named, (
        "these modules name the frontend sources in code -- the server serves the BUILT "
        "directory and nothing reaches past it:\n" + "\n".join(f"  {name}" for name in named)
    )
    assert not _sources(FRONTEND), (
        "the npm project has grown Python -- it is a TypeScript build, and a .py file in "
        "it is either in the wrong tree or a build step that belongs in package.json:\n"
        + "\n".join(f"  {path}" for path in _sources(FRONTEND))
    )


#: The page's names for the base map before there was a registry, and the registry's own.
#: The ids are data now, so the compiler checks none of them; the old ones survive only as
#: what ``registry.LEGACY`` keeps serving unregistered and the one alias tiles.ts keeps.
REGISTRY_PY = PKG / "domain" / "maps" / "registry.py"
FRONTEND_TILES_TS = FRONTEND / "src" / "tiles.ts"


def test_the_page_and_the_registry_agree_on_the_old_base_map_names():
    """Old ``#mode=`` links keep working only while both sides keep the same three names.

    ``registry.LEGACY`` is what the server serves before anything is registered, and the
    switcher in ``tiles.ts`` offers the same three when ``/api/maps`` cannot be read; the
    artwork's page-side alias ``artwork`` must resolve to the registry's id for it. Read by
    AST and regex, because importing either side would need FastAPI or a browser.
    """
    tree = ast.parse(REGISTRY_PY.read_text(encoding="utf-8"), filename=str(REGISTRY_PY))
    legacy = next(
        [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "LEGACY" for t in node.targets)
        and isinstance(node.value, ast.Dict)
    )
    tiles = FRONTEND_TILES_TS.read_text(encoding="utf-8")
    artwork = re.search(r'var ARTWORK = "([^"]+)";', tiles)
    assert artwork, "tiles.ts no longer names the artwork's registry id"
    assert artwork.group(1) == legacy[0], (artwork.group(1), legacy)
    assert re.search(r"var ALIASES[^=]*= \{ artwork: ARTWORK \}", tiles), (
        "the artwork alias is gone"
    )
    offered = re.findall(r'legacy\((ARTWORK|"[a-z]+")', tiles)
    names = [legacy[0] if name == "ARTWORK" else name.strip('"') for name in offered]
    assert names == legacy, (
        f"tiles.ts offers {names} before the registry answers; it serves {legacy}"
    )


def _ts_imports(path: Path) -> set[str]:
    """Every sibling module a ``.ts`` file imports -- bare, named, value or type.

    Two patterns rather than one because the two forms do not look alike: a side-effect
    import is the whole statement on one line, and everything else is recognised by its
    ``from "./x"`` tail, which is the only part guaranteed not to be split across lines.
    """
    text = path.read_text(encoding="utf-8")
    return set(BARE_IMPORT.findall(text)) | set(FROM_IMPORT.findall(text))


def _registering_modules() -> set[str]:
    """Every page module that declares a fetch, by module name."""
    return {
        path.stem
        for path in sorted(FRONTEND_SRC.glob("*.ts"))
        if REGISTER_CALL.search(path.read_text(encoding="utf-8"))
    }


def test_every_module_that_fetches_is_named_in_the_features_block():
    """The registrations only exist if the modules holding them are in the bundle.

    ``registerFetch`` runs when a module is evaluated, and a module is evaluated only if
    something imports it. ``load.ts`` used to be that something for all of them and
    deliberately is not any more, which leaves exactly one thing holding the features in: the
    block of bare imports at the top of ``main.ts``. Delete a line from it and the build
    succeeds, ``tsc`` is happy -- there is no unused name to notice -- Rollup drops the module,
    and a layer of the map is never fetched again. Nothing on the page says so; the checkbox
    is simply not there.

    Checked in both directions on purpose. A module that registers and is not listed is the
    failure above. A module listed that no longer registers is the same block turning into a
    list of imports nobody can explain, which is how it stops being read.
    """
    listed = set(BARE_IMPORT.findall(FRONTEND_MAIN_TS.read_text(encoding="utf-8")))
    registering = _registering_modules()
    assert registering, (
        "nothing calls registerFetch at module scope any more -- if the registry is gone, "
        "this rule and the paragraph about it at the top of this file should go with it"
    )
    missing = sorted(registering - listed)
    assert not missing, (
        "these modules register a fetch and nothing imports them for it -- add a bare "
        '`import "./name";` to the FEATURES block in main.ts, or their layers are dropped '
        "from the bundle with no error anywhere:\n" + "\n".join(f"  src/{n}.ts" for n in missing)
    )
    stale = sorted(listed - registering)
    assert not stale, (
        "these are imported for their side effect in main.ts's FEATURES block but register "
        "no fetch -- either they lost their registerFetch call (and their layer with it) "
        "or the line belongs somewhere the reason for it is visible:\n"
        + "\n".join(f"  src/{n}.ts" for n in stale)
    )


def _ts_importers_of(module: str) -> set[str]:
    """Every page module that imports ``module``, by module name."""
    return {
        path.stem
        for path in sorted(FRONTEND_SRC.glob("*.ts"))
        if path.stem != module and module in _ts_imports(path)
    }


def test_the_mechanism_imports_no_module_that_fetches():
    """The other half of the FEATURES rule, and the half that makes it a seam rather than a
    habit.

    If ``load.ts`` imported one feature module the arrangement would still work -- and the
    ratchet next door would still pass, because that module would be in the bundle. It would
    just be in the bundle for the wrong reason, and the day somebody tidied the import away
    the FEATURES block would take the blame for a failure it did not cause. So the mechanism
    side is required to know none of the names: ``load.ts`` runs waves, ``registry.ts`` holds
    a list, ``layers.ts`` hands out groups and ``layercontrol.ts`` draws the rows, and not one
    of the four can reach a module that declares a fetch.

    ``regions.ts`` is the one feature module ``load.ts`` still names, and it is allowed here
    because it registers nothing: ``/api/regions`` is geography -- no world to scope it to, no
    epoch to guard it against, fetched once for the life of the page -- so it is in neither
    wave, and importing it drops no registration. That is why the forbidden set is "the
    modules that register" rather than "the modules that draw": the rule then defines itself
    from the source instead of from a list somebody has to maintain.

    For ``layers.ts`` that is deliberately NOT the whole rule, because ``regions.ts`` is
    exactly the module a wider one has to catch. See the next test.

    ``registry.ts`` is held to the stronger version of ``state.ts``'s rule -- nothing at all at
    runtime, only ``import type``, which is erased. Everything that draws imports it, so
    anything it imported would be evaluated before all of them.
    """
    registering = _registering_modules()
    for path in FRONTEND_MECHANISM:
        reached = sorted(_ts_imports(path) & registering)
        assert not reached, (
            f"src/{path.name} imports a module that registers a fetch, which is what the "
            "registry exists to stop -- the feature declares what it wants fetched and this "
            "side runs the list:\n" + "\n".join(f"  -> src/{n}.ts" for n in reached)
        )


def test_no_mechanism_module_imports_a_module_that_imports_it():
    """The wider rule, and the one the ranks and the sections needed.

    "Imports no module that fetches" is the right forbidden set for ``load.ts``: what it must
    not know is what is in the waves. It is the wrong one for ``layers.ts``, which hands a
    named group to everything that draws -- and ``regions.ts`` draws without fetching, so the
    rule next door would let ``layers.ts`` import it. That import would be a ring, and rings
    between page modules are the specific failure this file was extended for: everything that
    draws reaches ``layers.ts``, so anything ``layers.ts`` reached back would be evaluated
    before all of them, and the two files would each look reasonable on their own.

    So the wider rule is stated as the absence of a cycle and derived from the source rather
    than from a list: none of the four mechanism modules may import a module that imports it.
    That covers every drawing module for ``layers.ts`` -- because importing ``./layers`` is
    what makes a module one -- and every consumer of the control, the registry and the loader
    for the other three, without anybody having to keep the set up to date.

    It is not implied by the rule above and does not imply it. ``markers.ts`` breaks both
    ways round; ``regions.ts`` breaks only this one; a feature that registered a fetch but
    imported none of the four would break only the other.
    """
    for path in FRONTEND_MECHANISM:
        reached = sorted(_ts_imports(path) & _ts_importers_of(path.stem))
        assert not reached, (
            f"src/{path.name} imports a module that imports it back, which is a ring: this "
            "side is a mechanism every feature reaches, so whatever it reaches would be "
            "evaluated before all of them:\n" + "\n".join(f"  -> src/{n}.ts" for n in reached)
        )

    values = [
        line
        for line in FRONTEND_REGISTRY_TS.read_text(encoding="utf-8").splitlines()
        if line.startswith("import ") and not line.startswith("import type ")
    ]
    assert not values, (
        "registry.ts imports something at runtime -- everything that draws imports it, so "
        "whatever this is would be evaluated before all of them:\n" + "\n".join(values)
    )


def test_no_colour_value_lives_in_palette_ts():
    """``palette.ts`` is the comparison, not the table, and the difference is checkable.

    The file used to hold every colour on the page, on the argument that colours picked
    against each other only stay defensible while they are in one place to be compared. That
    argument was about the COMPARISON and it kept the values by mistake -- which put each
    measured warrant ("dE 34 from the terrain", "dE 4.5 from the extractors, which is why that
    one was rejected") in a different file from the width, the tier ramp and the reasoning it
    was part of, and left the two networks that had refused to split as a carve-out in the
    file's own header.

    Now the values sit with their warrants and ``declareColours`` records them, so the audit
    compares the same table the header always argued about. This rule is what stops the old
    arrangement growing back one colour at a time: the first hex to reappear here would be a
    colour with no owner and no warrant, and it would look exactly like the file being helpful.

    Comments included, and that is not pedantry. The old header quoted the values it compared,
    and a quoted hex is the same failure as a declared one -- a colour written down where the
    audit cannot reach it. The exception list names its pairs as ``owner/name`` for that same
    reason, and the messages the audit prints carry no values either.
    """
    text = FRONTEND_PALETTE_TS.read_text(encoding="utf-8")
    found = [
        f"  line {n}: {hit.group(0)}  in  {line.strip()[:88]}"
        for n, line in enumerate(text.splitlines(), start=1)
        for hit in [HEX_COLOUR.search(line)]
        if hit
    ]
    assert not found, (
        "palette.ts holds a colour value. It holds the comparison; the values belong to the "
        "module that draws with them, declared through declareColours so the dE audit can "
        "see them -- a hex here is a colour with no owner and no warrant:\n" + "\n".join(found)
    )


def _router_sources() -> list[Path]:
    """Every router module. ``__init__.py`` is excluded -- it is the mount list, not a
    router, and it is the one file in the package that may name all the others."""
    return [path for path in _sources(WEB_ROUTERS) if path.name != "__init__.py"]


def test_a_router_sees_the_domain_and_its_own_two_helpers_and_nothing_else():
    """One module per concern only means something if the modules cannot reach each other.

    Three imports would each undo the split silently. **Another router**: the moment
    ``storage`` imports a helper from ``placements`` the two are one unit again, with a file
    boundary between them that only makes the coupling harder to see -- and the shared thing
    belongs in ``serial`` where every router can have it. **``app``**: a router importing the
    application it is mounted into is a cycle, and the loose end is the mount ORDER, which is
    what keeps ``/openapi.json`` and the committed ``api-schema.d.ts`` byte-stable.
    **``presenters``**: this layer serialises to JSON, and a formatted string arriving in a
    payload is a decision made in the wrong place -- the same rule
    ``test_domain_and_core_never_import_a_presenter`` states one layer down.

    Stated positively, as an allowlist, because that is the version that stays true: a
    denylist of three names would pass the day somebody imports a fourth thing.
    """
    stray = []
    for path in _router_sources():
        name = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node, _in_function in _import_nodes(tree):
            for target in _targets(node, _package_of(path)):
                allowed = (
                    _root(target) in sys.stdlib_module_names
                    or _root(target) in ROUTER_ALLOWED_ROOTS
                    or target in ROUTER_ALLOWED_EXACT
                    or _covers(target, ROUTER_ALLOWED_PREFIXES)
                    or (name, target) in ROUTER_EXTRA_EDGES
                )
                if not allowed:
                    stray.append(f"  {name}:{node.lineno} imports {target}")
    assert not stray, (
        "a router may import the standard library, fastapi/starlette, config/core/domain "
        "and the web package's own serial and terrain -- never another router, never app, "
        "never a presenter. Whatever is shared belongs in the serial package:\n"
        + "\n".join(sorted(stray))
    )


def _all_routers_declared() -> list[str]:
    """The module names in ``ALL_ROUTERS``, in order, read off the AST.

    By AST rather than by import: this module's first promise is that it runs on the
    standard library alone, and importing ``routers/__init__.py`` would need FastAPI installed.
    """
    path = WEB_ROUTERS / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        if not (isinstance(node.target, ast.Name) and node.target.id == "ALL_ROUTERS"):
            continue
        assert isinstance(node.value, ast.Tuple), "ALL_ROUTERS is no longer a literal tuple"
        return [
            element.value.id
            for element in node.value.elts
            if isinstance(element, ast.Attribute)
            and element.attr == "router"
            and isinstance(element.value, ast.Name)
        ]
    raise AssertionError("ALL_ROUTERS is gone from routers/__init__.py")


def test_every_router_is_mounted_exactly_once_and_app_mounts_only_the_tuple():
    """A router nobody mounts is a 404, and nothing anywhere fails to say so.

    That is the whole failure this catches: the module imports, the decorators run, the
    handlers are correct, and the route simply is not there. It has no test of its own
    either, because the test that would catch it is the one somebody forgot to write in the
    same breath as the mount. So the tuple is checked against the DIRECTORY.

    The second half is the mount itself. ``app.py`` must reach its routers by looping over
    ``ALL_ROUTERS`` and by no other means: one ``include_router`` outside that loop is how
    the path order stops being the tuple's order, and path order is what the committed
    ``api-schema.d.ts`` is generated from -- so the diff it produces would be in a file
    nobody edited, describing a change nobody made.

    Exactly once, not at least once: a router included twice registers every one of its
    paths twice, and FastAPI serves the first while ``/openapi.json`` lists them both.
    """
    declared = _all_routers_declared()
    on_disk = sorted(path.stem for path in _router_sources())

    duplicated = sorted({name for name in declared if declared.count(name) > 1})
    assert not duplicated, (
        "these routers are in ALL_ROUTERS more than once -- every path they carry is "
        f"registered twice: {duplicated}"
    )
    assert sorted(declared) == on_disk, (
        "ALL_ROUTERS and routers/ have drifted; an unmounted module is a 404 that nothing "
        "reports:\n"
        f"  never mounted: {sorted(set(on_disk) - set(declared))}\n"
        f"  no such file:  {sorted(set(declared) - set(on_disk))}"
    )

    source = WEB_APP_PY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(WEB_APP_PY))
    includes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "include_router"
    ]
    assert len(includes) == 1, (
        "app.py includes routers somewhere other than the loop over ALL_ROUTERS -- the "
        f"mount order is the tuple's order or it is nobody's ({len(includes)} calls found)"
    )
    loops = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Name)
        and node.iter.id == "ALL_ROUTERS"
    ]
    assert len(loops) == 1, "app.py no longer mounts the API by iterating ALL_ROUTERS"
    assert includes[0] in ast.walk(loops[0]), (
        "the one include_router in app.py is outside the ALL_ROUTERS loop"
    )


def test_the_one_file_api_stays_deleted():
    """``interfaces/web/api.py`` is dead, and re-creating it is the whole regression.

    The inverted ratchet, exactly as ``test_the_old_paths_stay_deleted`` runs it one layer
    up. This file was 2,174 lines holding eighteen endpoints, six serialisers and every
    constant the surface has; it is now thirteen router modules plus ``serial.py``. Nothing
    about that arrangement is enforced by anything else if a file called ``api.py`` may
    exist again -- the natural next edit is always "this bit does not fit anywhere, put it
    back in api.py", and the second endpoint to land there re-creates the original.

    Its test file goes with it. ``test_web_api.py`` was 3,048 lines and is now one module
    per router; a new one would be the same drift from the other end.
    """
    for candidate in (WEB / "api.py", WEB / "api"):
        assert not candidate.exists(), (
            "interfaces/web/api.py is back -- it was split into routers/ (one module per "
            "concern, mounted through ALL_ROUTERS) and serial.py (the shared vocabulary), "
            "and a handler that fits neither belongs in a router of its own"
        )
    assert not (REPO / "tests" / "test_web_api.py").exists(), (
        "tests/test_web_api.py is back -- the endpoint tests live in test_web_<router>.py, "
        "one file per router, plus test_web_static.py for the mount at /"
    )


def test_no_router_grows_back_into_a_one_file_api():
    """The cap that makes 'one module per concern' a measurement rather than an intention.

    ``api.py`` did not arrive at 2,174 lines; it got there one reasonable addition at a
    time, and no single one of those commits looked wrong. The cap is set well above every
    budget the split was planned against so that a module always has room to explain itself,
    and low enough that a second concern moving in shows up as a failing test rather than as
    a code review somebody has to remember to ask for.
    """
    over = [
        f"  routers/{path.name}: {len(path.read_text(encoding='utf-8').splitlines())} lines"
        for path in _sources(WEB_ROUTERS)
        if len(path.read_text(encoding="utf-8").splitlines()) > ROUTER_MAX_LINES
    ]
    assert not over, (
        f"a router module is over {ROUTER_MAX_LINES} lines -- that is a second concern, and "
        "it wants its own file and its own entry at the END of ALL_ROUTERS (never in the "
        "middle: the tuple's order is the committed schema's path order):\n" + "\n".join(over)
    )


def _annotation_name(node: ast.expr | None) -> str | None:
    """The terminal name of a return annotation, however it is spelled.

    ``StreamingResponse`` is an ``ast.Name``; ``fastapi.responses.FileResponse`` is an
    ``ast.Attribute`` whose ``attr`` is the name. Both appear in this package, and a rule
    that read only one of them would wave the other through.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.rsplit(".", 1)[-1]
    return None


WRITE_VERBS = frozenset({"post", "put", "patch", "delete"})


def _get_routes() -> list[tuple[str, str, int, ast.Call, ast.expr | None]]:
    """Every handler in ``routers/`` that answers a GET, read off the AST.

    ``(module, function, lineno, decorator call, return annotation)``. Both registration
    forms are walked: ``@router.get(...)``, and ``@router.api_route(..., methods=[...])``
    where the list contains GET -- the form the four byte-serving endpoints use because they
    answer HEAD from the same handler. A rule that matched only ``.get`` would have found no
    violation among them no matter what they did, which is the wrong kind of passing.

    By AST rather than by importing the app, for the reason every other rule here gives: this
    module's first promise is that it runs on the standard library alone.
    """
    found: list[tuple[str, str, int, ast.Call, ast.expr | None]] = []
    for path in _router_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call) or not isinstance(deco.func, ast.Attribute):
                    continue
                if not (isinstance(deco.func.value, ast.Name) and deco.func.value.id == "router"):
                    continue
                verb = deco.func.attr
                if verb == "api_route":
                    methods = next((k.value for k in deco.keywords if k.arg == "methods"), None)
                    names = (
                        {e.value for e in methods.elts if isinstance(e, ast.Constant)}
                        if isinstance(methods, (ast.List, ast.Tuple))
                        else set()
                    )
                    if "GET" not in names:
                        continue
                elif verb not in WRITE_VERBS | {"get"}:
                    continue
                found.append((path.stem, node.name, node.lineno, deco, node.returns))
    return found


def test_every_get_says_what_it_sends():
    """The last ratchet of the refactor, and the one the whole T series exists to install.

    A handler with no ``response_model`` publishes no response schema, so ``/openapi.json``
    types its ``200`` as ``unknown``, so ``api-schema.d.ts`` does, so the page has to declare
    the payload itself -- from OBSERVED bytes, which is a claim about the save it was read
    from rather than about the API. That file existed, it was called ``api-types.ts``, it
    reached 438 lines, and three of its nullability notes were wrong in the two directions a
    guess can be wrong: a ``| null`` nobody could produce, and a null the page did not guard.
    It is gone. This is what stops the next one.

    The failure is silent in the ordinary way: adding an endpoint without a response model
    compiles, serves, and is correct. Nothing says anything until somebody needs its type on
    the page and writes one down.

    The write verbs are walked too (``WRITE_VERBS``): the first POST was a decision made
    here, on 2026-09-27, and a write owes the page its reply type as much as a read does.
    """
    missing = []
    for module, name, lineno, deco, returns in _get_routes():
        if any(k.arg == "response_model" for k in deco.keywords):
            continue
        if _annotation_name(returns) in RESPONSE_CLASSES:
            continue
        if name in RESPONSE_MODEL_EXEMPT:
            continue
        missing.append(f"  routers/{module}.py:{lineno}  {name}()")
    assert not missing, (
        "these GET handlers publish no response schema, so the page cannot be typed from the "
        "server and will type itself from observed payloads instead -- which is the file this "
        "ratchet exists to keep deleted. Declare a TypedDict in EMISSION order and pass it as "
        "response_model (routers/floors.py writes the two rules out), or, if the handler "
        "returns a Response subclass, say so in its return annotation:\n" + "\n".join(missing)
    )


def test_no_stale_response_model_exemption():
    """An exemption that stops being needed must lose its entry, or the ratchet slips.

    ``WHITELIST``'s discipline one layer down: a list of allowed violations is only a ratchet
    while every line on it is still load-bearing. A name left here after its handler grew a
    ``response_model`` is a hole nobody can see, and it is exactly the hole the next handler
    of the same name would fall through.
    """
    routes = _get_routes()
    named = {name for _module, name, _line, _deco, _returns in routes}
    unknown = sorted(set(RESPONSE_MODEL_EXEMPT) - named)
    assert not unknown, (
        "RESPONSE_MODEL_EXEMPT names GET handlers that no longer exist in routers/ -- a "
        f"renamed or deleted endpoint leaves its exemption behind: {unknown}"
    )
    declared = {
        name
        for _module, name, _line, deco, _returns in routes
        if any(k.arg == "response_model" for k in deco.keywords)
    }
    fixed = sorted(declared & set(RESPONSE_MODEL_EXEMPT))
    assert not fixed, (
        "these handlers now declare a response_model and no longer need their exemption; "
        f"delete the entry from RESPONSE_MODEL_EXEMPT: {fixed}"
    )


def test_the_generators_reach_down_and_nothing_reaches_up_to_them():
    """``tools/`` as a pseudo-layer: stdlib, the ``gen`` extra, ``core``, and itself.

    Nothing walked this directory before. It is outside ``src``, outside the wheel and outside
    every other loop in this file, which made it the one place an import could drift without a
    test noticing -- and it is also the only code here that reads the installed game, so the
    drift that matters is cheap to make: one convenience import from ``domain.planning`` or
    ``presenters`` and a generator has quietly become an application module that happens to
    live outside the package.

    The five exceptions are measured rather than assumed and each carries its reason where it
    is declared; see ``TOOLS_EXTRA_ROOTS`` and ``TOOLS_EXTRA_PREFIXES``.
    """
    stray = []
    for importer, target in sorted(_tools_edges()):
        root = _root(target)
        allowed = (
            root in sys.stdlib_module_names
            or root in _GEN_EXTRA_ROOTS
            or root in TOOLS_EXTRA_ROOTS
            or _covers(target, TOOLS_ALLOWED_PREFIXES)
            or _covers(target, TOOLS_EXTRA_PREFIXES)
        )
        if not allowed:
            stray.append(f"  {importer} -> {target}")
    assert not stray, (
        "a generator may read the standard library, the `gen` extra, satisfactory_mcp.core "
        "and itself -- anything else either makes the server depend on a generation-time "
        "package or points a generator at a layer above core. Fix the import, or add it to "
        "TOOLS_EXTRA_ROOTS / TOOLS_EXTRA_PREFIXES with the reason:\n" + "\n".join(stray)
    )


def test_nothing_in_the_package_imports_a_generator():
    """The other direction, and the one that would actually break a clone.

    ``_violations`` already covers it -- no layer's allowed set contains ``tools`` -- so this
    spells the consequence rather than adding a rule: the generators are not in the wheel
    (``pyproject.toml`` ships ``satisfactory_mcp`` and ``pioneersav``, and nothing else), so an
    import of one from inside the package is an ImportError on every installed copy, and a
    working import in this repository right up until someone installs it.
    """
    reaching = {
        (importer, target)
        for importer, target in _edges() | _edges(PARSER_PKG)
        if _covers(target, ("tools",))
    }
    assert not reaching, (
        "the generators are not shipped -- nothing in the package or the parser may import "
        "them, and whatever is wanted belongs in core:\n" + _describe(reaching)
    )


def test_the_page_frame_matches_geo():
    """``map.ts`` keeps the map square as a literal (TypeScript cannot read Python); it must
    equal ``geo.MAP_SQUARE_M``, the one frame every Python module reads."""
    from satisfactory_mcp.domain.spatial import geo

    map_ts = SRC / "satisfactory_mcp" / "interfaces" / "web" / "frontend" / "src" / "map.ts"
    number = r"(-?\d+(?:\.\d+)?)"
    found = re.search(
        rf"MAP_SQUARE_M = \{{ x_min: {number}, x_max: {number}, y_min: {number}, y_max: {number} \}}",
        map_ts.read_text(encoding="utf-8"),
    )
    assert found, "map.ts no longer spells MAP_SQUARE_M as one literal"
    x0, x1, y0, y1 = (float(v) for v in found.groups())
    assert (x0, y0, x1, y1) == geo.MAP_SQUARE_M
