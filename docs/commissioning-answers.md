# Item 7, commissioning planner — answers

Answers to `commissioning-questions.md`, settled in conversation. Written from the Spire
Coast oil plant session (world `Han Solo`, 2026-07-27), where a commissioning sequence was
worked out by hand against a real save, then re-framed in review. Evidence cited is from
that session.

All seven questions are answered. The re-frame in Q1 changes what the tool is, so read
that first.

---

## Q1 — **It is not a build-order problem. It is a startup-order problem.**

The re-frame: *"Can't we just build an unpowered factory, and just power it after the build is
done?"* — and that dissolves most of the question.

Building costs materials, not power. A machine draws only when it runs. So the whole
787-building plant can be constructed in any order, at leisure, drawing nothing, and then
brought online block by block. The planner should therefore not partition the **build** at
all. It should sequence the **energisation**.

Consequences, all good:

- The (a)/(b)/(c)/(d) commit-granularity menu is moot. There is no commit granularity,
  because there is no power-constrained build.
- The objection to (c) — "revisiting every building block N times, which is the thing
  players hate" — disappears entirely. Each block is built once.
- Vertical-slice vs grid-positive stops being a preference question. Stages are cuts in a
  startup order, not in a construction order.

**Generators are free to energise.** `list_buildings` gives Fuel-Powered Generator a draw
of `0MW` (`250MW out`). Only consumers — extractors, refineries, blenders, water pumps —
spend headroom. So the constraint is:

> at every step, Σ(energised consumer draw) ≤ measured headroom + generation from
> generators already receiving fuel

The E1 numbers are unchanged, only their meaning is. As a startup order:

| Step | Energise | Draw | Headroom |
|---|---|---|---|
| 1 | 1 Oil Extractor, 4 Water Extractors | 204 MW | 831 → 627 |
| 2 | 5 HOR Refineries | 150 MW | → 477 |
| 3 | 4 Blenders | 300 MW | → 177 |
| 4 | *(wait for fuel to reach the generators)* | — | 177 |
| 5 | 20 Fuel Generators | **+5,000 MW** | → 5,177 |
| 6 | everything remaining | 1,811 MW | → 3,366 |

**Two things the planner must model that a build-order planner would not:**

1. **Fill time.** Between steps 3 and 5 the pipes and machines are filling and nothing is
   returning power. The deficit is carried for a real interval, not instantaneously. The
   sequence must be safe *during* the wait, not just after it.
2. **How a block is isolated.** Connecting everything to the grid and hoping does not work.
   Correction to an earlier draft of this doc: an overload does **not** brown out and slow
   machines — the fuse blows and the entire grid stops until it is reset by hand, including
   the plant that was feeding it. There is no graceful degradation, so the sequence has no
   safety margin to spend. The build needs **one Power Switch per block** so energisation is
   a switch flip and a misbehaving block can be isolated. This has to be built in from the
   start, so the tool should emit it alongside the sequence.

## Q2 — Resolved by Q1: bootstrap, not buildout. No babysitting.

The constraint: *"This is not a buildout question, this is a bootstrap question... I need a way to
start up one block after the other. I won't be babysitting it."*

So: read `power_report` headroom, allow a stage to consume it, and do **not** model
hand-feeding a generator. The black start here is the existing 32-generator coal plant,
i.e. ordinary grid headroom.

On the staleness objection from the questions doc: print the assumed headroom as a
labelled input line, the way `phase_requirements` prints its `trust` column. That pattern
already works in this project and makes a stale sequence visibly stale rather than
silently wrong.

## Q3 — **Partition**, and Q1 strengthens the case

Re-solving per stage would actively mislead. Concrete evidence: when the same plan was
re-solved with water extractors capped at 27 instead of 64, the solver switched from
`Residual Rubber` to the base `Rubber` refinery recipe — a genuinely different recipe mix
at reduced scale. A staged re-solve would have instructed the player to build base Rubber
refineries in stage 1 and tear them out by stage 3.

Under the Q1 re-frame this is no longer even a close call: the whole plant is built before
anything is energised, so stages are cuts in the startup order of a fixed machine set.
Partition is definitional.

Partition also preserves the most valuable property found in the session — the rig is
invariant at 13 extractors / 115 refineries / 92 blenders across *every* downstream
option, which is what let building start before the downstream questions were settled.

## Q4 — **Two independent axes**, and the re-frame separates them cleanly

Power was the only thing that gated the session — every sizing decision came from 831 MW
of headroom against a 2,465 MW plant. It is not in the questions doc's options list; it is
filed under Q2 as a constraint. For a power plant it *is* the axis, and `slice_of` already
totals it.

The materials bill was also requested ("Sure, why not, let's have it. We could even
build out a more architecturally principled calculator"). The Q1 re-frame makes these two
genuinely orthogonal outputs rather than competing budget axes:

- **Materials** — what it costs to build. Independent of order. Building build-costs are in
  Docs.json, reachable via `search_recipes(kind="building")`, and `bom.py` already exists.
- **Power** — what it costs to run, and the only thing constraining the startup sequence.
- **Shards** — a reported column, not a bound. They came within 17 of binding this session
  before the uncrafted slug pool turned up.
- **Machine count** — drop it. A weak proxy for something already measured exactly.

A principled construction calculator is worth doing on its own merits and no longer needs
to be entangled with commissioning.

## Q5 — Order by node status first: **prefer slices over `free` nodes**

The criterion that actually decided E1 as phase 1 is not in the options list. E1 was chosen
because its three nodes are `free`, while the other ten in the region are `tapped` by two
factories labelled "temporary" but still running. Building over free nodes means no
demolition and no disruption to live production, and `search_resource_nodes` already
reports `free` vs `tapped`.

This outranked earliest-grid-positive in practice, and it did **not** fight the
vertical-slice idea as the doc feared — a geographic cluster naturally forms its own
column: E1's 3 nodes → 1 crude trunk → 20 refineries → 16 blenders → 80 generators. The two
criteria agreed, because trunk topology is itself geographic.

## Q6 — **Stages are a first-class concept the MCP should detect from the save**

The requirement: *"I feel like the mcp should have fundamental capacity to identify stages."*

So this is not a printout. It is a domain concept, and — usefully — one that needs no new
persistence layer. If a stage is a partition of a stored plan (Q3), then which stage you
are in is *inferable* from the save by matching built machines against the partition.
`diff_vs_save` already matches machines by identity — a manufacturer on (building, recipe),
a generator on its building, an extractor on its node — and already orders actions
free-first. Grouping that output by stage gives stage identification for free.

Two things follow:

- `diff_vs_save(plan='spire-coast-full', stage=N)` for a single stage's delta.
- Stage detection with no argument: *"you are in stage 3; stages 1–2 complete; stage 3 is
  60% built and not yet energised."* Note that **built** and **energised** are now distinct
  states after Q1, so the tracker needs both — a fully-built unpowered block is a valid and
  expected condition, not an anomaly.

Per-stage layout restricted by `slice_of` + `max_floor_foundations`: yes, and it composes
with the above so decks accumulate rather than being redrawn.

## Q7 — Build order: **trunks, then sloops/shards, then commissioning**

The scope suspicion in the questions doc is correct. Independently ranked, before reading it:

1. **Trunk assignment (item 6).** 13 nodes were hand-packed into 6 pipes, discovering along
   the way that a pure node at 250% saturates exactly one Mk2 pipe and can never share.
   Bin-packing over data already held — coordinates, rates, pipe capacity — and
   `build_layout` already splits blocks by throughput. Biggest manual-work saver of anything
   in the session.
2. **Shard/sloop totals per plan (item 5).** ~20 lines. The 103-shard bill was computed by
   hand and **got wrong on the first attempt** (192, from assuming 3 shards per machine
   before reading that a shard adds 0.5 to *max* clock). Sloops are entirely unmodelled;
   `list_buildings` reports the slots (Blender 4, Refinery 2, Fuel Generator 1) and nothing
   consumes them. Sloops in the 92-blender block would roughly halve it.
3. **Commissioning (item 7).** Most valuable, most design-risky, and it wants the other two
   finished first — a stage needs its shard bill and its trunk assignment regardless.

It is not a one-off, though. A sequence was produced for E1 by talking it through; W1,
C1–C4, Module B and Module C are all still ahead, plus every future factory. Talking it
through cost real effort and does not survive compaction, which is the stated reason the
questions doc exists.

The Q1 re-frame also shrinks item 7 considerably. A startup sequencer over a fixed machine
set is a much smaller thing than a build partitioner that has to choose subsets under a
budget — `slice_of` plus a greedy walk in dependency order, rather than a search.
