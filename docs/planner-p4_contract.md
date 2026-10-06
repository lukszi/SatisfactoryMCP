# Planner P4: the contract

The build contract for phase P4 of [planner_vision.md](planner_vision.md) §8, *track*, with
vision §4.8 (commission and tracking), §2.6 "Half-built plans" and §2.4/§2.7 (asks). It builds
on P1 ([planner_slice_contract.md](planner_slice_contract.md)), P2
([plan_management.md](plan_management.md)) and P3 ([planner-p3_contract.md](planner-p3_contract.md))
and forks none of them. Every plan write goes through `PlanLog` and merge rule M1.

Where this file and the vision note disagree, this file wins for P4. Departures are in §14;
choices made where the docs leave a product question open are in §15.

Binding decisions carried in: Host check on every method; guarded writes with typed 409s and
version counters; op log with M1, undo is an inverse op, one shared state autosaved per
gesture, no drafts; the page follows the agent by default; one factory per plan; plan before
building, and plans stay editable while half-built; the solver picks recipes, required/banned
override it; spoilers off by default; need action = 5 states, blocked is yellow; biomass left
out of headroom by default; transport deferred; local only; the page never prompts the agent.

---

## 1. Scope

| In | Out (later phase or open) |
|---|---|
| **Track** as a third result tab (**build list · graph · track**) at the plan's head, addressable as `#dash=planner/<key>/track[/<stage>]` | Track of an old version; Track of an unsaved chat solve |
| One route that solves once and returns the diff (actions, ranges, targets, cost, neighbours, on-site census) **and** the startup stages matched against the save | ETAs, progress over time (timeline, §22 of the vision) |
| Startup waves: stage table with fill floors, the headroom they were built against, and **what the stages stand on** (live feeders) on demand | A dedicated Track map layer or new map colours (palette closed) |
| A stored **startup headroom** on the plan (`set headroom_mw`), read by the page, `diff_vs_save` and `commission_plan` alike | Chat writing `headroom_mw` (§15 C1) |
| The plan's **count-as-built scope** (`set factory`) editable on Track | Unnamed clusters as a scope |
| Live drift: Track re-requests on every new head, every game `save`, every label change, every biomass toggle | A faster channel than the 3 s save poll |
| **Stage renumbering announced**, page and chat, whatever moved it (edit, save, headroom) | Remembering the last-seen partition across page sessions |
| **Asks** `ask:N`: store, routes, ask bar, asks card, `ui_context` listing + seen marking, `ui_context(answered=)` | Answer text written back; asks from the map, factory detail or dashboard |
| `diff_vs_save(plan=)` / `commission_plan(plan=)` open Track on the page (follow) | Opening Track for an ad-hoc diff (no `plan=`) |

---

## 2. User flows

### F1 Open Track

1. On a plan's workbench the result tabs are **build list · graph · track** (`tabs2`). Picking
   **track** replaces the address with `#dash=planner/<key>/track`; picking another tab
   replaces it with `#dash=planner/<key>`. A deep link to `/track` opens the tab directly.
2. The page requests `GET /api/plan/track?key=<key>` (§5). While it is in flight the previous
   Track stays on screen dimmed with `tracking v<N>…`; the first time, `loading("the track")`.
3. The goal bar, sources, exports and recipe lists above the tabs stay live: the plan is
   editable while half-built (Q1). Every own edit lands a version and Track re-requests.
4. The head does not solve: the tab bar is absent (P3 rule) and the stale-result block adds
   `track needs a solvable version`. `empty: true` shows `the plan builds nothing: nothing to
   track`.

### F2 Read where the build stands

Top to bottom, one `dash-card` each (§9.2):

1. **Headline**: `you are in stage 2 of 4: 76% built (13/17), 13 proven running` (server
   text), then `save written <written_ago>` (the full `age_note` on hover; one line, decided
   2026-09-30), the scope note and the world-moved note when present.
2. **Renumber notice** when the partition changed since the page last showed this plan (F5).
3. **Startup headroom** and **count as built** controls (F4).
4. **Stages** table: stage · on · built · running · MW draw / gen · free after · state ·
   [map]. `built` is a range where identity is missing (`22..25`). State chips per machine
   state with `states.tone`; the stage row's need-action chip uses `actionTone`. Clicking a
   stage (or Enter) filters the jobs table to that stage and sets the address
   `…/track/<n>`; clicking it again (or ×) clears the filter.
5. **Next** line, free first: `unpause 2 · set recipe 4 · build 31..50`.
6. **Jobs** table (the diff rows): process · building · need · built · running · action ·
   where · note · actions. `action` words: `build 7`, `build 8..27`, `unpause 4`,
   `set recipe 1, then build 11`, `–` for nothing to do. `running` is `–` when no matched
   machine carries a monitor. Actions per row: **[map]** (when `bbox_m`), **[copy ids]**
   (the row's `selectors`), **[recipes]** (recipe rows: opens the P3 drawer), **[ask]**.
7. **Short** (cost lines with a shortfall): item · need · stock · lines · [ask].
8. **Nearby, not in the plan**: one line from `neighbours`.
9. **On site** (sited plans only): building · planned · on site · Δ.
10. **Startup order**: headroom used and where it came from, plant draw/generation, minimum
    slice, warnings, and per stage the fill floor `≥ 34 s before its generators produce` for
    waves that energise both consumers and generators. **[what the stages stand on]** loads
    `GET /api/plan/feeders` on demand (~0.7 s, `loading`) and lists extractors feeding
    running generators, each with [map] and [copy id]. Rows overlap where extractors feed the
    same generators, so the headline `total_mw` is the union reached from any of them,
    counted once, and never exceeds the save's generation.
11. **Caveats** footnote: the server `caveats` lines (energised vs built; ranges).

### F3 Show a job or stage on the map

1. **[map]** on a job or stage: `nav.onMap` switches to the map, reveals the **machines**
   layer (and each target's `node: <resource>` layer), then `panel.showBox(bbox_m)` flies and
   outlines. Stopped machines are already red and blocked yellow on that layer.
2. **[copy ids]** copies `machine:<a>,machine:<b>` (the verb's machines, else the matched
   ones) or `node:<id>,…` (build targets), ≤ 50 members; toast `copied N ids`.

### F4 Adjust while half-built

| Control | Gesture | Op pushed (one version) |
|---|---|---|
| Startup headroom: `pressed("measured <mw>")`, `pressed("nameplate <mw>")` | click | the one the *stage headroom* setting names (measured by default): `set headroom_mw null`; the other: `set headroom_mw <floor(figure, 10)>` |
| number field `given … MW` (> 0, ≤ 1,000,000) | Enter / blur | `set headroom_mw <n>` |
| Count as built: `<select>` *whole world* + every named factory | change | `set factory ""` / `set factory "<name>"` |
| Any bench control above the tabs | as P1 | as P1 |

Each is an ordinary own commit: Ctrl+Z undoes it, conflicts get the P1 chip, and the change
re-requests Track. `scope_error` (the named factory has no machines left) shows
`“<name>” has no machines in this save` in `--bad` above the select, and the tables are empty.

### F5 Stage renumbering announced

1. Track responses carry `partition_id` (§3). The page keeps, per plan key for the page
   session, the last shown `{partition_id, current, count, rev, save_id, headroom_mw}`.
2. When a new response has a different `partition_id`, a `plan-warning` line above the
   stages says, by cause (checked in this order):
   - rev changed: `v15 changed the stages: you were in stage 2 of 4, now stage 2 of 5`
   - headroom changed: `the new headroom changed the stages: …`
   - save changed: `the new save changed the stages: …`
   - `stages` went to none: `… now no startup order fits the headroom`
3. **[ok]** dismisses it. It is also dismissed by leaving the plan.
4. Chat gets the same fact in `diff_vs_save`/`commission_plan` (§6.2).

### F6 Ask chat from any row

1. **[ask]** sits on: Track job rows, stage rows, short rows; build-list rows; the graph node
   card; the workbench header (about the plan); pins-card rows.
2. Pressing it opens the one **ask bar** (`section.ask-bar`, `role="region"`,
   `aria-label="ask chat"`) at the top of the planner root: `ask chat about <label>` + a text
   input (≤ 200 chars) + **[queue]** **[cancel]**. Focus moves to the input. Only one ask bar
   exists; opening another replaces its subject.
3. Enter or **[queue]**: `POST /api/asks`. On 201 the page copies `ask:7 <text>` to the
   clipboard and toasts `queued as ask:7 · copied: paste it into chat`. The bar closes and
   focus returns to the button that opened it. Blank or > 200 chars: `fieldError`, nothing
   sent. Escape or **[cancel]** closes the bar and returns focus.
4. The **asks** card lists asks (all of them on the plans list; the plan's own at the end of
   Track): ask · question · about · state · actions **[copy] [delete]**. State chips (muted,
   text only): `waiting for chat`, `seen by chat 14:07`, `answered 14:09`.
5. The player pastes into chat. The agent calls `ui_context`, which lists the open asks with
   what they are about and marks them seen (§6.3). After answering, the agent calls
   `ui_context(answered=["ask:7"])`. Each change reaches every open page within ~1 s.
6. **[delete]** sends `DELETE /api/asks/{n}` with the row's `rev`; 409 shows the fresh row and
   the toast `ask:7 changed since you read it` (typically: chat just saw or answered it).

### F7 Chat opens Track

1. Chat calls `diff_vs_save(plan="north hmf")` or `commission_plan(plan="north hmf")`.
2. The tool journals `plan.view` with `args {"view": "track", "stage": n|null, "section":
   "stages"|"startup"}`.
3. Follow (default): after the current gesture ends the page goes to
   `planner/<key>/track` (`/<n>` when `stage` ≥ 1); `section: "startup"` moves focus to the
   Startup order heading. Toasts: `chat looked at the track of “north hmf”` **[open]**.
   Off: nothing.

---

## 3. Data: the track view

`domain/planning/progress/track.py` `track_view(g, st, state: PlanState, *, biomass=False) -> dict`
builds the whole response from **one** `build_diff_report` call (one solve). Changes that
feed it:

| Where | Change |
|---|---|
| `planlog.py` | `headroom_mw` joins `PLAN_SCALARS`; `_SCALAR_CHECK["headroom_mw"]` = optional number, > 0, ≤ 1e6, `null` clears. `PlanState.headroom_mw: float \| None = None`, in `to_dict`/`from_dict` (absent reads as `None`, schema stays 1). Merge key `headroom_mw` (scalar, M1). `describe_op`: `startup headroom 2,000 MW` / `startup headroom: save default` (null follows the save's default, measured since 2026-09-30). Not part of `plan_id` |
| `diff_service.build_diff_report` | New kwargs `headroom_mw: float \| None = None` (used for `commission` when given; source `"stored on the plan"`), `stored: PlanState \| None = None` (scope, siting and plan_id come from it instead of `st.plans.find`). Keeps the `Commissioning` it computes as `report.run` |
| `progress/stages.py` | `StageRow.key` (the `group_key`), `StageRow.states` via the same health pass; `Stage.describe()` (the words of today's `presenters/text/diff._stage_state`, moved so text and web share them); `Tracking.headline()` (today's `_stage_overview` headline); `partition_id(tracking) -> str` = first 10 hex of sha1 over `[[stage.index, [[repr(row.key), row.machines], …]], …]`; `""` when there are no stages |
| `presenters/text/diff.py` | Uses `Stage.describe` / `Tracking.headline`; output unchanged apart from §6.2 |

**Job row id** = `"job:" + "|".join(str(k) for k in group_key)`, e.g.
`job:recipe|Build_Blender_C|Recipe_Alternate_DilutedFuel_C`. Stable across solves and saves.

**Positions** are metres (projection `pos` / 100). `act` and `targets` are capped at 50
each; `selectors` names the same members. `bbox_m` = bounding box of `act`, else the matched
machines, plus `targets`; `null` when none has a position.

**Headroom source**: `headroom_mw` stored on the plan → `"stored on the plan"`; otherwise the
save's measured headroom → `"measured from the save"`, or with `?headroom=nameplate` its
nameplate → `"nameplate from the save"` (decided 2026-09-30, §15 C2). Biomass follows
`?biomass=`.

---

## 4. Data: asks

`domain/session/asks.py`, file `config.asks_dir()/<world>.json` (new sibling of
`pins_dir()`), sanitised like `focus.path_for`. Writers: the web process (create, delete) and
**every MCP process** (seen, answered), so every write holds `filelock.held(<file>)` and uses
`atomic.write_text`; readers take no lock.

```json
{"schema": 1, "version": 12, "next": 9,
 "asks": [{"n": 7, "text": "why does this need a Blender?",
           "about": {"kind": "process", "label": "Blender · Diluted Fuel",
                     "ref": "job:recipe|Build_Blender_C|Recipe_Alternate_DilutedFuel_C",
                     "plan": "a1b2c3d4", "rev": 14},
           "rev": 2, "created": 1790000000.1, "seen": 1790000060.0, "seen_by": "Claude Code",
           "answered": null, "answered_by": "", "answer": "", "deleted": false}]}
```

- `n` starts at 1 and is never reused; delete sets `deleted: true`. `version` counts every
  write; `rev` counts writes to one ask (create = 1; seen, answered, delete each +1).
- `state` (derived): `answered` if `answered`, else `seen` if `seen`, else `open`.
- `text`: stripped, 1–200 chars. At most 200 live asks. `schema` > 1 → `NewerSchema`.
- `about.kind` ∈ `plan`, `process`, `stage`, `item`, `pin`. `label` ≤ 120 chars, `ref` ≤ 200.
  `plan` (8-hex key) and `rev` optional; a given `plan` must be a live plan (404 otherwise).

```python
SCHEMA = 1; MAX_LIVE = 200; TEXT_MAX = 200
ABOUT_KINDS = ("plan", "process", "stage", "item", "pin")
class AskError(ValueError): ...
class AskMissing(AskError, KeyError): ...      # .n, .deleted
class AskStale(AskError): ...                  # .ask (current row)
def path_for(world_id: str) -> Path: ...
def read(world_id: str) -> dict: ...
def create(world_id: str, text: str, about: dict) -> dict: ...
def drop(world_id: str, n: int, rev: int) -> dict: ...
def mark_seen(world_id: str, ns: list[int], who: str) -> list[int]: ...      # newly seen only
def mark_answered(world_id: str, ns: list[int], who: str,
                  answers: dict[int, str] | None = None) -> list[int]: ...  # AskMissing
def live(world_id: str) -> list[dict]: ...     # AskRow (§5.2), newest last
def parse(text: str) -> int | None: ...        # "ask:7" -> 7, case-insensitive
```

---

## 5. Routes

All handlers declare `response_model`; 409 bodies are declared in `responses=`. `?world=` /
`?save=` as on every route. Writes pass `guard.py` unchanged (Host on every method, Origin on
writes). Newer-schema files are a 503 `{error, newer_schema: true}`.

### 5.1 Table

| Handler (operation id) | Method, path | Body / query | 2xx | Errors |
|---|---|---|---|---|
| `plan_track` (new, `planner.py`) | GET `/api/plan/track` | `key` (8 hex), `rev?` (default head), `biomass?: include\|exclude`, `headroom?: measured\|nameplate` (default measured) | `TrackResponse` | 404 unknown key/rev, save unreadable; 400 `ValueError` from the solve |
| `plan_feeders` (new, `planner.py`) | GET `/api/plan/feeders` | `biomass?: include\|exclude` | `FeedersResponse` | 404 save unreadable |
| `push_ops` (unchanged route) | POST `/api/plans/{key}/ops` | now also `set headroom_mw` | `PushedResponse` (`state.headroom_mw`) | as P1: 400, 404, **409 `OutdatedResponse`** |
| `asks` (new, `asks.py`) | GET `/api/asks` | – | `AsksResponse` | 404 save unreadable; 503 |
| `create_ask` | POST `/api/asks` | `AskCreateBody {text, about}` | 201 `AskRow` | 400 `AskError` (blank, too long, bad about, 200 live); 404 plan in `about` unknown; 503 lock/schema |
| `drop_ask` | DELETE `/api/asks/{n}` | `AskDropBody {rev}` | 200 `AskDropped {ok, n}` | 404 unknown or deleted; **409 `AskStaleResponse`**; 503 |

`PlanStateBody` gains `headroom_mw: float | None`. Every ask write from the web appends one
journal entry (§8).

### 5.2 Models (TypedDict)

```python
class TrackState(TypedDict): state: str; count: int          # graph.health state, as states.ts StateCount
class TrackMachine(TypedDict): instance: str; x_m: float | None; y_m: float | None
class TrackTarget(TypedDict): node: str; x_m: float | None; y_m: float | None; m: float | None
class TrackRow(TypedDict):
    id: str; kind: str                    # "recipe" | "extractor" | "generator"
    step: int                             # build order by chain depth (DiffRow.stage)
    stages: list[int]                     # startup stages this job is energised in
    process: str; building: str
    recipe_id: str | None; item: str | None   # item: main product class id (drawer), null otherwise
    need: int; have: int; have_min: int | None
    build: int; build_max: int | None     # build_max null = exact
    verb: str                             # "ok" | "unpause" | "setrecipe" | "build"
    count: int; reuse: int
    running: int | None                   # null when no matched machine is monitored
    states: list[TrackState]
    new_building: bool; note: str; delta_mw: float
    act: list[TrackMachine]; targets: list[TrackTarget]
    bbox_m: list[float] | None; selectors: str
class TrackStageRow(TypedDict):
    row: str; label: str; building: str; machines: int; total: int
    built: int; built_max: int; running: int; states: list[TrackState]
    draw_mw: float; generation_mw: float; to_build: int
class TrackStage(TypedDict):
    index: int; machines: int; built: int; built_max: int; running: int; dark: int
    complete: bool; state: str            # Stage.describe()
    draw_mw: float; generation_mw: float; available_before: float; available_after: float
    fill_s: float; waits_for_fill: bool
    states: list[TrackState]; rows: list[TrackStageRow]; bbox_m: list[float] | None
class TrackStartup(TypedDict):
    ok: bool; headroom_mw: float; headroom_source: str
    plant_draw_mw: float; plant_generation_mw: float; minimum_slice_mw: float; warnings: list[str]
class TrackPower(TypedDict):
    generation_mw: float; draw_mw: float; headroom_mw: float
    measured_headroom_mw: float; biomass: bool
class TrackCost(TypedDict): item: str; name: str; need: float; stock: float; short: float; lines: int
class TrackNeighbour(TypedDict): label: str; count: int
class TrackSiteRow(TypedDict): name: str; planned: int; standing: int
class TrackSite(TypedDict): text: str; planned_total: int; standing_total: int; rows: list[TrackSiteRow]
class TrackResponse(TypedDict):
    key: str; rev: int; name: str
    feasible: bool; empty: bool; headline: str; cause: str   # headline/cause: the solve's, when not feasible
    save_id: str; age_note: str; written_ago: str | None; plan_id: str
    scope: str; scope_note: str; scope_error: str; drift_note: str
    headroom_mw: float | None                                # as stored on the plan
    current: int; count: int; partition_id: str; stage_text: str   # Tracking.headline(); "" when no stages
    to_build: int; to_build_max: int; actionable: int
    unpause: int; setrecipe: int                             # the free-first line
    rows: list[TrackRow]; stages: list[TrackStage]; startup: TrackStartup; power: TrackPower
    cost: list[TrackCost]; neighbours: list[TrackNeighbour]; site: TrackSite | None
    notes: list[str]; caveats: list[str]; monitored: int
class Feeder(TypedDict): name: str; instance: str; x_m: float | None; y_m: float | None; mw: float
class FeedersResponse(TypedDict): feeders: list[Feeder]; total_mw: float; text: str

class AskAbout(TypedDict):
    kind: str; label: str; ref: str; plan: NotRequired[str | None]; rev: NotRequired[int | None]
class AskRow(TypedDict):
    n: int; id: str                        # "ask:<n>"
    text: str; about: AskAbout; state: str # "open" | "seen" | "answered"
    rev: int; created: float
    seen: float | None; seen_by: str; answered: float | None; answered_by: str
    answer: str                            # chat's one-line answer, "" for none (C4)
    plan_name: str | None                  # about.plan resolved at read time
    copy: str                              # "ask:7 why does this need a Blender?"
class AsksResponse(TypedDict): version: int; asks: list[AskRow]
class AskCreateBody(TypedDict): text: str; about: AskAbout
class AskDropBody(TypedDict): rev: int
class AskDropped(TypedDict): ok: bool; n: int
class AskStaleResponse(TypedDict): error: str; stale: bool; ask: AskRow
```

### 5.3 Rules

- `rows` order: `build_diff`'s own (chain depth, then machines, descending). `stages` in
  wave order. `cost` ≤ 12, hardest first (as `_cost`). Nothing is re-sorted by the route.
- `rev` other than the head is allowed (it tracks that version's arguments) but the page only
  asks for the head.
- `scope` = the plan's `factory`; a `SelectorError` becomes `scope_error` (200, empty lists).
- Not feasible → 200 with `feasible: false`, the solve's `headline`/`cause`, empty lists.
- `caveats`: the energised caveat always; the range caveat when any built count is a range;
  the no-monitor warning when `monitored == 0`. The page gets short forms
  (`track.PAGE_ENERGISED` / `PAGE_RANGE` / `PAGE_NO_MONITOR`); the text presenter keeps the
  long `ENERGISED_CAVEAT` / `RANGE_CAVEAT` / `NO_MONITOR`.
- Page-facing text carries no issue codes, property names, caps emphasis, ` -- ` or plan ids:
  `age_note` is `track.save_line` (file · written N ago · autosave), `drift_note` is
  `track.PAGE_DRIFT`, row `note` is `DiffRow.page_note` (no `then BUILD`, which the action cell
  shows, and no `NEW BUILDING TYPE`, which the chip shows), `notes` and `startup.warnings` go
  through `track.page_text`.
- `running` on a stage and on a stage row is null when none of its machines is monitored, as
  on a job row. A stage `state` range is written `a%..b% built`.
- `unpause` / `setrecipe` = sums of `count` over rows with that verb.
- **Ask 409**: `rev` ≠ current → `{error: "ask:7 changed since you read it", stale: true, ask}`.

---

## 6. MCP tools

### 6.1 Changes

| Tool | Change |
|---|---|
| `diff_vs_save` | With `plan=`: passes the recalled `PlanState` and its `headroom_mw` to `build_diff_report`; journals `plan.view` with `args {"view": "track", "stage": stage, "section": "stages"}`; prints the renumber note (§6.2). No new parameter |
| `commission_plan` | With `plan=` and no `headroom_mw`: uses the plan's stored `headroom_mw`, source `stored on the plan`; journals `args {"view": "track", "stage": null, "section": "startup"}`; renumber note. No new parameter |
| `ui_context` | New `asks` block and seen marking (§6.3). New parameter `answered: list[str] \| None` ("ask:N ids you have answered") |
| `plan_log`, `ui_context` since-you-looked, `list_plans` last change | `set headroom_mw` in words via `describe_op` |
| `plan_factory save_as` | Never emits `set headroom_mw`; an existing value survives a chat save |
| `INSTRUCTIONS` (`mcp/app.py`) | `… or 'what I have open', or quotes an ask: or pin: id, call ui_context first.` |

No new tool. Descriptions stay one line; `test_surface.BUDGET` holds.

### 6.2 Renumber note (chat side)

Each MCP process keeps `_stages_seen[(world_id, key)] = (partition_id, current, count, rev)`
from its last `diff_vs_save`/`commission_plan` on that plan. When the new `partition_id`
differs: note `the stages changed since you last read this plan (v14 -> v15): you were in
stage 2 of 4, now stage 2 of 5`. First read in a process: no note.

### 6.3 `ui_context` additions

```
focus: planner › "north hmf" v14 › track   selected: stage "stage 2 of 4"
pins: …
asks (2 waiting): ask:7 "why does this need a Blender?" about process "Blender · Diluted Fuel" in "north hmf" v14 · ask:8 "is stage 1 safe to switch on?" about stage "stage 1" in "north hmf" v14 (seen)
answer them, then ui_context(answered=["ask:7", …]) marks them done on the page
since you last looked: … · journal: 14:06 page queued ask:7
```

- Lists live asks in state `open` or `seen`, newest 6, each ≤ 200 chars, then `(+N more)`;
  `asks: none waiting` when there are none. The hint line prints only when asks are listed.
- Every listed `open` ask becomes `seen` (`mark_seen`, `seen_by` = client display name) and
  one journal `ask.seen` entry is written with `args {"n": [7]}`.
- `answered=`: each id marked answered first (`ask.answered` journal, `args {"n": [...]}`),
  then the normal reply, whose first line after the header is `marked answered: ask:7`. An id
  may carry one line after it, `"ask:7 <answer>"`, stored as the ask's `answer` (C4).
  Unknown or deleted ids: `! ask:9 does not exist (asks run to ask:8)` / `! ask:3 was
  deleted`; the others still apply.
- The whole reply stays under `CONTEXT_BUDGET` (3800).

---

## 7. Journal and SSE

No new SSE event name. Journal entries reach the page as `activity` within ~0.5 s.

| Journal kind | Writer | When | `plan`/`rev` | `args` | `text` |
|---|---|---|---|---|---|
| `plan.view` (extended) | tools | `diff_vs_save(plan=)`, `commission_plan(plan=)` | key/head | `{"view": "track", "stage", "section"}` | `diff_vs_save on plan "north hmf" v14` (as today) |
| `ask.add` | web | 201 create | `about.plan`/`about.rev` | `{"n"}` | `queued ask:7 “why does this need a Blender?”` |
| `ask.drop` | web | delete | as above | `{"n"}` | `deleted ask:7` |
| `ask.seen` | tools | `ui_context` listed new asks | null | `{"n": [..]}` | `chat saw ask:7, ask:8` |
| `ask.answered` | tools | `ui_context(answered=)` | null | `{"n": [..]}` | `chat answered ask:7` |

| Page reaction | Rule |
|---|---|
| `activity` `ask.*`, any actor (other tabs included) | refetch `GET /api/asks` (one in flight, latest wins); redraw asks cards and row markers |
| `activity` `plan.view`, `args.view == "track"`, other actor | follow: go to `planner/<key>/track[/<stage>]` after the gesture; toasts: toast [open]; off: nothing |
| `plans` for the open plan | as P1–P3; if the track tab is showing, re-request Track for the new head |
| `save` (unpinned save) | re-request Track if showing (drift live on every game save); feeders list cleared |
| save picked in the header (`load.onReload`, same world) | clear Track data and feeders, forget the solves, re-request Track if showing |
| `notes` (labels) | re-request Track if showing and the plan has a `factory` scope; refresh the scope select |
| biomass setting (`powerview.onBiomass`) | re-request Track and clear feeders |
| reconnect resync | refetch asks; re-request Track if showing |

---

## 8. Frontend

### 8.1 Modules

| Module | New/changed | Does | Primitives it must use |
|---|---|---|---|
| `planner-track.ts` | **new** | Renders the track tab (F2–F5): headline, renumber notice, headroom and scope controls, stages table, next line, jobs table, short, nearby, on site, startup order, feeders, caveats; row **[map] [copy ids] [recipes] [ask]** | `table` (numbers right-aligned, `onRow` for stages, `rowClass` for the picked stage), `button`, `pressed`, `copyButton`, `chip` + `states.tone`, `states.actionTone`, `loading/empty/error`, `fieldError`, `format.mw/count/range/pct/num`, `powerview.headroom`, `words.ts`, `nav.onMap`, `panel.showBox`, `asks.askButton` |
| `livestore.ts` | **new** | The one store loop pins and asks share: one refetch in flight (a second waits and wins), world/epoch guard, 409-with-row replaces the row, one delete per row at a time | `api.get`, `toast.friendly` |
| `asks.ts` | **new** | Asks client: store (`livestore`, `GET /api/asks`), `askButton(about, opener)`, the single ask bar (open/replace/close, focus in and back, Escape), `queue()` with clipboard + toast, `dropAsk()` with 409 handling, `asksFor(planKey)`, `onActivity(entry)`, `refetchAsks()` | `button`, `fieldError`, `copy.copyText`, `toast.note/fail`, `api.get/send`, `words.ts` |
| `asks-card.ts` | **new** | The asks card: `table` ask · question · about · state · actions; empty `no asks yet: ask chat from any row of a plan` | `table`, `button`, `copyButton`, `chip(…,"muted")`, `empty`, `error`, `link` (about → plan) |
| `planner-core.ts` | changed | `ResultTab` gains `"track"`; `bench.track = {data, error, seq, stage, notice, feeders}`; `bench.seen: Record<key, Partition>`; `loadTrack()` (latest wins by `seq`, dims while in flight), `loadFeeders()`, `pickStage(n)`; re-request hooks for head / save / notes / biomass | `api.get`, `latest("planner-track")` |
| `planner.ts` | changed | Address `planner/<key>/track[/<n>]` in `parts()`; closing the drawer returns to `/track` when the tab is track; follow for `plan.view` `view: "track"`; `onSaveEvent` / new `onNotesEvent` re-request Track; focus `tab: "track"`, selection `{kind: "stage", label: "stage 2 of 4", ref: "2"}` or `{kind: "process", label, ref: row id}` | `nav.dashParts`, `nav.go`, settings `follow` |
| `planner-result.ts` | changed | `TABS` + **track**; the track tab renders `planner-track`; **[ask]** on build-list rows and the node card | `tabs2`, `asks.askButton` |
| `planner-bench.ts` | changed | Header **[ask chat]** about the plan | `asks.askButton` |
| `planner-list.ts` | changed | Asks card below the pins card | `asks-card` |
| `pins-card.ts` | changed | **[ask]** per row (`about.kind = "pin"`) | `asks.askButton` |
| `pins.ts` | changed | `showPin` goes to the map through `nav.onMap` (no second implementation) | `nav.onMap` |
| `nav.ts` | changed | `onMap(action)`: runs `action` now when on the map, else after the hashchange that `go("")` causes (the code moved out of `pins.showPin`) | – |
| `panel.ts` | changed | `showBox(bbox_m, options?: {layers?: string[]})` reveals `layers` first, as `showPoint` does | `labels.reveal` |
| `format.ts` | changed | `range(lo, hi)`: `count(lo)` when equal or `hi` null, else `count(lo) + "–" + count(hi)` (en dash); `clock(ts)` for a time of day | – |
| `words.ts` | changed | `W.track`, `W.askChat = "ask chat"`, `W.stage(n, of)`, `VERB` (`ok: "–"`, `unpause`, `setrecipe: "set recipe"`, `build`), `ASK_STATE` (`open: "waiting for chat"`, `seen: "seen by chat"`, `answered: "answered"`), `W.countAsBuilt = "count as built"`, `W.wholeWorld = "whole world"`, `W.startupHeadroom = "startup headroom"` | – |
| `sse.ts` | changed | Forward `activity` to `asks.onActivity`; `notes` → `planner.onNotesEvent`; reconnect → `refetchAsks()` | – |
| `style.css` | changed | `.plan-track`, `.track-stage.picked`, `.ask-bar`, `.track-notice` | tokens only: `--panel`, `--ink`, `--line`, `--muted`, `--bad`, `--select`, `--fs-s`, `--fs-m`, `--sp-*`, `--r` |
| `api-shapes.ts` | changed | Aliases for every §5.2 name | – |

No new colour, no new dependency, no `declareColours` entry. Planner modules never import
`dashboard.ts`; `asks.ts` imports no planner module.

### 8.2 Layout and states

- Track lives in `div.plan-main` like the other tabs; the drawer placement rule of P3 applies
  unchanged. Tables scroll inside `.dash-scroll` with a sticky first column.
- At < 900 px the stage and jobs tables keep columns process/stage, built, action; the rest
  scroll horizontally inside the table, never the page.
- Every fetch goes through dashkit `loading/empty/error`; Track and feeders errors carry retry.
- Unknown is `–`, never 0; ranges stay `a..b`; no ETAs; no delta is coloured.
- The picked stage row carries `.picked` (outline `--select`) and `aria-selected="true"`.
- The ask bar is not a modal: no focus trap; Escape closes it.

---

## 9. Map

| Layer / interaction | Behaviour |
|---|---|
| **[map]** on a job row | `onMap` → `showBox(row.bbox_m, {layers: ["machines"]})` (+ the targets' `"node: <resource>"` layers); outline as factory fly-to |
| **[map]** on a stage | same with `stage.bbox_m` (the stage's matched machines) |
| Machine colours | Unchanged **machines** layer: stopped red, blocked yellow, the map's rule |
| Plan pad | Unchanged **plans** layer; the on-site census reads the same siting |
| No new layer | A Track layer would need colours; the palette is closed |

---

## 10. Chat ↔ page loop

| Chat does | Page (follow, the default) | toasts | off |
|---|---|---|---|
| `diff_vs_save(plan=…)` | opens `planner/<key>/track` (`/<n>` with `stage=n`) after the gesture | toast [open] | nothing |
| `commission_plan(plan=…)` | opens Track, focus on Startup order | toast [open] | nothing |
| an edit to the open plan | new head, strip, chat badges (P3); Track re-requests; renumber notice if the partition moved | same | same |
| `ui_context` listing asks | asks turn `seen by chat` within ~1 s | same | same |
| `ui_context(answered=[…])` | asks turn `answered` | same | same |

| Page does | Chat sees |
|---|---|
| sets headroom or scope | a version by `page` (`plan_log`, since-you-looked); the next `diff_vs_save`/`commission_plan` uses it and notes renumbering |
| opens Track, picks a stage or job | `ui_context` focus `track`, selection stage or process |
| queues / deletes an ask | `ui_context` `asks` block; journal lines |

The page never moves while a field has the cursor (P1 rule). The page cannot prompt chat: an
ask reaches it only when the player pastes it.

---

## 11. Performance budget

Measured 2026-09-28 on a copy of the user-data backup (3 stored plans, 6–11 job rows, newest
autosave), warm process, median of 5 (max in brackets). Route budgets are in-process
(TestClient), without the ~15 ms HTTP floor Windows adds.

| Call | Measured | Budget |
|---|---|---|
| `load_state` per request (projection cached) | 0.4 ms | – |
| `build_diff_report(plan=)` (prepare + diff + commission + track) | 10.9–15.6 ms (27.8) | `GET /api/plan/track` p95 ≤ 60 ms warm, payload ≤ 64 kB |
| of which `prepare` (the solve) / `build_diff` | 6.5–9.2 / 1.5–1.8 ms | – |
| `commission` / `track` with 1–3 stages | 0.02–0.09 / 0.23–0.43 ms | – |
| `build_commission_report` alone | 8.0–11.3 ms | not called: Track shares one solve |
| `power_report` (warm) | 0.5 ms | – |
| `live_feeders` (world-only) | 636–673 ms | on demand only, never per save |
| job rows payload (matched instances 66–76) | 4.2–5.8 kB | – |
| whole Track payload | up to 51 kB for a 33-job plan (the matched-machine `selectors` of rows with no action dominate) | ≤ 64 kB, accepted 2026-09-30; the selectors are not capped below 50, since a cap would change what **[copy ids]** copies. The page is local, so no gzip |
| game save → Track redrawn | save poll 3 s + projection parse ~4 s (pre-existing, per process) + route | ≤ poll + parse + 150 ms |
| page edit / chat edit → Track redrawn | – | ≤ 1 s |
| asks write → other tab / `ui_context` seen → page | – | ≤ 1 s |
| Track render | unmeasured | ≤ 30 ms at 40 job rows (the verifier measures it, A20) |

Reference-save fact that shapes F4: nameplate headroom is **116 MW** while the three plans'
minimum slices are 392–644 MW, so with nameplate no plan has a startup order; measured
headroom is 6,372 MW (one stage each), and a given 2,000 MW gives 2–3 stages. That is why
measured became the default (§15 C2). Not measured: a
sited plan (on-site census), a plan above 40 jobs.

---

## 12. Test plan

| File | Owner | Covers |
|---|---|---|
| `tests/test_track.py` (new) | backend | `track_view` on a known plan: rows/ids/order, ranges kept (`build_max`, `have_min`), `act`/`targets` capped with positions in metres, `bbox_m`, `stages` per row, `partition_id` stable on repeat and changed by a machine-count edit or a headroom change; `headroom_mw` stored → source `stored on the plan`; `scope_error`; infeasible and empty shapes; caveats present |
| `tests/test_asks.py` (new) | backend | store: create/drop, numbers never reused, `rev`/`version`, `AskStale`, text limits, 200 cap, newer schema refused, torn/missing file reads empty, `mark_seen` idempotent, `mark_answered` refusals, lock held by two writers |
| `tests/test_web_asks.py` (new) | backend | every route and status in §5.1 incl. 409 body and guard refusals (bad Host/Origin on POST/DELETE, bad Host on GET); journal entry per write |
| `tests/test_web_planner.py` | backend | `/api/plan/track` 200/400/404, `scope_error`, infeasible 200; `/api/plan/feeders`; `push_ops` with `set headroom_mw` (and 409 on a concurrent different value); `PlanStateBody.headroom_mw` |
| `tests/test_planlog.py` | backend | `set headroom_mw` validation, merge key conflict, inverse op, `describe_op` words, `plan_id` unchanged, old snapshots read `None` |
| `tests/test_ui_context.py` | backend | asks block, `(seen)`, newest 6 + `(+N more)`, seen marking + journal, `answered=` incl. refusals, budget |
| `tests/test_plan_tools_log.py` | backend | `diff_vs_save`/`commission_plan` journal `view: track` args; renumber note on the second read; stored headroom used by both tools; `plan_factory save_as` keeps `headroom_mw` |
| `tests/test_diff.py`, `tests/test_commission.py` | backend | text output unchanged after `Stage.describe` / `Tracking.headline` move |
| `tests/test_surface.py` | backend | description budget with `answered=` |
| `tests/test_architecture.py`, `tests/test_comment_budget.py` | both run | module rules, comment budget |
| frontend gates | frontend | `npm ci`, `npx tsc --noEmit`, `npm run build` |
| UI verification | verifier | §13 at 1440×900 and 390×844, screenshots read |

Python tests run with `PYTHONPATH=<worktree>/src` and the main venv; confirm
`satisfactory_mcp.__file__` is in the worktree; `SATISFACTORY_USER_DATA` points at a scratch
copy. Never ports 8712/8713.

---

## 13. Acceptance checks (what a UI verifier must see)

| # | Check |
|---|---|
| A1 | A plan shows tabs **build list · graph · track**; picking **track** makes the address `#dash=planner/<key>/track`; reloading that address opens Track; Back leaves it |
| A2 | Track shows the headline, save age, and the jobs table with need / built / running / action; a Water Extractor row reads a range (e.g. `8..27`), never one number; unknown running shows `–` |
| A3 | With the default (nameplate) headroom on the reference save, Track says no startup order fits and still shows the jobs table and the next line |
| A4 | Pressing **measured** (or entering 2000 in *given*) makes one version (header v+1), the stages table appears (2–3 stages at 2,000 MW), and Ctrl+Z returns to nameplate in one step |
| A5 | Changing the headroom so the stage count changes shows the notice, e.g. `v5 changed the stages: you were in stage 1 of 3, now stage 1 of 2`; **ok** dismisses it |
| A6 | Stage rows show state chips; stopped reads red, blocked yellow, no other hue appears; clicking a stage (and Enter) filters the jobs to it and the address ends `/track/<n>`; clicking again clears it |
| A7 | **count as built** set to a named factory makes one version, the scope note appears and counts change; a factory with no machines left shows the scope error |
| A8 | **map** on a job flies the map to the outlined box with the machines layer on; **copy ids** copies `machine:`/`node:` selectors |
| A9 | **what the stages stand on** shows `loading…`, then a list (or its empty state) within ~1 s |
| A10 | An edit in the goal bar while Track is open re-requests Track (dims, then refreshes) without leaving the tab |
| A11 | Touching the scratch save copy's newest `.sav` mtime (or a new autosave in a copy) re-requests Track within the save poll plus parse time |
| A12 | In-process `diff_vs_save(plan=…)` opens Track on the page within ~1 s (follow on); with `stage=2` stage 2 is picked; `commission_plan(plan=…)` lands focus on Startup order; with follow off nothing moves |
| A13 | **ask** on a job row opens the ask bar with focus in its input; Enter queues it: toast `queued as ask:N · copied…`, the clipboard holds `ask:N <text>`, focus returns to the button; blank text shows a field error and sends nothing; Escape closes and returns focus |
| A14 | The asks card (plans list and end of Track) lists the ask as `waiting for chat`; in-process `ui_context()` prints it with what it is about, and the page shows `seen by chat` within ~1 s; `ui_context(answered=["ask:N"])` turns it `answered` |
| A15 | Deleting an ask from a second tab on an old `rev` gets the 409 toast and the refreshed row |
| A16 | **ask** exists on build-list rows, the graph node card, the workbench header and pins-card rows |
| A17 | 390×844: no page-level horizontal scroll on Track, the ask bar or the asks card; tables scroll inside their frame |
| A18 | Tab walk through the Track controls, stage rows, job-row buttons, ask bar and asks card: every stop has the focus ring and a name; no console errors |
| A19 | No raw class ids, `null`, `NaN` or `undefined` anywhere on Track or the asks card |
| A20 | Track render for the largest stored plan ≤ 30 ms (`performance.now()` around the render) |

---

## 14. Departures from planner_vision.md

| # | Vision says | P4 does | Why |
|---|---|---|---|
| P4-1 | `POST /api/plan/diff` and `POST /api/plan/commission` | One `GET /api/plan/track` | Both share one solve and one partition; two routes would solve twice and could disagree |
| P4-2 | Headroom `[change]` on the startup order | A stored plan scalar `headroom_mw`, used by page and tools | Stage numbers must be the same on both sides; on the reference save the nameplate default yields no stages at all |
| P4-3 | Asks inside `ui/<world>.json`, one writer, no lock | Own file, file lock, MCP processes also write (seen, answered) | Chat marks asks read and answered |
| P4-4 | `read_by` only | `seen` and `answered` | The spec asks the agent to mark asks answered |
| P4-5 | Track as its own view | A third result tab inside the workbench, addressable | The goal bar and recipe lists stay live on it for free (Q1) |
| P4-6 | Stopped red / blocked yellow on Track rows as dots | `chip` with `states.tone` | One chip primitive; colour never the only cue |
| P4-7 | `[plan this]` on short items | Absent | Entry points are P8 |

---

## 15. Choices made here (smallest option; open for review)

| # | Choice | Alternative |
|---|---|---|
| C1 | Only the page writes `headroom_mw`; chat reads it and can still pass `headroom_mw=` to `commission_plan` for one call | A `plan_factory` parameter |
| C2 | ~~Nameplate stays the default headroom~~ **Decided 2026-09-30: measured by default**, page and tools; the shared *stage headroom* setting (page and chat, [shared-settings.md](shared-settings.md)) can pick nameplate | Nameplate by default |
| C3 | The last-seen partition lives in page memory (and per MCP process) | Stored in focus or per plan |
| C4 | ~~Asks carry no answer text~~ **Decided 2026-09-30: a one-line answer**: `ui_context(answered=["ask:7 <answer>"])` stores it and the page shows it beside the ask, truncated, full text on hover | Flag only |
| C5 | Ask buttons only in the planner (Track, build list, node card, header, pins card) | Map popups, factory detail, dashboard rows |
| C6 | Answered asks stay until deleted | Auto-hide after a day |
| C7 | Only `plan=` calls open Track | Ad-hoc diffs open a from-chat card |
| C8 | Live feeders on demand | Computed on every save (~0.65 s per save) |
| C9 | Only the track tab is in the address | Every result tab in the address |
| C10 | Scope select lists named factories only | Unnamed clusters too |
| C11 | No Track map layer; fly + outline on the machines layer | A layer of the plan's matched machines |

---

## 16. File ownership

**BACKEND** and **FRONTEND** run in parallel; this contract is read-only for both.
`api-schema.d.ts` is regenerated offline by the **integrator** from `create_app().openapi()`
after both land; nobody edits it by hand. Until then the frontend types against the §5.2
names through `api-shapes.ts` aliases.

| Group | Owns (create or edit) |
|---|---|
| **BACKEND** | `src/satisfactory_mcp/domain/planning/progress/track.py` (new); `domain/session/asks.py` (new); `domain/planning/progress/startup.py`; `domain/planning/progress/diff_service.py`; `domain/planning/progress/commission_service.py`; `domain/planning/stored/planlog.py`; `domain/session/journal.py` (only if `KINDS` is extended); `src/satisfactory_mcp/config.py` (`asks_dir`); `presenters/text/diff.py`; `presenters/text/commission.py`; `interfaces/web/routers/asks.py` (new); `interfaces/web/routers/__init__.py` (append `asks.router` at the end); `interfaces/web/routers/planner.py`; `interfaces/web/routers/planlog.py`; `interfaces/web/serial.py` (only if a shape is shared by two routers); `interfaces/mcp/tools/planning.py`; `interfaces/mcp/app.py` (`INSTRUCTIONS`); `tests/test_track.py`, `tests/test_asks.py`, `tests/test_web_asks.py` (new); `tests/test_web_planner.py`, `tests/test_ui_context.py`, `tests/test_plan_tools_log.py`, `tests/test_diff.py`, `tests/test_commission.py`, `tests/test_surface.py`, `tests/test_planlog.py`; `docs/web-wire.md`, `docs/mcp-surface.md`, `docs/planner_vision.md` (§8 P4 marked built), `docs/plan_management.md` (the Asks line), `docs/planner_p4.md` (new: what was built) |
| **FRONTEND** | `src/satisfactory_mcp/interfaces/web/frontend/src/planner-track.ts` (new); `asks.ts` (new); `asks-card.ts` (new); `planner-core.ts`; `planner.ts`; `planner-result.ts`; `planner-bench.ts`; `planner-list.ts`; `pins-card.ts`; `pins.ts`; `nav.ts`; `panel.ts` (`showBox` only); `format.ts` (`range` only); `words.ts`; `sse.ts`; `style.css`; `api-shapes.ts` |
| **integrator** | `frontend/src/api-schema.d.ts` (regenerated) |

**Seams.** The frontend codes against §5, §7 and §8 only. The backend never edits TS/CSS; the
frontend never edits Python, tests or docs, and reports UI facts for `docs/planner_p4.md` in
its final notes.

**Rules for both:** no code comments beyond a 1–2 line header on new files; `var`/`function`
style; no new dependencies; impersonal repo text; conventional commits without trailers; never
touch `%LOCALAPPDATA%/satisfactory-mcp` or the saves.

---

## 17. Built detection (Q7)

Supersedes F2 item 3 (the **count as built** control), the count-as-built row of F4, A7 and
C10. How detection works, its thresholds and what was measured is in
[planner_p4.md](planner_p4.md), "How built is found".

### 17.1 What `factory` means now

| Stored value | Meaning | Words (`describe_op`) |
|---|---|---|
| `""` (every existing plan) | **auto**: found at the plan's site on every request | `count as built: found automatically` |
| `"<factory name>"` | picked: that named factory counts, whatever detection says | `count as built: <name>` |
| `"/world"` | the whole world, the old default, kept for any one plan | `count as built: whole world` |
| `"/none"` | nothing built yet: nothing counts | `count as built: nothing yet` |

Factory names cannot contain `/`, so the two sentinels never clash with a name; any other
`/`-value is refused. The picked value always wins the count; detection then speaks only as a
`hint`. A picked factory with no machines left falls back to auto with a `fallback` line
instead of the old `scope_error` (which stays in the response, always empty). Renaming a
factory re-points the plans that name it (`edits.repoint_plans`, one head op per plan).

Detection writes nothing: it is a function of the plan version, the save and the labels.

### 17.2 Where a plan stands

| Plan carries | Search area |
|---|---|
| a siting with a footprint | the pad plus 50 m |
| a siting without one | a circle round the origin |
| only `near:` sources | a circle round each place |
| only `node:` sources | a circle round each node (never better than `likely`) |
| anything else (`region:`, `grid:`, compass, whole map) | none: **not placed**, no progress |

A circle's radius scales with the plan: the square its machines' footprints need, twice over
for belts and walkways, its half diagonal plus 50 m, never under 100 m. The radius only seeds;
the coherence clusters decide what belongs.

### 17.3 `TrackResponse.built_at` (`TrackBuiltAt`)

```python
class TrackBuiltCandidate(TypedDict):
    kind: str            # "factory" | "cluster"
    name: str            # factory name, or the unnamed cluster's suggested name
    proposal: int | None # cluster index, this save only
    machines: int
    rate_share: float    # share of the plan's rate it holds
    bbox_m: list[float] | None

class TrackBuiltAt(TypedDict):
    mode: str            # "auto" | "picked" | "world" | "none"
    confidence: str      # auto only: "sure" | "likely" | "unsure" | "nothing" | "no site"
    text: str            # the page line: "built at “oil setup”", "which factory is this plan?", …
    figure: str          # "12 / 16", "12–16 / 20" (unsure) or "–" (not placed)
    hint: str; fallback: str; area: str; picked: str
    built: int | None; built_max: int | None; total: int
    percent: float | None; percent_max: float | None   # of the planned rate
    candidates: list[TrackBuiltCandidate]
    missing: list[str]; also_here: list[str]; foreign: list[str]; node_owner: str
    labels_version: int; token: str                   # for naming a cluster from Track
```

`built`/`total` are machines at the plan's clock, capped per job at the plan's need; `percent`
is the covered share of the planned rate. When unsure, the jobs and stages come from the wider
set and `built`..`built_max` is the range. Not placed: `built` is null, the stage headline is
empty and the jobs table's column reads `anywhere` (world-wide counts, not progress).

### 17.4 `GET /api/plan/built`

`PlansBuiltResponse {rows: PlanBuiltRow[]}`, one row per live plan at its head: `key`, `rev`,
`mode`, `confidence`, `built`, `built_max`, `total`, `percent`, `percent_max`, `figure` (`?`
when unsure or when the plan does not solve), `text`. One solve per plan, cached in process per
(world, plan, rev, save, labels version). The plans list loads it after `/api/plans`.

### 17.5 Page

- Track headline card: one **built line** under the stage sentence: the figure (a button that
  switches machines and percent), the `text`, and the answers:
  sure `[map] [change]`; likely `[name it]` (unnamed cluster) `[not this]` `[map] [change]`;
  unsure one button per candidate plus `[nothing built yet]`; nothing `[change]`; not placed
  `[place]` (copies a `site_plan` call for chat) `[change]`; picked, whole world or nothing
  `[auto] [change]`. A second line lists `missing`, `also here`, foreign machines and the node
  owner; the hint carries `[use it]` / `[count them]`.
- `[change]` opens the old select (`data-ctl=track-scope`): found automatically, the unnamed
  clusters at the site, every named factory, whole world, nothing built yet.
- Picking an unnamed cluster names it: a one-field name bar (`data-ctl=built-name`) pre-filled
  with the suggested name posts `/api/labels`, then sets `factory` to the new name. A refused
  name leaves the plan alone.
- The **built progress** setting (planner group, `machines` or `percent`, kept in the browser)
  sets the figure on Track and in the plans list's new **built** column; `?` there links to
  the plan's Track.

### 17.6 Chat

- `diff_vs_save plan=` and `commission_plan plan=` print the built line after the save line,
  with the hint, fallback, missing and foreign lines, and one instruction line: unsure names
  `factory='<name>'` for one call or `plan_factory … for_factory='<name>'` to keep it; not
  placed names `site_plan`; an unnamed cluster names `name_factory select=['proposal:N']`.
- `factory=` on `diff_vs_save` and `for_factory=` on `plan_factory` also take `auto`,
  `whole world` (`world`) and `none`. A diff without `plan=` still counts the whole world.
- `list_plans`: the `factory` column is now `built`: `12/16 @oil setup`, `? which factory`,
  `not placed`.

### 17.7 Choices made here

| # | Chosen | Alternative left open |
|---|---|---|
| Q1 | Two plans on one site are detected independently; a machine may count for both | The nearest-site allocation pass (design §3.8) |
| Q2 | The unsure low end is the unnamed machines only | Only the machines inside the search area |
| Q3 | `[not this]` stores `/none` | A per-plan list of rejected clusters |
| Q4 | `[place]` copies a `site_plan` call | Siting from the map |
