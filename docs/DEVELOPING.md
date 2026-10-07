# Developing

This is the developer half of the README: how the code is laid out and tested, how the world
data gets regenerated, and where every byte of data comes from. For what the project *does*,
start at the [README](../README.md); for scope, decisions and architecture in full, read
[DESIGN.md](../DESIGN.md), which indexes the deeper documents in this directory.
Versions, branches and releases are in [releasing.md](releasing.md).

## Tests

```bash
uv sync --all-extras               # the type gate reads the web and gen extras too ("Types")
uv run pytest -q                   # the default run: needs nothing but this checkout
uv run pytest -q -m integration    # the other half: needs the game and at least one save
```

The default run reads committed fixtures only, so a clone with no game install passes it in
seconds. The map generators' own tests live in `tools/mapgen/tests/`; their
[README](../tools/mapgen/README.md) has the command. Frontend checks are `npm run check` (strict `tsc --noEmit`), `npm test` (Vitest) and `npm run build` in
`src/satisfactory_mcp/interfaces/web/frontend/` —
[the frontend README](../src/satisfactory_mcp/interfaces/web/frontend/README.md) covers the
dev loop, the layer modules, and the type story.

Architecture is enforced, not reviewed: the tests in `tests/architecture/` read the AST of
every module to prove imports run one way — `core` knows nothing, `domain` knows `core`,
`presenters` know `domain`, `interfaces` know everything. "Architecture rules" below says why
each rule exists.

```
src/satisfactory_mcp/
  core/       Docs.json loading, the save seam, plain-text helpers
  domain/     world state, progression, power, factories, spatial, the LP planner
              (planning/: solver, readout, analysis, layout, siting, progress, stored),
              and session/ (asks, focus, journal, pins shared by chat and the page)
  presenters/ all response formatting
  interfaces/ mcp/ (the FastMCP surface) and web/ (FastAPI + Leaflet map)
  server.py   the console entry point; no logic
  config.py   paths and environment
src/pioneersav/  the save parser, a standalone package
tools/           data generators
tools/mapgen/    the map generators, a uv workspace member (python -m mapgen)
```

`src/pioneersav` deserves its own sentence: it is a standalone, first-party package that reads
the save format across six `saveVersion`s, and the server talks to it through one subprocess
boundary, so a torn autosave or a format change cannot take the server down.

In planning signatures a PlanState is `stored`; `state`/`st` is a WorldState.

### Test suite

**Two runs.** The default run deselects `integration`; `-m integration` needs `Docs.json` and
the save folder. Two commands that each say what they need beat one that quietly does less than
it looks like it does.

**Layout.** `tests/` mirrors the code it tests: `core/`, `domain/<package>/`, `presenters/`,
`mcp/`, `web/` (one file per router, in folders named after the router packages),
`pioneersav/`, `data/` (the committed world tables held
against the game), `tools/` (the generator scripts and the map job runner), `mapgen/` (the map
generator's own tests, until they join `tools/mapgen/tests/`), `frontend/`, `architecture/` and
`docs/` (every relative link, anchor and section reference in the docs resolves).
Shared helpers and fixtures live in `tests/support/` and are imported as
`tests.support.<module>`; a test module never imports another test module or `conftest`, which
`tests/architecture/test_test_imports.py` enforces. Pytest runs with `--import-mode=importlib`
and the repository root on `pythonpath`, so two folders may hold files of the same name.

**Private user data.** An autouse fixture points `SATISFACTORY_USER_DATA` at the test's own
`tmp_path` and clears the cached `config` paths, so plans, labels, the activity journal, pins,
asks, advice, the page focus and the shared settings are never the reader's; the `user_data`
fixture is that root. `data_dir` and `cache_dir` are not redirected: a test that needs a
`data/local` tree builds one. A module fixture runs before any test's root is in force, so one
that reads or writes user data (the sensitivity sweep reads the shared settings) wraps itself
in `tests.support.user_data.private_user_data` over a root of its own.

**Web tests.** A module that drives the app calls `pytest.importorskip("fastapi")` at module
scope, because the web stack is an optional extra. It gets the app through `client` (the shared
fixture world), `labelled_client`, `fresh_state_client` (a new world per request),
`stateless_client`, or `tests.support.web.client_over` for a hand-built world; none reads a
`.sav`. `client_over` reuses its apps within a worker: an app's first request builds the
schema of every route, about 0.1 s, which once cost the default run 48 s of CPU over 477
apps; the web tests alone at 4 workers went from 21 s to 13 s. Each use still calls
`create_app`, which takes a millisecond, and lends a reused app that app's `state` (the two
loaders, a new save watcher and a new map job runner); on the way out the app gets an empty
`state` and goes back to the pool. A test that opens a client inside another gets a second
app. `tests/web/test_app_reuse.py` holds both rules. A test that needs anything else of its
own on the app, such as `dependency_overrides`, builds its app with `create_app`.

**The reference world.** `tests/fixtures/save_projection.json` is the sidecar projection of one
real save, named in `tests/support/reference_world.py` and committed because the `.sav` is not.
Re-cut it from the repository root with

```bash
uv run python -m satisfactory_mcp.core.saveio.extract \
    "<saves>/Han Solo_280726-230847.sav" > tests/fixtures/save_projection.json
```

`-m` because that is how the server spawns the extractor; the sidecar's stdout is the fixture.
Re-cut from a newer save of the same world (`save_identifier` `X2faPVKjX06VaRzClNv5KQ`), never
from another one: the `projection` fixture asserts the world, and several modules in `src`
justify a decision with a number measured on this fixture. After a re-cut, expect
`tests/data/test_reference_counts.py` to fail, and update both its assertion and the comment in
`src` it names.

**The reference field.** Planner tests plan over `REFERENCE_FIELD`, a bounding box, rather than
`region:Spire Coast`. Region names are advisory: when the region layer was re-derived from the
game's own map areas, Spire Coast went from 51 nodes to 18 and every planner number moved
although the planner had not changed. The box is the bounding box of the 51 nodes the old
selector returned, widened by 1 cm on each edge so that sub-centimetre node positions cannot
fall out of it. It also holds 17 nodes (8 limestone, 5 iron, 2 copper, 2 raw quartz) that are
irrelevant to plans maximising power from crude and coal, which is why the hand-verified
numbers held unchanged. The stored `spire-coast-full` plan keeps its region selector on
purpose. `SPIRE_COAST_NODES` is the second sanctioned field: the 18 `node:` ids that region
resolved to when the payback tests were written, frozen so only a node-table change moves them.

**`live` and `state`.** `state` is the committed projection, frozen; `live` is the newest save
on the machine, for tests that measure the tool's real answer. `live` skips on `SaveError`, so
a machine with the game and no save reports skips rather than errors. An MCP tool test hands
the tools its world through `use_world`, which replaces `app.load_world` (docs/mcp-surface.md).

**Speed.** `-n 8` is measured. On a 16-core, 32-thread machine with other work holding about a
tenth of it, the default set (2,668 tests) takes 36 to 39 s at 8 workers and the integration set
(1,185) a median 28 s, against 43 to 54 s and 31 s before the web apps were reused, the longest
tests spread and the sensitivity sweep made module-wide. Before the typing gate added its two
pyright runs, the default set took 134 s serial, 76 s at 2 workers, 42 to 45 s at 4 and 28 s at
8, 59% of linear at 8. More workers stopped paying long ago: every worker imports and collects
the suite alone, about 4 s before its first test, and when last counted, 16 and 32 workers were
slower than 8. `--dist loadfile` and `-p no:cacheprovider` were within noise of the default and
were not adopted; `--dist worksteal` was too, until the longest tests below were spread over
it. After a hardware change, re-measure `uv run pytest -q -n <k>` and the same with
`-m integration` for k in 4, 6, 8, 12, 16 and auto, three samples each.

**The whole-folder tests.** Three integration tests parse every save on the machine (vendor
parity, trailer arc lengths, lightweight records) and carry the `whole_folder` marker. They
used to be moved to the front of the collection on the belief that xdist then dealt them to the
workers first. It does not: the default `--dist load` hands each worker a contiguous run of a
quarter of its average share, 36 tests of the integration set, so all three landed on the first
worker and ran back to back, and that worker finished last. The suite now runs
`--dist worksteal`, which opens by dealing each worker, in turn, an equal run of what is left,
and later moves the tail of a busy worker's queue to an idle one. `conftest.py` puts each
whole-folder test at the head of a different worker's opening run (`heads_of_shares` in
`tests/support/fanout.py`), so the three start together on three workers. Its hook runs last,
because the split depends on the count `-m` leaves. The default run's two pyright runs in
`tests/architecture/test_typing.py` carry the `long` marker and are spread the same way: next
to each other in one worker's run, they ran back to back, 29 s of a 44 s default run.

Each whole-folder test fans its saves out through `tests/support/fanout.py` at a third of the
logical CPUs, so the three side by side about fill the machine: at 10 the integration set took
25 s, as at 16, for 50 CPU-seconds less. While they ran one after another, each at half the
CPUs, widths 4, 8, 16, 24 and 32 measured 43.9, 32.5, 22.6, 25.0 and 24.0 s. Starting them
apart rather than together, the second and third 36 tests behind the first, measured no better.
`in_order` returns results in submission order, so per-save messages and early stops match a
serial loop; `SATISFACTORY_TEST_FANOUT` overrides the width.

**The vendor parity bank.** While `pioneersav` was being written, its acceptance test was a diff
against the vendored GPL-3.0 parser, leaf for leaf. Deleting that library destroyed the diff, so
it was banked first: `tests/fixtures/vendor_parity.json` holds, per save and per projection key,
a digest of what the vendored parser produced in its last minutes, when the two agreed on all 20
keys of all 31 saves it could read. `tests/pioneersav/test_vendor_parity.py` replays that
comparison. It catches this parser drifting from the agreement every claim in
[savparse-notes.md](savparse-notes.md) rests on; it cannot catch a fault both parsers shared,
which is why the notes also record predicates measured against the bytes.

The bank is never re-recorded: re-banking against this parser would replace an independent
measurement with its own output. Instead every later schema is filtered back to the schema-11
shape through the explicit list `POST_11_ADDITIONS`, one entry per change and annotated with its
schema; a structural guess ("drop what the bank has never seen") would also absorb a field
emitted by mistake. A change confined inside a key that is dropped whole needs no entry: schema
14's pipe actor column, schema 15's spline tangents and schema 20's belt actor column (which
also moved the tangents from column 3 to 4) all live inside `belts` or `pipes`. Schema 17's
`power` is new and listed, but who is wired to whom has been `graph["power"]` since schema 11,
so a sidecar that reordered or dropped a power edge still fails the comparison. The list is
pinned in both directions against the committed fixture, so a new top-level key with no entry,
or an entry for a key that is gone, fails on a clone with no game.

Two entries are corrections of a banked key rather than additions, and both are undone instead
of retiring `inventories` from the comparison. Schema 16 moved the Personal Storage Boxes, the
HUB container and the Blueprint Designer's contents (10,667 units over 31 item classes on the
reference save) from `machine` to `storage`; `_unfix_16` subtracts them from `storage` and adds
them back to `machine` from this parser's own `storage` rows, exactly and in integers. The cost
is stated: a misread of one of those containers moves both values together and cancels, a
blindness confined to eight containers of one key. Schema 19 moved crate contents out of
`machine` into a `crate` bucket of their own (schema 18 had added `crates` and deliberately left
them); `_unfix_19` folds them back, and because nothing is subtracted a miscounted crate still
moves the digest.

### Architecture rules

`tests/architecture/` holds the rules that read the source rather than run it, standard
library only, so they pass on a clone with no game, no Node and no web extra. The one exception
is the type gate, which runs pyright from the dev extra ("Types" below). The shared graph
walker is `tests/support/import_graph.py`; every import node at any depth is an edge, so a lazy
import inside a method body is checked like a top-level one.

**Import direction** (`test_import_direction.py`). The layer table maps module prefixes to
`core`, `domain`, `presenters`, `interfaces` and the pseudo-layers `sdk` (mcp, pydantic, the web
stack) and `tools`. `WHITELIST` once held the violations that existed at the start of the
layering refactor; each died in a named phase, the list is empty, and a new entry has to be
argued for in the diff that adds it. Presenters are named a second time by literal package, so
editing the table cannot dissolve the rule the refactor existed for: domain and core return
data, never formatted text. `FORBIDDEN_PATHS` are the eight pre-refactor import paths, deleted
after the shims that forwarded them; a forwarding path is a second name for one module, and the
forwarding one is the one that goes stale. `LAYERED_HOMES` and `ROOT_ENTRIES` catch the failure
the edge walker cannot: a package folded away leaves no bad edge behind.

The parser `pioneersav` is a standalone library: it may not import the application, and
exactly one application module (`core.saveio.extract.parser`, which runs in the child process)
may import it. The subprocess boundary gives crash isolation, returns a 2.9 MB parse's memory to
the OS and keeps the projection small enough to commit; one convenience import would quietly
undo all three.

`tools/` is a layer above everything: a generator may read the standard library, the `gen`
extra, the `gpu` extra (CuPy, for `mapgen renders --gpu`), `core`, `mapgen` and itself, and no
part of the package may read a generator, which is not in the wheel, or the `gpu` extra. The measured exceptions are numpy, scipy and platformdirs (hard dependencies),
`pioneersav` (a one-shot CLI is the caller the subprocess boundary protects, not one it applies
to) and `domain.spatial`, because the generator that writes the terrain field reads the package
that reads it, so the two cannot disagree about the format.

**Optional extras** (`test_optional_extras.py`). `ooz`, `texture2ddecoder` and Pillow are the
`gen` extra, and optional means optional at import time: a clone without them imports every
module, runs this suite and serves the map. Only `core.gameassets` may name them, and only from a
function body. That proof is only as good as the AST, so the package may not use `importlib`,
`__import__` or `sys.path` either. Besides the standard library and `core` it may import numpy
and typing_extensions, both hard dependencies.

**Line caps** (`test_module_caps.py`). No module of the application or the parser passes 850
lines; routers and MCP tool modules stop at 650, generators outside `tools/mapgen` at 800
lines and 150 per function. `tools/mapgen/tests/test_architecture_mapgen.py` holds mapgen's
modules to 600 lines and 150 per function; a command held thin keeps a shrink-only ceiling at
its measured size. The caps count ruff's formatting, so mapgen keeps `# fmt: skip` to a
module-level literal table: a skip that packs a signature or a call onto fewer lines is a cap
measured on text ruff would not write. `api.py` was 2,174 lines and `planning.py` 2,615 before they were
split, and neither got there in one commit, so a cap is what makes "one module per concern" a
measurement. The general cap is what `core/gameassets/staticmesh.py` measured when it was the
largest module. Its record types have since moved to `meshdata.py`; the readers stay, because
`tools/mapgen` calls them through the module.

**Routers** (`test_router_registry.py`). A router may import the standard library, FastAPI,
`config`, `core`, `domain` and the web package's `serial` and `terrain` — never another router
(the shared thing belongs in `serial`), never `app` (a cycle, and the mount order is what keeps
`/openapi.json` byte-stable) and never a presenter (a formatted string in a payload is a
decision made in the wrong place). The one exception is the event stream importing the
watcher's event names. `ALL_ROUTERS` must match the directory, each router mounted exactly once
and only through the loop in `app.py`, because an unmounted router is a 404 nothing reports and
the tuple's order is the committed schema's path order. Every routed handler declares a
`response_model` or returns a `Response` subclass in its annotation; without one the endpoint's
`200` is `unknown` in `api/schema.d.ts`, and the page ends up typing it from observed payloads,
which is how a 438-line hand-written types file with wrong nullability once came about. The four
byte-serving endpoints declare `response_model=object`, the unconstrained body they have always
published, and return a `Response`; an exemption list stands for any later one, and an exemption
that stops being needed fails.

**The page** (`test_frontend_layout.py`). `static/` is build output: gitignored (it embeds
minified Leaflet, and the repository distributes no build), complete when present, every file
carrying the build banner except Leaflet's copied licence, and nothing else in it. No Python
names the frontend sources in code — the seam is the built directory — except the one
not-built instruction `app.py` serves at `/`; comments and docstrings may. Each module that calls
`registerFetch` is held in the bundle only by its bare import in `main.ts`'s FEATURES block, so
the two are checked against each other in both directions: a dropped line would build, type-check
and silently lose a map layer. The mechanism modules (`load.ts`, `registry.ts`, `layers.ts`, the
layer control and its pickers, `palette.ts`) import no module that fetches and no module that
imports them, since everything that draws reaches them and whatever they reached back would be
evaluated first; `registry.ts` imports types only. `palette.ts` holds the colour comparison and no
colour value, so every colour sits with its owner and its warrant.

**Prose** (`test_comment_budget.py`): see [comments.md](comments.md), rule 9.

### Types

`tests/architecture/test_typing.py` runs pyright over `src/` and `tools/` under
`[tool.pyright]` in `pyproject.toml`, in strict mode everywhere, and wants zero errors. Tests
are outside the type rules.

- **Strict mode, no exceptions.** `typeCheckingMode = "strict"` covers every module; there is
  no per-package list and no error budget. A comment that would take a line or a file out of
  the count (`# type: ignore`, `# pyright:`) fails the gate, and so would an unneeded one
  (`reportUnnecessaryTypeIgnoreComment`).
- **Two Pythons.** `pythonVersion = "3.13"` is the venv's own Python, because numpy's and
  scipy-stubs' stubs, as the venv installs them, are written for it: read as 3.11 they turn
  `Unknown` (numpy's `collections.abc.Buffer`, for one). A second run passes the oldest Python
  `requires-python` admits as `--pythonversion`, so a standard-library name that arrived later
  still fails; that run drops only the `reportUnknown*` rules a newer stub trips over.
  `pythonPlatform = "Windows"` is fixed so that every machine counts alike.
- **Every signature is typed.** ruff's ANN rules run over `src/` and `tools/`; tests are exempt.
- **The environment is part of the result.** pyright (`pyright[nodejs]`, which brings its own
  Node) and `scipy-stubs` are exact pins in the dev extra, because a new checker or stub release
  changes what it reports. The web and gen extras have to be installed too. The gate first
  checks the environment against the three extras and fails with `uv sync --all-extras` when it
  does not match. An import that does not resolve, or a package whose `py.typed` is missing,
  hides every error behind it, so those rules fail the gate as the environment's fault, with the
  rebuild to run, before any code error is counted.
- **A `cast` is counted.** It is a claim the checker takes on trust, so `CAST_BUDGETS` holds the
  number per area, and the number only goes down: the gate fails on a count above it, and on a
  count below it with the numbers to write. A file counts toward the longest key that holds it.
  Type the producer instead; `core/jsontypes.py` has `is_object_dict`, `is_object_list` and
  `is_object_sequence` to narrow an `object` without one.
- **A loaded document binds to a boundary type.** `json.load`, `pickle.load` and `tomllib.load`
  return `Any`, which strict mode does not see, so bound straight to a TypedDict they are an
  unchecked cast. The gate wants each one assigned to `JsonValue`, `object` or
  `dict[str, object]`; a check, or one named `cast`, narrows it from there. Going the other way,
  `to_json` and `to_json_object` copy a typed value into a checked `JsonValue`, and
  `require_object` and `require_list` narrow one with a `TypeError` for the wrong shape.
- **`extraPaths` is required.** The venv's editable install points at the main checkout, so in
  a git worktree pyright would resolve `satisfactory_mcp` to the main checkout's code;
  `extraPaths = ["src", "tools/mapgen/src", "."]` puts the worktree's own source first. The gate
  passes the interpreter running the tests as `--pythonpath`.
- **`typing.Any` is banned** in `src/` and `tools/` by ruff's TID251. Data crossing a boundary
  (`json.load`, a sidecar, a request body) is `JsonValue` or `JsonObject` from
  `core/jsontypes.py`, narrowed with `isinstance`, or cast once to a TypedDict where a schema or
  version check already guards the read. At runtime `JsonValue` is a named `TypeAliasType`
  rather than a string alias, because pydantic cannot resolve a string alias inside a response
  model. pyright reads the plain recursive alias under `TYPE_CHECKING` instead: pyright 1.1.414
  loses the `TypeAliasType`'s self-reference when another module evaluates `JsonObject` before
  `jsontypes` itself, and reports it in `jsontypes.py`. No module imports `Any` now. One that
  had to would be named on its own line in `[tool.ruff.lint.per-file-ignores]`; the gate fails
  on an entry that is no longer needed and on a glob.
- **The save parser's values.** `pioneersav` decodes every property, container element and
  trailer field into one `SaveValue` (`pioneersav/values.py`). The extractor reads them as that
  boundary type and narrows them with `extract/readers.py`'s `to_float`, `to_int` and
  `as_sequence`, which give the same answer and raise the same error as `float()`, `int()` and
  indexing. A field the projection carries exactly as the save wrote it is a `cast` to the
  schema's type.
- **A seam moves in one step.** A TypedDict is not assignable to a bare `dict`, nor the other
  way, so a producer cannot type its return while its callers still annotate `dict`. The
  producer declares the type and every caller reads it in the same change; a `cast` out to a
  loose type on one side and back on the other hides a mismatch from the checker.
  `ParsedObject.properties` stays loose, and `saveio.rows` takes a `Mapping[str, object]`,
  which accepts both.
- **Plan arguments** travel as `Mapping[str, object]`, or `dict[str, object]` where they are
  built (`recall_plan`, `with_overrides`). They become `solver.scenario.PlanKwargs`, the type
  `build_scenario` declares, by one `cast` where every key is already checked: `PlanArgs.kwargs`,
  `store.Plan.kwargs` and the call in `solver/prepare.py`. A read of one value from a loose
  mapping narrows it (`plan_args.sources_in` for `sources`) rather than casting it.
- **Empty dataclass fields.** `field(default_factory=list)` leaves the element type unknown in
  strict mode and ruff refuses a `lambda: []`, so a field names its own type:
  `field(default_factory=list[str])`.
- **Shapes.** Data with a fixed key set is a TypedDict, or a dataclass when it never leaves
  the process. The save projection's is `core/saveio/schema.py`: `Projection` and the rows of
  [save-projection.md](save-projection.md) §6.16, held against the committed fixture. A JSON
  shape a domain package builds is declared in that package's `views.py`, where the web
  publishes it from; wire rules 2 and 5 of [web-wire.md](web-wire.md) say why each one is a
  `typing_extensions.TypedDict` and why two with the same fields are one.
- **A handler says what it returns**: its `response_model`'s TypedDict, `| JSONResponse` where it
  can refuse. FastAPI reads the `response_model` and never the annotation once one is given, so
  the annotation changes nothing on the wire and lets pyright check the body. A field published
  as an open object is `Mapping[str, object]`, which pydantic describes exactly as it did `dict`.
  Where a domain function still returns a loose `dict`, or a `str` the wire closes into a
  `Literal`, the handler casts once and says why. A projection list is read guarded:
  `serial.object_rows` drops a torn row that is not an object and keeps the schema's row type.
- **Arrays and stubs.** A numpy array is typed by its dtype through `core/arrays.py`
  (`F32Grid`, `U8Grid`, `BoolMask` and the rest); the gate fails on a bare `ndarray` in an
  annotation. A plane that float arithmetic produces is a `FloatGrid`, because numpy's stubs
  widen float32 with a Python float to float64 while the run keeps float32. scipy is typed
  by `scipy-stubs`, and pyooz, which ships no types, by the local stub `typings/ooz.pyi`. CuPy
  ships none either: `typings/cupy/` covers the calls the render's CUDA kernels make, so the
  gate needs no `gpu` extra.
- **Platform branches test `sys.platform` itself** (`interfaces/web/childproc.py`): pyright
  narrows on that expression, not on a name that holds its value.
- **Speed.** One pyright run over 443 files took 20 s on one thread. With `--threads 8` it
  measured 7 s alone; 12 and 16 threads were no faster. Inside the parallel suite it took 10 s,
  on one worker. The gate makes two runs, one per Python.

### Code-quality scan

`sonar-project.properties` configures a SonarQube scan; Python coverage comes from
`coverage.xml` and the frontend's from its `coverage/lcov.info`, so both test runs go first.
The frontend's `npm test` ends by rewriting `lcov.info`'s source paths with `/`, since a
Windows run writes `\`, which the Linux scanner container cannot resolve (frontend/README.md,
"Tests"). The frontend's `test/` and mapgen's `tools/mapgen/tests/` are scanned as tests and
excluded from the sources.
The server is a local SonarQube Community container with its own Postgres, kept outside the
repository (for example a compose file in `%USERPROFILE%\.sonarqube-satisfactory\`) and
published on host port 9100. From the repository root in PowerShell, with an analysis token in
`%USERPROFILE%\.sonar-token`:

```powershell
uv sync --all-extras --all-packages
uv run pytest -q --cov --cov-report=xml
npm --prefix src/satisfactory_mcp/interfaces/web/frontend test
if (-not $env:SONAR_HOST_URL) { $env:SONAR_HOST_URL = "http://host.docker.internal:9100" }
$env:SONAR_TOKEN = (Get-Content "$env:USERPROFILE\.sonar-token" -Raw).Trim()
$scm = @(); if (Test-Path .git -PathType Leaf) { $scm = @("-Dsonar.scm.disabled=true") }
docker run --rm -e SONAR_HOST_URL -e SONAR_TOKEN -v "${PWD}:/usr/src" `
    sonarsource/sonar-scanner-cli sonar-scanner @scm
Remove-Item Env:SONAR_TOKEN
```

`SONAR_HOST_URL` is the server as the container sees it; set it first to scan against another
server. `-e NAME` without a value hands the container the variable, so the token never appears
on a command line. In a linked git worktree `.git` is a file naming a host path the container
cannot open, and the scanner aborts on it, so there the scan runs without SCM data.

**Security hotspots reviewed as safe.** The scan flags these for review; each is safe for the
reason given, so it is marked "Safe" in the scan rather than changed.

| Rule | Where | Why it is safe |
|---|---|---|
| S5332 (`http://`) | `config.web_url`, `interfaces/web/guard.refusal` | The web map binds to loopback only (`WEB_HOST = "127.0.0.1"`); the guard compares a request's `Origin` with that same plain-HTTP address. Nothing leaves the machine, so there is nothing for TLS to protect. |
| S4790 (weak hash) | `pioneersav/header.py`, `domain/advice/advisory.py`, `domain/planning/progress/stages.py` | No hash protects anything. The MD5 is the save format's own body digest, which the game checks; the SHA-1s name advisories and stage plans. Each call passes `usedforsecurity=False`. |
| S4828 (signals) | `interfaces/web/childproc.py` | `os.kill(pid, 0)` only asks whether a process exists. `kill_tree` ends the generator this server started, or adopted after matching its pid and creation time. |
| S5852 (regex backtracking) | `core/gamedata/normalize.py` | The input is the game's own Docs file, read from the local install: short description strings and class paths. |
| S5852 | `domain/collectibles/table.py` (`_GLUED_INDEX`) | Linear: the look-behind admits one start per run of digits. |
| S5852 | `domain/maps/jobs.py` | The input is the progress lines a map generator of this repository prints. |
| S5852 | `tools/collectibles/identity.py` | A developer tool over instance names from the game and the saves. |
| S5852 | frontend `api/client.ts` (`fillPathParam`) | The input is one of the page's own route templates, a constant. |

## Solver threads

Every `scipy.optimize.milp` and `linprog` call goes through `core/solverlane.run`, which runs it on
one of four daemon threads that live as long as the process. On Windows with CPython 3.13 and
SciPy 1.18, a thread that has run a HiGHS solve can spin forever while it exits, holding the GIL
and blocking every later thread start. The web server hits this when an idle AnyIO worker thread
retires: static files, `/api/*` and SSE all stop, and a py-spy dump shows the main thread in
`threading.start` beside a frameless thread holding the GIL. A loop that starts one thread per
solve reproduces it within about fifty solves; the same loop through the lanes ran 2,800 solves
without a stall. Solver threads therefore never exit before the process does, and new solver
code calls `solverlane.run` instead of the solver directly.

## Regenerating world data

You do not need any of this to use the project: every world table the server uses is committed
under `data/`. The generators exist so the tables can be rebuilt from your own installed game
after a map update, and so the map's imagery — which is the game's artwork and is therefore
**never committed** — can be produced locally. They need `uv sync --all-extras` and a
Satisfactory install. Approximate runtimes on one mid-range machine:

The map generators are one package, `tools/mapgen/`, with one command per output. Install it
with `uv sync --all-packages` and run `uv run python -m mapgen <command>`.
[Its README](../tools/mapgen/README.md) lists every command, its options and the package map.

| Command | Produces | Runtime |
| --- | --- | --- |
| `python -m mapgen heightmap` | 1 m heightfield (ground, bare terrain, top) and the rock pack → `data/local/heightmap/`, 75 MB | ~4 min |
| `python -m mapgen caves` | cave masks → `data/local/caves/` | 6 s sweep |
| `python -m mapgen paint` | landscape paint layers → `data/local/paint/`, 54 MB | ~25 s |
| `python -m mapgen renders` | terrain, satellite and painted base-map renders, with the live-sun light by default → `data/local/renders/`, 1.7 GB without the light | ~30 min without the light |
| `python -m mapgen artwork --enhance` | the game's map artwork as tiles (upscaled on a GPU) → `data/local/` | ~13.7 min |

The old entry scripts (`tools/gen_world_heightmap.py`, `gen_map_renders.py`,
`gen_map_image.py`, `gen_paint_layers.py`, `check_map_fill.py`) keep their paths as thin
shims to these commands, and every render sidecar records them as its generator. The web
map's job runner starts `python -m mapgen <command>` itself.

The other generators are scripts in `tools/`, sharing `mapgen.common` for the `--game` flag
and the `gen` extra check:

| Generator | Produces | Runtime |
| --- | --- | --- |
| `tools/gen_item_icons.py` | one PNG per item → `data/local/icons/` | ~14 s |
| `tools/gen_world_resource_nodes.py` | the node table → `data/world_resource_nodes.json` | ~4 s |
| `tools/gen_resource_nodes.py` | the served node table → `data/resource_nodes.json` | <1 s |
| `tools/gen_region_names.py` | the region-name grids → `data/region_names.json` | ~3 s |
| `tools/gen_world_collectibles.py` | the collectible table → `data/world_collectibles.json` | ~40 s for 99 saves |

Run them as `uv run --extra gen python tools/<name>.py`. Imagery and the heightfield land in
`data/local/`, which is gitignored and stays that way; the committed tables in `data/` only
change when the game's map does. `gen_resource_nodes.py` projects the world node table and
needs no game install. `gen_region_names.py` reads the map frame and the biome calibration
from `mapgen`. `gen_world_collectibles.py` is an entry point for the `tools/collectibles/`
package, which [world-collectibles.md](world-collectibles.md) explains.

`gen_item_icons.py` finds each item's `mSmallIcon` texture in `FactoryGame-Windows.utoc`
case-insensitively, because five items spell a directory differently from the container
(`Mam`, `Medkit`, `Cyberwagon`, and `Golfcart` twice). Two pixel formats occur, `PF_DXT5`
(BC3) on 634 of the 747 icons and `PF_B8G8R8A8` on 113, with no BC7; both decoders hand back
BGRA, which read as RGBA turns a copper ingot cyan instead of raising. On build 495413, 747 of
the 750 classes carrying `mForm` name an icon and all 747 decode; the other three name no
texture, so the page's text tile is their correct rendering. The default `--px 256` writes
41.1 MB of PNG; `--help` lists the smaller sides.

## Data provenance

Every world table under `data/` is a first-party extraction: facts, coordinates and identifiers
read out of a locally installed copy of the game by the generators in `tools/`, with no artwork
shipped in this repository as data — the map's imagery is generated locally into a gitignored
directory. (The README's screenshots show that imagery through the running tool; they are
documentation of this project, and the game content visible in them remains Coffee Stain's.)

Two third-party sources were used earlier and both retirements are kept on the record rather
than tidied away:

- The region layer was once traced from satisfactory.wiki.gg's Biome Map (CC BY-SA 4.0). It is
  now read from the game's own `FGMapAreaTexture` and `UFGMapArea` assets — boundaries and names
  alike — so no share-alike obligation reaches this repository.
- The resource-node table was once vendored from the MIT-licensed
  [rockfactory/satisfactory-logistics](https://github.com/rockfactory/satisfactory-logistics)
  node set. It is now read from the node actors of the game's own `Persistent_Level.umap`; the
  parity record of that replacement lives in `_meta.retired_mit_table` inside
  `data/world_resource_nodes.json`, pinned by tests.

The save parser has the same history: a GPL-3.0 library was vendored here until `pioneersav`
replaced it and it was deleted; the measured agreement between the two is banked in
`tests/fixtures/vendor_parity.json` and replayed by the test suite. No copyleft licence reaches
this repository.
