# Planner power priority: the contract

A per-plan setting for how hard the planner trades machines for power. It builds on P1
([planner_slice_contract.md](planner_slice_contract.md)) and P3/P4 and forks none of them:
the setting is one more `PlanArgs` scalar, written through `PlanLog` and merged by M1.

One end builds **fewer machines at full clock**, which saves build materials and floor space.
The other builds **more machines, underclocked**, which saves power. The right point depends on
where the plan stands (a cramped cliff or a grid near its limit), so it lives on the plan, not
in a global setting.

---

## 1. What the solver does today

The LP solves throughput in machine-equivalents `v` per process. The readout
(`optimize.solve`, `emit`) then places **`ceil(v)` whole machines, all at the same clock
`v / ceil(v)`**. It never runs `floor(v)` machines at 100% with the last one underclocked: a
uniform clock is exact, always a clean ratio, and draws the least power for that throughput
because `clock ** e` is convex. Extractors are pooled per node group and placed the same way
under the node cap. Row power is exact: `machines × P_full × clock ** e`, with `e` read per
building from `mPowerConsumptionExponent` (1.321929 for extractors and manufacturers,
generators linear).

So step 0 of this setting is exactly today's build, and every existing plan keeps its numbers.

## 2. Semantics: five named steps

`power_priority` is a whole number **0–4**. Each step is a cap on the clock of every
production machine it may split:

| Step | Max clock | Machines (about) | Power per unit of output | Saved | Marginal MW per extra machine |
|---|---|---|---|---|---|
| 0 | 100% | ×1 | 1.000 | – | 0.32 P |
| 1 | 75% | ×1.33 | 0.912 | 8.8% | 0.22 P |
| 2 | 50% | ×2 | 0.800 | 20.0% | 0.13 P |
| 3 | 33% | ×3 | 0.702 | 29.8% | 0.075 P |
| 4 | 25% | ×4 | 0.640 | 36.0% | 0.052 P |

`P` is one machine's draw at 100%. From `P = P_base × c ** e`, `n` machines at `1/n` clock draw
`n ** (1 − e) = n ** −0.32` of one machine's power for the same output. The marginal column is
`d(power)/d(machines) = (e − 1) × P × c ** e`.

**Why steps and not a 0..1 weight.** The only thing a player sets in game is a clock, and
machine counts are whole. A weight would need a hidden mapping to a clock anyway, and two
weights 0.01 apart would usually build the same factory. Named steps are sayable in chat
("step 2: no machine above 50%"), reproducible, and merge as a plain scalar.

**Why the steps stop at 25%.** The saving flattens fast: half of the 36% at step 4 is already
there at step 2, for a quarter of the extra machines. Past 25%, even a Blender (75 MW, the
hungriest common producer) saves under 5 MW for each extra Blender: going from 25% to 20%
buys 75 × (0.640 − 0.596) = 3.3 MW each. 5 MW per machine is the price
`Scenario.machine_cost_mw` already puts on a machine when the LP trades machines for power, so
beyond 25% the planner would contradict its own price. The game's floor of 1% is never near.

## 3. How rows are split

For each build row the readout knows `units = clock_mode × v`, the machine-equivalents at 100%,
and `baseline = ceil(v)`, today's count. At step `s` with cap `c = POWER_PRIORITY_CLOCKS[s]`:

```
machines = max(baseline, min(ceil(units / c), floor(units / min_clock)))
clock    = units / machines                       # uniform, as today
```

- **Never fewer machines than step 0**, so a row the player already asked to underclock via
  `clocks` modes is never merged back.
- **A row already under the cap is not split**: 0.3 machine-equivalents is one machine at
  30% at every step.
- **Never below the building's minimum clock** (`min_clock`, 1%).

Rows that are **never split**, at any step:

| Row | Why |
|---|---|
| Extractors (miners, pumps, oil, wells) | Bound by nodes: one node's output cannot be spread over two machines, and taking more nodes is a different plan. For a Resource Well the power sits on the Pressurizer, which serves all its satellites. |
| Generators | Exponent 1.0: fuel and output are both linear in clock, so splitting saves nothing. |
| Rows with Somersloops | Each extra machine would need its own sloops. |
| Overclocked rows (a `clocks` mode above 100%) | The player asked for that clock, and the shards it costs. |
| Rows under 0.01/min (left out of the build table) | Not a build instruction. |

The LP is untouched: every flow, export and input is identical at every step, and only
machine counts, clocks and exact MW move. The LP's linear power figure is a conservative bound
at every step, so a plan that fits the grid at step 0 fits at every step.

`plan_id` hashes `power_priority` only when it is not 0, so every existing plan keeps its id.

## 4. The plan state

| Field | Type | Default | Kind | Maps to kwarg |
|---|---|---|---|---|
| `power_priority` | int 0–4 | 0 | scalar | same |

- Written with `{"op": "set", "field": "power_priority", "value": 2}`. Anything but a whole
  number 0–4 is refused as `InvalidOp` (a boolean, a float, text or null included).
- M1 as for every scalar: the same value from both sides is `same`, a different value is a
  conflict on key `power_priority`, and it merges with edits to any other field. Undo is the
  inverse `set`.
- Words (`describe_op`): `power priority 2: machines at most 50%`, and for 0
  `power priority off: machines at full clock`.
- An older snapshot or log without the field reads 0.
- `recall.PLAN_DEFAULTS["power_priority"]` is `None`, not 0, so a chat call can reset a
  stored step with `power_priority=0`; the usual limitation (a recall cannot reset a
  parameter to its default) does not apply to this one.

## 5. Showing the trade-off

One solve is read out at all five steps (`Solution.power_steps`), so the page and chat can
show every step without solving again. `power_priority.ladder` turns it into:

```
PowerPriority { step: int, splits: bool, steps: PowerStep[] }
PowerStep {
  step, max_clock,
  machines, mw_draw,           # the whole plan at this step, as the headline counts it
  extra_machines, saved_mw,    # against step 0
  cost: [{item, amount}],      # build cost of the extra machines
  foundations                  # 8 m foundations the extra machines stand on
}
```

`splits` is false when no row changes at any step (a power plant, an extraction-only plan);
`steps` is empty when the plan does not solve. `SolveResponse.power` carries it.

- **Page.** A slider in the workbench's **power** row, built on the dashkit `slider`
  primitive, with one line of explanation. While dragging, the line under it reads the ladder
  (`50%: 24 machines (+12) · 278 MW draw (−69 MW) · +120 Motor, +120 Encased Industrial
  Beam, … · +84 foundations`). Releasing commits one `set` op, one gesture, and the result
  re-solves as for any edit. With `splits` false the slider is disabled and says why.
- **Chat.** `plan_factory` takes `power_priority` (0–4); `plan_layout` takes it too, since
  the layout counts machines. The notes carry one line: what the current step saves against
  step 0, and what the next step would add. Every other planning tool reads the stored step
  through recall.
- **Sync.** The field rides the existing plan events: a chat save is a new version, the page
  follows it on the plans SSE feed, and the slider moves; a page gesture is a new version
  that `ui_context` and the plan log report to chat.

## 6. Worked example

Plastic from 300 m³/min crude with Residual Fuel (the `test_power_priority` fixture): the
same 200 Plastic/min at every step.

| Step | Refineries | Draw | Saved | Extra |
|---|---|---|---|---|
| 0 | 12 | 347.1 MW | – | – |
| 1 | 17 | 310.6 MW | 36.6 MW | 5 |
| 2 | 24 | 277.7 MW | 69.4 MW | 12 |
| 3 | 35 | 245.7 MW | 101.4 MW | 23 |
| 4 | 47 | 223.5 MW | 123.6 MW | 35 |

Step 2 saves 5.8 MW per extra Refinery; step 4 averages 3.5 MW.

## 7. Choices made here (open for review)

| # | Choice | Alternative |
|---|---|---|
| C1 | Five fixed steps, 100% to 25% | A continuous slider, or a floor below 25% |
| C2 | Extractors never split, even where free nodes of the same purity are in scope | Spread extraction over unused nodes in the selection |
| C3 | The split is a readout, not an LP objective | Let the LP pick per-row clocks with a machine price (the `clocks` + `machine_cost_mw` route, which stays available) |
| C4 | The page shows build cost and foundations; belts, pipes and power poles for the extra machines are not counted | Price logistics per extra machine |
| C5 | Default 0 for every plan, new and old | A world-level default step |
