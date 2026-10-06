# Planner vision: factory planning in the UI, together with chat

A design note, not a plan of record. Nothing here is built unless it says so.

The request, 2026-09-27:

> *"I want to be able to interact with the factory planning in my UI. Envision that in depth."*
>
> *"I want it to be interactive with chat too. So that I can talk to my MCP user and have the
> changes reflected in the UI. Do stuff in the UI and have a back and forth with my agent."*

So there are two pillars, with equal weight:

1. **The page plans on its own.** Goal, recipes, power, layout, site and tracking all work
   without typing a word.
2. **The page and the chat work on one plan together.** What the agent does shows up on the
   page. What the player does on the page, the agent can see on its next turn.

The page stays **local only**. The chat runs in the player's Claude client, never in the page. §2.8
keeps the two apart.

This builds on [frontend_vision.md](frontend_vision.md) (Planner wireframe §2.5, flow §4.1,
phases 8–10). §11 lists where it departs from that note, and why.

Timings marked *measured* were taken on 2026-09-27 against the live world (newest autosave,
three stored plans) in a warm process. Anything else is labelled.

---

## 1. What planning is in this codebase today

### 1.1 The pipeline

```
goal ──► scope ──► recipe set ──► LP solve ──► whole machines ──► bill ──► layout ──► site ──► startup ──► track
 │         │           │              │              │              │         │          │         │           │
objective  sources     exclude_/      two-phase      ceil(x) at     slice_of  build_     siting    commission  diff_vs_save
target     only_free   only_recipes   HiGHS, every   derived clock  shards,   layout     record    waves       + track()
exports    water_ext.  recycle_once   item == 0      (§8.4)         sloops,   floors,    (not a    under       stages
minimums   supplied    sloops                                       MW exact  buses,     constr.)  headroom
```

Section numbers like §8.4 refer to [planning.md](planning.md).

| Step | Domain code | Tool | What it really does | Limits that matter to a UI |
|---|---|---|---|---|
| Goal | `planning/solver/scenario.py` `build_scenario` | `plan_factory` | Objectives: `max_mw`, `max_item`, `min_raw`, `min_machines`, `min_power`. "X per minute of Y" = `exports=[Y]` + `export_minimums={Y: X}` (the `design_factory` prompt uses `min_machines`) | There is no single "target rate" argument. `exports` **replaces** `[MW]`, and exporting MW forbids drawing from the grid (§8.9) |
| Scope | `spatial/nodes/selectors.py`, `spatial/nodes/` | same | Selectors pick nodes. `annotate` marks tapped/reachable. Water is a cap (`WATER_EXTRACTOR_CAP_ASSUMED` or `water_extractors`) | The LP picks extractor **counts per (building, resource, purity)**, not nodes. Only `trunks` names nodes |
| Recipes | `search.match_recipes`, `_ban_processes` | same | Every unlocked part recipe is allowed. Patterns ban or keep. A pattern that matches nothing is refused | No "use recipe R for item I". Only global bans and keeps |
| Solve | `planning/solver/optimize.py` `solve` | same | Two-phase LP. Every item balances to exactly zero. The duplicate-pid guard and the free-lunch audit run on every solve (§8.3) | Clocks are **derived**. `clocks` and `extractor_clocks` offer modes; there is no per-row clock |
| Machines | `build_table.build_rows` | same | `ceil(x)` machines at `x/ceil(x)`. Extractor modes are folded. Negligible rows are omitted but still counted | – |
| Bill | `planning/readout/slice.py`, `report.py` | same | Exact MW, shard bill, sloop use, flows with line counts, buildings never built, overclocked rows, zero exports | The plan draws from the grid freely unless MW is exported (`grid_import_mw = 1e6`) |
| Byproducts | `planning/analysis/byproducts.py` `analyse` | `explain_byproducts` | 2 + up to 8 solves. Names stuck items, whether they can be sunk, and legal consumers | Takes **8 of the 18 plan arguments** (gap G6) |
| Routes | `planning/analysis/recipe_routes.py`, `bom.py` | `compare_recipe_options`, `bom` | Whole map, per item, in isolation | Do not see the plan's sources or exclusions |
| Layout | `planning/layout/schematic.py`, `layout/service.py`, `trunks.py`, `materials.py`, `site_partition.py`, `fit.py` | `plan_layout` | Blocks by line count, one bus per item, floors by SCC-condensed depth, deck cap, head ordering, trunks, materials, site partition, fit to a platform | A **schematic**: no world coordinates, no belt routing (§8.5) |
| Site | `planning/siting/` | `site_plan`, `plan_factory site_at=` | A *record* of origin, yaw and footprint. `survey` counts what stands on the pad. Terrain is read at the pad | Never fed to the LP. Not part of `plan_id` |
| Where to mine | `spatial/ranking.py` | `rank_build_sites` | Fields for **one resource**, with a weighted score and every raw term | Siting by the whole bill is not built (roadmap §2.1) |
| Startup | `planning/progress/startup.py` | `commission_plan` | Waves under nameplate headroom; generators refund power | A startup order, not a build order (§8.5d) |
| Track | `planning/progress/diff.py`, `diff_service.py`, `stages.track` | `diff_vs_save` | Matches machines by identity. Actions UNPAUSE → SETRECIPE → BUILD. Water is a range. Stages appear for a stored plan | "Running" can be proven. "Unpowered" never is |
| Unlock value | `planning/analysis/sensitivity.py` | `rank_unlocks` | One counterfactual solve per locked alternate | – |
| Context | `progression/shards.py` etc. | `power_shards`, `somersloops` | Held, committed and free | – |
| Built factories | `factories/query.py`, `cohere.py`, `trace.py`, `floors.py` | `factory_query`, `propose_factories`, `select_machines`, `trace_upstream`, `factory_floors` | Built flows (inputs in deficit), clusters, traces, decks | Inputs to planning, not planning itself |

### 1.2 What is stored, and where

| What | Where | Rule |
|---|---|---|
| Plans | `config.plans_dir()/<world>.json` (user data dir; `SATISFACTORY_USER_DATA` overrides; uncommitted today) | **The request is stored, never the solution** (`store.py`). Recall re-solves. One plan has `name`, `args` (non-default `PLAN_ARGS`, 18 keys), `notes`, `plan_id`, `factory`, `created`, `provenance`, `siting` |
| Labels | `config.labels_dir()/<world>.json` | Same pattern |
| Game save | read only | Never written |

`plan_id` hashes the args **and** the save-derived inputs, so "world moved" can be detected.
`provenance` records what each selector resolved to, so "field moved" can be detected too.

---

## 2. The loop: page and chat on one plan

### 2.1 Process model today (checked, not assumed)

| Fact | Evidence |
|---|---|
| The MCP server is **stdio**, one process per client session | `server.py` `mcp.run()` (stdio default). Claude Desktop config and `~/.claude.json` both launch `uv run … satisfactory-mcp` as `type: stdio` |
| **Several MCP processes run at once** | 3 × `satisfactory-mcp` and 1 × `satisfactory-mcp-web` running when this was written |
| The web server is a **separate process** (FastAPI, 127.0.0.1:8712) | `satisfactory-mcp-web` script |
| They share nothing in memory. They **share the plan and label files** | `config.plans_dir()`, `labels_dir()`. Each process parses the save itself (~4 s, per `watch/watcher.py`) |
| Cross-process signalling today = **the web server polls file mtimes every 3 s** | `SaveWatcher`: `save` for `*.sav`, `notes` for labels/plans `*.json`. The payload is `{filename, mtime}`, and both trees use `<world>.json`, so the page **cannot tell a plan write from a label write** |
| So chat → page **already half-works** for *saved* things | `plan_factory save_as`, `site_plan` and `name_factory` write a file → within 3 s `notes` fires → `sse.ts` refetches `/api/factories`, `/api/factories/health`, `/api/plans` → the pad outline and labels redraw |
| Nothing travels page → chat | The MCP tools read the save and the stores; no page state is written anywhere |
| MCP SDK | `mcp` **1.28.1** installed (`>=1.28,<2` in pyproject) |

What is missing: unsaved agent work (a `plan_factory` with no `save_as` leaves no trace), any
page state the agent can read, who changed what, and conflict handling.

### 2.2 Shared state: one source of truth

The model (L5, 2026-09-27): **one plan state, shared by the page and chat, versioned like
git.** There are no page drafts. The page autosaves every move as a versioned change. A writer
on either side knows the version it started from; when it pushes onto a newer one, the server
merges or tells it to merge.

Everything shared lives in the user data directory. The **server processes** are the only
readers and writers. The browser reaches it through the web server.

| Store | File | Writers | Holds |
|---|---|---|---|
| **Plan log** (replaces `plans/<world>.json`) | `plans/<world>/<key>/ops.jsonl` + `snap/<rev>.json` (§2.6) | chat, page | Append-only **operations** per plan (set rate, add a banned recipe, move the site…), each with `rev`, `base_rev`, actor, time. Snapshots of the full plan state every N ops. It is the history, the undo record and the plan half of the activity feed |
| Labels | `labels/<world>.json` | chat, page | Factory names. **Lock + version counter** (the detect work in progress). Moving onto the same log later is worth it: `amend_factory` add/drop are set operations and would merge cleanly |
| Pins | `pins/<world>.json` (new) | page (chat too if L6 says yes) | Stable short ids `pin:N` → an object (plan, process, machine, factory, field, node, point) + label + note |
| Focus | `ui/<world>.json` (new) | page only | What is on screen: view, plan + rev, tab, selection, map centre and zoom, a heartbeat |
| Asks | inside `ui/<world>.json` | page | Questions queued for chat: `ask:N`, text, object refs, `read_by` |
| **Activity journal** | `activity/<world>/<writer>.jsonl` (new), one file per writing process | each process, its own file | Everything that is **not** a plan edit: agent solves that saved nothing, view switches, focus, label events, asks |

**Locking.** Pins, focus and asks have one writer (the web server), so they need no lock. The
journal has one file per process. **The plan log has many writers** (N chat processes plus the
page), so an append takes a short per-plan lock (`O_CREAT|O_EXCL` lock file, break after ~10 s
as stale). The lock covers "read head, merge, append". It is held for milliseconds.

The **activity feed** the page shows is the plan logs and the journal, merged by time.

Provenance on every op and journal entry:

```json
{"rev": 13, "base_rev": 11, "ts": 1790000000.1,
 "actor": {"kind": "chat", "client": "claude-code", "pid": 8248}, "sav": "3f2a91c0",
 "op": "add", "key": "banned", "value": "Recipe_Alternate_BoltedFrame_C",
 "merged_over": [12]}
```

`client` comes from the MCP `initialize` handshake (`ServerSession.client_params.clientInfo.name`,
present in SDK 1.28.1), so the page can say *"changed in Claude Code"* rather than just "chat".
The page writes `{"kind": "page"}`.

### 2.3 Chat → page

Every MCP tool that edits a plan appends ops to its log; every other planning or labelling call
appends a journal entry. The web server tails both and pushes an SSE event. The page reacts
according to the **follow setting**.

| Agent does | Recorded as | Page (following) | Page (not following) |
|---|---|---|---|
| `plan_factory` (no save) | journal `plan.solve` + request args + `plan_id` | Opens a **"from chat" card** in the Planner; the graph and tables show it (the page re-solves the args, 15 ms) | A toast: *chat solved "HMF 15/min"* **[open]** |
| `plan_factory save_as` / `rename_plan` / `forget_plan` (with `base_rev`) | ops in the plan log | The open plan moves to the new head and shows a **changed-by-chat diff** (§2.7) | Badge on the plans list |
| `site_plan` | `plan.site` | Map flies to the pad; the outline pulses once | Pad redraws in place |
| `plan_layout`, `diff_vs_save`, `commission_plan`, `explain_byproducts` | `plan.view` + tab + args | Switches to that tab of that plan | Toast |
| `name_factory`, `amend_factory`, `rename_factory`, `forget_factory` | `label.*` | Map outlines the machines; the Factories list selects the row | Outline redraws |
| `show_on_map` (extended, §7.1) | `focus` + point/object | Map flies there. **Links stop being the only way** | Toast |
| any read tool | nothing | – | – |

**Follow setting** (Settings, per browser): *follow* (**the default, L2**), *toasts only*, *off*.
While the player is dragging or typing, "follow" waits until the gesture ends and then moves. The
page never takes the screen away mid-gesture. An edit chat makes to the plan already on screen
needs no following: it simply shows up (§2.7).

**Latency.** Target ≤ 1 s from the tool returning to the page updating: poll the journal
directory every 0.5 s (a `stat` per writer file), then re-solve (~15 ms). The 3 s save poll
stays as it is. Measure once built; there is nothing to measure yet.

**The request is carried, never the result.** Ops and journal entries hold arguments, never
solutions. The page re-solves. This is the store's existing rule, and each entry stays under
1 kB.

**Two views of one world.** Each entry carries `sav`. When the agent's process read a different
save from the page, the card says so: *"chat solved against sav:3f2a, you are on sav:9c01"*.
Each MCP process picks the newest save on its own, so this happens around an autosave.

### 2.4 Page → chat

**The agent cannot be prompted by the page.** Nothing in MCP lets a server start a turn. The
agent sees page state only when it calls a tool or reads a resource on the player's next message.
Everything below is designed around that.

| Mechanism | What it gives the agent | Works in |
|---|---|---|
| **`ui_context` tool** (new, read only) | One compact block: is the page open (heartbeat), world and `sav` on both sides, focus (view, plan **and its head rev**, tab, selection with `pin:` ids), pins, unread asks, and **plan ops and activity since this session last looked** | Any client with tools |
| **Plan versions in every answer** | Every plan-reading tool prints `plan "north hmf" v14`. Every plan-writing tool takes `base_rev` (§2.6). The page's edits are ordinary versions, so the agent just reads the plan | Same |
| **`satisfactory://ui/context` resource** | The same text as `ui_context`, for clients that attach resources | Claude Code `@`-mentions resources (documented behaviour, not tested here). Claude Desktop: unverified |
| **Pins** `pin:N` | Accepted wherever a selector is: `sources` (a field or node), machine selectors, `site_at`, `show_on_map at=`. The player says "why is pin:4 short?" | Any |
| **Asks** `ask:N` | The page's **Ask chat** button saves a question plus the objects it is about, and copies `ask:7 why does this need a Blender?` to the clipboard. The player pastes it into chat. `ui_context` lists unread asks, and marks them read once the agent has fetched them. The page shows *"seen by chat ✓"* | Any |
| Server `instructions` | One line: *"When the user refers to 'this', 'here', 'what I selected' or an ask:/pin: id, call ui_context first."* `FastMCP(name, instructions=…)` sends it at initialize | Whether each client shows instructions to the model is **unverified** |

`ui_context` output sketch (text, budgeted like the other tools):

```
# page open (heartbeat 4s ago) · world "Spire" · page sav:3f2a = yours
focus: planner › "north hmf" v14 › Graph   selected: process "Blender · Diluted Fuel" (pin:4)
pins: pin:3 field Iron Ore @1204,-3410 "the good iron" · pin:4 process Blender·Diluted Fuel
asks (1 unread): ask:7 "why does this need a Blender?" about pin:4
since you last looked: "north hmf" v11 -> v14 by page: v12 rate HMF 10 -> 15/min ·
  v13 +banned "Alternate: Bolted Frame" · v14 site moved 120x96 y30
  · journal: 14:06 page opened "old test"
```

"Since you last looked" uses a read cursor **held per MCP process**. A new session starts with
the last few entries and says so.

### 2.5 What MCP 1.28.1 and the clients actually support

| Feature | SDK 1.28.1 (read in `.venv`) | Claude Code | Claude Desktop | claude.ai web | Used here? |
|---|---|---|---|---|---|
| Tools | yes | yes | yes | n/a | **yes**, the backbone |
| Resources (read) | yes | `@`-mention (documented, untested here) | attach (unverified) | n/a | mirror only |
| Resource **subscriptions** / `resources/updated` | `get_capabilities` hard-codes **`subscribe=False`**; `send_resource_updated` exists but can only be sent inside a session | unverified | unverified | n/a | **no**. Even if honoured, a notification does not start a model turn |
| `listChanged` | off by default (`NotificationOptions`) | unverified | unverified | n/a | no |
| **Elicitation** (server asks the user mid-call) | `ctx.elicit()` in FastMCP | unverified | unverified | n/a | optional: could ask the player to settle a chat-side conflict (§2.6). Always with a plain-text fallback |
| Sampling | `create_message` exists | unverified | unverified | n/a | **no**. It would put a model call behind the page |
| Server instructions | `FastMCP(instructions=)` | unverified | unverified | n/a | yes, one line |
| Transport | stdio, SSE, streamable HTTP | stdio (configured) | stdio (configured) | **cannot reach a local stdio server** | stdio stays |

"Unverified" means not checked in this session. Before building anything that depends on
it, test it against the actual client.

### 2.6 Versions, merging and undo: the plan log

The requirement: *"the agent knows the version of the plan he's working on, but like git, when he wants to
push he gets notified a new version exists, and he may merge his changes in there."*

**Why not the old store.** Today every writer loads the whole plan file, changes it and saves
it. With several MCP processes and the web server writing, **one silently drops the other's
change** (`atomic.write_text` prevents a torn file, not a lost update). The plan log replaces
that store (gap G1).

#### Operations: the unit of change

An op is the smallest edit that means one thing to a player. Its **merge key** decides what it
can collide with.

| Op | Plan field | Merge key | Kind |
|---|---|---|---|
| `set` | `objective`, `target_item`, `only_free_nodes`, `allow_sinks`, `machine_cost_mw`, `water_extractors`, `sloops`, `belt_ipm`, `pipe_m3min`, `notes`, `factory` | the field | scalar |
| `put` / `del` | `export_minimums[item]` (the **rate**), `supplied[item]` | field + item | map entry |
| `add` / `remove` | `exports`, `sources`, **`required`**, **`banned`** (Q4), `recycle_once`, `clocks`, `extractor_clocks`, `logistics_items` | field + member | set member |
| `site` | origin, yaw and footprint together | `site` | one value: a pad move is one gesture |
| `create`, `rename`, `forget`, `restore` | the plan itself | `lifecycle` | – |
| `undo` | the inverse ops of an earlier rev, tagged `undoes: <rev>` | as its inverse ops | – |

Q4's lists are **sets**. So "chat banned Bolted Frame" and "page required Pure Iron Ingot" are
different keys and merge cleanly, where a whole-list overwrite would have collided.

Chat does not send ops. Its tools take whole arguments as they do today, plus `base_rev`. The
server **diffs those arguments against the base snapshot** to get the ops, then merges them like
any other write. The page sends ops directly.

#### The merge rule (proposed; see §9 M1)

A write is *(ops, base_rev)*. Under the plan lock the server takes *theirs* = every op since
`base_rev`, and checks each of *mine* against them:

| Mine vs theirs | Result |
|---|---|
| Different merge keys | **clean** |
| Same key, same resulting value (both set rate 15, both banned X) | **clean**; mine is dropped as already applied |
| `set` / `put` / `del` on the same key, different values | **conflict** |
| `add X` vs `remove X` on the same list | **conflict** |
| Cross-list: `required X` vs `banned X` | **conflict** (a semantic pair, listed explicitly) |
| `site` vs `site` | **conflict** |
| Anything vs their `forget` | **conflict**: "plan was forgotten" |
| `rename` vs `rename` to different names | **conflict** |

- **All clean:** mine is appended at head+1… and the answer says *"merged onto v14 (you were on
  v11); others changed: rate 10→15 (page), site moved (page)"*.
- **Any conflict:** nothing is applied. The answer is **outdated**: the head rev, every op since
  `base_rev`, and the conflicting keys. The writer merges and pushes again with the new
  `base_rev`. Partial application is refused, because half a gesture is a plan nobody asked for.
- **A clean merge can still solve badly** (both sides' edits together are infeasible). That is
  not a conflict. The result says INFEASIBLE with its usual diagnosis, and the merge note names
  the ops from each side.

#### Chat side

- Every plan-reading tool prints the version: `plan "north hmf" v14`.
- Every plan-writing tool (`plan_factory save_as` over an existing name, `rename_plan`,
  `forget_plan`, `site_plan`) takes **`base_rev`**. Writing an existing plan without it is refused
  with the head rev ("read it first"). Creating a new name needs none.
- An *outdated* answer lists the other side's ops in words, so the agent can re-plan and push.
  Where the client supports elicitation, the agent may instead ask the player which value to keep
  (unverified, §2.5).

#### Page side (autosave)

- **Every gesture is a push.** Releasing a slider, ticking a box, adding a chip, dropping the
  pad. While a slider or pad drag is in progress the page solves locally (the solve route is
  stateless) and pushes once on release, so one move is one version rather than a hundred.
- The page is almost always on the head, because chat's ops arrive over SSE within ~1 s. A
  conflict needs chat to push the same key inside that window, so it will be rare.
- **Clean merge:** invisible, apart from the chat badge on what chat changed.
- **Conflict:** the page shows the head (chat's value). An inline chip sits on the control that
  collided: *"Claude Code set the rate to 15 while you set 12: [keep 15] [use mine: 12]"*. "Use
  mine" is a fresh op on the new head. The chip does not block other editing. Unresolved chips
  are also listed in the Activity panel. Nothing is lost: the rejected op is kept, marked
  `rejected`, in the page's own journal.

#### Storage

- `ops.jsonl`, append-only, under the per-plan lock. Typically about 150 bytes per op.
- **Snapshot** of the full plan state every **N ops (proposal: 50)**, and at `create`, `restore`
  and migration. Loading any rev = nearest snapshot at or below it + replay. At 50 ops, a replay
  is at most 49 dictionary updates, so it costs microseconds. Cadence is open question N1.
- A snapshot holds **arguments only**, never a solution. The store's request-only rule stands.
- **Migration:** each plan in today's `plans/<world>.json` becomes `create` + a snapshot at v1,
  keeping its `plan_id`, provenance and siting. The old file is kept read-only.
- `plan_id` (the solve-input hash) and `rev` (the edit counter) are different things. Both are
  shown. "World moved" still compares `plan_id`.

#### Undo

- **Undo is a new op**, the inverse of an earlier one: `set` back to the old value, `add` ↔
  `remove`, the previous `site`. It is never a rewind, so it merges like any edit, and it can
  conflict like any edit. Undoing something chat has since changed again is a conflict, which is
  exactly right.
- **Page Ctrl+Z** undoes **the player's own** last op on this plan. Ctrl+Shift+Z undoes that undo.
  Chat's edits are undone from the Activity panel (**Undo** on any op) or by asking chat.
- **Chat undo:** `plan_log(name, undo=<rev>)`. `restore=<rev>` emits the ops that turn the head
  back into that version, as one new rev.
- **Forget** is an op too, so a forgotten plan can be restored for as long as its log is kept
  (N2).
- The log **is** the history. It replaces the earlier idea of keeping 20 full copies per plan.

#### Half-built plans (Q1)

A plan stays editable in every tab, Track included. An edit is just another op, and nothing
resets. Track re-diffs on every new rev. When an edit renumbers the startup stages (the
partition follows the arguments, §8.5e of planning.md), Track says so beside the stage table:
*"v15 changed the stages: you were in S2 of 4, now S2 of 5"*.

### 2.7 Conversation affordances in the page

| Affordance | Behaviour |
|---|---|
| **Changed-by-chat diff** | When chat's ops land on the open plan, a strip lists them (`v12 HMF 10→15`, `v13 +banned Bolted Frame`) and the result deltas (`+2 Assemblers, −40 MW`, from re-solving both revs, ~30 ms). Changed graph nodes and table rows are outlined for a few seconds. A **"chat"** text badge marks them, not a new hue (palette closed) |
| **Accept / undo** | A chat *solve* that saved nothing arrives as a card: **[apply to this plan] [new plan from it] [dismiss]**. "Apply" diffs its args against the head and pushes the ops (the same merge rule). A chat *edit* is already a version, and carries **[undo]** (an inverse op). The agent either proposes (a bare solve) or edits (a write with `base_rev`), and the page offers the matching action |
| **Conflict chip** | See §2.6, page side |
| **Pins** | Right-click any object (process row, graph node, machine, factory, field, node, map point): **Pin** gives the next `pin:N` and copies it. Pins show as small numbered tags on the map and graph. Numbers are never reused. A pin whose object is gone (a dismantled machine) says so instead of vanishing |
| **Ask chat** | Any object menu, or the selection: **Ask chat…** takes a one-line question and creates `ask:N` + clipboard text. The Asks list shows unread / seen by chat |
| **Copy as tool call** | Every plan: `plan="north hmf"` plus its version, or the full `plan_factory(…)` call. Every selection: its selector (existing click-to-copy) |
| **"Chat is looking at"** | When a chat entry arrives while following, a strip names the client and what it touched, with **[stop following]** |
| **Activity panel** | Plan ops and journal entries from both sides, newest first, filter by actor. Each row links to its object; each plan op has **[undo]** |

### 2.8 Local only: this is not a chat pane

- The page holds **no model key** and makes **no network call**. It talks only to
  `127.0.0.1:8712`.
- The conversation happens in the player's Claude client. The page and the client meet only through
  the **local disk**, via the MCP server process that the client itself launched.
- The rejected option, a chat pane inside the page, stays rejected. Nothing here needs it, and
  it would need a key and the network.
- New page writes (plan ops, focus, pins, asks) pass the same Host/Origin guard as every other
  write.

### 2.9 Channel options considered

| Option | Latency | Coupling | Verdict |
|---|---|---|---|
| mtime poll of the store files (exists) | ≤ 3 s | none | Keep for saves. Too coarse for unsaved solves and provenance |
| **Journal files, one per writer, polled at 0.5 s** | ≤ ~1 s | none; works with the web server down | **Chosen.** It is the provenance log too |
| MCP process POSTs to the web server | instant | needs the web server up, and a guard exemption or a token | Later, only if 1 s feels slow. Measure first |
| One process: MCP over streamable HTTP inside the web app | instant, in memory | client config change; `/mcp` would need DNS-rebinding and Host protection; the MCP lifetime is tied to the web server | Not now. Claude Desktop support for HTTP-configured local servers is unverified |
| SQLite with change polling | ~poll | new store format and a migration | No. The op log needs a store anyway, but JSON lines keep it readable, appendable under a lock file, and diffable by hand |

### 2.10 Loop wireframe: activity and asks

```
+-- Activity ------------------------------------------ [page] [chat] [all] --+-- Asks --------------------------+
| 14:06  Claude Code  solved "HMF 15/min" (no edit)       [open] [apply]      | ask:7  why does this need a       |
| 14:05  page         north hmf v14  site moved y30       [fly] [undo]        |        Blender?  about pin:4      |
| 14:02  page         north hmf v13  +banned Bolted Frame [undo]              |        seen by chat ✓ 14:07       |
| 13:58  Claude Code  north hmf v12  sloops 0->4          [undo]              | ask:8  is pin:3 big enough?       |
|        merged onto v11 · result: +2 Blenders, -118 MW                       |        unread  [copy] [delete]    |
| ⚠ 13:57 conflict: rate — chat 15, you 12  [keep 15] [use 12]               | [Ask chat about selection…]      |
| follow: ( ) off  ( ) toasts  (o) follow            chat sessions seen: 2    |                                   |
+-----------------------------------------------------------------------------+-----------------------------------+
```

---

## 3. Interaction model (the page on its own)

### 3.1 The unit of work: the plan itself, at its head version

The page always edits **the stored plan** (the `PLAN_ARGS` + `required` + `logistics_items` +
siting). There is no draft, no Save button and no discard.

- Each gesture becomes one or more ops, pushed with the page's `base_rev` (§2.6). The server
  merges, the page solves the new head (~15 ms) and redraws.
- During a drag the page solves locally without pushing, and pushes once on release.
- **Starting points** (§3.2) create a plan at once, auto-named (`HMF 10/min · Grass Fields`),
  renameable at any time. Whether throwaway exploration should go to a reusable **scratch plan**
  per world instead of piling up named plans is open question N4.
- Every plan shows `plan "…" vN` and its equivalent tool call, with copy (frontend_vision
  principle 7).

### 3.2 Starting a plan

| Entry point | Where | Plan it creates | Domain today |
|---|---|---|---|
| **Item at rate** | Goal bar; Recipes "Plan this"; search | `min_machines`, `exports=[item]`, `export_minimums={item: rate}` (`design_factory` preset) | yes |
| **Max power** | Goal bar; Power "plan a plant" | `max_mw`, sources from the map (`plan_power_plant` preset) | yes |
| **A factory's shortfall** | Factory detail › Balance: "plan this input" per deficit row | item = the deficit item, rate = the deficit (nameplate or measured; **the player picks**, both shown), `for_factory`, site suggested at the factory centroid | yes (`FactoryView.inputs()`) |
| **A plan's shortfall** | Track: "short: 8,800 Rubber, nothing makes it" | item and rate from the diff cost table | yes |
| **A milestone or elevator shortfall** | Progress | from `phase_requirements` | yes |
| **A proposal or factory, "again, bigger"** | Proposals; factory detail | `only_recipes` it runs, target = its top output × N, `node:` ids it taps | **no**, G8, ask first |
| **What can this field make** | Map right-click a field | `sources=[near:x,y@r]`, `max_item` per candidate | partly |
| **A stored plan** | Plans list | opens it at its head | yes |
| **A chat solve** | Chat card "new plan from it" (§2.7) | the journal entry's args | new (journal) |

### 3.3 Editing live

| Control | Argument | Notes |
|---|---|---|
| Rate field and slider | `export_minimums[item]` | Solves while dragging (§3.4) |
| Objective | `objective` | The power-blind warning (§8.2i) shows when it fires |
| Exports chips | `exports` | MW is an explicit chip. Removing it shows "may now draw from the grid" |
| Sources | `sources` | Region click, drawn circle (`near:`), node shift-click (`node:`), `pin:` refs. Live "38 nodes, 11 fields" plus the selector text |
| Free nodes only | `only_free_nodes` | – |
| **Require a recipe** per process row | `required` (new, Q4, G4) | Alternates drawer (§3.5). One `add` op |
| **Ban** a recipe or process | `exclude_recipes` = `banned` | Works for generators and extractors too (§8.2c). One `add` op |
| Recycle once | `recycle_once` | – |
| Extractor overclock | `extractor_clocks` modes | The shard bill updates; free shards are shown |
| Spread to save power | `clocks`, `machine_cost_mw` | Advanced, hidden by default |
| Per-row clock | – | **Not modelled.** The derived clock is power-optimal (§8.4). Ask first (Q7) |
| Somersloop budget | `sloops` | Free sloops from `somersloops` shown beside it |
| Water extractors | `water_extractors` | BINDING shown when the cap holds the answer |
| **Import instead of mine** | `supplied={item: rate}` | Per raw input: *mine* or *import N/min*. The result says MODULE PLAN |
| Grid power | MW export on/off | Capping at headroom is G7 |
| Belt / pipe tier | `belt_ipm`, `pipe_m3min` | Default: best unlocked |
| Pin a logistics row | `logistics_items` | Presentation only |

### 3.4 What recomputes, and how fast (measured)

Warm process, live save, 490 in-scope nodes, 126 unlocked part recipes, **172 LP columns**
(whole map).

| Call | Median | Covers |
|---|---|---|
| `build_scenario` | 2–3.4 ms | args → Scenario, node scope, `plan_id` |
| `solve` (HiGHS, two phases) | 4.9–5.4 ms | – |
| `prepare` | 6.5 ms | scenario + solve |
| **`build_plan_report`** (= `plan_factory` minus text) | **10.3 ms** HMF 10/min · 12.5 ms max MW whole map · 16.6 ms with 4 extractor clock modes · 14.3 ms `sloops=10` | + free-lunch audit + slice |
| Same, stored plans | 10.4–13.6 ms | `north oil rig` = 801 machines |
| Same, on a fresh `WorldState` | 10.9 ms | the web builds a state per request |
| `build_layout_report` | 6.9–10.4 ms | floors, trunks, materials |
| `build_diff_report` | 9.8–14 ms | 14 ms when the graph facet is cold |
| `build_commission_report` | 7–9.4 ms | – |
| `bom` / `byproducts.analyse` / `compare_routes` | 12.5 / 13.3 / 29.6 ms | – |
| `sweep_unlocks` (79 locked alternates) | **317 ms** HMF · **621 ms** stored plan | 80 solves |
| `load_state` / `proposals` facet | 109 ms / 424 ms | cached per save |
| Save parse after a new save | ~4 s | **documented** in `watch/watcher.py`, not measured here; pre-warmed |

**Consequences:**

1. **Every edit can re-solve.** About 15 ms of server time for the whole result. No job queue,
   no progress stream, no caching, no incremental solve. A push adds a lock, a head read and one
   appended line, likely under a millisecond, but unmeasured.
2. **Slider solving while dragging:** one request in flight, the latest value wins, and stale
   responses are dropped by sequence number.
3. **Only the unlock sweep is slow** (0.3–0.6 s): a button with a spinner, still a plain request.
4. **The first request after a save pays the parse** (~4 s), usually hidden by the pre-warm. The
   page says "reading new save…".
5. **Re-solving a chat entry costs the same 15 ms**, so carrying args instead of results
   (§2.3) costs nothing a person would notice.
6. **Stale figure:** planning.md §8.6 says `diff_vs_save` costs "~1.5 s per call". It measures
   10–14 ms. That line needs correcting (not done here).

Not measured: a late-game save with every alternate unlocked. Measure when it matters.

### 3.5 Recipe swaps: the alternates drawer

- It lists every unlocked recipe making that row's main product (`alternates_for_item`). Locked
  recipes are greyed, with *granted by*.
- Each unlocked option shows **Δ machines, Δ MW, Δ raw** against the plan's head: one
  counterfactual solve per option, ~10 ms each (G5).
- Each locked option shows its `rank_unlocks` gain, if the sweep has run.
- Buttons: **Require** / **Ban** / **Let the solver choose** (removes it from both lists). Each
  is one op.
- The deltas are facts. None is ranked or coloured "best".

`required` (Q4) means "this recipe makes this item". Inside the domain it excludes every other
recipe for that item at solve time, on top of `exclude_recipes`, so a recipe unlocked later is
still excluded. A required entry that blocks the solve (not unlocked, or it needs a banned
input) is **named** in the result, never silently dropped.

### 3.6 Autosave, naming, versions

| Need | How |
|---|---|
| Persist | Every gesture pushes ops (§2.6). There is no Save button and no dirty marker. The header shows `v14 · saved` or `pushing…` |
| Undo / redo | Ctrl+Z pushes the inverse of the player's last op; Ctrl+Shift+Z undoes that undo. The page caches results by request JSON, so the redraw is instant |
| Copy | **Duplicate** creates a new plan at v1 from the head (`create` + snapshot) |
| Rename, notes, factory link | `rename` / `set` ops. Names stay case-insensitively unique |
| Forget | A `forget` op, after confirmation. It can be restored while the log is kept |
| **Versions** | The Versions list is the log: every rev, who, what. **View vN** re-solves that snapshot read-only. **Restore vN** pushes the ops back to it as one new rev |
| Deep link | `#dash=planner/<name>&v=<rev>` opens that version read-only; with no `v`, the head |
| Result snapshot | **Q13**: a display-only result summary per rev would make "since v11: +2 Assemblers" free, with no re-solve. The log stores arguments only |

---

## 4. Views

The Planner is a **whole-page view** like the dashboard (`dash=planner[/<plan>]`), with a map
split for Site (§11 D1). Every view marks chat-changed rows with the **chat** badge (§2.7).

### 4.1 Workbench (key screen 1)

```
+-- Planner > north hmf  v14 · saved · last: Claude Code 20s ago  [Versions] [Duplicate] [Pin] [Ask chat] [⋯] ------+
| ⓘ Claude Code v13: +banned Bolted Frame (+2 Assemblers, −40 MW)  [undo]                     follow: on [off]     |
| GOAL  (o) item [Heavy Modular Frame v] [ 10.0 ]/min ═══●════  ( ) max MW  objective [min machines v]              |
| FROM  [region: Grass Fields x] [pin:3 x] [+ on map]  free only [ ]   -> 38 nodes, 11 fields  [copy selector]      |
|       imports: [Steel Beam  mine | import __/min]   water extractors [auto: cap 200]   sloops [0] / 16 free          |
| RECIPES  required: Pure Iron Ingot, Heavy Encased Frame   banned: Recycled*, Bolted Frame chat   [+]  clocks [100 v]|
+----------------------------------------+------------------------------------------------------------------------+
| ! draws 1,178 MW from the grid         | [Graph] [Build list] [Power] [Byproducts] [Layout] [Site] [Track]     |
| ! Screws 1,413/min = 2 belts           |                                                                        |
| 105 machines · 15 processes            |   (active tab)                                                        |
| −1,178 MW net (exact)                  |                                                                        |
| must build first: Manufacturer         |                                                                        |
| exports: HMF 10.0/min                  |                                                                        |
| [undo] [redo]  [copy as plan_factory]  |                                     plan a1b2c3d4 · sav:3f2a       |
+----------------------------------------+------------------------------------------------------------------------+
```

- Warnings and binding constraints come first.
- An infeasible or empty version shows the headline, the supply diagnosis and "explain
  byproducts". The last feasible version's result stays greyed, labelled with its rev, with
  **[undo last change]**.
- A game `save` event re-solves the head and highlights what moved. A new rev from chat does
  the same, with the chat strip.

### 4.2 Production graph (key screen 2)

```
 depth 0            depth 1               depth 2                 depth 3
 ┌──────────────┐   ┌─────────────────┐   ┌───────────────────┐   ┌────────────────────┐
 │Miner Mk2 pure│══►│Foundry ×16 95.7%│══►│Steel Beam ×9 94.2%│══►│Heavy Encased Frame │══► HMF 10/min
 │Iron Ore ×4   │   │Steel Ingot      │   │Constructor   [p4] │   │Manufacturer ×4 chat│
 └──────────────┘   └─────────────────┘   └───────────────────┘   └────────────────────┘
 ┌──────────────┐        ║ Coal 240/min         ║ 2 belts Mk5
 │Miner Mk2 pure│════════╝
 │Coal ×3       │
 └──────────────┘
```

| | |
|---|---|
| Shows | Process nodes (machines, clock, building, recipe) and item edges (rate, belt/pipe lines). Raw on the left, exports and sinks on the right. Cycles in one column. Pin tags; chat badges |
| Columns | `graph.chain_depth`, the SCC condensation shared by diff and commission (§8.5h), sent by the server. Not recomputed in TypeScript |
| Controls | Node: alternates, ban, pin, ask chat. Edge: pin to logistics, explain byproduct. Hover: rates |
| Data | `POST /api/plan/solve` → `processes[].rates`, `flows`, `depth[]` |
| Reuse | `frontend/src/dash/graph.ts` already draws a built factory's recipe-group graph (frontend_vision.md §9.8). It takes any `{nodes, edges}` of that shape, so a plan can be mapped onto it; its longest-path layers would yield to the server's `depth[]` |
| Rendering | Hand-written SVG, no library. Edge width ∝ log(rate). No new hues |

### 4.3 Build list

| | |
|---|---|
| Shows | Per process: building, recipe, machines, clock, MW (exact), in/out, shards, sloops. Totals per building, never-built first. Construction materials (`plan_layout show=materials`) with need/have/short |
| Controls | Sort, group by building or floor, copy |
| Data | Solve payload + `POST /api/plan/layout?show=materials` |
| Honesty | "Belts and pipes not costed: no route" (§8.5f) |

### 4.4 Power budget

```
 PLAN         draw 1,178 MW (exact)   generation 0    net −1,178      shards 0/22 free    sloops 0/16 free
 GRID NOW     headroom now      6,034 MW  ─────────────────────────  after plan  4,856 MW
              headroom at full    711 MW  ──                         after plan   −467 MW  ◄ red
 [ ] plan must be self-powered (exports MW)          startup order: Track › Startup
```

Data: solve payload + `/api/power` (`headroom_mw`, `measured_headroom_mw`). The two headroom
figures are the dashboard's; neither leads, and each is red when negative. Facts only.

### 4.5 Byproducts

| | |
|---|---|
| Shows | Items exported, sunk or looped: rate, sinkable or not, sink points, consumers (unlocked/locked). On an infeasible plan: the stuck item (`analyse`) |
| Controls | Add to exports, allow sinks, recycle once, "consume with…" |
| Data | Solve payload; `POST /api/plan/byproducts` (after G6) |
| Gap | The player remedies (roadmap §3b) are not built. Show nothing rather than invent (Q5) |

### 4.6 Layout and floors

```
 Floor 3  production  h 11 m  ┌Manufacturer×4┐                          peak 1,200 found. · site 280×280 m
 Floor 2  logistics   h 4 m   ═══ Steel Beam 2×belt ═══ Encased Beam ═══
 Floor 1  production  h 6 m   ┌Assembler×6┐┌Assembler×6┐┌Constr×9┐
 Floor 0  production  h 6 m   ┌Foundry×16─────────┐┌Constr×19┐┌Constr×12┐   ▲ water +2 floors: 1 pump Mk2
 [deck cap: 0 v] [order: chain | head] [belt Mk5 v] [pipe Mk2 v] [fit to: (none) v]  → fits "North Steel" slab: 82%
```

| | |
|---|---|
| Shows | Floor stack to scale (`Footprint.pack`), buses, fluid head with pumps (a lower bound), fit verdict |
| Controls | `max_floor_foundations`, `order_floors_by`, tiers, `factory=` fit, trunks and sites sub-views |
| Data | `POST /api/plan/layout` |
| Not shown | Coordinates and routes (§8.5) |
| Slab packer (garykuepper fork, parked) | `slab_foundations` / `aisle_foundations` grid-pack blocks on a rectangular slab. If merged, blocks gain positions **inside the sited pad** and could be drawn on the map. That departs from "no coordinates" (positions on a declared slab, still no routes). **Q6** |

### 4.7 Site on the map (key screen 3)

```
+---------------------------- map (split) ---------------------------+-- Site ----------------------------+
|        ① ⬤ Iron Ore field  score 0.82   [pin:3]                     | pad 120 × 96 m (given)  yaw 30°     |
|             ╲  straight line, 410 m, LOWER BOUND                   | at 1,204, -3,410 m  z 46 m          |
|        ┌╌╌╌╌╌╌╌╌╌╌╌╌┐  ◄ drag body, ↻ corner, edge resize, 8 m snap |   sited by: Claude Code 14:05 chat |
|        ╎   pad      ╎                                              | terrain: z 38–61 m, slope 7°, dry  |
|        └╌╌╌╌╌╌╌╌╌╌╌╌┘                                              | on the pad now: 14 foundations,     |
|   ② ⬤ Coal field 0.61          water 340 m away, 46 m below         |   2 belts, 0 machines               |
| layers: [x] candidate fields  [x] in-scope nodes  [ ] trunks       | v14 · saved  [Undo] [Rank: Coal v]  |
+--------------------------------------------------------------------+-------------------------------------+
```

Data: a `site` op on drop (`POST /api/plans/{key}/ops`), `GET /api/site/survey` (throttled while
dragging, no push), `GET /api/sites/rank`. When chat runs `site_plan`, the page (following)
flies here, and the pad shows "sited by chat" with Undo. A pad chat moved during the player's drag is
a `site` conflict: the chip offers *keep chat's pad* or *use mine*.

### 4.8 Commission and tracking (key screen 4)

```
+-- Track: north oil rig  (plan a1b2c3d4 · world moved since save ▲) --- live · last save 3m ago -----------------+
| you are in STAGE 1 of 4: 76% built (13/17), 13 proven running                                                     |
| stage  on   built   running  MW draw / gen      free after  state                                                  |
| S1     17   13      13       −701 / +1,750      1,761       76% built                 [show on map]                |
| S2     72   22..25  23       −1,627 / +10,000   10,134      31–35% built                                           |
|------------------------------------------------------------------------------------------------------------------|
| NEXT (free first)   UNPAUSE 2 · SETRECIPE 4 (Assemblers, no recipe, 20 m away) · BUILD 31                          |
| process                 need   built    running  action          where                                            |
| Blender · Diluted Fuel  37     30..30   28       BUILD 7         ●2 stopped (red) ●1 blocked (yellow)  [fly]        |
| Water Extractor         31     8..27    –        BUILD 8..27     range: no node identity                          |
| SHORT  Rubber 8,800 (no machine makes it) [plan this] [ask chat]                                                 |
| Startup order: headroom 711 MW (nameplate) [change]  → W1 16 machines 661 MW …                                   |
+------------------------------------------------------------------------------------------------------------------+
```

- Data: `POST /api/plan/diff` (`plan=` so stages appear) and `POST /api/plan/commission`.
- Each game `save` and each new rev re-runs the diff (~10 ms).
- **Editable while half-built (Q1).** The goal bar and the recipe lists stay live on this tab.
  An edit renumbering the stages is announced (§2.6).
- Stopped machines are red and blocked ones yellow, the map's rule.
- Built counts stay ranges where identity is missing. "Dark" is never reported as "unpowered".
- No ETAs, no progress over time: those wait for the timeline (§22).

### 4.9 Plans list

| name | target | objective | sited | status | version | last change |
|---|---|---|---|---|---|---|
| north oil rig | – | max MW | 1204,-3410 y30 120×96 | world moved | v4 | Claude Code, 2h: sloops 0→4 · [undo] |

Status comes from the `list_plans` logic (moved to the domain, G2): `build_scenario` per plan
(2–4 ms) plus `prov.compare`.

---

## 5. Map integration

| Feature | Behaviour | Data | Constraint |
|---|---|---|---|
| Pad | Dashed outline (exists), now with move/rotate/resize handles, 8 m snap, 15° with Shift | siting | Same maths as `siting.contains_cm` / `footprintCorners` |
| Pad preview | Census and terrain while dragging, ~4/s | `GET /api/site/survey` | Time `Field.window` first |
| Candidate fields | **Numbered pins** sized by untapped rate; popup shows every raw term and the weights | `GET /api/sites/rank` | A heat raster needs a colour ramp: Q12 |
| Whole-bill siting | One pin set per raw input + a "nearest field per input" table | G9 | A second table, not folded into the score (roadmap §2.1) |
| In-scope nodes | Free / tapped by the right extractor / tapped by the wrong one | `PlanRequest.node_rows` | The LP picks counts, not nodes |
| Trunks | Node-chain polylines, rate/capacity, `UP 40 m (1× Pump Mk2)` | `plan_layout show=trunks` | `run` is a straight-line lower bound |
| Inputs | Dashed lines from source fields, supplier factories, water | trunks, `site_water` | Distance, not a route |
| Source picking | Circle → `near:`; region click → `region:`; node → `node:`; pin → `pin:` | `GET /api/select/nodes` | The selector text is always shown |
| **Chat focus** | Journal `focus` / `label.*`, plan-log `site` ops → fly, outline, pulse (when following) | `activity` / `plans` SSE | Waits until the player's drag ends |
| **Pins** | Numbered tags on pinned points, fields, machines, factories | pins store | – |

---

## 6. Write and state model

One source of truth per plan: its **op log** (§2.6). Nothing about a plan lives only in the
browser or only in one MCP process.

| State | Lives in | Writers | Concurrency | Guard |
|---|---|---|---|---|
| **Plans** | op log + snapshots | chat (N processes) + page | per-plan lock; `base_rev` on every write; auto-merge, or **outdated** on a real conflict | page: Host/Origin + `as_of` |
| Labels | `labels/<world>.json` | chat + page | lock + version counter (detect work); later the op log | Host/Origin + `as_of` |
| Pins, focus, asks | new small stores | page (web process) | single writer, no lock | Host/Origin |
| Activity journal | per-process files | every process | append only, one writer per file | – |
| Undo | the op log (inverse ops) | both | as any write | as any write |
| Result cache, open tab | browser memory | page | – | – |
| Follow setting, split sizes | `localStorage` (try/catch) | page | convenience only | – |
| Solutions | nowhere | – | re-solved, ~15 ms | – |

**Write path, identical for both sides:**

```
writer ──(ops or full args, base_rev)──► lock plan ─► read ops since base_rev ─► merge (§2.6)
   ├─ clean    ─► append at head+1…, snapshot if due, unlock ─► "merged onto vN; others changed …"
   └─ conflict ─► unlock, append nothing ─► "outdated: head vN, ops since vB, conflicting keys …"
```

A chat write sends full arguments; the server turns them into ops against the `base_rev`
snapshot first. A page write sends ops.

**SSE.** Replace the one `notes` kind with:

- `plans`: payload `{plan, rev, actor, ops summary}`. The page applies it directly when it is on
  `rev-1`, and otherwise fetches `GET /api/plans/{key}/ops?since=`.
- `labels`.
- `activity`: journal entries.

`save` stays as it is. The web server tails the per-plan `ops.jsonl` files and the journal
directory every 0.5 s (§2.9).

---

## 7. Backend work

### 7.1 Routes and tools

Typed routes (`response_model`, regenerate `api-schema.d.ts`) call domain functions only
(frontend_vision §5.1). `PlanRequestBody` = `PLAN_ARGS` + `required` + `logistics_items` +
siting. Plans are addressed by a stable `key`, so a rename is just an op.

| Route / tool | Method | Domain source | Cost | W |
|---|---|---|---|---|
| `/api/plan/solve` | POST (args, or `key`+`rev`) | `build_plan_report` + `slice_of` + `chain_depth` | 10–17 ms | – |
| `/api/plan/alternates` | POST | new `swap_deltas` (G5) | ~10 ms × options | – |
| `/api/plan/byproducts` | POST | `byproducts.analyse` (after G6) | 13 ms | – |
| `/api/plan/layout` | POST | `build_layout_report` | 7–10 ms | – |
| `/api/plan/diff` | POST | `build_diff_report` | 10–14 ms | – |
| `/api/plan/commission` | POST | `build_commission_report` | 7–9 ms | – |
| `/api/plan/unlocks` | POST | `sweep_unlocks` | 0.3–0.6 s | – |
| `/api/bom`, `/api/compare` | POST | `build_bom`, `compare_routes` | 13 / 30 ms | – |
| `/api/plans` | GET: head rev, status, last op, actor | plan log + `list_plans` logic (G2) | ~5 ms/plan | – |
| `/api/plans` | POST (create, duplicate) | plan log `create` | ms | W |
| `/api/plans/{key}` | GET (`?rev=`) | snapshot + replay | < 1 ms (unmeasured) | – |
| **`/api/plans/{key}/ops`** | POST `{base_rev, ops[]}` → merged `{rev, others}` or **409 outdated** `{head, since[], conflicts[]}` | plan log merge (G1) | ms | W |
| `/api/plans/{key}/ops` | GET `?since=` | plan log | ms | – |
| `/api/plans/{key}/undo` | POST `{rev}` | inverse ops → same merge | ms | W |
| `/api/pins`, `/api/asks` | GET / POST / DELETE | new | ms | W |
| `/api/ui/focus` | PUT (debounced, + heartbeat) | new | ms | W |
| `/api/activity` | GET (since, actor) | plan logs + journal, merged | ms | – |
| `/api/events` | SSE (exists) | + `plans`, `labels`, `activity` | – | – |
| `/api/site/survey` | GET | `siting.survey` + `Field.window` | **measure** | – |
| `/api/sites/rank` | GET | `rank_build_sites` body → domain (G2) | measure | – |
| `/api/select/nodes` | GET | `select_for` + `annotate` | < 5 ms | – |
| `/api/gamedata/alternates` | GET | `alternates_for_item` domain | ms | – |

**MCP surface.**

| Tool | Change |
|---|---|
| `plan_factory`, `plan_layout`, `diff_vs_save`, `commission_plan`, `rank_unlocks`, `list_plans` | Print `plan "…" vN` whenever a stored plan is involved |
| `plan_factory save_as=<existing>`, `rename_plan`, `forget_plan`, `site_plan` | New **`base_rev`**; refused without it on an existing plan. Answer: merged note or **outdated** with the ops since `base_rev` |
| `plan_factory` | New `required` argument (Q4) beside `exclude_recipes` |
| **`plan_log`** (new) | `name`, `since=` (ops in words), `undo=<rev>` and `restore=<rev>` (both need `base_rev`) |
| **`ui_context`** (new, + resource mirror) | Focus, pins, asks, plan ops and journal since this session's cursor |
| `show_on_map` | Also journals a `focus` event |

That is two new tools and one new argument on four write tools. The descriptions stay one line
each, because they are resident in every session's schema.

### 7.2 Gaps

| # | Gap | State today | Needed for | Size |
|---|---|---|---|---|
| **G1** | **Plan op log**: the store, the merge, snapshots, migration; `base_rev` on the MCP write tools | Load → mutate → save in N MCP processes and the web; lost updates are silent | Everything that writes a plan, from either side | **Medium, first** |
| **G0** | Activity journal | Only mtime polling | Chat solves on the page, focus, "since you last looked" | Small |
| G2 | Logic in tool bodies | Plan save, list status, plan detail, rename validation, site ranking live in `tools/planning/` / `tools/spatial/` | Routes must share it | Medium, mechanical |
| G3 | No structured results | Text presenters only | Every route | Medium: 7 report types |
| G4 | `required` recipes (Q4) | Global bans only | Require in the drawer; named blockers when infeasible | Small |
| G5 | No in-plan what-if per alternative | – | Alternates deltas | Small |
| G6 | `byproducts.analyse` takes 8 of 18 args | – | Explaining the plan shown (the §8.5a drift class) | Small |
| G7 | Grid import all or nothing | `1e6` unless MW exported | "Fit in my headroom" | Small, **ask (Q9)** |
| G8 | No plan seeded from a built factory | – | "Again, bigger" | Medium, **ask (Q10)** |
| G9 | Whole-bill siting | Roadmap §2.1 | Site tab | Medium |
| G10 | Labels on the op log | Lock + counter (in progress) | Label merge and undo across both sides | Small once G1 exists; later |
| G11 | Pins not in selector grammars | – | `pin:N` in chat | Small per grammar |
| G12 | Layout has no positions | By design; fork parked | Blocks on the map | **Q6** |
| G13 | Stale figure in planning.md §8.6 | "~1.5 s" | – | One line |

### 7.3 Performance, honestly

- **The solver is not the bottleneck.** ~5 ms per LP, ~15 ms per full report, ~8 kB of JSON for a
  15-process plan.
- **The log is cheap by construction.** A push is a lock file, a tail read since `base_rev`, a
  merge over a handful of ops and one appended line. A load is one snapshot plus ≤ N−1 ops.
  None of it is measured, because none of it exists. Time it in P1 together with the lock's
  worst case (a chat push racing a page push).
- **Autosave volume.** One push per gesture, not per slider tick. Even a busy evening is
  hundreds of ops (tens of kB).
- **SVG graph rendering** may cost more than the solve on big plans. Unmeasured.
- **Threads.** FastAPI runs sync routes in a pool. Whether HiGHS releases the GIL is unverified.
  It does not matter at one user.
- **Cold paths:** a ~4 s parse per new save in **each** MCP process and the web server
  (pre-existing), 0.42 s for proposals, 0.3–0.6 s for the unlock sweep.

---

## 8. Phased roadmap

**P1 already runs the loop in both directions, and it needs the op log.** Autosave has no
"read-only first slice": the first page edit is a shared write.

| Phase | Slice | Page → chat | Chat → page |
|---|---|---|---|
| **P1: log + loop + workbench** | G1 plan op log (store, merge, snapshots, migration, `base_rev` + versions in the MCP tools, `plan_log`); G0 journal; `plans`/`activity` SSE; follow on by default; workbench with live solve and autosave for goal, rate, sources, exports, bans, required (G4), water, sloops, extractor clocks; summary, build list, power budget; undo (inverse ops); conflict chip; `ui_context`; Ask chat | **yes**: the agent reads the plan at its version, the focus and the asks | **yes**: chat edits land as versions; chat solves open as cards |
| P2: history and plan management **(built 2026-09-27; [plan_management.md](plan_management.md) lists what is left open)** | Versions list, view/restore, duplicate, rename/forget from the page, changed-by-chat diff with result deltas, Activity panel; G2 | "since you last looked" | same |
| P3: graph, alternates, pins **(backend built 2026-09-27; [planner_p3.md](planner_p3.md))** | Production graph, alternates drawer (G5), pins + `pin:` in selectors (G11) | pins | chat badges on nodes |
| P4: track **(backend built 2026-09-28; [planner_p4.md](planner_p4.md))** | Diff with stages, actions, ranges, startup waves, drift live on save; editable while half-built, with stage renumbering announced | ask from any row | `diff_vs_save`/`commission_plan` open Track |
| P5: site | Draggable pad (`site` ops), survey, candidate fields, trunks, inputs | pinned fields and points | `site_plan` flies the map |
| P6: layout | Floor stack, deck cap, head order, fit; slab packer if Q6 | – | `plan_layout` opens Layout |
| P7: byproducts | G6, recycle once. Modules and `supplied` stay later (Q2: one factory per plan) but are not designed out | – | – |
| P8: entry points | From deficit, plan shortfall, milestone; proposal if Q10 (G8) | – | – |
| P9: unlock value; labels on the log | Unlock sweep against the head; G10; an elicitation trial for chat-side conflicts | – | – |
| P10+ | G7, G9, per-row clocks, a faster channel: in an order still to be picked | – | – |

---

## 9. Questions: decisions needed

Nothing still open below is assumed above. Memory `verify-play-patterns`: ask, don't encode.

### 9.1 Settled 2026-09-27

- **L1, clients.** Claude Code today; Claude Desktop wanted; the web "maybe". Code and Desktop are
  both local stdio clients, so the loop holds for both unchanged. Web is covered through Remote
  Control: the chat runs in Claude Code on this machine and the MCP server stays local. A
  claude.ai remote connector is parked: it would need a publicly reachable server, which
  conflicts with the 127.0.0.1 binding. That requirement is unverified; check it before any work
  towards one.
- **L2, follow.** Auto-follow by default; toasts-only and off stay as settings (§2.3).
- **L5, state.** One shared plan state, versioned like git. Autosave on every move. Writes carry
  `base_rev`, and a newer head means merge or retry. The history is an op log plus snapshots,
  and that log is also the undo record and the activity feed. It replaces drafts and "chat wins"
  (§2.6, §6).
- **Q1, when you plan.** Before building, then adjusted a bit during. The workbench leads, and
  plans stay editable while half-built (§2.6, §4.8).
- **Q2, scope.** One factory per plan for now. Chains are later work, not designed out.
- **Q4, recipes.** The solver chooses. **Required** and **banned** lists override it. Required
  means "this recipe makes this item"; a required entry that blocks the solve is named (§3.5).
- **Old Q14** (should the page write plans at all): answered by L5, yes.

### 9.2 Proposed, needs a decision

- **M1. Confirmed 2026-09-27.** The merge rule in §2.6: auto-merge when edits touch different keys; *outdated* only on
  a real conflict (same scalar, same map entry, add vs remove of one member, required vs banned
  of one recipe, site vs site, anything vs forget). Nothing is applied partially.

### 9.3 Open

**The log (new)**
- **N1. Snapshot cadence.** Every 50 ops (proposed), or by size or time?
- **N2. Retention** (was L7). Keep every op forever (small: ~150 B each), or compact old history
  into snapshots after some months? Retention is also the undo horizon and how long a forgotten
  plan can be restored.
- **N3. What counts as a list conflict.** Beyond M1:
  - Two **required** recipes for the same item: allowed (both may run), or a conflict?
  - Chat **bans** a recipe the plan currently **uses** while the player edits the rate: clean by keys,
    but it changes what the rate edit means. Clean (proposed), or flag it?
  - A clean merge that turns the plan **infeasible**: accept and show it (proposed), or refuse?
- **N4. Exploration.** Should every starting point create a named plan, or go into a reusable
  **scratch plan** per world (itself versioned, and visible to chat as `plan="scratch"`) until you
  name it?
- **N5. One move = one version.** Proposed: a push on release of a slider or drag, and once per
  discrete click. Finer (every tick) or coarser (every few seconds)?
- **N6. Chat without `base_rev`.** Refuse (proposed, so chat always reads before writing), or
  treat a missing `base_rev` as "the head I last read in this session"?

**The loop**
- **L3.** Should *every* agent solve appear on the page, or only the ones the agent explicitly
  shows (`show_on_map`)?
- **L4.** Should the agent call `ui_context` on its own when you say "this" (a server instruction,
  costing context every time), or only when you mention `ask:`/`pin:`?
- **L6.** May the agent **create pins** (e.g. pin the fields it recommends), or only read yours?
  *Decided 2026-09-30: yes, with `show_on_map(pin=True)`.*

**How you plan**
3. For "X per minute of Y", which default: **fewest machines**, **least raw**, or **least power**?

**Recipes and clocks**
5. **Byproducts:** sink, package, a dedicated consumer, or reshape so there are none?
6. **Exact foundation layouts:** is the floor schematic enough, or do you want blocks placed on the
   pad (the parked slab packer)?
7. Do you **overclock production machines**, or only extractors?
8. Somersloops: **spend in plans, or hoard**?

**Power, inputs, siting**
9. Should a plan's grid draw be **capped at real headroom** (G7), or is the before/after display
   enough?
10. **"Again, bigger"** from a built factory or proposal (G8): wanted?
11. Do you **pick the ground first** and fit the plan, or **plan first** and find ground?
12. Candidate fields: numbered pins, or a heat layer? A heat layer is a palette decision, and those
    need the map in front of you.

**Versions**
13. Keep a **display-only result summary** per rev? It would show "since v11: +2 Assemblers"
    without re-solving; the log itself stores arguments only.
14. **Labels onto the op log** (G10) once the plan log exists, or does the lock + counter stay
    enough for labels?

---

## 10. Constraints carried over

- **Local only.** No graph library, no CDN, no model key, no network from the page (§2.8).
- **Palette closed.** Stopped red, blocked yellow; chat is marked by a text badge, not a hue. Any
  map colour goes through `declareColours` and the ΔE audit.
- **Facts, not advice.** Deltas are shown, never ranked. No ETAs.
- `–` for unknown, never 0. Ranges stay ranges.
- **One question, one answer.** Routes and tools call the same domain functions. TypeScript
  sorts and lays out drawings. It does no planning and no merging: merges happen on the server.
- **Prose in docs**, not code comments.

---

## 11. Where this departs from frontend_vision.md, and why

| # | frontend_vision says | This note says | Why |
|---|---|---|---|
| D1 | Planner is a 340 px rail panel (§2.1, §2.5) | A whole-page view like the dashboard, with a map split for Site | §8.3's width argument applies harder to a graph plus a process table |
| D2 | Title: "every tool, no chat needed". Q2 asks whether chat stays the main way in | The page and chat are **co-equal partners on shared state** (§2) | Request, 2026-09-27: "interactive with chat too" |
| D3 | The browser holds no planning state; `notes` SSE refetches | Plans are an **op log** shared by both sides, with autosave and no drafts. Focus, pins and asks are server-side, and a journal carries provenance | L5; the agent can only see what the server can read |
| D4 | Phase 9: guard + plan CRUD (save, rename, forget) | Git-like versions with `base_rev` and auto-merge (G1), in the **first** slice | Autosave makes the first page edit a shared write. Several MCP processes plus the web server write the same plans; lost updates are silent today |
| D5 | "Measure solve latency before choosing sync or async" (§5.2) | Measured: sync everywhere | ~15 ms per solve, 0.6 s worst case |
| D6 | One `notes` SSE event (built) | `plans`, `labels`, `activity` | The page cannot tell the trees apart (same filename in both) |
| D7 | Compare routes fills `only recipes` (§4.1) | Per-row **required**/**banned** with in-plan deltas | Q4. `only_recipes` is global and bans every other item's alternates |
| D8 | Site drag is in phase 9 | Site at P5, after Track | Tracking only reads; siting is a second write surface. Reorder if Q11 says ground comes first |
