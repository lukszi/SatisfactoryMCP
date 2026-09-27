# Backlog: what is broken, what is missing, in the order to fix it

From three audits on 2026-08-02 (tool-chain coherence, map/text seam, unexposed domain
knowledge) plus the field report an LLM client filed after playing with the surface.
Deduplicated: where two audits found the same thing from different sides it appears once, with
both citations. Numbers are stable and are cited elsewhere, so a closed item keeps its number
rather than being deleted. Parked FEATURES are [parked.md](parked.md) §21 and §22; work that is
not a defect is [roadmap.md](roadmap.md).

**State, 2026-08-10: 39 of the original 40 closed, and item 36 is the one remaining — a
test-file assertion, not a surface feature.** Items 37 and above were filed later and are not
part of that count.

P0, P1, P2 and P5 are done outright. What is left is P3, and only P3.

**On 2026-08-10 the owner withdrew this file's standing posture on convention drift** — *"I
want a unified and sleek interface, no duplicate vocabulary even to keep backwards compat."*
Five rows closed that day as **breaks** rather than aliases: the retired spellings error and
name their replacement. They closed in a chain, and the chain is the point — settling the
radius syntax exposed that the two sides accepted different *places*, and settling that removed
the last place resolver in the codebase that was not `resolve_origin`. **There is now one place
grammar, one radius grammar and one resolver.** Two deliberate rows survive, because settling
them is a judgement about the surface and not a defect.

**Two sections were added on 2026-08-10 and are deliberately not counted above.**

**P6, items 37–42** — in-game measurements the fluid model is waiting on. Not code defects at
all: each is a rig to build in the reference world, and every one of them needs someone in the game.
They are here because the alternative is that they are remembered rather than findable.

**P7, items 43–55** — thirteen items that lived only in [roadmap.md](roadmap.md), in the parked
ledgers or in conversation, and were therefore invisible to anyone reading this file. Filed so
that one document answers "what is left".

Each closed row names the commit that closed it. The claims below were re-verified against the
code on 2026-08-06 rather than taken from the commit titles, and two of them turned out to have
been wrong when they were written; those are marked.

**The one structural finding, which explained a third of this list, is closed.** The
projection's richest recent additions — storage (15), floors, crates (18), the crate inventory
bucket (19) — had gone to the web map and never to the LLM, so an assistant could not ask what
the map drew. The general fix was one habit rather than one commit: **a projection key is not
finished until both interfaces read it.** It held. The habit is the part that has to survive,
and the two modules landed yesterday — `domain/world/logistics.py` and `domain/world/timeline.py`
— are already the same shape from the other direction: correct, cached, and read by nobody.

---

## P0 — answers that were wrong or crashed

All closed. These produced a false belief in the reader, so nothing outranked them.

| # | What it was | Closed by |
|---|---|---|
| 1 | `trace_upstream` raised `TypeError` for every factory label and every selector — its own parameter help listed labels FIRST — because it indexed `resolve_factory`'s `list[str]` as dicts. | `6918c57` |
| 2 | `show_on_map` handed the player a satisfactory-calculator.com link for every target except a sited plan, so the README's flagship example opened a site that cannot draw their world. `local_map_url` is now unconditional and comes first; the README says the public map comes second. | `0e9730b`, `6d3fdae` |
| 3 | `describe_location` stated "there is no heightmap in any data this reads" while `/api/inspect` printed terrain from the 1 m heightfield on the same machine. It now passes `terrain_field` through the seam that was always there and prints `terrain_m`. | `25147cb` |
| 4 | `machine:<id>` accepted any string and answered "0 machines" for machines that exist; printed ids were truncated to their last 10 chars and resolved to nothing. Unknown ids now raise `SelectorError` naming them, and instances print in full. | `32274b9`, `986d513` |
| 5 | `commission_plan(limit=60)` sat outside its own published schema, so a client echoing the default got a hard error. | `fc59db9` |
| 6 | `search_recipes` and `alternates_for_item` took `save` but not `world` while computing HAVE/LOCKED from world state, so a multi-world install was answered against the default world. | `e164ca2` |
| 7 | Belt length disagreed between halves — the map drew the true Hermite spline, `search_conduits` summed chords, up to 16.4 m out on one piece. `_length_m` now integrates the span and falls back to the chord only where no tangents exist. | `4af96e3` |
| 8 | `list_regions` printed a raw centroid, which for a concave region lands in its neighbour. `label_anchor()` is now the single implementation, called by both sides. | `e588fcb`, `4a26bfd` |
| 9 | Three different "already named" filters over one clusterer. `LabelStore.covers()` is now the one predicate, called by the router and the tool. | `1ba254e`, `a5714ca` |
| 9b | `/api/factories` published `score: 0.0` for every proposal: the merge loop computed real per-cluster cohesion and the constructor hardcoded zero. Keyed by `frozenset` beside `seeds` and looked up by `held`, exactly as `seeded_by` does. | `ddd6c30` |
| 9c | `mam_research` said cost was checked against "carried, crates and the Dimensional Depot" when schema 19 deliberately took crates OUT of spendable stock. Both the tool note and the same stale sentence in `save-projection.md` now say storage containers, and name what does not count. | `a0a2ec9` |
| 9d | `mam_research` ignored `research["unlocked_trees"]` and `research["ongoing"]`, so a node in an unopened tree read `READY` and research already underway read `todo`. Now `TREE SHUT` and `RUNNING {n}s`, both excluded from `startable=`. | `a0a2ec9` |
| 9e | `trace_upstream` carried `Trace.ambiguous` and `Trace.truncated` and reported a possibly over-counted, depth-truncated walk as complete. Both print, and a truncated walk says it is a FLOOR. | `43fac87` |

## P1 — instructions the client could not follow, and dead ends

All closed.

| # | What it was | Closed by |
|---|---|---|
| 10 | 15 tools printed "call again with `offset=N`" and had no `offset` parameter; only 5 of 44 accepted one. 23 of 50 tools take one now, `search_resource_nodes` among them, and the sentence is gated: passing `offset` to `render.table` is the caller's DECLARATION that it pages, and everything else says "narrow the query, or raise limit". | `91c4d90` |
| 11 | `search_conduits` printed `connects: pipe:333 -> pipe:335` and told the reader to follow it while taking no run id. `resolve_origin` now accepts `chain:`/`pipe:` for every tool that resolves a place. The spelling was settled on the tool's ident; the map's belt popup keeps `chain #n` as a caption of the same number. | `8f6f03e` |
| 12 | `diff_vs_save` ordered actions and dropped the instance ids it had already computed. Each actionable row carries `act_instances` — the verb's own machines, deliberately not the first three of `have_instances` — rendered as reusable `machine:` selectors. | `ce2ba66`, `ae87521` |
| 13 | Bare platforms were listed by an index no tool accepted. `slab:<n>` now resolves for `describe_location`, `show_on_map` and `plan_factory(site_at=)`, and closes item 28 with it. | `1b6b242`, `56abf09`, `70df1ae` |
| 14 | `diff_vs_save` documented a plan-id cross-check that `plan_factory` never printed for an unsaved plan. | `7d063c9`, `97ce1f6` |

## P2 — the two halves disagree, or one half is blind

All closed.

**15 — CLOSED.** Floors, storage contents and crates were closed first: `factory_floors`
(`interfaces/mcp/tools/floors.py`), `storage` and `crates` (`interfaces/mcp/tools/inventory.py`),
landed by `4f47e33`, `571a59a` and `3678718`.

The power half was less blind than this row originally claimed, and the correction mattered
because it changed the remedy. Power ISLANDS already reached text:
`domain/factories/identity.py::bases` calls `graph.machine_components("power",
skip=graph.towers())` and `factory_map show=candidates` prints them under
`## power islands (bases)`.

What landed instead is the salvage [roadmap.md](roadmap.md) §5 asked for, in two steps.
`deadb0b` named the machines **no wire reaches**. `9201bc9` widened it to the case `deadb0b`
could not see: a machine can be wired and still have **no generator anywhere on its circuit**.
Two disjoint lists, `unwired` and `no_generator`, are reported beside every state, and
`stalled`'s old "power, or a monitor that lies" is now precise in both directions.

It is deliberately **not** the per-circuit ledger, which stays **measured false** — and the
widening explains §5's "two strays of 5/5": they are two rows of five Oil Refineries wired to
each other and to no generator at all. The claim, its two refusals and the open-switch blind
spot are written up in [save-projection.md](save-projection.md) §6.1a.

| # | What it was | Closed by |
|---|---|---|
| 16 | The map said `OreIron`, the assistant said `Iron Ore`; `/api/nodes` was the only payload that did not resolve a display name. `NodeRow.resource_name` now does. | `838d8ab`, `bc2aaa8` |
| 17 | The map had no `LOCKED` state: a node the text surface excluded as unreachable was drawn as an ordinary free dot. `NodeRow.reachable` carries it, `None` where the unlock set is unknown. | `839d389` |
| 18 | `name_factory` and `site_plan` wrote to the labels and plans directories while the watcher globbed `*.sav` only, so the one collaborative moment was the one the map missed. Both directories are watched and publish a second event kind. | `5b694d1` |
| 19 | Every popup printed an MCP selector and nothing on the page could copy one. Click-to-copy in `dom.ts`/`copy.ts` upgraded every popup at once. | `27c5f66` |
| 20 | No tool could name an item you own or where it is: 151 container rows, crate contents and four inventory buckets were read by the web routers and by zero MCP tools. `stock(item=, where=)` prints spendable, carried, storage, depot, buffers and crates, and with `where=True` joins to region names. `machine_buffers()` has a reader at last. | `571a59a`, `3678718` |
| 21 | The only measured numbers in the project survived as two scalars: every per-machine figure weighted by its own 300 s monitor was destroyed at the moment it was computed. `FactoryView.measured_draw_mw` accumulates in the loop that was already running; it prints in `factory_query(of="power")` and as a `measured MW` column in `factory_health`. | `c39a391` |
| 22 | `phase_requirements` printed what a phase still needed and stopped, while `mam_research` did the stock join 150 lines away in the same file. `have` and `short by` columns, plus a deliverable verdict. | `2acc502` |
| 23 | `tapped_by` and `tapped_clock` were computed for every node on every call and rendered as the bare word "tapped". The occupant now prints as `Miner Mk2 @250% OFF`, with z. | `f205ade` |
| 24 | Truncation without an envelope, in four places: `somersloops`, `diff_vs_save`'s cost table, and `factory_health`'s `blocked_on` and `starved_of`. All four pass `total`. | `91c4d90`, `8f37f21`, `26da417` |
| 25 | 3-D positions rendered as 2-D throughout: `MachineRow.pos` never printed, `factory_sites` dropping centroid z, occupied slabs losing the bbox and z-span that bare slabs got. All print. | `4fa18da`, `85a3776` |

## P3 — convention drift

**All eleven closed**, in one chain on 2026-08-10 under the owner's instruction: *one
vocabulary, no alias kept alive for compatibility.* Each unification exposed the next
divergence under it — the radius syntax showed that the two sides accepted different **places**,
closing that removed the last place resolver which was not `resolve_origin`, and the inventory
taken for the last three rows turned up a fourth family nobody had named.

**A correction, kept because the ledger is a record and not a scoreboard.** Until 2026-08-10
three of these rows read "deliberately left alone" and "narrowed". **Nobody ever decided that.**
The words were written on 2026-08-08 by the pass that closed the rows around them, in this
file's voice, and a reader — including the assistant, later, out loud to the owner — took them
for a ruling. They were open and unexamined the whole time.

- **Two radius grammars — CLOSED** (`538f08f`). `@` won on an argument rather than a
  preference: `save-projection.md` states that **commas inside one term are ORed**, so a radius
  as a third comma value made `near:` the one exception to a rule the language already had.
  `near:<place>@<radius_m>` on both sides, parsed by one shared `origin.parse_near`, and the
  radius is now **required** — the machine side's undocumented 150 m default was the same
  complaint one level down. The comma form errors with the rewrite generated from the input.
  The grammar is written out in [selectors.md](selectors.md).
- **`describe_location`'s two floats — CLOSED** (`538f08f`). `x_m`/`y_m` are removed. `at=`
  strictly subsumed them and is what every other tool speaks. No web router called it. One
  echo fell out with them: `at="239,-1928"` used to print the coordinate twice, and the repeat
  is now suppressed when the resolved name is the coordinate itself.
- **Five spellings of "which view" — CLOSED** (`c78125b`). `show=` is the parameter; `of=`,
  `detail=`, `mode=`, `status=` and `group=`-as-a-view are retired and error with the caller's
  own value rewritten. The value sets stay per tool **on argument rather than by omission**:
  `show=` asks "which of *your* views", not "which kind of thing", and the only two words that
  recur across the eight tools — `all` and `nearest` — already agree.
- **`kind=` meant five vocabularies — CLOSED as a split** (`b5de971`). They were five
  questions about five row types, so they stopped sharing the word: `recipe_kind=`,
  `building_kind=`, `container_kind=`, `conduit_kind=`, one grammar so a schema reader knows
  which vocabulary to expect. `search_resource_nodes(kind=)` keeps the bare word and is now the
  surface's only `kind`, because it is shorthand for the node selector's `kind:` term and
  renaming the parameter alone would have given one tool two spellings of one filter. **The
  `"all"` bug is fixed at the selector** — every filter term reads `all` as "no filter" — so it
  cannot come back per tool. Two things fell out: `search_recipes` used to treat an unknown kind
  as a filter matching nothing, so `recipe_kind="parts"` printed an empty table that read as
  "the game has no such recipe", and it raises now; and the undocumented `any`/`both` are gone.
- **Name filters — CLOSED** (`41d31e2`). `query=` is the one name filter and `search=` is
  retired; `query=` was already primary on the two tools whose own name is `search_`.
  `with_resource=` was a second spelling of `resource=`, which is the axis three other places
  already use, and is retired. `group=` **stays** on `collected_from_world`, where it is a real
  category filter and where the wire, the JSON envelope and the domain view all already spell it
  `group` — renaming only the MCP parameter would have made that agreement worse. Its second,
  unrelated meaning went with the view row above.
- **`near:` accepts a different set of PLACES on each side — CLOSED** (`798efca`). Both
  selector modules are handed the world state and call `origin.resolve_origin`; **neither
  resolves a place itself any more**, which is what the divergence was actually made of. Seven
  kinds work everywhere `near:` appears — `x,y`, `me`, a factory, `node:<id>`, `slab:<n>`,
  `chain:<n>`/`pipe:<n>`, `plan:<name>` — and the two map facts among them resolve with no save
  at all. `select_machines` stopped taking six facets and takes the world state, which also
  made `st.proposals` lazy: that half-second view was previously built on every call, by both
  callers, whatever the term said.
- **A fourth spelling of "which place": `show_on_map(target=)` — CLOSED as a split**
  (`798efca`). The superset was not one thing. `node:<id>` and `plan:<name>` are points and
  moved into the shared resolver, so every place-taking tool now accepts them, and the
  parameter is `at=`. The resource name **stayed**, respelled `resource:<name>`: it centres on
  the centroid of EVERY node of a resource, which names a set rather than a place. The answer
  already had to warn that such a centroid can be open water, and in the shared resolver it
  would let `plan_factory(site_at=)` site a factory in the sea. The bare name is retired, so
  unprefixed text is a factory label here as everywhere. Both halves are argued in
  [selectors.md](selectors.md).
- **`factories/select.py`'s module docstring said slabs sort "largest first" — DONE**
  (`798efca`). It says "by its own printed index", which is what `_resolve`'s code comment
  always said.
- **Boolean filters have no convention, and one of them is a duplicate — OPEN.**
  `search_resource_nodes(only_free=)` and five planning tools' `only_free_nodes=` are the **same
  question** — "only nodes nothing is already extracting" — spelled two ways across six tools.
  Around them `only_alternates`, `include_locked`, `include_events`, `unnamed_only`, `empty`,
  `where` and `show_cost` mix `only_`, `include_` and bare names with no rule, and two of them —
  `stock(where=)` and `diff_vs_save(show_cost=)` — are **which-view questions wearing a
  boolean**, which is arguably a sixth spelling of the row above. Found by the inventory that
  closed the last three rows, on 2026-08-10, and deliberately not touched by it because it was
  not what was approved.
- **`rank_build_sites(top=)` is the last alias on the surface — OPEN.** It is marked DONE below
  as "`top=` is a deprecated alias", which was the right call under the old posture and is the
  wrong one under the new: an alias is exactly what the owner asked to stop keeping alive. It
  should error naming `limit=`. Three lines.
- **`rank_build_sites(top=)` unbounded — DONE.** It takes `Limit`; `top=` is a deprecated alias.
- **`render.table`'s `limit=` a documented no-op — DONE** (`91c4d90`). It truncates.
- **Three off-map spellings — DONE.** One `OFF_MAP` constant in `domain/spatial/regions.py`.

## P4 — missing round-trips

- **`flow.py` did not know the T junction — DONE.** `_BODIES` listed the cross and both tanks
  by build class, so a `Build_PipelineJunction_T_C` was not one volume: its three ports stayed
  three unjoined nodes and the pipe network was CUT at every T. Measured on the owner's newest
  save, which holds 12 of them: 92 of 657 pipes could not be oriented that now can, and **no
  confident answer changed** — it cost coverage, not correctness. Invisible until now because
  the reference fixture contains no T junction. `domain/world/headlift.py` avoids the whole
  class of bug by matching on `Building.native`, where the T and the cross are one class.
- **Plans: no rename, no way to read a stored plan's arguments — DONE** (`0dd05e4`).
  `rename_plan`, and a detail view that reads one stored plan in full without solving it.
- **Labels: no rename; cannot add or drop one machine — DONE.** `rename_factory` and
  `amend_factory` (`add=`/`drop=`/`prune_missing=`, the selector grammar on both sides). A label
  holds a materialised id set, so membership is set arithmetic and nothing had to be
  re-anchored. Rename refuses a taken name *and* a taken slug, because `find` matches both, and
  the id moves with the name or the old one goes on resolving. Dropping the last machine is
  refused and names `forget_factory`. A stale anchor goes only when `prune_missing` asks.
  "Already named" goes through `covers()`, per item 9. Stored plans scoped to the factory follow
  the rename.
- **Sitings: `list_plans` showed `x,y` only — DONE.** Yaw and footprint are in the row.
- **`factory_sites` rows carried no identifier — DONE** (`85a3776`), with altitude back.
- **`maplink.COLLECTIBLES` was dead because its keys were a vocabulary nothing else spoke —
  DONE.** It said `hard_drives` and `slugs_green` where the placement table says
  `crashed_drop_pod` and `power_slug_blue`, so the join was impossible rather than merely
  unwritten. Re-keyed to the table's own categories. `collected_from_world` now emits a local
  map link first and the public one after, and the page grew a `pickups=` fragment key so a
  link can tick a layer that starts off. The structural lesson holds — a projection key is not
  finished until both interfaces read it — and here neither did, because the key was spelt in
  a third language.
- **`/api/collectibles` carries no `looted`, so the map cannot tell an empty drop pod from a
  full one — DONE** (`3b46170`). `CollectibleRow.looted: bool | None`, schema regenerated.
  Non-null **exactly** on a `crashed_drop_pod` whose `observed` is `"standing"`, because the
  flag is a live pod body's own `mHasBeenLooted` — 30 looted, 58 unlooted and 11 never streamed,
  of the 99 remaining. **Null means one thing, that no loot flag was read, and never "not
  looted"**, which is `false`. The map draws it on the fill axis: a hollow ring is looted, a
  faint disc was never read, and only a solid dot still holds a drive. Dashed was refused
  because it already means locked on a node, paused on a machine and planned on a plan. The
  text note is narrowed rather than deleted, because the public map still draws all three
  alike.

## P5 — capability already built, not yet reachable: WIRE IT UP

Decided 2026-08-02: **wire these up, do not delete them.** All closed but item 36,
which was misfiled from the start.

| # | What was unreachable | Closed by |
|---|---|---|
| 26 | `UnlockDelta.unlocked_by` and `.ok` — `rank_unlocks` said an alternate was worth 14,540 MW and never said which schematic granted it, and an infeasible solve reported `gain=0`, indistinguishable from worthless. A `granted by` column and an explicit `INFEASIBLE:` note. The highest-value wiring on this list: the tool's whole purpose is deciding what to research, and it withheld the name of the thing to research. | `b251b2b`, `3ffc3cf` |
| 27 | `flow.py`'s `basis` — the *why* behind every inferred pipe direction — reached `/api/pipes` and nothing else. A `basis` column on `search_conduits`. | `73e0ccb` |
| 28 | `Structures.machines_on()` and `.summary()` had no consumer in `src/`. Both are now the machinery behind `slab:<n>`, which closes item 13 with it. | `1b6b242`, `56abf09` |
| 29 | `FactoryView.internal()` — "the mark of a self-contained line", per its own docstring — was rendered by no `factory_query` aspect. Now an aspect. | `f78d0a7` |
| 30 | Top-level `pipe_networks`, 19 rows, no consumer outside one test. `search_conduits show="networks"`. | `5e1c06d` |
| 31 | `list_pending_hard_drive_choices` held each option's granted recipe list and used it only to decide whether to append "(nothing new)". It prints them. | `4222b21`, `6133e5e` |
| 32 | `progression["last_active_schematic"]` and `research["last_used_hard_drive_id"]` had no consumers. "What you were last working on" is in `world_summary`; the last drive spent is context in both hard-drive tools. | `889fec4` |
| 33 | `somersloops` read `boost_in_save` so a caller could cross-check the computed boost, then never printed it. It prints, with a note when the two disagree. | `8f37f21` |
| 34 | `research["ongoing"]`. Closed as part of 9d. | `a0a2ec9` |
| 35 | `load_collectibles(strict=True)` and `CollectiblesUnreadable` existed to tell "you never ran the generator" from "what it wrote is broken", and zero callers passed `strict=True`. The collectibles service now does, and the error path says which. | `68ddd8c` |

**36 — PARTIAL, and this row was wrong when it was written.** The `_solve(drop_actor=, cuts=,
one_way=)` ablation knobs in `domain/world/flow.py` were filed here as "never passed by
anything". Two of the three already were:
`tests/test_pipe_flow.py::test_the_two_models_never_disagree_where_both_of_them_speak` passes
`one_way=False` and `cuts=False` and asserts the 118-pipe overlap agrees, and it has done since
`ef59c62`, which predates this backlog. The audit that filed the row searched `src/` and read
the absence as proof.

What is genuinely left is `drop_actor=`, which nothing passes anywhere, and the assertion this
row asked for: that ablating each knob degrades the riser-violation count. Today the test proves
the two models agree, not that either is doing work. It is still **not a surface feature** — its
honest home is that test file.

## P6 — measurements the model is waiting on

Rigs to build in the owner's world, parked with the fluid experiment. [fluids_model.md](fluids_model.md)
is the authority for every constant and every rule these would move, and rows 37–39 and 41 name
the section that holds the design rather than repeating it; rows 40 and 42 are written out in
full because that document does not carry them yet. **The save file is the instrument** — the
method, including the two ways this experiment has already lost a measurement, is that
document's *How to measure this again*.

Order matters at the top of the list. **37 and 38 both unblock 39, and 39 must not be run before
one of them lands.** 40, 41 and 42 are independent of all three and of each other.

**37 — The dense series on the suction rig. OPEN, and the cheapest thing on this list.** The rig
is already standing: `HL_BUFFER` column, tank drained to ≈19%, feed severed, one Mk1 at centre
−9.591176. Change nothing, and save every ≈30 s for ≈5 minutes, copying each autosave to a stable
name as it appears. Read the suction piece `…2147203939` in each. Two saves 299 s apart show that
piece full and then empty, and cannot tell a line that filled once and drained from one that
fills and empties on a cycle — the whole reading of the rig turns on which it is. See
*A running pump fills its own suction line* and the discharge bullet in *Open*.

**38 — The discharge sink. OPEN.** The same rig, given somewhere to put fluid: a descending Mk2
pipe from the pump's outlet into a spare 400 m³ Fluid Buffer, **every crest at or below
`pump centre + 22.801` = +13.21 m** and never above the column's +23.0 cap, so the sink is inside
the pump's measured reach by construction and a failure cannot be blamed on height. This tests
the leading candidate for why a pump drawing 4.0 MW for 497 s moved 11.6 millilitres: that a
capped dead end with no consumer is not a discharge at all. **Cost to weigh first:** reusing the
`BUF_OUT` buffers would end that closed pair, which is the control the model document leans on
for what a pumpless, flat, closed system does on its own.

**39 — `SUCK_HIGH`. BLOCKED on 37 or 38.** The second half of the suction rig, designed and
needing no construction: move the Mk1 to a centre of **+10.000 m**, which is 25.25 m above the
tank connector — 2.24 m past the 23.006 estimator and 1.01 m past the assumption-free 24.244
ceiling — with the column already reaching +23.0. It decides whether a pump's inlet is ungated or
bounded by its own reach, which is the only open question in the model that could turn a silence
into a fault. **It cannot be read on a rig that passes nothing**: "the column above the pump is
dry" says nothing about suction until the rig is shown to move fluid at all.

**40 — Flow versus height, with a Fluid Buffer as the flow meter. OPEN, and not yet in the model
document.** The rating behind `machine_head_lift_m` is the one constant on that page taken purely
from the game's own prose: a dead-end column measures the **ceiling** and says nothing about the
10 m rating. **The rig:** one Water Extractor, one pipe, one **Fluid Buffer** — no power on the
line, no solids, no recipe, nothing that can throttle. `Δstored_m3` between two saves ≥60 s apart
is the delivered flow, and the buffer is passive as long as it is read between **20% and 80%**
fill (above 100% it transmits head and stops being a sink). Three climbs, rebuilding only the top
of the run: **2 m** as the control, which must read the nameplate; **10.5 m**, which is the
question; **11.5 m**, which must read zero and so proves the rig can fail. Buffer base =
extractor connector + climb − 1.75000. **This supersedes an earlier four-machine design that
could not have worked:** one extractor at 120 m³/min feeding one Coal Generator at 45 has 2.7×
headroom, so the flow could halve twice and the generator would still read 100% uptime.

**41 — The 8 m trapped-air rig. OPEN.** Whether the deficit below a waterline is a fixed **volume**
per piece or a fixed **fraction** of its capacity — the one question `HL_FINE` could not answer,
because the 7.0 m³ capacity floor makes a 2 m piece hold nearly as much as a 4 m one. **The rig
uses LONGER pieces, not shorter**, which is the opposite of the standing assumption: an 8 m column
has capacity 14.87 m³, safely above the knee, and the two hypotheses predict estimator gaps of
≈1.85 m and ≈0.92 m — 18× the noise apart. Design and arithmetic are in *Open* in
[fluids_model.md](fluids_model.md).

**42 — The free control: dismantle two pumps on one coal network. OPEN, not yet in the model
document, and it costs about thirty seconds in-game.** Thirty-two Coal Generators in the owner's
base sit **8.150 m above their extractors** at 100% uptime and 96% supply utilisation, which is
the most flow-sensitive load it has — but every one of those networks has pumps on it, so none of
them is currently evidence about machine head lift. Two of the networks carry only **two pumps
each** (numbered components 0 and 18 by the walk that found them, so re-derive the numbering
rather than trusting the index). Dismantling both pumps on **one** of them leaves 8 generators
machine-fed over 81.5% of the stated 10 m rating, with the other three coal plants standing as
untouched controls. If those eight keep running, the rating carries a real load at 8.15 m; if
they starve, the model has its first measurement of the rating rather than the ceiling. This is
the only item on this list that needs no new construction at all.

## P7 — filed late: work the audits found and this ledger never carried

Everything above came from the three audits of 2026-08-02 and the client field report. These
did not: they are items that lived only in [roadmap.md](roadmap.md), in the parked ledgers, or
in the running conversation, and were therefore invisible to anyone reading the backlog. Filed
on 2026-08-10 at the owner's instruction so that one document answers "what is left".

**Numbers quoted from memory rather than from a measurement are marked so.** This project has
been wrong about a cost by 100× in both directions, so a figure without a source is a hypothesis
about performance and not a reason to build anything.

### The modules that are finished and read by nobody

This is the structural defect that explained a third of P0–P5, recurring: **a projection key, or
a domain module, is not finished until both interfaces read it.**

**43 — `domain/world/timeline.py` has no tool.** Roadmap §2.3. The row, the identity key that
survives autosave rotation, the index file and the pairwise comparison all shipped; measured at
112 s to index 50 saves cold, for 101 kB. What is missing is a tool and **the window note every
answer must print with it**. The value is real and it grew: a 300 s window in one save is a
reading, a series of them is a rate, so "when did this stop", "at this rate, N hours" and sink
points-per-minute become honest only once there is a second sample. **The hard corollary is
undischarged and binds whoever takes this: do not build ETA-from-a-300 s-window before the
timeline is consumed.** That is a forecast dressed as a reading, and it is the exact class of
confidently-wrong answer this project exists to avoid.

**44 — `domain/world/logistics.py` has no tool.** Roadmap §2.2, [parked.md](parked.md) §21.
`Link`, `PhysicalGraph.feeds/drains` and `build_physical_graph` are correct and cached and reach
no surface. Note that §21's original geometric-join prescription was **struck** after being
measured 28.7% wrong, so read the parked entry for what survived before designing anything on
top of it.

**45 — `GameData.warnings` and `projection["warnings"]` reach no reader.** Roadmap §3. The first
reaches a bare `len()` in `tools/resources.py:32`; the second has zero consumers anywhere. Two
note blocks, in `world_summary` and in the save resource, close it.

**46 — The unread-class census.** Roadmap §3. Fifteen lines mirroring the null-yaw census.
**It must land in the same commit as item 45 or it is invisible by construction.** It also needs
a hand-built "seen and dismissed" allow-list, or its first run reports 4,300 berry bushes.

### Things that lie rather than fail

**47 — `skew_from_meta` returns `None` unconditionally on the shipped table.** Roadmap §3. The
guard is structurally incapable of firing on the event it exists for. `installed_build()`
returns the exact pin string every artifact records and still has no runtime caller. Ten lines
closes it for five pinned artifacts — and this is the guard that would announce a map update
having moved the pinned node and collectible tables, which is a failure mode that has already
bitten.

**48 — `docs_path()` raises instead of returning `G:\SteamLibrary`.** Roadmap §3,
`config.py:25`. A function lying rather than failing.

**49 — The comment-budget failure message names no remedy.** Roadmap §3, fifteen minutes. The
cheapest way to satisfy a failing ratchet is to delete an explanation, in a codebase whose prose
is the design record. The message should name the intended remedy instead. Caps are
per-**directory**, so "raise this file's cap" is not available and the message must not imply it.

### Fluid work that is not the head-lift model

**50 — Pipe build review, a build-advice tool.** Roadmap §3b, from the FICSIT Plumbing Manual.
Two mistakes the manual names, both decidable from the contracted runs and machine positions
already held, and neither performed by any tool in the ecosystem: *a feed pipeline built below
its machine inputs* (Lesson 8 — head lift is the same for every pipe in a network, so a feed
line below its machines starves all of them the moment the level drops), and *pumps stacked with
no vertical separation* (pump lift does not stack pump-to-pump — three Mk2 pumps in a row give
50 m, not 150 — it stacks only with gravity gained after the pump). The output is **advice about
what was built, not a fault**, so it wants its own tool rather than a column on a health report.

**51 — Byproduct remedies in `explain_byproducts`.** Roadmap §3b, Lesson 9. The LP balances a
byproduct as an equality and is mathematically right while being physically naive: it has no
idea the second refinery clogs when its output is fed back to the first. The manual ranks four
remedies — underclock the extractors and cap them with a valve (stable only at 100% efficiency),
dedicate refineries to the byproduct (most stable), feed it to other machines, or package and
sink it (worst). A planner change, unrelated to the head-lift model.

**52 — Pipe fill is not in the projection.** Every fluid measurement to date has had to parse
the raw `.sav` with `pioneersav`, because the projection does not carry `mFluidBox`. That is the
right call while the model is moving and the wrong one once it settles. **It needs a
`SCHEMA_VERSION` bump, so it must be coordinated** — a second Claude instance has been doing
belt work in parallel and the two changes must not collide.

### Reported without a measurement, and needing one first

**53 — Belt instances have no stable ident.** A belt can be addressed as `chain:N` but an
individual belt actor cannot, and giving it one is believed to need an `actorIndex` in the
projection — hence a `SCHEMA_VERSION` bump and the same coordination as item 52. **Re-derive the
requirement before bumping**: this was stated in conversation and never written down against the
code.

**54 — Derived views are rebuilt on every MCP start.** Recorded in conversation as **≈815 ms**,
and that figure is **[UNVERIFIED]** — measure it before designing a cache. The single-flight
memoisation and the `SaveWatcher` pre-warm already landed and cut a cold read from 8.40 s to
4.19 s and a warm `load_projection` from 98.8 ms to 0.44 ms, so the remaining start-up cost may
already be smaller than remembered.

**55 — A terrain water-lift column, and a valve splitter check.** Both were named in
conversation and neither has a written specification. **Write down what each would claim, and
what save evidence supports it, before either is built.** Filed so they are not lost, not
because they are ready.

## Not doing, and why

- **Region tint 256 m vs lookups 64 m** (14.1% vs 5.3% mislabel): known, documented, and the fix
  is a different grid, not a bug fix. Leave it, or print the confidence word on the tint.
- **A "copy question" affordance on the map**: the LLM does not read the page and a generated
  English sentence guesses at the human's question. A selector and a coordinate are enough, and
  since item 19 they can be copied.
- **Coordinate rounding drift** (`serial._m` rounds, text emitters truncate): sub-metre and
  cosmetic. Worth one sweep if someone is already in those files.
