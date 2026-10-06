# SatisfactoryMcp — Design Spec

An MCP server that helps plan Satisfactory factories: recipe/resource lookup, save-file analysis of
progress and unlocks, spatial resource queries, and LP/MILP factory optimization.

**Status:** implemented. 42 tools, 4 resources, 3 prompts, 1,409 tests passing. See README.md for usage.
**Target game version:** 1.2.2.1 (`saveVersion 60`, `buildVersion 495413`).
**Licence:** PolyForm Noncommercial 1.0.0 — see [LICENSE](LICENSE) and [§13](#13-licence).

Every number in this document was measured from the local game files or the live save unless explicitly
marked `[UNVERIFIED]` or `[WIKI]`. Claims were produced by one agent and independently re-checked by a
second; ~90 corrections from that pass are folded in.

---

## The document set

This file is the **spine**: what the project is for, what was decided, where the data comes from, how
the tree is arranged, and the contract that turns the game's dump into numbers. Everything else lives
in `docs/`, and **section numbers are unique across the whole set** — a reference to `§8.5d` or `§6.11`
resolves through this table wherever it is written, in a document or in a docstring.

| sections | file | what it holds |
|---|---|---|
| §1–§5, §13, appendices | **this file** | scope, decisions, data sources, architecture, the normalization contract, the licence, and the reference world every number was measured against |
| §6, §6.9–§6.11, §13a, §13b | [docs/save-projection.md](docs/save-projection.md) | what the sidecar emits and how each fact in it was verified; the parser that replaced the vendored one, and the parity that can never be re-run |
| §7, §17, §18, §19 | [docs/spatial-and-map.md](docs/spatial-and-map.md) | coordinate frame, regions, node lookup and the selector language; the map's three base layers, the mode model and the heightfield's water channel |
| §8, §9 | [docs/planning.md](docs/planning.md) | the LP/MILP formulation, layout, commissioning, diffing against the save, and the hard-drive advisor |
| §10, §11, §12 | [docs/mcp-surface.md](docs/mcp-surface.md) | the tools with their transcripts, the context budget, caching, and the testing contract |
| §14, §15, §15b, §16, §16b, §19–§22 | [docs/parked.md](docs/parked.md) | open questions, parked work with the measurements that would otherwise be redone, and finished work kept as a record |
| §23 | [docs/residency.md](docs/residency.md) | what an autosave costs the two processes, and why the answer is a pre-warming watcher rather than a resident daemon |
| §24 | [docs/plumbing.md](docs/plumbing.md) | head lift and flow: which of the manual's constants the dump confirms, the one it does not carry, what the save measures about pipes, buffers and pumps, and the connection → head lift → flow rate ladder a missing fluid is diagnosed on |

Four documents carry no section numbers of their own. Two are ledgers rather than design:
[docs/backlog.md](docs/backlog.md) is every known defect with the commit that closed it, and
[docs/roadmap.md](docs/roadmap.md) is what to build next, what not to, and the measurements
behind both. The other two each state one thing in one place, so that it is not restated
anywhere else. [docs/selectors.md](docs/selectors.md) is the selector grammar — the three
vocabularies this surface uses to point at nodes, machines and places, the rules all three
obey, and the divergences between them that are deliberate.
[docs/fluids_model.md](docs/fluids_model.md) is the fluid model — how head lift propagates,
every constant marked as stated by the game, measured in-game, or assumed, the experiments
behind the measurements, what the model still gets wrong, and how to measure it again.

Older than the split and unchanged by it: [docs/savparse-notes.md](docs/savparse-notes.md) is the save
format as derived, byte by byte, and [docs/commissioning-questions.md](docs/commissioning-questions.md)
with its [answers](docs/commissioning-answers.md) and
[docs/multisite-questions.md](docs/multisite-questions.md) are the planner briefs §8.5d and §8.5k came
out of.

---

## 1. Scope

Driving use case, in the user's words: *"I'm stuck building an oil powerplant in the north of the map.
The MCP should understand what oil sources are in the north, what alternate recipes I have, what
production facilities I need and have unlocked, and be able to do calculations and optimization."*

Plus, added during design: **advise which alternate recipe to take after a hard-drive hunt.**

Five capabilities:

1. **Game data** — items, recipes (including alternates), buildings, rates, power. Never hardcoded.
2. **Save state** — unlocks, built factory, power, node occupancy, progression, MAM/hard-drive state.
3. **Spatial** — which resources are where, what's already tapped, where to build.
4. **Optimization** — LP/MILP over the recipe graph with correct byproduct handling.
5. **Hard-drive advice** — rank the actual pending offers by marginal value.

Explicit non-goals: editing saves (strictly read-only), blueprint generation, in-game overlays,
belt/pipe *routing* geometry (only throughput accounting and altitude deltas).

---

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Python 3.11**, `uv`-managed | User preference; best MILP ecosystem. |
| D2 | Game data from the **local `Docs/en-US.json`**, normalized in-process | Ships with the game, updates on patch, authoritative. No web API, no hardcoding. **No committed snapshot fallback** — the server is only ever run on a machine with the game installed, so a fallback would be untested weight and a second source of truth to drift. |
| D3 | **`Headers.zip` + `FactoryGame.usmap`** as the authority for save property names | The game ships its own C++ headers — property semantics are quoted, not guessed. |
| D4 | Save parsing via **GreyHak `sav_parse`** behind a **subprocess sidecar** | Supports `saveVersion 60` exactly; parses in 3.7 s. Sidecar for version-fragility isolation, crash isolation, and a tiny test fixture. |
| D5 | **`scipy.optimize.milp`** (HiGHS) | Fastest at scale, ships with scipy, no external binary, BSD/MIT. |
| D6 | **Per-item equality balance** with explicit export/sink whitelists | The only formulation that models stalling byproducts correctly. |
| D7 | **Exact geometry for computation, advisory names for labelling**; sources chosen by a selector language | No biome geometry ships with the game, so names must be hand-authored and cannot be trusted numerically. |
| D8 | **FastMCP over stdio**, compact TSV responses | Context budget is the binding constraint. |
| D9 | Read the **actual pending hard-drive offers** from the save | They are persisted; no need to rank an abstract pool. |

---

## 3. Data sources

### 3.1 `Docs/en-US.json` — the game data dump

`G:\SteamLibrary\steamapps\common\Satisfactory\CommunityResources\Docs\en-US.json`
10,643,994 B, **UTF-16LE with BOM** → `json.load(io.open(path, encoding='utf-16'))`.
Shape: list of 114 groups, each `{"NativeClass": "...FGRecipe'", "Classes": [...]}`. 2,868 classes total.

Load + normalize measured at **~50 ms cold**, so no disk cache is needed
(see [§11](docs/mcp-surface.md#11-caching)).

**Contains no version field of any kind.** `buildVersion` lives in the *save* header, not here. Cache
identity must therefore be `sha256(file)`, never a version number.

### 3.2 `Headers.zip` — C++ ground truth

`CommunityResources/Headers.zip`, 1.95 MB, 1,143 files (`FGResearchManager.h`, `FGSchematic.h`,
`Unlocks/*.h`, …). This is how save property names and their semantics are established — e.g. that
`mSavedOngoingResearch` stores *time remaining*, not an absolute timestamp.

Headers only; **no `.cpp` ships**. Behaviour not stated in a header or doc comment is inference and must
be labelled as such.

Companion: `CommunityResources/FactoryGame.usmap` (2.68 MB) — property-name map, useful for resolving
save properties the headers don't cover.

### 3.3 The save file

`%LOCALAPPDATA%\FactoryGame\Saved\SaveGames\<steamid>\<session>.sav`

**Worlds, not files.** Saves are grouped into worlds by **`saveIdentifier`** from the header — verified
stable across all 28 parseable files in the user's directory (`X2faPVKjX06VaRzClNv5KQ`), with
`sessionName` as the display label. `list_worlds` returns worlds; `select_world` / an explicit path picks
one. Within a world the default is **newest by mtime**, and every response states which file it read and
how old it is.

**Unsupported saves must be bucketed, not fatal.** Of 63 files in this directory, **35 fail to parse** —
pre-1.0 saves with `saveHeaderType` 1, 8 or 9, rejected at the header check. Of the 28 that parse, 25 are
`saveVersion 52` (v1.1.0.4–1.1.2.2) and 3 are `60` (v1.2.2.1). So:

- `list_worlds` reports parseable and unsupported counts separately, with the reason.
- A world may legitimately contain mixed save versions; the projection records the version it read.
- Cross-version diffing is therefore supported and useful ("what changed since December?").

> **Hazard: saves are live and rotate.** Three autosaves overwrite in place every ~5 min while the game
> runs. Between two agent runs 30 min apart the same filename went 44,269 → 44,258 objects, Iron Plate
> 9,837 → 8,855. Consequences, all normative:
> - Cache key **must** include `st_mtime_ns` and `st_size`.
> - A parse may tear mid-write — treat parse failure as expected, not exceptional.
> - Never present save-derived counts as stable facts across turns; re-read.
> - Autosaves can catch the factory mid-restructure, so diagnostics must distinguish "broken" from
>   "under construction".

### 3.4 Resource node type and purity — the one gap, now closed

Node actors serialize **only** `mResourcesLeft` and their transform. Resource type and purity are
**not in the save**, and no biome/region geometry is in `Docs.json`. So a static table is required.

**They are, however, in the game's packaged assets.** `FactoryGame-Windows.utoc`/`.ucas` contain
`Map/GameLevel01/Persistent_Level.umap`, where each node is a placed actor carrying `mResourceClass`
and `mPurity`; `CommunityResources/FactoryGame.usmap` ships specifically so external tools can
deserialize them. An earlier draft of this spec called this data unverifiable from the game files.
That was wrong — it was unextracted, not unverifiable.

`data/resource_nodes.json` is therefore a **projection of one first-party source**:
`data/world_resource_nodes.json`, the node actors of the installed build's `Persistent_Level.umap`
read out of the IoStore container by `tools/gen_world_resource_nodes.py` (via `core.gameassets`,
the same reader the collectibles and heightmap use). It carries the node set, resource
(`mResourceClass`), purity (`mPurity`), position (composed root-component transform) **and** the
satellite → fracking-core link — the `mCore` ObjectProperty every `BP_FrackingSatellite` export
ships, 118 of 118, a reference in the game's own data rather than an inference.

Two third-party sources preceded it, and both retirements are on the record rather than tidied away:

* a **GPL** SCIM-derived table supplied the well grouping until the `mCore` read replaced it;
* an **MIT** set vendored from rockfactory/satisfactory-logistics (`world_resource_nodes.mit.json`,
  itself an FModel dump of the same `Persistent_Level.umap`, cut from a 2024 build) supplied the node
  set until this repo read the package itself. Parity at retirement:
  **same 626-actor composition, purity equal on all 625 shared ids, resource equal on all 594
  comparable, 600 positions inside the old file's whole-centimetre rounding** — and every
  disagreement accounted for as the game moving the map after 2024: 25 nodes moved 9.5–80.4 cm
  vertically, one pure Limestone node renamed and moved 150 cm, both confirmed independently against
  saveVersion 52 vs 60 save actors. The full record is `_meta.retired_mit_table` in
  `data/world_resource_nodes.json`, pinned by `tests/test_nodes_provenance.py`.

> **The bug the two-source era found, and the mistake that hid it.** SCIM was missing
> `BP_ResourceNode11`, a pure Limestone node worth 480/min. The save proves it exists — 459
> `BP_ResourceNode_C` actors against SCIM's 458. It went unnoticed because the original validation
> checked the join in **one direction only**: every table entry appeared in the save (607/607), which
> says nothing about save nodes missing from the table.
> `test_node_table_matches_the_save_in_both_directions` still counts save actors against table rows
> per kind — the requirement outlived both sources it was written against. (That same node is the one
> the game later renamed; see above.)

Excluded on purpose: `BP_ResourceDeposit_C` (hand-mineable only, no extractor can be placed — the
world table emits no deposit rows and counts all 2,662 of them in `_meta.deposits`) and
`BP_FrackingCore_C` (produces nothing itself; referenced as `well_core` on satellites).

Geysers are labelled `Desc_Geyser_C`. The name exists nowhere in `Docs.json` — a geyser is not an
item, it is a placement target for the Geothermal Generator — and the asset carries no
`mResourceClass` for them, so the label is synthetic and both files say so.

**Coverage is measured, not assumed**: every `gen_world_resource_nodes.py` run sweeps all 4,521
`GameLevel01` packages and refuses to write if any emitted class is placed outside the persistent
level, so a map update that streams nodes into cells cannot silently shrink the table.

---

## 4. Architecture

```
SatisfactoryMcp/
  pyproject.toml   README.md   DESIGN.md   docs/
  data/
    region_names.json             # GENERATED layer 2: label rasters + confidence, from
                                  # the game's own map areas (256 m served, 64 m for lookups)
    resource_nodes.json           # GENERATED: 608 nodes, type/purity/position, with provenance
    world_resource_nodes.json     # GENERATED SOURCE: the game's own node actors, §3.4
    world_collectibles.json       # GENERATED: map placements + per-instance collected state
    local/                        # the reader's own map renders and tiles; gitignored,
                                  # never shipped, and the page works without it
  src/satisfactory_mcp/
    server.py          # thin: imports the tool modules, re-exports, main()
    config.py          # env: SATISFACTORY_DOCS, SATISFACTORY_SAVES, cache dir
    core/              # knows nothing about anything above it
      gameassets/      # GENERATION-TIME only, used by tools/gen_*.py and nothing else
                       # (§19, docs/parked.md):
                       # iostore.py (the game's own .utoc/.ucas container, Oodle
                       # decompressor injected) + packages.py (a cooked package's
                       # exports, property tags and transform chain)
                       # provenance.py (which build an artifact was cut from, and the
                       # staged rename that stops one saying two things at once)
                       # textures.py (a mip chain's length, BC1 -> RGBA) +
                       # pyramid.py (tiles/{z}/{x}_{y}.png, cut and renamed into place)
      gamedata/        # loader.py (UTF-16 read, NativeClass grouping)
                       # uestruct.py (UE struct-string parser)
                       # normalize.py (-> items / recipes / buildings / schematics)
                       # model.py  search.py  footprint.py  constants.py
      saveio/          # projection.py: spawns the extractor, validates, caches
                       # extract.py: runs IN the child, builds the schema-16 projection
      text.py          # num + plural ONLY — the two helpers domain may reach
    domain/            # returns dataclasses and dicts, NEVER formatted text
      world/           # state.py: WorldState, a thin aggregate over the facets
                       # identity  inventory  census  carriers  water  sites  flow
      progression/     # unlocks  phases  research  harddrives  shards
      power/           # report.py: PowerLedger
      factories/       # model  build  structure  identity  cohere  labels
                       # select  query  health  trace  resolve  floors
      spatial/         # geo  nodes  regions  select  maplink  origin
                       # ranking  elevation  heightfield
      collectibles/    # table  removed  service
      planning/        # optimize  scenario  prepare  slice  diff  layout
                       # supply  bom  fit  store  byproducts  compare  carrier
                       # advisor  commission  materials  sensitivity  sites  trunks
                       # recall  report
                       # + one *_service module per tool-sized use case
    presenters/
      text/            # ALL response formatting: primitives.py (TSV, envelopes,
                       # truncation) + one module per concept
    interfaces/
      mcp/
        app.py         # the mcp object + what more than one tool group needs
        tools/         # one module per concern; importing it registers everything
          gamedata.py  world.py  progression.py  factories.py
          spatial.py   planning.py  harddrives.py  resources.py  prompts.py
      web/             # optional [web] extra: app.py (create_app)  serial.py  terrain.py
                       # routers/ (one module per concern, ALL_ROUTERS is the mount order)
                       # __main__.py (the console script)  watch.py (save-file SSE)
        frontend/      # BUILD-TIME ONLY: the page's TypeScript, built by Vite into
                       # static/ and excluded from the wheel
        static/        # UNTRACKED: the bundle the build writes; gitignored — no game
                       # assets, no dependency's compiled code in the tree
  src/pioneersav/      # our parser: reads all 66 saves, six saveVersions. A standalone
                       # library — it imports nothing from satisfactory_mcp, and only
                       # core/saveio/extract.py imports it, inside the child process
  tools/               # a package, not a directory of loose scripts: gen_*.py, plus
                       # _common.py (DEFAULT_GAME, the shared --game parser, require_gen)
  tests/
    fixtures/          # tiny Docs slice + ~9 kB save projection (committed)
```

**Layout rules.** Imports run one way: `core` knows nothing, `domain` knows `core`, `presenters` know
`domain`, `interfaces` know everything. Only the interface layer may import the MCP SDK. All formatting
lives in `presenters/text/` — context efficiency is cross-cutting and silently regresses if each tool
formats its own output, so a domain function returns a dataclass and a `render_*` function turns it into
the TSV a model reads. The two exceptions are `core/text.py`'s `num` and `plural`, which domain code may
use for note strings it embeds in its own results. `server.py` contains no logic; it stays at the package
root because the console script names `satisfactory_mcp.server:main`.

None of that is checkable at runtime — a lazy `import` three frames deep inside a method body loads fine
and violates the architecture silently. So `tests/test_architecture.py` parses every module with `ast` and
looks at *every* import node at *any* depth. It runs stdlib-only in under a second, and its whitelist of
tolerated violations is empty.

**The shims are gone.** `docs/`, `save/`, `graph/`, `spatial/`, `planning/`, `tools/`, `app.py` and
`render.py` survived the moves for exactly one commit, as alias packages that re-registered each old
submodule name against the module object at its new home. That bought the test suite a gradual move
without a 2,000-line import diff landing on top of the moves themselves. Every caller now spells the
layered home, the old names are deleted, and `test_the_old_paths_stay_deleted` inverts the ratchet: a
path whose only job is to forward an import is a second name for one module, and the forwarding one
always goes stale. `git log --follow` reaches through every move.

**Generation time is not runtime.** `core/gameassets/` and `tools/` exist to cut the artifacts under
`data/` out of the installed game; nothing the server answers a request with goes through them. They sit
in `core` because four generators share them, and `tools/` is a package so the suite imports a generator
by name rather than loading a file by path. Their decoders — `pyooz`, `texture2ddecoder`, `pillow` — and
the render caches' codec, `zstandard`, are the optional `gen` extra, **optional at import time**: the
server, the parser, the domain and the whole test suite run on a machine with none of them installed, and
`tests/test_architecture.py` reads the AST to keep it that way. The full record is §19, in
[docs/parked.md](docs/parked.md).

**The web adapter.** `interfaces/web/` is a *sibling* of `interfaces/mcp/`, not a layer above it: both are
thin adapters over the same domain, and neither imports the other — the web app duplicates the two-line
`lru_cache` game loader rather than reach into the MCP app for it. It answers JSON instead of TSV, because
its reader is a browser and not a model, and it converts every coordinate from the save's centimetres to
metres on the way out. `create_app(state_loader, game_loader)` takes both loaders as arguments so the whole
HTTP surface is testable against the committed fixture projection with no game install and no `.sav`.
FastAPI and uvicorn live in the optional `web` extra and may be imported only from this package, which is
the same AST-checked rule that confines the MCP SDK to `interfaces/mcp/`. The page itself is TypeScript
under `web/frontend/`, built by Vite into the **untracked** bundle at `web/static/` — gitignored, because
minified Leaflet is a dependency's compiled code and the repository does not carry it. A fresh clone runs
`npm ci && npm run build` there once (until then the server answers `/` with that instruction and the
JSON API is unaffected), and `tests/test_architecture.py` insists both that nothing under `static/` is
ever tracked and that every built file carries the build banner. The map ships no game textures and no
map tiles: Leaflet is bundled into that page with its BSD-2-Clause licence beside it in `static/vendor/`
— copied at build time from `node_modules/leaflet/LICENSE`, so every build stays self-compliant for
anyone who ever distributes one — and everything drawn on it is data the save and the docs dump already
contain.

### 4.1 The save seam

`core/saveio/projection.py` is the **only** module that knows a save parser exists. It spawns
`python -m satisfactory_mcp.core.saveio.extract`, which imports `pioneersav` and prints a JSON
projection to stdout. `-m` rather than a constructed file path, with `src/` put in front of the child's
`PYTHONPATH`: the child then resolves the extractor and the parser through the same import machinery
this process used, so it cannot run a copy that has drifted.

Reasons (licensing is *not* one of them — it never was, and the parser is ours now):

- **Version fragility.** The parser refuses an unrecognised `saveVersion` rather than guessing. Every
  game patch can break it until the format is re-derived. One seam = one module to touch.
- **Crash isolation.** A parser crash or a torn autosave can't take down the server.
- **Memory.** A 2.9 MB save inflates to far more while it is walked, and a child process hands every
  byte of it back to the OS on exit. A long-lived stdio server does not otherwise get that.
- **Test seam.** The projection is ~9 kB of JSON. Commit that instead of a 2.9 MB `.sav`; the entire test
  suite then runs with no game install and no parser.

The parser itself is `src/pioneersav`, a standalone package beside the application rather than inside
it: it implements a file format and knows nothing about factories, plans or MCP. `tests/test_architecture.py`
pins both halves — `pioneersav` imports nothing from `satisfactory_mcp`, and `core/saveio/extract.py` is
the only module in the application allowed to import `pioneersav`, because everything else reaches it
through the subprocess.

Subprocess overhead measured at **~60 ms**, and only on cache miss.

> **The sidecar must never inherit the server's stdin.** `subprocess.run(capture_output=True)`
> redirects stdout and stderr but leaves stdin inherited — and the MCP server's stdin *is* the client's
> JSON-RPC pipe. Handing that to a child means anything touching it blocks for ever, and could consume
> bytes the client sent to the server. The symptom was maximally unhelpful: every save-reading tool hung
> until its 180 s timeout, with no error, no log and no partial output, and **only** when launched as a
> real MCP server. Calling the same functions directly always worked, because then stdin is a terminal.
> Fixed with `stdin=subprocess.DEVNULL`; `tests/test_sidecar_spawn.py` pins it, along with the
> neighbouring trap that `sys.executable` must be an interpreter and never the console script, which
> would spawn a second MCP server that waits on stdin and emits nothing.

---

## 5. Normalization contract

### 5.1 UE struct-string parser

Most fields are UE struct strings. Grammar, exhaustive over the dump:

```
value := list | quoted | bare
list  := '(' entry (',' entry)* ')'
entry := (KEY '=')? value
```

Keyed lists → `dict`, positional → `list`, leaves → `str`. Recursive descent, quote-aware —
`split(',')` and regex approaches are wrong because `(`, `)`, `,` occur inside quotes.

Measured: 14,666 struct-shaped fields, **0 parse errors**, 0.15 s total, max depth 5, largest field
6,147 chars.

Six traps, all normative:

1. **Three fields arrive as real JSON, not strings**: `mUnlocks`, `mSchematicDependencies`, `mFuel`.
   Their *inner* fields are still struct strings. The parser must accept `str | list | dict`.
2. **UE omits struct members equal to their default.** `Schematic_Goat_C.mCost` has no `Amount`.
   Default missing `Amount` to 0.
3. **`FullName` is absent on 1,316 of 2,868 classes.** Key on `ClassName` only.
4. **`mProducedIn` has two token shapes** — asset paths (943) and raw native names (51). Take the text
   after the last `.` and strip the `'...'` wrapper.
5. **Single-entry lists**: `((A=1))` → `[{...}]` but bare `(A=1)` → `{...}`. Wrap dicts before iterating.
6. **Skip whitespace after delimiters** — 16 fields use `, `.

### 5.2 Items and fluids

`is_fluid = mForm in {RF_LIQUID, RF_GAS}`. Scan **all 13 native classes carrying `mForm`**, not just
`FGItemDescriptor`/`FGResourceDescriptor` — otherwise `Desc_LiquidBiofuel_C` (biomass) and the nuclear
rods are missed. `RF_INVALID` (555) = building/vehicle descriptors, not items.

15 fluids: 10 `RF_LIQUID` (Crude Oil, Water, Fuel, Turbofuel, Heavy Oil Residue, Nitric Acid, Sulfuric
Acid, Alumina Solution, Dissolved Silica, Liquid Biofuel), 5 `RF_GAS` (Nitrogen, Rocket Fuel, Ionized
Fuel, Dark Matter Residue, Excited Photonic Matter). All `Desc_Packaged*` and `Desc_Fuel_C` are solid.

**Fluid amounts are ×1000** — and not always whole thousands (`Recipe_Battery_C` has Sulfuric Acid 2500).
Divide as **float**, never integer. `mEnergyValue` follows the same rule: MJ **per litre** for fluids,
per item for solids.

### 5.3 Recipe partition

```
kind = 'building' if product[0] in FGBuildingDescriptor      # 547
     else 'part'  if mProducedIn ∩ MANUFACTURERS            # 291
     else 'manual'                                          # 34
```

`MANUFACTURERS` = `FGBuildableManufacturer` (8) **∪ `FGBuildableManufacturerVariablePower` (3)** = 11
classes. Omitting the second group silently loses 43 Particle-Accelerator/Converter/Quantum-Encoder
recipes.

**Assert `872 == 547 + 291 + 34` at build time** — it will break loudly on a game patch.

- `FGCustomizationRecipe` (106) is a **separate NativeClass**, not inside the 872. Filtering is free.
- Do **not** use `mGameplayTags == "Recipe.Part"` — against the correct 11-class set it misses 25 and
  wrongly includes 22.
- No recipe is craftable in more than one machine (verified 0), so `machine` is a scalar.
- Of the 291, 24 are Packager pack/unpack → 267 real transformations.

### 5.4 Alternates and the unlock gate

**`is_alternate` = reachable from a schematic with `mType == 'EST_Alternate'`** (109 schematics →
109 distinct recipes). Never use `"Alternate" in ClassName` — that set is 110 and wrong in both
directions: `Recipe_Alternate_Turbofuel_C` and `_EnrichedCoal_C` come from the Sulfur MAM tree,
`_PolyesterFabric_C` from a Mycelia research, `_Silica_Distilled_C` is `EST_Custom`; meanwhile
`Recipe_PureAluminumIngot_C` *is* a hard-drive alternate with no `Alternate` in its name.

Schematic → recipe: `mUnlocks[] → Class == 'BP_UnlockRecipe_C' → mRecipes` (399 schematics). Handle:

- `BP_UnlockBlueprints_C` also carries `mRecipes`.
- `BP_UnlockInventorySlot_C` — 2 schematics, **zero recipes** (both "Inflated Pocket Dimension").
- `BP_UnlockSchematic_C` (44) — **do not expand recursively.** Doing so pulls in 23 `CBG_*`
  customization schematics and inflates the derived set to 514, of which 113 aren't actually unlocked.
  One deliberate exception: `Quartz_Purified` → `Silica_Distilled`.
- Filter event schematics via `mRelevantEvents` (`(EV_Christmas)`) or 16 FICSMAS recipes leak in.

Recipe count per alternate schematic: `{0 recipes: 2, 1: 105, 3: 2}`. **Two** schematics carry 3
(`Schematic_Alternate_TurboHeavyFuel_C` and `_TurboBlendFuel_C`, each bundling
`Recipe_PackagedTurboFuel_C` + `Recipe_UnpackageTurboFuel_C`).

> **The unlock gate is `FGRecipeManager.mAvailableRecipes`, read directly from the save.** Do not
> reconstruct it from schematics. The intersection method over-reports by 8 on the real save (134 vs 126
> true unlocked part recipes), because recipes can arrive by more than one route — e.g. Charcoal and
> Biocoal are available via `Schematic_Alternate_EnrichedCoal_C` even though their own alternate
> schematics are unpurchased.
>
> **31 of the 405 entries in `mAvailableRecipes` have no `FGRecipe` in `Docs.json`** (24 `Recipe_Swatch_*`,
> 5 `Recipe_Material_*`, 2 skin recipes). Use `.get()` and filter customizations, or lookups `KeyError`.

### 5.5 Rate formulas

All from `Docs.json` fields.

```python
qty = raw / 1000.0 if item.is_fluid else raw
per_min = qty * 60 / recipe.mManufactoringDuration  # typo is in the data
cycle_s = mManufactoringDuration / (clock * building.mManufacturingSpeed)
```

`mManufacturingSpeed` is 1.0 for all 11 machines; keep it in the formula.
`mManualManufacturingMultiplier` must be read **per recipe** — only 219 of 291 are 1.0.

**Extraction:**

```python
base = mItemsPerCycle * 60 / mExtractCycleTime
if RF_LIQUID in mAllowedResourceForms or RF_GAS in mAllowedResourceForms:
    base /= 1000  # fluid-only; applying it to miners is 1000x wrong
actual = base * PURITY_MULT[purity] * clock
```

| building | impure | **normal** | pure | power |
|---|---|---|---|---|
| Miner Mk1 | 30 | **60** | 120 /min | 5 MW |
| Miner Mk2 | 60 | **120** | 240 /min | 15 MW |
| Miner Mk3 *(locked)* | 120 | **240** | 480 /min | 45 MW |
| Oil Extractor | 60 | **120** | 240 m³/min | 40 MW |
| Water Extractor | 60 | **120** | 240 m³/min | 20 MW |
| Well Extractor | 30 | **60** | 120 m³/min | **0 MW** |
| Well Pressurizer | — | — | — | **150 MW** |

There is only **one tier** of Oil Extractor. Wells: `Σ_satellites(60 × purity) × pressurizer_clock`; the
pressurizer produces nothing and costs a flat 150 MW.

**Power:**

```python
consumption = (
    base_MW * clock**mPowerConsumptionExponent * boost**mProductionBoostPowerConsumptionExponent
)
boost = mBaseProductionBoost + n_sloops * mProductionShardBoostMultiplier
```

> **Read `mPowerConsumptionExponent` per building. Never derive it.** It is `1.321929` for
> manufacturers, extractors, water pumps and fracking, but **`1.600000` for generators**, pipeline pumps,
> storage, rail — and for `Build_DroneStation_C`, which is clockable and draws 100 MW yet uses 1.6. Also:
> the shipped literal `1.321929` is **not** `log2(2.5)`; they differ by 9.05e-7. Don't "correct" it.

Underclocking uses the same formula (`0.5**1.321929 = 0.3997`). `mMinPotential = 0.01`.
Only **24** buildings have `mCanChangePotential = True` (62 merely carry the field).

**Somersloops:** `mProductionShardBoostMultiplier` is **per building** — 1.0 (Constructor/Smelter/
Packager, 1 slot), 0.5 (Refinery/Assembler/Foundry, 2 slots), 0.25 (Manufacturer/Blender/Hadron/Quantum
Encoder, 4 slots). Always products to max 2×. Power exponent 2.0, so **output ∝ boost, power ∝ boost²** —
the first sloop in a machine is always the cheapest, and 1 sloop × 36 refineries beats 2 × 18.
`mCanChangeProductionBoost = True` on exactly 10 buildings (the 11 manufacturers minus Packager);
extractors and generators cannot take sloops.

**Variable power** (Particle Accelerator, Converter, Quantum Encoder — `mPowerConsumption = 0`): draw
comes from the *recipe*. `min = mVariablePowerConsumptionConstant`,
`max = const + mVariablePowerConsumptionFactor` (**Factor is a range, not a multiplier** — proven because
building-level `mEstimatedMininumPowerConsumption`/`Maximum` exactly bracket the recipe values).

`mPowerConsumption` is normally a float but **`Desc_Locomotive_C` serializes it as a struct**
(`(Min=25,Max=110)`), so a blanket `float()` crashes.

**Generators:**

```python
fuel_per_min = mPowerProduction / energy_MJ * 60
water_m3_min = (
    mPowerProduction * mSupplementalToPowerRatio * 60 / 1000
)  # iff mRequiresSupplementalResource
```

| generator | MW | fuel | water |
|---|---|---|---|
| Fuel-Powered | **250** | 20 m³/min Fuel · 7.5 Turbofuel · 4.167 Rocket · 3 Ionized | **none** — `mRequiresSupplementalResource = False`, verified |
| Coal | 75 | 15 Coal · 7.143 Compacted · 25 Coke | **45 m³/min** |
| Nuclear | 2500 | 0.2 U-rod → 10 waste/min | **240 m³/min** |
| Biomass (automated) | 30 | Leaves 120 · Wood 18 · Solid Biofuel 4 | none |
| Geothermal | variable | — | none |

Geothermal: `mVariablePowerProductionFactor = 200` is the **normal**-geyser average;
`avg = 200 × purity_mult`, range 0.5–1.5× avg. Not clockable.

> **Docs gap:** `Build_GeneratorBiomass_C` and `Build_GeneratorIntegratedBiomass_C` (10 built in the save)
> **do not exist in `Docs.json`** — only `Build_GeneratorBiomass_Automated_C` does. Needs a documented
> fallback, not a `KeyError`.

Generators are modelled **linear in clock** for both output and fuel (energy-conserving; `Docs.json` has
no production exponent, and the 1.6 there is an inherited default on a zero-consumption building).

**Throughput:** belts `mSpeed / 2` → 60 / 120 / 270 / 480 / 780 / **1200** (Mk6 locked).
Pipes `mFlowLimit × 60` → **300 / 600** m³/min. Both self-corroborated by each building's own
`mDescription` prose — assert that at build time.

### 5.6 Hardcoded constants register

Four values are **not** in `Docs.json`. They live in one module, each with a comment saying so, and each
unit-tested. Nothing else may be hardcoded.

| constant | value | justification |
|---|---|---|
| `PURITY_MULT` | `{impure: 0.5, normal: 1.0, pure: 2.0}` | Every extractor's `mDescription` states the base rate as the *normal* rate, and the computed base matches exactly. |
| `POTENTIAL_SHARD_SLOTS` | `3` → max clock 2.5 | `Desc_CrystalShard_C.mExtraPotential = 0.5` is in Docs; the slot count is not (`mPotentialShardSlots = 0` everywhere). `[WIKI]` for the 3. Corroborated by the save: 447 buildings carry an `InventoryPotential`, 41 are non-empty, and their contents are exactly `{1: 6, 2: 16, 3: 19}` — **none holds 4**, and the highest clock in the world is 2.5. |
| `BELT_SPEED_TO_IPM` | `0.5` | Cross-checked against belt `mDescription` at build time. |
| `FLUIDS_CANNOT_BE_SUNK` | `True` | Physical rule that contradicts the data. **Confirmed by the user.** |

> **The fluid-sink conflict — resolved: fluids cannot be sunk.** `Docs.json` says Heavy Oil Residue has
> `mResourceSinkPoints = 30, mCanBeDiscarded = True`, as do Fuel (75), Water (5), Turbofuel (225), so a
> purely data-derived rule would permit sinking fluids. It is wrong: the AWESOME Sink has a **conveyor**
> input. Fluids must be packaged first (`Desc_PackagedOilResidue_C` is solid, 180 pts). Conversely
> `RF_SOLID` alone is too permissive — 26 solids have 0 sink points and 15 have `mCanBeDiscarded = False`.
>
> **Rule:** `sinkable = is_solid AND mResourceSinkPoints > 0 AND mCanBeDiscarded`.
> The solid half is data-derived; the fluid half is the hardcoded constant above, and it is the
> conservative direction — a plan that never relies on dumping a fluid cannot stall on one.
> The AWESOME Sink itself draws **30 MW** and must be charged in the power balance.

**The shard maths lives with the constant, not next to it.** `max_clock()` and `shards_for_clock()`
are in `constants.py` because they are the only two places `POTENTIAL_SHARD_SLOTS` meets data:
`max_clock = 1 + slots × mExtraPotential` and `shards = min(slots, ceil((clock − 1) / mExtraPotential))`.
Writing `2.5` as a literal anywhere would bury the one game-knowledge input inside a derived number.
Two traps, both found by measurement:

- **Round before ceiling.** Saved clocks are floats and 2.0 arrives as 1.9999999 often enough that a
  bare `ceil()` bills a 200% machine for a third shard.
- **`Desc_WAT1_C` (the Somersloop) shares the `FGPowerShardDescriptor` native class.** Selecting the
  class counts Somersloops as overclocking capacity. It has `mExtraPotential = 0` and
  `mExtraProductionBoost = 1`, so filtering on `extra_potential > 0` separates them without either
  class ever being named in code.

---

## 13. Licence

**PolyForm Noncommercial 1.0.0**, the full text in [LICENSE](LICENSE) with the Required Notice
(`Copyright Lukas Szimtenings (https://github.com/lukszi/SatisfactoryMCP)`). Free for any
noncommercial purpose; commercial use needs a separate licence from the owner. It covers this
repository's code, tooling and extracted tables and grants nothing over the game's content —
Coffee Stain Studios retains all rights to Satisfactory and its assets.

The section used to say "none — private project, all rights reserved", and the history that made
publication clean is kept on the record: the vendored GPL-3.0 parser `sav_parse` was replaced by
the first-party `pioneersav` and deleted (parity banked in `tests/fixtures/vendor_parity.json`),
the SCIM-derived purity data and the wiki-traced region geometry were likewise replaced by
first-party extractions, and README.md's provenance section states the current posture in full.
No copyleft licence reaches this repository. The web map compiles Leaflet (BSD-2-Clause) into
its untracked bundle at build time and copies the Leaflet licence beside it.

---

## Appendix A — current save state

`Han Solo`, 315 h, vanilla, `saveVersion 60` / `buildVersion 495413`.
**Snapshot only** — this is a live rotating autosave; every count drifts.

**Progression:** Current game phase `GP_Project_Assembly_Phase_3`, target `Phase_4`, and
`mTargetGamePhasePaidOffCosts` is empty — **nothing delivered toward Phase 4 yet**, so the whole of
Assembly Director System 4000 / Magnetic Field Generator 4000 / Thermal Propulsion Rocket 1000 /
Nuclear Pasta 1000 is outstanding. The deprecated `mGamePhaseCosts` array *also* claims 500 Modular
Engine and 100 Adaptive Control Unit are owed on Phase 3; that is **frozen and wrong** ([§6.4](docs/save-projection.md#64-space-elevator-phases--two-records-and-only-one-is-alive)).
Tier 6 fully complete; **tier 7 is 3/5** (Hazmat Suit and Hoverpack outstanding). Phase 5 costs are
absent from the save entirely. 226 purchased schematics, **405 available recipes**, 60 inventory slots.

**Power Shards: 22 free, 97 committed, 119 owned.** The 97 are read from the `InventoryPotential` of the
41 overclocked buildings; 2 of those hold a shard their current clock does not use.

**Alternates: 30 recipes unlocked** of 109. Oil-relevant **held**: Recycled Plastic, Recycled Rubber,
**Diluted Fuel** (Blender), **Diluted Packaged Fuel**, **Heavy Oil Residue**, **Polymer Resin**, plus
**Turbofuel** and Compacted Coal (both via the Sulfur MAM tree, not hard drives).
Oil-relevant **missing**: Turbo Heavy Fuel, Turbo Blend Fuel, Coated Cable.

**Unlocked but never built:** Blender (0), Geothermal Generator (0), Alien Power Augmenter.
**Locked:** resource wells (Pressurizer), Miner Mk3, Conveyor Mk6.

**Built:** 36 Refineries, 32 Coal Generators, 23 Water Extractors, 20 Fuel Generators, 15 Packagers,
13 Oil Extractors, 10 biomass generators — **all 10 paused**. 16 buildings paused in total; 46 machines
over/underclocked; 8 Smelters in the north with **no recipe set**.

**Installed extraction:** crude **2,070 m³/min** across 13 pumps (1,230 north + 840 west, some at 250%
clock); coal 2,400/min from 8 miners; **sulfur only 30/min** from one impure node — the binding
constraint on every turbofuel route.

**Northern crude:** 4 clusters. Of the 13 northern nodes, **10 are already tapped**; the remaining
headroom is 3 nodes / 240 m³/min at x ≈ 147,000, well east of the build cluster at x ∈ [−44k, +57k].
The map has 30 pumpable oil nodes (8 pure / 12 normal / 10 impure) plus 18 well satellites that are
unreachable while wells are locked.

**Hard drives: 25 unclaimed, each a live 2-way choice** (50 distinct schematics, no duplicates, all
`EST_Alternate`, all dependencies met). Drive **25 has exhausted its reroll**; the rest have 1 each.
Plus 1 loose drive in the Dimensional Depot. Nothing currently being researched.

Notable pending offers: **HD9** Turbo Heavy Fuel *vs* Steel Rod · **HD25** Turbo Blend Fuel *vs* Leached
Caterium Ingot · **HD34** Coated Cable *vs* Charcoal · **HD17** Plastic Smart Plating *vs* Steel Canister ·
**HD15** Biocoal *vs* Coated Iron Canister.

Of the 109 alternates, **24 have unmet `BP_SchematicPurchasedDependency_C` gates** — everything behind
Schematic_8-x/9-x (Diamonds, Dark Matter, nuclear, Electric Motor …), since the highest purchased
progression schematics are 7-1/7-2/7-4-1/7-5.

---

## Appendix B — the oil finding

The reason the northern oil plant is hard is not layout. It's that **every crude→fuel route the user has
unlocked emits Polymer Resin**, resin only terminates in plastic or rubber, and Recycled Plastic +
Recycled Rubber *create* both from fuel rather than absorbing them. With MW as the only permitted export
the LP abandons crude entirely and returns **8,610 MW from coal** — the oil is worthless.

Given a solid outlet the picture inverts. Best route on current unlocks is **Alt Heavy Oil Residue →
Diluted Fuel** (Blender): 60 crude → 160 Fuel, **30.00 net MW per m³/min crude** versus 7.58 for the base
Fuel recipe — **3.96×**. Diluted Packaged Fuel is a Blender-free fallback, worse by ~11%, needing no new
building type.

**Design consequence, and the user's decision:** exports are `{__MW__, Plastic, Rubber}` with minimum
target rates, not MW alone. The resin then terminates in product the user wants, and the plant is both
feasible and worth building. Water is the next binding constraint (~11 Mk2 pipes at scale, plus more
Water Extractors), and the first Blender has to be built.

Two unpriced upsides the first analysis missed: **4 northern geysers** are worth ~675 MW of fuel-free,
byproduct-free power via the already-unlocked Geothermal Generator, and the already-unlocked **Alien Power
Augmenter** is worth ~686 MW per somersloop versus ~426 MW for production boost — so sloops probably
shouldn't go into refineries at all.
