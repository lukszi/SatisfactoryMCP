# Item 7, commissioning planner — open questions

Written before implementing, to survive a context compaction. These are decisions I
should not make alone, because each one changes what the tool *is*, not how it is coded.
Everything below assumes `planning/progress/startup.py`, built on `slice_of` (§8.2e, in
[planning.md](planning.md)).

The premise: a solved plan is a 43,000 MW end state that cannot be built in one sitting.
Commissioning cuts it into an ordered sequence of stages, each of which is buildable and
— ideally — pays for the next one. `slice_of` already totals any subset's power, flows,
shards and sloops, so the planner is a loop that chooses subsets. The questions are all
about *how to choose*.

## Q1 — What makes a stage "done"? (the commit-granularity rule)

The one I flagged as carrying the design risk. Candidates:

- **(a) Self-powered slice.** A stage is complete when its own `net_mw >= 0`: it burns
  what it makes and adds nothing to the grid but does not draw from it either. Clean
  invariant, testable, and it matches "I don't want to brown out my base". But early
  stages of an oil plant cannot reach it — an extractor + refinery block draws megawatts
  and generates nothing until the generators land, so stage 1 would have to be the whole
  vertical column at once.
- **(b) Grid-positive slice.** Stronger: each stage must *increase* net grid power. The
  self-financing reading. Fewer, larger stages.
- **(c) Whole-chain vertical slice at reduced throughput.** Build the entire node →
  refinery → generator column, but at 1/N of the machines. Every stage looks like the
  final plant, just smaller. Power-positive from stage 1, and each stage is a working
  factory rather than a half-built one — but it means revisiting every building block N
  times, which is the thing players hate.
- **(d) Buildable-in-one-trip.** Stage boundary set by construction cost (a materials
  budget) rather than by power at all.

**My inclination is (c) with an (a) floor**: vertical slices, each sized so the slice is
at least self-powered, N chosen as the smallest count where stage 1 clears its own draw.
But this is exactly the question where your actual play pattern decides it, and I have
been wrong about play patterns in this project before (the temporary wiring setup).

## Q2 — Does a stage's power have to stand alone, or may it lean on the existing grid?

The save knows the current grid: `power_report` has generation, draw and headroom. A
stage that draws 2,000 MW is fine if you have 3,000 MW spare and fatal if you have 200.

- Ignore the save and require every stage to be self-sufficient — portable, pessimistic.
- Read `power_report` headroom and allow a stage to consume it — realistic, but the
  answer then changes every time the save rotates, and a plan you printed yesterday
  stops matching.

Related: should the *first* stage be allowed to run at a loss on the assumption you will
babysit it (hand-feeding a generator to bootstrap)? That is what a player actually does.

## Q3 — Is the stage sequence a partition of the final plan, or a series of whole plans?

- **Partition:** stage k builds machines that are all present in the final solve; the
  union of stages == the plan. Nothing is ever torn down. Constrains the cut but keeps
  the promise that following the sequence lands exactly on the plan you approved.
- **Re-solve per stage:** stage k is its own `max_mw` solve under a budget. Better local
  answers, but stage 3 may want a recipe mix that makes stage 2's machines wrong, and
  now the tool is telling you to rebuild. I think partition, but say so.

## Q4 — What is the budget axis?

To cut anything I need one number per stage to bound. Options, in order of how much data
we already have:

- **Machine count** — trivially available, weak proxy.
- **Construction materials** — `bom.py` exists and buildings have build costs in
  Docs.json. This is the honest axis ("stage 1 needs 1,200 Reinforced Iron Plate") and
  probably what actually gates you.
- **Power shards / sloops** — already totalled per slice, and genuinely scarce.
- **Wall-clock or trips** — not modellable.

Materials is the one I would pick, and it is also the one that pulls the most new code in.
Is a materials bill per stage something you want, or is machine count enough for now?

## Q5 — Which order, when several stages are equally valid?

Given a partition into slices, the sequence still has freedom. Optimise for:
earliest grid-positive, fewest distinct buildings per stage (so you unlock/haul less),
or geographic locality (build the north half first)? Locality is available — every
machine block has a position from the layout — but it fights the vertical-slice idea.

## Q6 — What does it emit?

A table of stages with power/flows/materials is the minimum. Beyond that:

- Should each stage carry a **layout** (`build_layout` restricted to the slice), so the
  decks accumulate rather than being redrawn? That is genuinely useful and cheap given
  `slice_of` + `max_floor_foundations`.
- Should it be **persistable** like plans (`store.py`), so "which stage am I on" is a
  thing the MCP can answer against the save? That is `diff_vs_save` per stage, and it is
  the feature that would make this a commissioning *tracker* rather than a one-shot
  printout. Big scope jump; worth naming before I start.

## Q7 — Scope check

Is this actually the next thing you want built, or was it aspirational when we listed it?
It is ~250 lines plus tests plus a DESIGN section, and items 5 and 6 (sloops, trunks)
were both smaller and more immediately useful. If the oil plant is the driving case, a
commissioning sequence for *that specific plan* might be better produced by us talking
through it once than by a general tool.
