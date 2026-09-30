# Planning: the optimizer, the layout, and what an unlock is worth

Part of the [SatisfactoryMcp design spec](../DESIGN.md) — §8 is the LP/MILP formulation and
everything built on it (layout, commissioning, diffing against the save, constraining the recipe
set); §9 is the hard-drive advisor. Section numbers are continuous with the rest of the spec;
[DESIGN.md](../DESIGN.md) indexes it.

---

## 8. The optimizer

### 8.1 Formulation

Variables: `x_p ≥ 0` (machine-equivalents at 100% clock), `n_p ∈ ℤ₊` with `n_p ≥ x_p / clock`, plus
`raw_i`, `out_i`, `snk_i`.

**Power is a pseudo-item `__MW__`** — negative coefficient on machines, positive on generators. This makes
the power balance just another mass-balance row and keeps every objective linear.

**Balance, for every item — equality:**

```
Σ_p x_p·(out_ip − in_ip) + raw_i − out_i − snk_i == 0
```

Objectives: `max_mw`, `max_item`, `min_raw`, `min_machines`, `min_power`.
Extra rows: somersloop budget, belt/pipe throughput caps, machine cap.

**Two-phase lexicographic solve is mandatory.** With only `n_p ≥ x_p`, any larger `n_p` is optimal — an
unguarded run returned machine counts of 1e12. Phase 1 optimises the goal; phase 2 pins that value and
minimises machine count.

### 8.2 The byproduct rule — the crux

1. Every item's balance is `== 0`.
2. `exports` and `sinks` are **explicit whitelists**, never "all items".
3. `sinks` restricted to `is_solid AND mResourceSinkPoints > 0 AND mCanBeDiscarded`; each AWESOME Sink
   draws 30 MW.
4. Export **targets** are first-class: `max MW subject to plastic ≥ X, rubber ≥ Y` — still linear.

Why it matters, measured on crude→plastic from 300 m³/min:

| formulation | plastic | refineries | power |
|---|---|---|---|
| everything exportable (`net ≥ 0` in disguise) | 200/min | **10** | 300 MW |
| **correct:** HOR `== 0`, fuel/coke real exports | 200/min | **11.67** | 350 MW |
| **correct, plastic-only site** | **infeasible** | — | — |

Naive calculators return row 1 — 10 refineries and *no machines consuming the 100 m³/min of Heavy Oil
Residue*. In game the pipe fills and the line stalls. The correct answer needs **17–25% more buildings**,
and it can say "impossible", which a naive model never does.

### 8.2a Naming what an infeasible plan cannot get

`plan_factory` already names buildings the world has not built (`must build first: Blender`). It said
nothing about the resource end, and that asymmetry was expensive: a rocket-fuel plan returned a bare
INFEASIBLE, `explain_byproducts` correctly reported no byproduct was stuck, and the real cause —
**Nitrogen Gas exists only as resource-well satellites, and this world has no Pressurizer** — had to be
recovered by hand by cross-referencing a node scan against a recipe. `planning/supply.py` closes it.

Three things had to be fixed for an INFEASIBLE response to say anything at all:

1. **Every early `Solution("infeasible", …)` filed its reason under the wrong field.** The tenth
   positional field is `machine_penalty_mw`, not `warnings`, so `"phase 1 infeasible: …"` went into a
   float and the caller got an INFEASIBLE with no reason attached. This is most of what "bare INFEASIBLE"
   actually was. Now passed by keyword.
2. **An export or target nothing in scope can make is named without any probe.** An export with no
   producing column cannot be exported at any rate, and the distinction is the one a player acts on:
   *no unlocked recipe makes it* versus *its recipe needs a machine you have not unlocked*.
3. **Missing raw supply is decided by the LP, never a graph walk** — the same rule §8.2 and
   `byproducts.py` follow. A backward walk from the target over-reports (every raw input of every route,
   including routes the plan would never take), and a forward "what can I make" closure under-reports,
   because Recycled Plastic and Recycled Rubber each need the other's product yet the *pair* net-creates
   both from Fuel. So one probe re-solves with a free supply of every resource the scope cannot extract,
   and `raw_used` names the ones the plan actually wanted. **One extra solve, on the infeasible path only.**

Both outcomes are reported, and the negative one is worth as much:

| probe | reported |
|---|---|
| solves | every resource it drew on is *a* cause — the only change was making it available |
| still infeasible | raw supply is **not** the problem; something else is binding |

Measured on the reference save: max Steel Ingot ≥ 100/min scoped to `resource:Iron Ore` is infeasible,
and the probe names **Coal — no node in scope (62 elsewhere on the map)**. Forcing 10⁷ Steel Ingot on
Spire Coast is also infeasible, and there the probe correctly declines to blame supply.

**What it cannot prove.** It does not apportion blame: two missing resources that are jointly needed are
both listed, with no claim about which is *the* blocker, and a resource the probe used because it was
merely cheap is still listed. The probe's own draw rate is deliberately **not** printed — it maximises
against an unlimited supply, so quoting it would read as a requirement it never established.

The *why* beside each resource is a separate, purely factual read of the node table, in this order: no
node in scope (with the map-wide count) → nodes in scope but none reachable → reachable but all tapped
(`only_free_nodes`) → reachable and free but modelled by no extractor (well satellites and geysers,
which `build_scenario` never turns into extractor columns). If none of those hold it says the cause is
not established rather than inventing one.

`nodes.blocking_buildings` supplies the actionable half — *which* building to go and unlock. It is
strictly sharper than the `reachable` flag in one direction that matters: `reachable` asks only whether
an extractor of the right **kind** is unlocked and never whether it can tap that **resource**, so an
unlocked Miner Mk2 makes a crude oil node read as reachable while nothing on the map can pump it. It
also names the Pressurizer, which appears in no extractor table because it extracts nothing, and
without which every satellite of a well yields exactly zero.

### 8.2b Every export needs a balance row

Export **columns** come from what you asked for; balance **rows** came only from items
some process touches. An export that is neither produced nor raw therefore got a column
with nothing tying it to production, and an unconstrained column is wrong in two ways:

| call | before | after |
|---|---|---|
| `max_item` on an unmakeable target | objective pushes the free column up forever → HiGHS **UNBOUNDED** → surfaces as a bare INFEASIBLE | bounded, objective 0, reason stated |
| `export_minimums` floor on an unmakeable export | floor is a lower bound on that free column, so it is met **out of thin air** — `min_power` reported `exports: Nitrogen Gas=100` on a world with no recipe and no reachable node | honestly INFEASIBLE, with the cause named |

The second is the serious one: a confidently wrong plan does more damage than a bare
INFEASIBLE, which is what the whole diagnostic work above was fixing.

`MW` is excluded from the new rows on purpose — it balances on the power row, and a
second row there would force generation to zero.

**The fix needed a companion.** Pinning the export to 0 turns a loud failure into a quiet
one, so `supply.unmakeable()` — previously reachable only on the infeasible path — now
also runs when the plan *succeeds*, appending "…it is pinned to 0 in this plan". That
branch needs no LP probe, so it is free.

Test note: of the three regression tests, two fail without the fix. The third (no floor,
`min_power`) reads 0 either way because nothing rewards raising that column; its docstring
says so rather than implying it catches the bug.

### 8.2c Three gaps a planning session found

**Generator burn had no name to ban.** `exclude_recipes` searches `game.recipes`, but
generator burn and extraction are *synthesised* in `optimize.py` from building data —
they are not recipes and have no entry in Docs.json. So `"Coal-Powered Generator on
Coal"`, the exact string the build table prints, matched nothing. The recourse was
deleting 20 generators by hand, which only worked because coal happened to be a leaf in
that plan.

Patterns are now matched against synthesised processes too — by label, building name, or
consumed item — so `"Coal-Powered Generator on Coal"`, `"Coal-Powered Generator"` and
`"Coal"` all work. **Every pattern is offered to both matchers.** Recipe-first precedence
looked tidier and was wrong: `"Coal"` matches Biocoal/Charcoal/Compacted Coal, so under it
the pattern never reached the generators and "do not burn coal here" silently did the
opposite. A pattern matching neither still refuses, which is the behaviour the reporter
explicitly asked to keep.

**Water was modelled as placeless and unlimited.** It has no node entry, no purity and no
geometry anywhere this project can read — pumps point at `FGWaterVolume` objects. The
count was bounded by a private `_WATER_EXTRACTOR_CAP = 200`, chosen only to keep the
column from being unbounded.

That is not cosmetic. On a measured plan water was **12,400 m³/min across 105 extractors —
the largest fluid in the plant, larger than its Fuel**. It is also the only fluid that must
be sourced at sea level and cannot be gravity-fed, so it drives deck ordering.

> **Corrected 2026-07-28 (player).** This section originally added "on a 138×136 m ocean
> platform whose perimeter fits roughly 27", treating **shoreline as the constraint**. It is
> not. Pumps go on foundation platforms built out over open water, so frontage plays no
> part and only area matters: 105 pumps at 20×18 m occupy 37,800 m², a **194 m square**,
> smaller than that same plan's own 512 m site, and cost 945 foundations / 4,725 Concrete
> against the 32,645 Concrete its deck already needs. The 105-extractor plan was never
> implausible. What binds is vertical, not horizontal — see the sea-level rule above.

Three changes: the constant moves into the §5.6 register as
`WATER_EXTRACTOR_CAP_ASSUMED`, labelled the only entry with no data behind it; a
`water_extractors` parameter lets a player state what their site holds (capping the same
plan to 27 costs 89,712 → 74,016 MW, a 17.5 % difference previously invisible); and any
plan using more than `WATER_EXTRACTOR_WARN_AT` says outright that siting is unmodelled.
Water pump rows also stop reporting `?` for resource and purity — a Water Extractor sits
on a volume, so it now reads `Water / n/a (water volume)` rather than looking broken.

**The cap does bind, and the terrain cannot retire it.** `WATER_EXTRACTOR_CAP_ASSUMED`
was documented as set high enough not to bind. Measured on the reference world: a
whole-map `max_mw` takes **all 200** and wants 246 — raising the cap to 400 buys 7,850 MW
(180,272 → 188,122, 4.4 %). Aluminium, the canonical water-hungry plan, uses 21 and is
nowhere near it. So the number was load-bearing on exactly the plans that never mentioned
it, and the 30-pump warning threshold never fired for the ones that did.

The terrain field (`Field.window`, `Field.nearest_water`) does **not** replace it. Submerged
area is not an extractor count: shoreline geometry, clearance and overlap are level data no
raster here carries, and deriving a count from `submerged_pct` is precisely the
confidently-wrong answer this project exists to avoid. What the field replaces is the
*silence*. Every plan that pumps now names the cap, says whether it is `BINDING` or merely
present, and — given `site_at` — quotes what was measured at the pad: the submerged share
and water level, or the distance to the nearest standing water and how far below the dry
ground it sits, joined to the pump-measured sea level from `world/water.py`. `site_at` still
changes no number the LP produces, and the `plan_id` is deliberately blind to it.

**Degenerate sub-1 % rows were clock modes, not an LP artefact.** Offering a node set at
several clocks creates one column per mode, and the modes share a node cap, so the solver
may split across them arbitrarily — 0.615 machine-equivalents at 100 % plus 0.0201 at
150 %. That printed as two rows with an *identical* label, the second a whole miner at
2 % clock, reading as a real build instruction.

Extractor modes of the same (building, resource, purity) are now folded before read-out.

**The first fold broke the cap**, and the failure is worth recording. Two quantities have
to be carried separately: the node cap constrains **machine count**, `sum(v)`, while
extraction is **node-units**, `sum(v × clock)`. Pooling units and re-expressing them at one
mode's clock preserved the rate and silently inflated the count — `water_extractors=54`
came back as **64 machines at 149.7 %**, because 54 machines' worth of units read at a lower
clock needs more machines. It looked correct at 27 only because that solution happened to
use a single mode. The fold now keeps `sum(v)` as the count and lets the clock absorb the
rate: `built = ceil(sum(v))`, `clock = units / built`.

**Binding also had to move to the group.** It was tested per process against that
process's `max_count`, but grouped modes share one cap, so a solve spreading extractors
over two clocks left every column below its own limit and reported nothing binding — while
the cap was fully consumed.

**A negligible row is not the same problem.** A degenerate basis can leave a *recipe*
column at 0.0001 machine-equivalents making 0.0017/min — one item every ten hours. There is
one clock mode, so there is nothing to fold it into. The row is omitted from the build
table (a whole machine at 0.0087 % clock reads as an instruction) but the machine is
**still counted**: dropping both silently turned a measured "9 buildings" into 8 in
`compare_recipe_options`. Omitting a row is presentation; changing a total is not.

This is the one place the reporter's original instinct — filter below an epsilon — was
right, and it was right for the *opposite* reason to the extractor case. The two look
identical in the table and need opposite treatment: fold one, omit-but-count the other.

### 8.2d Building footprints, exposed

`docs/footprint.py` has always derived an axis-aligned box per building from
`mClearanceData`, and `plan_layout` has always used it for foundation counts — but
nothing surfaced it. `list_buildings` now carries **size** (W×D×H) and **found** (8 m
foundations one machine covers):

| building | size | found |
|---|---|---|
| Smelter | 5×10×4.5 m | 2 |
| Manufacturer | 18×20×11 m | 9 |
| Water Extractor | 20×18×12 m | 9 |
| Nuclear Power Plant | 36×42×10 m | 30 |
| Quantum Encoder | 22×50×14 m | 21 |

Foundations round up **per axis** — a 5×10 m Smelter takes two, not one. The `found`
column is deliberately **per machine and ignoring shared edges**, so `N × found` is an
upper bound: a row of N machines needs fewer, because two 20 m machines side by side span
40 m and want 5 tiles rather than 6.

That caveat used to be merely *stated*, and both `plan_layout` and the water-siting note
had independently grown their own copy of the wrong `N × found` arithmetic. It is now
computed once, by `Footprint.pack(n, columns=)` (§ 8.5g).

The rotation trap is documented in the module and now has a test: the Fuel Generator's
clearance is several thin boxes at 45° increments approximating a round machine, so taking
the largest box naively gives 22×4 m instead of ~20×20 — roughly **1,000 foundations
understated across a 176-generator plan**.

This feeds straight back into §8.2c's water problem. "Siting is not modelled" is abstract;
*"77 of them pack into 11×7 = 220×126 m (448 foundations, 2,240 Concrete), or a single pier
20×1,386 m (522 foundations)"* is something you can hold against a build.

The frontage half of this was **wrong and is gone**. It quoted "about 1,920 m of shoreline
in a single line", which assumed pumps line a shore; they do not, they sit on platforms out
over open water. Quoting both and calling it balanced did not help — one of the two numbers
was answering a question nobody had, and it was the one that made large water plans look
impossible.

**A length is still given, just not that one.** The player lays platform modules, so "how long
is this" is a real question — it is the *pier extent*, 1,386 m for 77 pumps, not a stretch
of coast. Both shapes are printed because they answer different halves: the block is the
cheapest way to buy the area, the pier is what you measure modules against.

**And it is packed, not multiplied.** `Footprint.foundations` says outright that it ignores
shared edges, so `n × foundations` is an upper bound and not a build — two pumps side by
side span 40 m and need 5 tiles, not 6. Across 77 pumps that is **448 foundations against
693**, a third of the concrete. `Footprint.pack(n, columns=)` does the arithmetic and the
naive figure now appears only as the thing being corrected.

### 8.2e Plan slices, and the shard bill

Every plan-level question that is not "solve it" is the same operation: take some of the
processes and total their power, flows, shards and sloop slots. `planning/slice.py` is that
operation; the shard bill is one call to it and commissioning will be it in a loop.

**Two power figures, and the difference is not rounding.** `mw_linear` is what the LP
optimised — power proportional to machine-equivalents. `mw` is exact, after whole machines
are placed at a derived clock. `clock**exponent` is convex, so N machines below 100% draw
*less* than the linear estimate. The identity that holds is

```
solution.net_mw == sum(mw_linear) - sink_mw        # exactly
```

and the exact figure is better — 43,101 MW against 43,092 promised on a measured plan.
**Headroom checks must use the exact one**: it is what the machines actually draw, and
erring the other way would reject a slice that fits.

`sink_mw` belongs to the plan, not to any process — an AWESOME Sink is charged per belt
line of sunk material and no column owns it. A partial slice reports 0 rather than a
prorated invention.

**The shard bill.** `plan_factory` now prints it from the clocks the plan already chose:

```
power shards: 109 needed (64x Water Extractor @150% = 64, 7x Oil Extractor @250% = 21, …);
you hold 22 free + 407 craftable = 429 -- affordable after crafting slugs
```

The arithmetic is easy to get wrong by hand and was: a shard raises the **maximum** clock by
0.5, so 150% costs one and only 250% costs three. Assuming three apiece gave 192 where the
answer is 109.

**Somersloops are reported, never spent**, and only where they do something. Generators and
extractors carry slots with `can_boost = False`, so `boost_for` correctly returns 1.0 — the
first version advertised a Fuel Generator block at "1x output", which is nonsense dressed as
a recommendation. Those slots are counted separately as *unboostable* and never presented as
capacity. `Process.sloops` remains 0 everywhere: nothing plans them yet.

### 8.2f Somersloops: spread, never stacked

The solver has carried `sloop_budget`, `Process.sloops` and `boost_for` since the
formulation was written and **nothing ever set them**, so every plan silently ran at zero
sloops while the readout printed how many slots were going spare. `sloops=` is now a plan
argument, persisted with the plan and hashed into `plan_id`.

It is a **budget, not a switch** — how many you will actually commit — and it defaults to
0. Somersloops are the only genuinely finite resource in the game (a fixed number exist on
the whole map), so a plan that quietly assumed them would be unbuildable in a way no other
default is.

**The modelling correction: offer every count, not just full-or-empty.** Output is linear
in sloops and power is quadratic in the boost they produce:

| sloops in a Blender | boost | power |
|---|---|---|
| 0 | 1.00x | 75 MW |
| 1 | 1.25x | 117 MW |
| 2 | 1.50x | 169 MW |
| 4 | 2.00x | 300 MW |

So marginal output per sloop is flat while marginal power rises, and under a binding
budget **spreading strictly dominates**. Offering only 0-or-full made the solver pay the
worst rate on the scarcest resource in the game: at a budget of 16 it built 8 Refineries
at 2 sloops for 113,945 MW where 16 at 1 sloop gives **114,065 MW**. Measured, and a test
pins the comparison rather than the argument.

`PlanSlice` keeps **spent** slots apart from **empty** ones, because one is a bill and the
other is a suggestion — conflated, a plan that spends none would report 662 somersloops
needed. `sloops_used` counts against WHOLE machines, so it can exceed the budget the LP
solved under (the same rounding that turned a 54-extractor cap into 64 machines); the tool
checks and says so rather than quoting a number that is quietly too small.

**Committed sloops are reported as unknown, never as zero.** A slotted Power Shard shows
up in an `InventoryPotential` component; the production-boost equivalent appears nowhere in
this save under any of the three plausible property names, nor in Docs.json. `free` counts
loose sloops only — 16 on the reference save, 1 carried and 15 in the Depot — and the note
says so, because reporting 0 committed as if measured would overstate the pool for anyone
past mid-game. Mercer Spheres share the WAT prefix and are counted separately.

### 8.2g A recycled fluid needs no recycling logic

Aluminium is the canonical loop: **Alumina Solution** drinks 180 Water/min and
**Aluminum Scrap** hands 120 back, and water cannot be sunk (§5.6), so the returned water
has to go somewhere. Nothing in this project recycles it, and nothing needs to.

Water is ONE balance row. The scrap recipe is a producer on that row, the alumina recipe a
consumer, the extractors another producer, and the equality settles it:

```
4x Alumina Solution                 -720.0 m3/min
4x Water Extractor on normal Water  +480.0
2x Aluminum Scrap                   +240.0
                              NET     +0.0
```

The loop is not a loop in the LP — it is two columns touching one row, which is the same
reason § 8.2 gives for equality balances in the first place. A tree walk would have to
decide how many times to go round; the LP has no notion of "round".

**Two bugs fell out of checking it.**

`water_extractors=0` did not mean zero. `int(x) if x else DEFAULT` treated an explicit
zero as absent and substituted the 200-pump assumption, so a plan told it had **no water**
came back making 480 Aluminium Ingots on 480 m³/min of it. Zero is a meaningful answer —
it is exactly what you ask of an inland site — and it is now `is None`, not falsiness.

With that fixed the plan solved to **nothing, and said nothing**: `buildings=0, exports:`
with no complaint, because an all-zero solve is genuinely optimal. Every recipe was present
and unlocked so `unmakeable` had nothing to report either. The supply probe now runs on an
empty solve for the same reason it runs on INFEASIBLE — a bare zero is not a diagnosis:

```
! this plan is EMPTY -- it solved to zero machines. That is optimal, not broken
! missing raw: Water (water comes from water volumes, and no Water Extractor is unlocked)
```

### 8.2h Running a cycle once, without banning it

*"The Recycled recipes are wanted, just not recursively."* `exclude_recipes` cannot say
that — banning the recipe also bans the useful single pass.

`recycle_once` names processes that may run but must not feed each other. For every item
the named set both makes and eats, consumption INSIDE the set is capped by production
OUTSIDE it:

```
Σ(consumed by named)  ≤  Σ(produced by everything else)
```

That is exactly one pass, and there is no pass counting anywhere. On the coupled Spire
plan:

| | free | once |
|---|---|---|
| net MW | 83,471 | **81,254** (−2.7%) |
| plastic eaten inside the loop | 100 | 150 |
| plastic made **outside** | **0** | **150** |
| Residual Plastic | never built | **8** |

Unconstrained, plastic is made *entirely* inside the loop and some goes straight back in
while Residual Plastic never runs. Constrained, Residual Plastic supplies the single pass
and the constraint binds exactly (150 ≤ 150). Both Recycled recipes still run, which is
the whole point of not reaching for `exclude_recipes`. The 2.7% is the price of refusing
to recurse, and quoting it makes this a decision rather than a preference.

**The cycle is NAMED, not detected, and that is the design.** The first attempt detected
cycles automatically and found **24 items** on this recipe set — because every
package/unpackage pair is a cycle (Water → Packaged Water → Water), as are the aluminium
and fuel loops. Constraining all of them made every plan INFEASIBLE. Only the caller knows
which loop they mean, so the argument takes patterns in the same grammar as
`exclude_recipes`, and a pattern matching nothing is refused rather than ignored.

**Not a pass count.** `max_cycle_passes=2` would need the cycle unrolled into indexed
copies with its items split per pass — a different formulation, not a flag. One pass is
exactly expressible; a number that only looks precise is worse than a named mode.

### 8.2i Solving a plant in pieces

*"Three loops instead of one."* A module is a plan with no nodes of its own and declared
inputs: `supplied={item: rate}` hands it what another plan makes, as a free raw input up to
that rate. `Scenario.raw_caps` had always supported this and nothing reached it.

Given the rig's 2,300 Polymer Resin, the resin plant solves to **30 Residual Plastic + 13
Residual Rubber** — the hand-built module, derived. Chained across all three:

| | machines | net MW |
|---|---|---|
| three loops | 765 | 99,386 |
| one solve | 787 | **99,730** |

That looked like decomposition costing **0.35%**. It was not. Chasing where 344 MW went
found the whole of it in **one row**: the same 10,300 m³/min of water, on 31 pumps at 247%
drawing 2,052 MW, where the single plan used 64 at 134% for 1,887.

**A power-blind objective overclocks, and the cost is invisible in its own answer.**
`max_item Fuel` does not price power, and phase 2 breaks ties by minimising **machine
count** — so among all solutions hitting the target it takes the fewest machines, which
means the highest clocks, and power goes as `clock**1.32`:

| pumps | clock | draw for the same water |
|---|---|---|
| 31 | 247% | 2,178 MW |
| 52 | 165% | 2,017 MW |
| 64 | 134% | 1,887 MW |
| 86 | 100% | **1,716 MW** |

Re-solved with `min_power` and `export_minimums`, the rig runs 64 pumps at 120% and the
chain comes to **99,806 MW against the single plan's 99,730** — decomposition *wins* by
77 MW, on 798 machines against 787. So the real rule is not "splitting costs optimality",
it is **a module must price whatever the whole plant cares about**; a power-blind module in
a power plant is the bug. `plan_factory` now says so when an objective that ignores power
produces overclocked rows.

### Solver tolerance at an interface

The rig exports **2299.9998** Polymer Resin. That is not instability and not a real
fraction: the build is 115 refineries at 100% making 20/min each, which is exactly 2,300.
HiGHS returned **114.99999** machine-equivalents — a relative residue of 8.7 × 10⁻⁸, right
at its default tolerance — and the printed clock rounds to `1.000000` and hides it. The
displayed row and the displayed rate simply do not reconcile in the last digit.

It is harmless inside one solve and not harmless **across** two, which is why `raw_caps`
carries the tolerance described below.

**A module needs a selector that selects nothing.** `sources=["bbox:…"]` over open water
gives zero nodes, which is what stops the resin plant tapping crude and re-deriving the
rig; water survives it, because water is placeless and has no node to lose.

**The boundary trap, which is inherent to chaining.** `Solution.exports` is rounded to 4 dp,
so the rig exports **2299.9998** resin while the resin plant's demand needs exactly 2300.
Fed on as an exact cap, the module comes back **INFEASIBLE** — a whole plant lost to two
ten-thousandths. `raw_caps` now carries the same kind of tolerance phase 2 already uses to
pin its objective (1e-6 relative, 0.002/min on 2,300). The `binding` check had to move with
it: a cap nudged up by a relative epsilon and compared against a fixed absolute one stopped
reporting an input consumed to the last drop, so the constraint still bit and the response
stopped saying so.

**Supplied items are free, and the output says so loudly.** `advisor` records what
forgetting this costs — a basket fed in as free raw inflated a northern baseline from
92,269 MW to 171,882. Correct for a module, whose inputs are paid for where they are made;
badly wrong as a whole-plant comparison. So a plan using `supplied` is labelled a MODULE
PLAN, its inputs are named with their rates, and it says outright that whatever produces
them must export at least that much or the chain does not balance.

### 8.3 Guards

Both run on **every** solve:

- **Duplicate-process-id assertion.** A real bug: a process id built from building+purity but omitting the
  *resource* made `MinerMk2:normal` collide between coal and sulfur, so one column produced both. The
  model balanced, passed a mass-balance re-check (which keyed off the same colliding id), and returned
  5,985 MW instead of 8,610.
- **Free-lunch audit.** Strip extractors, zero `raw_caps`, maximise MW; must return exactly 0.

### 8.4 Clocks, machine counts and logistics

**Machine counts are whole buildings at a derived clock.** A solve returns `x` in
machine-equivalents; the readout reports `ceil(x)` machines all clocked to `x / ceil(x)`. So 52.8
becomes **53 Blenders at 99.6%**. That is exact, always a clean ratio, and how the game is actually
played — a fractional count is the ratio, not a rounding error.

It is also provably the best way to run that throughput: power is `c^1.321929`, which is **convex**, so
for a fixed total `Σc` the sum `Σc^k` is minimised by spreading the clock *equally*. A uniform clock
therefore beats any mixed set, and the derivation needs no solver support at all.

Because of that, **ratio underclocking is free and automatic**, and solver-side integrality is off by
default: forcing whole machine-equivalents makes exact ratios unreachable and can turn a feasible plan
infeasible, since every item balance is an equality.

**Sub-100% clock modes are a different question, and are priced not banned.** Offering `clocks=[0.5, 1.0]`
lets the solver *spread* a fixed throughput over more machines purely to save power — measured at
+1140 MW for +441 machines, i.e. 2.58 MW per extra machine. That is a real option, so each machine costs
`machine_cost_mw` (default **5 MW**, set just above that measured figure) whenever the objective is
`max_mw` or `min_power`. For `max_item` / `min_raw` no penalty is applied because underclocking gives no
throughput benefit at all and is never selected.

The warning fires **only** for genuine spreading, never for a derived ratio clock. An earlier version
warned on any clock below 100%, which meant a routine 99.4% ratio looked like a tradeoff the caller
should second-guess.

Two proven facts kept from the research:

- **Linear power is a safe over-estimate below 100% clock** (`c^k ≤ c` for `c ≤ 1, k > 1`), which is what
  the LP optimises; the readout then recomputes each row's draw exactly at the derived clock, so the
  reported figure is never worse than the solve promised.
- **Overclocking above 100% is never selected for a throughput objective** — adding 150/200/250% modes
  changed the optimum by exactly 0 MW. It is also not offered by default because it consumes Power
  Shards, which nothing here counts.

**Logistics are reported, never constrained.** Every plan lists each item's flow with the belt or pipe
lines it implies at the current tier. A throughput *cap* would be wrong — parallel lines are legal and
the game has no global limit — but a plan that silently needs 7 Mk2 pipes of water is not a plan. Water
is modelled as unlimited, which on this map it effectively is, so the extractor count and pipe count are
surfaced explicitly rather than hiding inside a ratio.

> **The table must be askable, because volume order buries the question.** The rows rank by flow, and
> the table used to truncate at a hardcoded 6 while ignoring the tool's own `limit`. On the reference
> Spire Coast plan that cut it at Polymer Resin: **Plastic ranks 7th** — one of the two items that plan
> exists to size — so the answer was invisible and had to be reconstructed by multiplying machine counts
> by recipe rates. It now honours `limit`, and `logistics_items=["Plastic", "Rubber"]` pins named items
> whatever they rank. Pins are **added to** the limit rather than carved out of it: naming two small
> items must not push two big ones out, or one blind spot is simply traded for another. Anything hidden
> is counted in the note, and the multi-line warning says how many flows it did not list.

**Somersloops were dormant** by explicit decision, and are no longer. The machinery existed
(`Process.sloops`, `Scenario.sloop_budget`, per-building boost multipliers) and nothing populated the
budget. `sloops=` now does (§8.2f), gated on the research (§6.9), with installed ones read exactly —
OQ4 is closed. The Alien Power Augmenter is still a different model and still unbuilt.

### 8.5 Layout: blocks, buses and floors

`plan_layout` turns a solved plan into a buildable **schematic** — modules, connections, floor
assignment and a space budget. Explicitly *not* a blueprint: no world coordinates and no belt routing,
because there is no terrain heightmap in any data available here and inventing one would be worse than
declining.

**Footprints are derived, not hardcoded.** Every buildable carries `mClearanceData`, so machine
dimensions come from the game: Constructor 8×10×6, Refinery 10×22×15, Blender 18×16×10.5,
Manufacturer 18×20×11. Foundations follow at the 8 m grid.

> **The rotation trap.** Clearance is a *list* of boxes, and they can carry a `RelativeTransform`
> rotation. The Fuel Generator's is several thin boxes at 45° increments approximating a round machine,
> so taking the largest box naively yields **22×4 m** instead of the real ~20×20 — understating a
> 176-generator plan by roughly 1,000 foundations. Every box is transformed by its quaternion and the
> union taken.

**Blocks come from throughput, not taste.** 46 Refineries drawing 1,380 m³/min of crude cannot share one
manifold when a Mk2 pipe carries 600 — that is 3 lines, so it is 3 blocks. **Line count is block count**,
which makes the split derived. A secondary cap (24 machines) keeps a manifold physically sensible.

**Connections are buses, not pairings.** The LP gives net balances, not who feeds whom; recovering
specific producer→consumer pairs is a min-cost flow with no unique answer absent geometry. Each item gets
one bus that producers feed and consumers draw from — which is what a manifold physically is.

**Floors come from chain depth**, computed on the graph's **condensation**. A plain longest path is
unavailable because the recipe graph contains real cycles (Recycled Plastic ↔ Recycled Rubber), and
naive relaxation does not settle on one — it lifts every member a stage per pass, so depth ends up
reporting how long the loop ran. Collapsing each strongly connected component makes the graph acyclic and
puts cycle members on one floor, which is also correct physically. Floor height clears the tallest
machine on it; logistics decks are 4 m.

Site size is the **peak** floor, not the sum, since floors stack.

### 8.5a plan_layout must take the same arguments as plan_factory

`plan_layout` accepted neither `extractor_clocks` nor `water_extractors` (nor `clocks` or
`machine_cost_mw`), so it silently **re-solved at defaults** and schematised a different
plan than the one it was asked to draw — measured at **15,043 MW against the 83,737 MW
plan**, because base extraction is roughly a sixth of overclocked. Both tools now take the
same solve-shaping set and agree; a test asserts the signatures stay in step, because the
failure is silent and looks like a legitimate operating point.

**Floors follow chain depth, which is a correctness property and not a physics one.**
Chain depth puts a consumer above its producer so the schematic reads in build order.
Fluids do not care: a pipe running downhill is free, one running uphill needs head, and
water can only be drawn at sea level whatever the chain says.

The consequence is that chain-depth ordering tends to make *everything* climb. On a
measured oil plan, nothing fell:

| fluid | direction | floors | rate |
|---|---|---|---|
| Water | climbs | 4 | 11,500 m³/min |
| Fuel | climbs | 2 | 9,200 m³/min |
| Heavy Oil Residue | climbs | 2 | 4,600 m³/min |
| Crude Oil | climbs | 2 | 3,450 m³/min |

Reordering by hand — water extractors at sea level under the blenders, generators one
above, refineries on top with crude arriving high — leaves only water and fuel climbing one
storey each, and lets residue and crude fall for free.

`fluid_head()` reports this and `plan_layout` surfaces it. It is deliberately **not**
optimised: the right stack depends on terrain, on where crude arrives, and on how much
pumping the player will accept, none of which this model has. Naming the cost is what lets
a planner disagree with the default.

### 8.5b Capping a deck, and naming the head

**`max_floor_foundations=` inverts the layout question.** Uncapped, `plan_layout` answers
"how big a site does this need" by giving each chain stage a deck of whatever size it
wants — 496×496 m on a measured oil plan. A player with a finished platform is asking the
reverse. Same computation, run backwards:

| cap | production decks | peak | site |
|---|---|---|---|
| none | 5 | 2,920 | 440×440 m |
| 1225 (35×35) | 7 | 1,200 | 280×280 m |
| 900 (30×30) | 8 | 900 | 240×240 m |

**Total foundations are conserved at 4,719 across every cap** — the same machines stacked
differently — and a test asserts it, because a total that moved would mean the cap was
dropping or duplicating blocks. A block larger than the cap gets a deck of its own rather
than being split: a block is one manifold.

**Elevation was never missing.** `z` is in the node table and in every machine position and
was read by nothing but `geo.cluster`'s centroid, so a planner reasonably concluded the tool
had no z-data and guessed pump counts by hand. Node rows now carry it, and a fluid field
reports its head span — the 13 Spire Coast crude nodes cover **−17 to 23 m, a 40 m span**.

Reported as a span, **never as a pump count**: head per pump is a game rule this project has
no data for, and a test asserts no pump count is invented. Solid fields say nothing, because
a coal field climbing 200 m costs a belt nothing.

### 8.5b2 Elevation, sampled -- because there is no heightmap

`z` reached the node table and the trunk head spans, but a bare coordinate still had no
answer. Nothing in Docs.json or the save carries terrain. What both carry is a lot of
things whose Z is exact:

| source | count | is it ground? |
|---|---|---|
| resource nodes | 608 | **yes** -- a node rests on terrain, and needs no save |
| foundation/wall pieces | 8,347 | no -- built elevation |
| production buildings | 566 | no -- built elevation |

So `describe_location` answers with a **sample**, its count and its spread, never an
interpolated surface. A single number invented from three points 40 m apart reads as
measured and is not.

**Ground and built are separate populations, and that is the load-bearing decision.** A
foundation is wherever the player put it -- often deliberately level across a slope. On this
world's main platform the probe finds **805 structures against 1 node**, so an average would
silently *become* the platform height while still being labelled ground. Reported apart,
their difference is the interesting number instead: the fill already stacked there.

Even that difference is refused below `MIN_GROUND_SAMPLES = 3`. Not a statistical
threshold -- a refusal: one node is a point, and a point is not a ground level. Quoting a
25 m fill from a single sample would be an invented number wearing a measurement's clothes.

Unsurveyed ground says so and names the radius, the same way the tool already returns
'off-map or ocean' rather than guessing the nearest land region. And an old projection
(the committed fixture is schema 5, predating `structures`) loses one source rather than
failing.

### 8.5c Which nodes share a pipe

`logistics` already counts LINES -- `ceil(rate / capacity)` -- which is the right total and
says nothing about which nodes share one, and the layout schematic starts at the factory
edge with the crude already arrived. `plan_layout show="trunks"` fills the gap:

| trunk | nodes | rate | run | head |
|---|---|---|---|---|
| T1 Crude Oil | 3 | 600/600 | 289 m | |
| T3 Crude Oil | 2 | 450/600 | 118 m | |
| T4 Crude Oil | 1 | 600/600 | 0 m | |
| T6 Crude Oil | 3 | 600/600 | 290 m | **UP 40 m** |

**A trunk is a line, not a blob.** Pipes are laid end to end and each node joins the one
running past it, so nodes are ordered along a nearest-neighbour chain from the node
furthest from the destination inward, and the chain is cut wherever the next node would
overflow. Capacitated clustering would give tighter blobs and a worse answer: two nodes
40 m apart on opposite sides of a run are not on the same pipe.

**This is where the head span becomes actionable.** `search_resource_nodes` reports the
Spire crude field spanning 40 m (§ 8.5b). Attached to a trunk, the answer is sharper --
five of the six runs are flat and *one* climbs the whole 40 m. `lift_m` is signed and
measured inward, so the sign is the answer: downhill needs no pumping. Head is reported for
**pipes only**; a belt does not care that its sulfur climbs 218 m.

**And the pump count is now real.** This section previously said head-per-pump was a game
constant with no source and refused to give a number. It is `mDesignPressure` in Docs.json
— **20 m on a Mk1 pump, 50 m on a Mk2** — and it was there all along under a name nobody
grepped for, because the search terms were "head" and "lift" and the game calls it
*pressure*. The third instance in one session of treating a failed grep as proof of
absence (§ 6.9 has the other two). So T6 reads `UP 40m (1x Pipeline Pump Mk.2)`.

Two things keep it honest. It quotes **the best pump the player has actually unlocked**,
not the best that exists — quoting Mk2 to someone without it understates the build by more
than half. And it stays a **lower bound**, because pipe friction and the head a full pipe
holds on its own are not modelled: it answers "at least this many", which is what sizing a
build needs.

Two cases are surfaced rather than smoothed over. A single node above one line's capacity
(a pure Crude Oil node at 250% makes exactly 600 m3/min) gets a run of its own instead of
being split silently -- it is a real problem the player solves with a second pipe off one
extractor. And Water Extractors sit on no node, so they get no trunk and are named in a
note, because 9,200 m3/min vanishing from a table that otherwise conserves every unit
would read as a complete answer.

`run` is the straight-line chain and is labelled a LOWER BOUND: there is no terrain here,
so a drawn route would be invented -- the same line § 8.5 draws around the schematic.

### 8.5d Commissioning: a startup order, not a build order

The re-frame that removed most of this problem came from a question: *"Can't we just build an
unpowered factory, and just power it after the build is done?"*

Building costs materials, not power -- a machine draws only when it runs. So the whole
821-building plant is constructed at leisure, drawing nothing, and then energised block by
block. **There is no power-constrained build order to search for.** The commit-granularity
question that looked like the hard part (partition by self-powered slice? by grid-positive
slice? by vertical column?) simply dissolves, and with it the objection that vertical
slices make you revisit every block N times. Each block is built once.

What is left is one hard constraint:

> at every step, sum(energised consumer draw) <= headroom + generation from generators
> already receiving fuel

**Generators are free to energise** -- `power_mw == 0`, `power_production_mw == 250`, read
from the dump. Only consumers spend headroom, so a wave costs its consumers and refunds its
generators, and the refund pays for the next wave. On the measured Spire Coast plan
(14,524 MW draw, 121,875 MW generation) that converges in four waves from 711 MW free:

| wave | machines | draw | generation | free after |
|---|---|---|---|---|
| W1 | 16 | 661 MW | +1,249 MW | 1,299 MW |
| W2 | 49 | 1,171 MW | +6,493 MW | 6,622 MW |
| W3 | 284 | 5,093 MW | +41,957 MW | 43,485 MW |
| W4 | 472 | 7,598 MW | +72,176 MW | 108,062 MW |

**The bound is hard, not advisory.** Exceeding available power in Satisfactory does not
degrade gracefully -- the fuse blows and the whole grid stops until reset by hand,
including the plant that was feeding it. So the tool also recommends **one Power Switch per
block**, which has to be built in from the start; energising is then a switch flip and a
misbehaving block can be isolated.

**A wave never pays for itself.** Between energising a wave's refineries and its generators
burning fuel, the pipes are filling and nothing is coming back, so `available` only grows
once the wave completes. This is the difference between a sequence that works and one that
looks fine on paper.

**And the wait now has a number.** "Wait, then next wave" was the least actionable line in
the sequence, and the wait is exactly the interval the deficit is carried through.
`Wave.fill_s()` sums the slowest cycle at each chain depth — every stage must finish one
full cycle before the next sees anything — divided by clock, since a machine at 250%
finishes in 40% of the base time. The measured Spire waves come to **≥ 35 s**
(1 s extraction + 12 s refining + 6 s blending + 16 s packaging).

It is presented as a **floor**, not an estimate, and the reason is the recurring one: a
pipe's fluid volume is not in Docs.json. The only dimension there is `mRadius`, which is
collision geometry, and turning it into litres would be a guess dressed as a measurement —
so pipe transit is excluded and dominates on a long run. Machine input buffers are out for
a related reason: their capacity is per-BUILT-machine and these machines do not exist yet.
A generator contributes zero, because it burns continuously and has no cycle to wait
through.

**The floor is reported.** One machine of every process -- the cheapest slice that still
feeds the whole chain -- costs **631 MW** here against 711 free. Below that no startup order
exists at all, and the tool says so and names the number rather than emitting a sequence
that trips on step one.

Two honest limits. Waves are power-ordered, **not ratio-balanced**: whole machines cannot
hit the plan's ratios at the bottom of the ramp, so early waves run starved. That errs safe
-- a starved machine idles and draws less than modelled -- and is stated rather than dressed
up as a balanced mini-plant. And headroom is printed as a **labelled input**
(`source: power_report, nameplate`), so a sequence computed against a save that has since
moved is visibly stale instead of quietly wrong.

Node choice follows the same least-work-first idea (§ 8.5c). The ranking is by what a node
COSTS to take, which is not "prefer untapped": a node already carrying the extractor this
plan wants is cheapest of all, untapped is next, and a node held by the WRONG extractor is
last because taking it means demolishing something running. On the reference save all
thirteen Spire crude nodes are tapped -- every one by the Oil Pump the plan wants -- so a
plain free-first rule would have ranked them all equal-worst.

### 8.5e Which stage am I in — detected from the save, never stored

The requirement: *"I feel like the mcp should have fundamental capacity to identify stages."* So a stage is a
domain concept, not a printout — and usefully, one that needs **no new persistence layer at all**.
A stage is a partition of a stored plan (§ 8.5d), and `diff_vs_save` already matches built machines
against a plan by identity, so *grouping that output by stage* is the whole feature. Nothing is
written; a stored plan is still only a stored *request* (§ 10.1a) and is re-solved every call.

Three modules and no new tool, because each already owns exactly one half of the answer:

| owns | module | contributes |
|---|---|---|
| the partition | `commission()` | which machines are in wave *k* |
| the matching | `build_diff()` | which of them exist in the save |
| the evidence | `graph.health.assess()` | what each existing one is doing |

`track()` only joins them, on `diff.group_key` — the same key the diff matches on, promoted from
private to public for exactly this reason. Joining on anything else (the display label, the building
class) would let the tracker credit a Refinery on Alt HOR with one making alumina, which is the
failure §8.6 exists to prevent, re-introduced one layer up.

**Built machines fill the earliest stage first.** Identical machines are indistinguishable in the
save — nothing records which Refinery was *meant* for wave 2 — so progress is assumed to have been
made in the order the sequence prescribes, and within a stage the machines proven to be running are
taken first. This is a modelling decision, not a measurement, and it is stated in the code that makes
it. Any other rule needs evidence the file does not carry.

#### Built and energised are different states, and the save proves only one of them

This is the crux, and conflating the two would make the tracker lie on the most important day of a
build. Under the Q1 re-frame you construct the entire plant unpowered and then energise it block by
block, so **fully built and wholly dark is the expected state**, not an anomaly. What the file
actually supports, measured on the reference save rather than assumed:

| signal | present | what it settles |
|---|---|---|
| `uptime` (300 s productivity window) | **517 of 566** machines/extractors/generators | `produce_s > 0` **proves** the machine ran, so it **had power**. The only positive evidence of energisation in the file. |
| `uptime` at zero | the other side of the same field | Proves nothing. Unpowered, starved, blocked and idle are indistinguishable. |
| `buffers` | every record | Names a *supply* cause (blocked / starved), which **excludes** power as the explanation but can never confirm it. |
| `paused` (`mIsProductionPaused`) | 16 actors | A different thing entirely: the player switched this machine off, recorded per machine whatever the grid is doing. |
| `clock` (`mCurrentPotential`) | 46 actors | A slider position, not a state. Does not move when power does. |
| power wires (`graph.power`) | 1,287 edges | "Wired to nothing" is knowable. Wired is **not** energised. |
| `mHasPower` | **0 of 44,307 objects** | Carries no `SaveGame` specifier on `UFGPowerInfoComponent` (checked in Headers.zip). Not in the file. |
| `mCircuitID` | **0** | Same — grid membership is rebuilt at load, so *which grid a machine is on* is not readable. |
| `BP_CircuitSubsystem` | 1 object, **empty property set** | Confirms the above from the other direction. |
| `mIsSwitchOn` | *is* `SaveGame` on `AFGBuildableCircuitSwitch` | …but this world has built **no power switch at all**, and the projection does not read one. Even the per-block switch § 8.5d recommends would be unreadable today. |

So a stage reports `running` — what it can prove — and **refuses to convert silence into
"unpowered"**. The residue after the save's own explanations (paused, starved, blocked, no recipe,
dead node) are taken out is reported as `dark`, health.py's `stalled` bucket plus the unmonitored,
and named as *consistent with* not being energised rather than as evidence of it. Same precedent as
`sloop_budget()` reporting committed sloops as unknown rather than zero (§ 6.5) and
`phase_requirements` labelling stale rows instead of filtering them (§ 6.4). A save carrying **no**
monitor at all — the committed test projection is one — reports *no evidence either way*, which is a
different answer from "nothing is running" and must never be printed as one.

#### The surface

No new tool. `diff_vs_save` gains one argument:

* `plan=<name>` alone appends the stage table — a stored plan is what makes a stage number worth
  writing down, so recalling one turns grouping on without being asked.
* `stage=<n>` narrows to one stage's delta, and **drops the cost table**: a stage is a switch-on, not
  a build step, so splitting the materials bill across stages would describe a build nobody does.
* `stage=0` asks for the overview without a stored plan, at the stated cost that the numbering
  renumbers whenever an argument or the world moves.
* Plan-id drift is reported *here*, not only in `list_plans`, because a stage number is a milestone a
  player remembers and a re-solve against a moved world can renumber the whole partition under them.

Grouping is off by default: the numbering is only stable for a stored plan, and a diff nobody asked a
stage question of should not pay the context for one (the 2,600-char diff budget is pinned). Where
the stage table appears, the older *"place it in ≥18 proportional slices"* line is suppressed — that
is the answer from **before** the startup-order re-frame, and printing it beside a startup order tells
the player to partition a build that is not partitioned.

Measured against the reference save with the stored `spire-coast-full` plan (787 buildings, 99,730 MW,
711 MW free):

| stage | on | built | running | MW | free after | state |
|---|---|---|---|---|---|---|
| S1 | 17 | 13 | 13 | −701 / +1,750 | 1,761 | 76% built |
| S2 | 72 | 22..25 | 23 | −1,627 / +10,000 | 10,134 | 31–35% built |
| S3 | 524 | 7..23 | 10 | −10,024 / +76,500 | 76,610 | 1–4% built |
| S4 | 174 | 0 | 0 | −2,901 / +26,750 | 100,458 | not built |

→ *you are in **stage 1** of 4: 76% built (13/17), 13 machines proven running.*

Built counts stay a **range** wherever identity is unavailable, for the same reason § 8.6 gives — and
that is why `running` can sit *above* the low bound without contradicting it: the low bound throws
away every Water Extractor that cannot be attributed to this plan, and some of those are running. The
two columns have different denominators, and the report says so rather than letting the numbers
argue with each other.

### 8.5f What a plan costs to build

The startup re-frame separated running cost from construction cost (§ 8.5d), and only the
first had ever been measured. `plan_layout show="materials"` measures the second:

```
machines=821  foundations=4719  distinct_parts=15
costliest: 488x Fuel-Powered Generator = 78,080 parts, 4719x Foundation = 23,595 parts

item                      need    have   short   for
Concrete                 23595   60622          Foundation
Rubber                   24400    5996   18404  Fuel-Powered Generator
Motor                    10705    5254    5451  Fuel-Powered Generator, Blender, Refinery
Heavy Modular Frame        920     244     676  Blender
```

**This does not replace `diff._cost`, and the difference is the point.** That charges the
**delta** -- what is left to place -- filtered to what you are short of and ranked by how
hard the shortfall is to fix: a shopping list for the next session. This charges the
**whole plan**, every item whether or not you hold it, attributed to the buildings that
want it. Neither is derivable from the other, and a reader who conflates them will think
the plant is cheaper than it is, so the output says so out loud.

**Foundations are the number nobody had.** They are not machines, so no build table counted
them, and 4,719 of them at 5 Concrete each is 23,595 Concrete — larger than every machine
line except Rubber. It is charged at `total_foundations`, not `Layout.foundations`: the
latter is the PEAK floor, which is what sizes the *ground* because floors stack, but
concrete is poured for every storey. Charging the peak would understate the deck by the
height of the stack.

Two refusals, both familiar. **Belts and pipes are not costed** -- their cost is per metre
and there is no route, so a length here would be the largest invented number in the
project; line counts and the trunk lower bound are offered instead. And the bill stops at
**build-gun components, not ore**: flattening would have to guess a depth through the
Recycled Plastic / Recycled Rubber 2-cycle, which is exactly why `bom` uses an LP. The two
compose -- this says "10,705 Motors", `bom` says what a Motor costs.

### 8.5g One sizing primitive

"How much floor do N of these need" was being answered in two places with the same wrong
arithmetic. `plan_layout` sized every block as `footprint.foundations × n`, and the
water-siting note had independently grown its own copy — while `Footprint.foundations`
says in its own docstring that it is per-machine and **ignores shared edges**.

It is one computation and it is now in one place: `Footprint.pack(count, columns=)`.

| pumps | `n × found` | packed block | pier |
|---|---|---|---|
| 30 | 270 | 10×3 = 200×54 m, **175** | 540 m, 204 |
| 64 | 576 | 8×8 = 160×144 m, **360** | 1,152 m, 432 |
| 77 | 693 | 11×7 = 220×126 m, **448** | 1,386 m, 522 |
| 105 | 945 | 15×7 = 300×126 m, **608** | 1,890 m, 711 |

`columns=1` gives a single row, whose *length* is what platform modules get measured
against. Left at 0 it searches every column count — and **two false starts are worth
recording, because both looked obviously right**:

*Squarest* was the first rule, on the reasoning that perimeter waste costs tiles. It is
wrong: four Oil Extractors (8×13 m) laid 3×2 span 24×26 m and need **12** tiles, where
four in a row span 32×13 m and need **8**. Unfilled grid slots and depths that land just
past a tile boundary lose more than the perimeter saves.

*Cheapest* then produced ribbons: the true tile optimum for 77 pumps is 2×39, a
**40×702 m** strip that wastes nothing at either edge, saves 4%, and is not a thing anyone
builds. So the default is cheapest among shapes within `MAX_BLOCK_ASPECT` (4:1) — the one
judgement call in the module, labelled as such — **plus the single row, always**. Keeping
the row as a candidate is what preserves the guarantee that this never exceeds
`n × foundations`; without it a 5-machine Lookout Tower block came out at 6 tiles against
the old 5, making the change an improvement on average and a regression in places.

**It moved real numbers.** The Spire Coast plan's deck fell from 6,529 foundations to
**4,719** — 28% — its peak floor from 3,774 to 2,920, and its site from 496 m square to
**440 m**. Everything downstream inherited the correction at once: the concrete line in the
build bill (§ 8.5f), the deck-cap table (§ 8.5b), and `assess_fit`'s verdict on whether a
plan fits an existing platform. That is the argument for one primitive rather than two
agreeing implementations — the second copy is not merely duplication, it is a second thing
that can be wrong on its own.

The layout tests did not need changing, which is the other half of the point: they assert
conservation across caps and `peak == max(floor)` rather than hardcoded totals, so they
survived a 19% shift in the underlying number and would still have caught a cap that
dropped or duplicated a block.

### 8.5h Duplicated logic found by asking

A sweep for "same computation implemented twice", prompted by the sizing primitive above
turning out to be exactly that. Ranked by whether the copies can actually disagree.

**Chain depth — fixed, and it was a live bug.** `commission._depths` relaxed depths
iteratively with a cap while `layout.chain_depth` condenses strongly connected components,
and `diff` already used the latter. On a Recycled-shaped 2-cycle they disagree outright:

| process | `chain_depth` | the relaxation |
|---|---|---|
| ore | 0 | 0 |
| plastic | **1** | 7 |
| rubber | **1** | 8 |
| sink | **2** | 8 |

The relaxation splits cycle members across stages, which is wrong (they have to be
energised together) and unstable (the depth depends on the iteration cap). It mattered
across modules, not just locally: `track` joins a commission wave against a diff row, so
the two halves of "which stage am I in" could order the same plant differently. Now one
function, with a test asserting the two agree on the reference plan.

**Carrier and line count — fixed. Three copies, not two.** `layout._carrier` /
`_lines_for` and `optimize._logistics` both derived carrier, unit, capacity and
`ceil(rate / capacity - 1e-9)` from `item.is_fluid` — and `Item.unit` had already
centralised the unit string both re-derived.

The shared epsilon is the tell. `- 1e-9` is load-bearing (1,560/min over a 780/min belt is
exactly two lines; binary rounding makes it three) and nobody arrives at it independently,
so one copy came from the other. They had already drifted on `capacity <= 0`: layout
returned 1 line, optimize returned `None`. Unreachable — capacity comes from a tier lookup
with a non-zero fallback — but a divergence inside duplicated code is a bug waiting for the
day it becomes reachable. Now `planning/carrier.py`, resolved toward 1, because the count
feeds block splitting and `None` would need a guard at every use.

**Centroid and spread — fixed, six sites.** `graph/identity.py` and `graph/query.py`
each carried `sum(p[0])/len(p)` plus `max(dist(a, b))`; `diff` had a named `_centroid`;
`app`, `select` and `trunks` inlined theirs again. `geo.Cluster` had both all along, just
shaped for node dicts rather than `(x, y)` tuples — which is how the copies started.

Now `geo.centroid` and `geo.diameter_m`, with two behaviours pinned that the copies
disagreed about. **Empty returns `None`, not the origin**, because (0, 0) is a real and
important place here — the world centre every compass direction is measured from — so
answering it for "no points" is a plausible wrong location rather than an obvious one.
And spread compares **`i < j` only**: the inline copies iterated
`for a in points for b in points`, computing every pair twice plus the zero diagonal.
Same answer, double the work — 317k distance calls instead of 158k at 563 machines — and
a test pins that the cheaper form changed no number.

**`math.dist(...) / 100` vs `geo.distance_m` — fixed, with one deliberate exception.**
`cohere`, `identity`, `select`, `trunks`, `elevation` and `tools/spatial` each retyped the
centimetre conversion by hand, in three different shapes: `/ 100.0` after the fact,
`<= link_m * 100` scaling the threshold instead, and `limit = radius_m * 100.0` hoisted
into a variable. All now compare in metres.

`graph/structure.py` keeps raw `math.dist`, and that is correct: it works in centimetres
throughout, against cm thresholds (`LINK_XY`, `LINK_Z`, `STAND_ON`) measured in the save's
own units, and reports nothing to a caller in metres. There is no conversion there to get
wrong, and introducing one would mean dividing by 100 only to compare against constants
that would have to be rewritten. Said in the module, so it does not get "fixed" later.

`trunks.run_m` needed the other thing entirely — a **3D** length, because the vertical leg
of a pipe is real pipe. It was passing 3-tuples to `math.dist` and letting it silently do
3D, which put the distinction in the shape of a tuple rather than the name of a function.
Now `geo.distance_3d_m`, separate from `distance_m` rather than a flag on it, because
dropping Z is a modelling decision: for "is this near that", a 40 m climb is noise against
a 400 m walk.

A test walks the source tree and fails if `math.dist` appears anywhere outside those two
modules, so a hand-typed conversion cannot creep back in.

### 8.5i Carrier tiers, assumed for a year

`belt_ipm=780` and `pipe_m3min=600` — Mk5 and Mk2 — were hardcoded defaults, and an entire
design session ran on them with nothing checking the tiers were unlocked. They were, on
this save. Had Pipeline Mk.2 been locked, **every pipe count doubles**: six crude trunks
become eleven and the deck stops fitting. Silent-wrong-by-default is the worst failure mode
a planner has.

Three separate bugs came out of checking:

**The tier is now read from the save.** `best_belt()` / `best_pipe()` pick the fastest
UNLOCKED tier, and `list_buildings` marks every row HAVE or LOCKED with its built count —
the same treatment `alternates_for_item` already gave recipes. Mk6 belt is locked here and
the pick correctly refuses to reach for it.

**`items_per_min` is not the test for "is a belt".** A Personnel Elevator reports 400/min
and carries *people*; a Conveyor Lift duplicates a belt tier's rate. Selection is by native
class (`FGBuildableConveyorBelt`, `FGBuildablePipeline`), or a naive "fastest thing with a
rate" can name something that is not a belt at all.

**`belt_tier` and `pipe_tier` had never worked.** The lookup keyed on
`name.replace("Conveyor Belt ", "")`, which yields `"Mk.5"` — while the parameter defaults
were `"Mk5"` and `"Mk2"`. Nothing ever matched; every call fell through to the hardcoded
780/600, and it looked right only because those were the same numbers. `belt_tier="Mk3"`
would have quietly planned at Mk5 speed. Tokens are normalised now and an unknown tier is
refused by name rather than silently defaulted.

**And the tier reached the schematic but not the solve** — §8.5a's drift, again, in a new
place. `plan_layout` passed its resolved rates to `build_layout` and left the scenario on
the default, so `pipe_tier="Mk1"` changed the block split while the trunk view (which reads
`sc.pipe_m3min`) stayed on Mk2: one response describing two different plants. Carrier
throughput is a stored plan argument now, because it shapes the solve — `belt_ipm` prices
sinks — and not merely the drawing.

### 8.5j What an unlock is worth to THIS plan

`advise_hard_drive` answered this for the two options of one pending drive. The question
underneath — across every alternate *not* unlocked, which would change the factory being
built — was being answered by tracing the recipe tree by hand.

`rank_unlocks` is one counterfactual per candidate, reusing `advisor._solve_with` rather
than growing a second copy of that machinery. On the measured Spire Coast plan:

```
baseline=107257.64  candidates=79  movers=1
gain      vs base  alternate                    machines  on offer  needs
14539.71  +13.6%   Alternate: Turbo Blend Fuel  38        drive 25  Blender
```

**One of 79.** And it is sitting in a pending hard drive with no rerolls left, which turns
`advise_hard_drive_pick` from "here is what is in the pool" into "here is what it is worth
to the plant you are building".

**Sweeping everything, because it is cheap.** A solve here takes **0.01 s**, so all 79 cost
about a second. An earlier guess of 1.5 s per solve had me designing a relevance filter —
only test recipes touching an item the plan already moves, which would have cut 79 to 21 —
and that filter would have been actively wrong: a recipe that opens a chain the plan cannot
currently reach touches none of its items *by definition*, and is exactly the interesting
case. Measuring first removed the need for the cleverness.

**A zero is an answer.** 78 of 79 change nothing, and saying so is the point: "you are not
missing anything here" is the decision the hand-walk was producing. So the sweep reports
how many were tried, not only the winners.

Three things keep the number honest. `gain` is oriented so larger is always better, because
`objective_value` is already sign-normalised for max/min and a `min_raw` plan that halved
its ore would otherwise report a large *negative* gain and sort last. Every delta is an
**upper bound**: a candidate is solved as if any machine it needs already existed — Turbo
Blend Fuel wants a Blender this world has never built — with that machine named beside the
number.

And each row names **what the gain switches on**. That began as a plan to flag "consumes an
item that crosses no site boundary", which the planner correctly called fuzzy — Turbo Blend
Fuel also drags in Sulfur and Petroleum Coke, a bigger architectural change than the fuel
return. Listing the processes the counterfactual *activates* and the baseline did not needs
no judgement at all: `Coal-Powered Generator on Coal, Petroleum Coke`. A headline that turns
on reintroducing a chain you deleted on purpose is a decision, not a free win.

**The trap this tool set for its own author.** Run with ad-hoc arguments it measures a
different plant from the one saved, and the answers genuinely differ:

| plan | movers / 79 | Turbo Blend Fuel |
|---|---|---|
| ad-hoc Spire Coast, no exclusions | 1 | +14,540 MW, +13.6% |
| `spire-coast-full` as saved | **0** | — |

The saved plan bans Turbofuel and coal generators, so the recipe produces a fuel it cannot
burn and is worth exactly nothing. Both answers are correct; only one is about the factory
being built — and this write-up originally reported the wrong one. A call without `plan=`
now says so and names the saved plans it might be ignoring.

### 8.5k Sites: accounting, not optimisation

A planner asked for a joint multi-site solver, then talked themselves out of it while
answering what the sites were:

> The thing that actually bit me wasn't optimisation across sites — it was accounting
> across sites.

The planner's three-module oil plant (rig / generator hall / resin plant) turned out to be
**preference-driven**. Only the rig's siting is forced, by water being drawable at sea
level; the hall has no siting constraint at all and the resin plant only wants a shoreline
of its own. A joint LP with no per-site cap would collapse all three into one — and that
collapse would be **correct**, because nothing in the model prices distance. Honouring a
preference the user never expressed as a constraint would be the optimiser being wrong.

So `plan_layout show="sites"` builds the half with a defensible answer: declare a
partition, report what crosses it.

```
site     machines  net_MW        from      to        item           rate   carrier
A-rig    284       -13,982.82    A-rig ->  B-hall    Fuel           9200   16x pipe
B-hall   460       115,000       A-rig ->  C-resin   Polymer Resin  2300   3x belt
C-resin  43        -1,270.29     A-rig ->  C-resin   Water          1100   2x pipe
```

That reproduces the hand-built interface table exactly, and the coupled variant reproduces
the other one: A→B drops to 14 pipes and a new A→C Fuel link appears at 1,150 m³/min on 2.
**One interface going from zero to nonzero is the whole difference between the two
architectures** — 99,729.62 MW against 83,470.97, both pinned as regression tests.

**The error it exists to catch.** Reconciling by hand, the planner computed generator count from the
rig's total fuel output, assuming all 9,200 m³/min reached the hall. The resin plant's
plastic cycle was drinking some. A partition cannot make that mistake — the plan it cuts is
already mass-balanced by the LP's equality rows — but an *incomplete* partition can hide
it, so unassigned processes are named and the table is declared incomplete. A process
claimed by two sites is reported rather than resolved: a machine is in one place, and
first-wins would hide the ambiguity behind a plausible table.

Two attributions are refused. A shared flow is split between consumers **by share**,
because the LP gives net balances and never who fed whom — the same reason § 8.5 models a
bus rather than producer-consumer pairs. And site power **excludes the AWESOME Sink
charge**, which belongs to the plan as a whole (§ 8.2e).

**What is deliberately not built:** the joint solve. It becomes worth having when two sites
compete for one scarce input — *"I need 2,000 plastic: Spire Coast oil or Western
Beaches?"* — which this plant never did. The one constraint that was observed to bind and is
genuinely site-scoped is **water access**, since water is only at sea level: capping the
reference plan to 27 extractors cost 18.7% and shifted the recipe mix. Making
`water_extractors` per-site is the one piece that needs the solver, and it should wait for
a case where it binds.

### 8.5l Four ways the surface refused to answer

From a second planner's session review. Each cost real hand-work, and none needed new
modelling — only a surface that reached what the data already held.

**A site keyed on the item it produces matched nothing, silently.** Generators produce
`__MW__`, so a site spec of `{"hall": ["MW"]}` matched no process LABEL, came back empty,
and dropped 460 generators into `unassigned` — taking the 9,200 m³/min fuel flow, the one
number a multi-building plan exists to report, out of the interface table. The
incompleteness note fired, but nothing said *which* site was empty. Empty sites and dead
patterns are now named, and the note says what a pattern matches: a label, a building or a
recipe, never a produced item.

**`sloop_budget` existed and nothing exposed it.** The only way to learn how many
Somersloops you held was to guess a `sloops=` budget and read the shortfall warning — you
had to guess the budget to discover the budget. `somersloops` mirrors `power_shards`:
free / committed / owned, where they are, and which machines hold them.

**`list_buildings(kind="all")` matched nothing**, and the AWESOME Sink and both Pipeline
Pumps fell through every branch of the kind filter. So sink draw could not be checked
against the 30 MW the optimizer actually charges, and pump head — added the same day —
was unlistable. A caller fell back on general knowledge, which is the exact failure the
rest of this surface is written to prevent. Kinds are a table now; every building is
reachable and an unknown kind lists the valid ones.

**`recipe_detail` refused a display name** it could resolve, sending a caller to
`search_recipes` and back. It uses `match_recipes` now — the same resolution
`exclude_recipes` has always had — and an ambiguous name lists its candidates rather than
pretending to be unknown.

### 8.5m Ordering floors by head, and paying for the risers

`fluid_head` has always said *"water can only be drawn at sea level, so putting its
extractors at the bottom with consumers above lets the rest of the stack fall"* — and then
ordered floors by chain depth anyway. Naming a cost and defaulting to the arrangement that
pays it is the gap; a planner reordered it by hand and halved the water lift.

`order_floors_by="head"` searches the orders. Floors may be permuted freely, because a pipe
runs in either direction and only the **upward** leg costs pumps — chain depth is a
correctness property (a consumer above its producer reads in build order), not a physics
one. The one fixed point is **Water Extractors pinned to the bottom deck**: water cannot be
drawn anywhere but sea level, so a stack that lifts water to reach it is not a build
however good its arithmetic.

| | chain | head |
|---|---|---|
| water lift | 4 floors | **2 floors** |
| Heavy Oil Residue | +2 floors | **−4 (falls)** |
| pipe-storeys | 66 | **52** |
| pumps | 48 | **46** |

**The search metric is a proxy, and it is checked.** It minimises pipe-storeys weighted by
LINE COUNT — pumps serve one pipe each, so 10,300 m³/min of water is eighteen risers, not
"10,300 units of badness". But pumps round *up* per line, so a 21% better proxy bought only
4% of pumps here. A proxy that can be wrong in the small can be wrong in the large, so both
stacks are built and their real pump counts compared, and the head order is discarded if it
does not win. Two floor builds, against 40,320 if the search itself counted pumps.

**And the risers are now in the bill.** Pumps were absent from `show="materials"`
entirely, so a fluid-heavy plan understated its own build by 46 buildings. Metres come from
the floors actually crossed rather than storeys times an assumed storey, head per pump from
`mDesignPressure`, and the tier is the best the save can place. Still a lower bound: pipe
friction and the head a full pipe holds are not modelled.

### 8.5n Tracing what feeds what

`factory_query` answers this between labelled sets. The question underneath was
unanswerable, and it is the one a cutover asks: thirteen Oil Extractors sit on the Spire
nodes, twenty Fuel Generators are burning, and repiping the wrong extractor first drops
several GW.

**The measured answer is one.** Of sixteen built Oil Extractors, exactly one reaches the
running generators; the other fifteen reach nothing. So "repipe the extractors" is fifteen
safe moves and one that browns out the base — and `commission_plan` now says so beside the
wave rather than leaving it to a tool you have to remember to call.

**Direction is read, not inferred.** Every material edge already carried the connector
role at each end. Of 2,300 connectors landing on a production machine:

| connector | count | oriented |
|---|---|---|
| `Input` / `Output` | 2,002 | ✅ |
| `PipeInputFactory` / `PipeOutputFactory` | 126 | ✅ |
| `FGPipeConnectionFactory` | 172 | ❌ by name |

**92.5% state it outright, and every one of the remaining 172 sits on an extractor or a
generator** — an extractor only produces and a generator only consumes, so the machine's
own nature settles the edge exactly. Segments with neither end known (belt-to-belt,
pipe-to-pipe) are walked **both ways**: over-reporting a feeder is recoverable, missing one
is what costs 5 GW.

A measurement trap worth recording: asking whether *both* ends name a direction reports
**0% of 11,664 edges orientable**, which is true and useless. The far end is nearly always
a belt, and a belt has no direction as an object — only the machine end does.

**Logistics is traversed, not reported.** A trace from the generators touches 331 nodes at
depth 72, almost all conveyor. The walk passes through and lists only machines, the same
thing `graph.query` does to find a factory boundary.

**Only proven-running generators are charged.** `power_at_risk` counts a generator that
produced inside the last complete 300 s window; one that did not may be idle for a dozen
reasons, and charging it would inflate the risk of touching a line that is already dead.

### 8.6 Diff vs save — what to actually change

`plan_factory` says what the factory should be. `diff_vs_save` says what to do about it. The hard part
is not arithmetic, it is deciding which existing machine **counts** toward the plan.

**Identity, never position.** Three keys, each forced by what the save stores:

| plan row | key | why |
|---|---|---|
| recipe | `(building, recipe)` | `mCurrentRecipe` is per machine and is exactly what the player changes. |
| generator | `building` only | A generator has no recipe; `mCurrentFuelClass` is whatever is piped in. 176 on Fuel + 20 on Turbofuel is **196 identical buildings and one plumbing decision**. |
| extractor | the node it occupies | Resolved from `mExtractableResource`, and the plan's extractor columns were built from those same node rows. Exact, no inference. |

Class-only matching is the failure this exists to avoid: the save has **36 Refineries, of which 5 run
Alt HOR** and 31 make copper, plastic and alumina. "You have 36, build 10" is arithmetically true and
tells the player to break their copper line. Off-recipe machines are named as a reuse pool, never counted.

**Spatial proximity is not part of matching**, and the evidence is in this save: the region raster is
±256 m advisory and splits the single 458-building site across *three* region names, 300 m single-linkage
merges the oil plant and the main base (only ~900 m apart) into one cluster, and the plan's own build
targets sit 0.4–2.5 km out. No radius separates them. Position is used only to *report* (`where(km)`
distance bands from the anchor — the centroid of the in-scope tapped extractors, the only thing a plan
pins to the ground) and for one 200 m proximity test that gates what is **mentioned**.

**Where identity is unavailable, emit a range.** All 23 Water Extractors point at `FGWaterVolume`
objects that are not node keys (OQ5), so none can be attributed to a plant. 31 needed vs 23 built is
reported as **build 8..27** — low bound counts every pump in the world, high bound only those standing
among the plan's own machines (4 at the plant, 13 at the main base, 6 at 2.5 km). A single number there
is a confident lie in whichever direction it falls.

**Action taxonomy, free actions first**, because materials are the scarce resource:
`UNPAUSE` → `SETRECIPE` → `BUILD`. On this save that turns "build 4 Assemblers" into four dropdowns —
4 Assemblers with **no recipe set** stand 20 m from the plan's Compacted Coal Assembler, so reusing them
has zero opportunity cost. Paused and recipe-less machines appear **only where they intersect the plan**;
the 10 paused biomass generators belong to `world_summary`, not here.

**There is no `DISMANTLE` verb.** Machines standing among the plan but not in it are listed for the
player to judge. Two guards keep that block honest: the 200 m radius (which excludes the 32 Coal
Generators at 887–1060 m) and a shared-item test — without it, one matched Assembler standing in the main
base swept in 22 Iron Ingot Smelters and 19 Iron Rod Constructors. What survives is the right answer: the
Diluted Packaged Fuel route the 37 Blenders replace.

**Clocks.** The plan's clock is the budget, and the note compares **totals**: the built machines'
clocks added up, plus the machines still to build at the plan's clock, against the plan's machines
× clock. Only a total more than 2% off gets `clocks give 52% of the planned rate (plan: 1 at
95.24%)`. A player may clock down to the exact rate or spread one rate over more machines at a
lower clock, which saves power (the game's exponent is 1.3219, so n machines at 1/n clock draw
n^-0.32 of the power). So a water row planned as 1 pump at 95.24% and built as 2 at 47.62% says
nothing, and neither the machine count nor one machine's clock ever fires the note on its own. A
total above the plan is noted only when there are no more machines than the plan needs, since
extra machines in the scope may belong to another plant. Rows without node identity (water) use
the pumps within 200 m of the plant. The note never becomes the action: the oil pump at 250% still
leaves its row `ok`.

**Ordering** reuses the layout's SCC-condensed `chain_depth`, so extractors fall out at stage 1 and
generators last with nothing special-cased. Power is **incremental** — charging the plan's total would
double-count the 20 Fuel Generators that already draw. "Power first?" then becomes a number: 4,655 MW of
new draw against 831 MW of headroom, and since an LP solution is a **ray**, any fraction of it is itself
feasible and self-powered, so the answer is "build it in ≥6 proportional slices".

**Cost** totals `build_cost` for the build counts against `WorldState.stock()` — carried + storage +
Depot, never machine buffers. Only shortfalls are shown, ranked by shortfall ÷ existing production lines,
so the finding is *"8,800 Rubber and you have no machine making Rubber"* rather than a shopping list.
Items with no automatable recipe (Portable Miner) have zero lines by nature and are demoted.

**Signature: re-solve, no plan handle.** `diff_vs_save` takes `plan_factory`'s arguments verbatim and
re-solves; the server keeps no state and a handle would be invalidated by every autosave rotation. Both
tools print `[plan <id>/save <id>]`. `plan_id` hashes the arguments **plus the save-derived solve inputs**
(unlocked recipes, extractor node census, buildable set), so two responses sharing it are provably the
same plan; `save_id` hashes the machine census, so *same plan, different save* is the mid-build signal.
Solving is deterministic on identical inputs (verified), which is what makes the id meaningful. Cost:
~1.5 s per call and the model must repeat the arguments.

Infeasible and empty plans short-circuit. An empty table would read as "you already have it", which is
the opposite of both.

### 8.8 Constraining the recipe set

Every planning tool takes `exclude_recipes` and `only_recipes`, applied in `build_scenario` so one
translation serves all of them. Patterns resolve in widening order — class id, exact display name, then
case-insensitive substring taking **every** match.

Substring-takes-all is the load-bearing choice: `["Recycled"]` must drop both Recycled Plastic and
Recycled Rubber, because banning half a two-recipe loop leaves the loop intact and the ban useless.
Exact-name match short-circuits it, because `"Plastic"` is the literal name of `Recipe_Plastic_C` and
there would otherwise be no way to target a single recipe whose name is a substring of others.

**A pattern matching nothing is reported, never ignored.** A silently dropped ban returns a plan happily
using the recipe the user forbade, which is worse than refusing because it looks like compliance. The
`plan_id` also covers the recipe set, so a banned-recipe plan cannot be confused with an unbanned one by
`diff_vs_save`.

Worked example on the reference save — max MW from Spire Coast with 300 plastic and 300 rubber required:

| | net MW | routes chosen |
|---|---|---|
| unrestricted | 40,337 | Residual Rubber, **Recycled Plastic**, Residual Plastic |
| `exclude_recipes=["Recycled"]` | 33,313 | Residual Rubber, Residual Plastic |

Banning the loop costs **7,024 MW**, which is the useful output: the tool does not argue, it prices the
preference. Note the direction — the loop is 9× more expensive than resin routes *per unit* in
isolation, but once cheap resin is exhausted it beats scaling resin production, so the solver was right
to use it at the margin.

### 8.9 Player position, node overclocking, and spelling

**Player position** comes from the `Char_Player_C` pawn's transform, never from
`BP_PlayerState_C` — that actor sits at the world origin, so reading it would report every player at
(0, 0). With several pawns the one holding a build gun wins, since that is the one being played.
Exposed as `whereami` and as the `near:me@<radius>` selector.

> **The trap, which I walked into.** The player position must be passed to `select_nodes` as `player`,
> *not* as `origin`. `origin` also turns direction selectors into cones, so supplying it silently
> changed `"north"` from *the northern half of the map* into *a 60° cone from wherever the player is
> standing* — quietly altering every plan scoped by direction. Seven unrelated tests caught it.
> `test_supplying_a_player_never_reinterprets_a_direction` pins it.

**`extractor_clocks`** overclocks source nodes only. This is the usual play: a node set is fixed, so
running it faster is the only way to get more from it, whereas overclocking production machines mostly
burns power. On Spire Coast, `[1.0, 1.5, 2.0, 2.5]` takes 43,092 MW to **107,258 MW**.

> **Its trap: clock modes of one node set share physical machines.** Each mode is its own column, so
> capping them individually would let the solver mine every node once *per mode* — four nodes offered at
> two clocks would silently become eight. `Process.group` ties them together under one shared
> constraint. Modes above a building's `max_clock` are dropped rather than invented.

**`power` and `mw` are interchangeable** in objectives (`max_power` = `max_mw`, `min_mw` = `min_power`)
and in exports (`MW`, `mw`, `power`, `Power`). Both words turn up in the same conversation and neither is
more correct. Normalisation happens in `Scenario.__post_init__`, so exactly one spelling reaches the
dispatch — otherwise `max_power` would fall through to the unknown-objective branch. The same resolution
now applies to `export_minimums` keys, where a minimum written `"MW"` never matched the power
pseudo-item and was a floor the LP silently ignored.

**An export token that resolves to no item is refused by name.** It used to pass through as the raw
string, which entered the LP as an item id nothing produces and no balance row can satisfy — a bare
INFEASIBLE with nothing pointing at the typo. This is the same call `recipe_errors` makes for a ban that
matched nothing: a silently mangled export whitelist describes a different factory from the one asked
for, and §8.2 makes `exports` the most load-bearing argument in the model.

> **`exports` REPLACES the default `[MW]`; it does not extend it. Kept, and now documented.** The reason
> is mechanical rather than stylistic: `grid_import_mw` is derived from the export set, because a power
> plant that imports power to export it is unbounded — so exporting MW also forbids drawing from the
> existing grid. Auto-appending MW would therefore silently force *every* item plan to be self-powered,
> which is a different question from the one asked. It cost a real session four INFEASIBLE calls anyway,
> so the tool docstring now spells out all three shapes (`["MW"]`, `["Plastic", "Rubber"]`,
> `["MW", "Plastic", "Rubber"]`) and every refusal quotes `EXPORT_HELP`.

### 8.7 Degeneracy

`min_raw` LPs are **degenerate** — equally optimal vertices give materially different raw vectors (water
−57.78 vs −46.67 on the same objective). Either apply a documented lexicographic tie-break or label
reported raw vectors as one of several optima. Never present a degenerate component as *the* number.

`Scenario.raw_weights` is the mechanism: a per-resource weight in the `min_raw` objective, defaulting to
1.0 and applied to both the raw columns and the extractor columns. A weight of 0 only makes sense as the
first half of a lexicographic pair — minimise the priced resources, then pin them and minimise the free
one, or the free one comes back at its stand-in cap. `bom` uses it for water ([§10.1c](mcp-surface.md#101c-bom--the-flattened-bill));
`compare_recipe_options` predates it and pins its primary resource by cap instead.

---

## 9. Hard-drive advisor

The save persists the real offers, so the tool advises an actual decision.

`BP_ResearchManager_C.mUnclaimedHardDriveData` → `[{HardDriveID, PendingRewards[], PendingRewardsRerollsExecuted}]`,
quoted from `FGResearchManager.h`. Per `UFGResearchSettings`: 2 schematics per drive, 1 reroll each
(`config`-driven defaults, so a packaged `DefaultGame.ini` could override them — treat as semi-verified).

### 9.1 Ranking method

Centrepiece is **marginal value by counterfactual LP**: solve the objective with the candidate's
recipes enabled vs disabled, report the delta in real units (+MW, −crude m³/min, −machines, −power).
The counterfactual must add **all new recipes of the schematic** (two carry three).

Because a recipe can be worthless for power and excellent elsewhere, evaluate every candidate against a
small standard battery — max net MW from a resource basket; min raw for a fixed target part; min
machines; min power — and **never collapse them into one score without naming the tradeoff**.

Report alongside, explicitly labelled as heuristics not maths: byproducts created/removed, new building
types required (and whether they're unlocked *and built*), water/pipe burden, belt pressure, complexity.

### 9.2 Methodology requirements learned the hard way

- **Deltas ramp; they don't step.** A sweep reporting Turbo Blend Fuel at a flat +641 MW up to a
  "crossover" at 3,280 m³/min water was an artifact of a coarse grid. Real values: +641 at 3,000,
  **+513 at 3,200**, +64 at 3,270, +0.064 at 3,279.99. Sample finely near any trigger, or report the
  crossover as a curve.
- **The baseline must include everything the player has.** A demo concluded "neither turbofuel alternate
  adds a single MW" using a baseline with only the Fuel generator — while admitting raw coal as input and
  ignoring the **32 coal generators the user has already built in the north**. State the baseline
  generator and recipe set explicitly in every response.
- **Price cross-objective costs.** Coated Cable saves 86.67 copper ore/min but costs +50 crude/min, and at
  the model's own crude shadow price of 29.89 MW per m³/min that's 1,494 MW of forgone power. A tool that
  reports only the ore saving is misleading.
- **Charge only genuinely new infrastructure.** One run charged all 3,280 m³/min of water as newly
  extracted, including 400 m³/min from 4 already-built extractors.

### 9.3 Unverified mechanics — must be labelled in output

- **CONFIRMED (player, 2026-07-28).** Picking one option does **not** forfeit the other: the unchosen
  schematic **returns to the pool** and a later drive can offer it again. Only the drive is spent. This was
  the inference the headers supported -- the doc comment on `GetAvailableAlternateSchematics` excludes
  "unclaimed hard drive rewards" from candidates, so return-to-pool follows *if* `ClaimHardDrive` removes
  the entry -- and it is now demonstrated in game rather than merely plausible.
  **It inverts the advice.** The module had described this as "a one-off irreversible choice", which argues
  for agonising over each pick and hoarding drives against a better roll. The opposite is correct: take
  whatever helps the factory you have now, because the other alternate comes back around. Both hard-drive
  tools now say so on every response, since the rule appears nowhere in the game's own UI.
- **Reroll has a documented fallback**: *"If the required number isn't met, we fall back to
  excludedSchematics to fill up the list"* — so rerolling with a thin pool can re-serve excluded
  schematics rather than failing. Advice like "rerolling is pointless" is wrong.
- The rollable pool is **not static**, since unchosen options stop being reserved once a drive is claimed.

