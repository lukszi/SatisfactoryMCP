# Planner payback horizon: the contract

A per-plan trade between two real costs: the **one-off cost of extra machines** and the
**running cost of the power** they save. It replaces the five-step clock cap
(`power_priority`, 2026-09) and builds on P1 ([planner_slice_contract.md](planner_slice_contract.md))
without forking it: the settings are `PlanArgs` scalars, written through `PlanLog` and merged
by M1.

A separate switch, **overclock the last machine**, builds a row one machine short and lets the
last one carry the fraction. It is weighed against the same horizon.

---

## 1. The trade

Underclocking spreads a row over more machines. Power per machine goes as `clock ** e`
(`e` = 1.321929), so the same output on more, slower machines draws less. The machines are
paid once; the power is paid for as long as the factory runs. With a horizon of `H` hours of
play, each row minimises

```
cost(n) = K_b · n  +  H · r · power(n)          power(n) = n · P · (units / n) ** e
```

- `K_b`: the building's **build points** (§3.1).
- `r`: the **running price** of power, points per MWh (§3.2).
- `P`: one machine's draw at 100% for its recipe; `units`: the row in machines at 100%.

The best clock has a closed form that does not depend on the row's size:

```
c*_b = ( X / ((e − 1) · P) ) ** (1 / e),     X = K_b / (H · r)     clamped to [min_clock, 1]
```

**"Pays back within H"** means the last machine added repays its build points in exactly `H`
hours of saved power; the machines added before it repay sooner. The readout gives the
average too.

## 2. Semantics

- `payback_hours` is a float **0–100**. The page's stops are **0, 1, 2, 5, 10, 20, 50, 100 h**
  (`optimize.PAYBACK_STOPS`); a stored value between stops is kept as it is.
- **0 h is today's build, exactly.** No column is rewritten and phase 2 is unchanged, so the
  LP is byte-for-byte the one before this feature, and so are the counts and the `plan_id`.
- **Inside the LP (D5a).** At `H > 0` each recipe column at the 100% mode is offered at its
  building's `c*` instead (`optimize.best_clock`). There is still one column per recipe and
  its pid does not change, so solve time is unchanged. Phase 2 stops minimising the machine
  count and minimises `Σ (K_b + H · r · draw) · v` in points. Phase 1 (the goal) is
  untouched, so recipe choice moves only among routes that tie on the goal.
- **Required and banned always win.** `build_scenario` removes banned recipes and every rival
  of a required one before the LP exists. The horizon changes column clocks and costs, never
  the column set (`test_required_and_banned_hold_at_every_horizon`).
- **Readout.** A spread row is `ceil(v)` machines at a uniform clock, as before. Because the
  column already runs at `c*`, that count never draws more than the LP promised.
- **Never in the trade:** extractors (bound by their nodes, overclocked by `extractor_clocks`
  as before), generators (exponent 1), Somersloop rows, rows on a `clocks` mode other than
  100%, and rows under 0.01/min.
- `plan_id` hashes the horizon, the price and the build points only when `H > 0`, and the
  overclock switch only when on. Every existing plan keeps its id.

## 3. Prices

All derived per save in `planning/prices.py` and cached per projection
(`prices.prices_for`). Nothing here is stored in the plan log.

### 3.1 Build points (N2a, N3a)

```
available  = stock + 60 · max(0, nameplate production − nameplate consumption)   per item
tier       = clamp((available / (20 · q)) ** −½, ¼, 4), snapped to ¼, ½, 1, 2, 4
K_b        = Σ q · sink_points · tier   +   area_m2 · (1000/64 + 60/64)
```

- **Stock** is `inventory.stock()` (carried, storage, Depot). **Nameplate** rates come from
  every unpaused machine's recipe at its clock; measured uptime is not used, because a full
  output reads as idle.
- **Reference: 20 more machines** of that building. Nothing made and nothing stocked is ×4;
  well stocked is ×¼.
- **Hysteresis.** A line keeps its last tier while the raw value stays within a factor of 2 of
  it (√2 past the snapping boundary). The memory is per world, in process memory.
- **Floor:** the machine's own footprint (`Footprint.area_m2`) at 15.6 points/m², which is
  1,000 points per 8 × 8 m foundation, plus that foundation's 5 Concrete (0.94 points/m²).

Fixture save: Refinery 9,064, Manufacturer 13,040, Blender 146,162 (Heavy Modular Frame is
scarce), Constructor 1,433, Smelter 845.

### 3.2 Running price (P1b)

The **grid mix**: the MW-weighted running price across the save's generation, the same
generators `power_report` counts (wired, unpaused).

| Source | Points / MWh |
|---|---|
| Geothermal | 0 |
| Coal | 36 |
| Uranium Fuel Rod | 209 |
| Fuel | 360 |
| Turbofuel | 405 |
| Solid Biofuel | 384 |

- Each fuel is priced at its **own sink points** per MWh burnt: `fuel/min · 60 · sink / MW`.
  Fluids carry sink points in the game data although the game will not sink them. Water is
  free. **No scarcity tier** applies to fuel: a working fuel chain burns what it makes, and
  the tier would read ×4 on every grid.
- **Biomass burners** count only when the shared `biomass` setting is on, as in headroom.
- Geothermal counts at 0, so a geothermal-heavy grid makes power look cheap. That is chosen.
- Fixture save: Fuel 5,000 MW, Coal 2,550 MW, mix **251 pts/MWh**.
- `power_price` on the plan overrides the mix (0–100,000 points per MWh).

## 4. The stops

One solve is read out at every stop with **its recipes fixed** (`Solution.payback_curve`), so
the page can preview a stop without solving. A last entry flagged `plain` is 0 h with no
overclock; every stop is compared with it. Moving the slider re-solves, and the recipes may
then change (§2), so the solved figures can differ from the preview.

## 5. Overclock the last machine (D4, P3a)

A row of `units` with a fraction `f > 0` becomes `floor(units)` machines: all at 100% but the
last, which runs at `(1 + f)`. 4.2 Refineries are 3 at 100% and 1 at 120%. Because `f < 1`,
the last machine never exceeds 199.9% and never needs more than **2 Power Shards**.

- **Eligible rows:** the spreadable rows of §2 whose building can overclock that far, with at
  least one whole machine.
- **Weighed, not forced.** Its cost `K_b · n + H · r · power` is compared with the row's
  spread at the same horizon, and it is used only where it is cheaper. At 0 h it always wins
  (power is free there); past a row's break-even it does nothing and the notes say so.
- **Shards in hand.** Candidates are taken greedily by points saved per shard while the
  save's **free** shards last (`shard_budget()["free"]`); the rest keep the spread and are
  listed as `without`. Craftable shards from slugs are reported beside it, never spent.
- The LP never sees it. The row carries `last_clock`; `clock` stays the average
  (`machines × clock` is still the throughput), the exact MW includes the overclock, and the
  shard bill charges only the last machine.
- The pick is made whether or not the switch is on, so the page can say what it would save.

## 6. The plan state and the shared defaults

| Field | Type | Default | Kind | Maps to kwarg |
|---|---|---|---|---|
| `payback_hours` | float 0–100 \| null | null | scalar | same |
| `overclock_last` | bool \| null | null | scalar | same |
| `power_price` | float 0–100,000 \| null | null | scalar | same |

- **null follows the shared setting** (`payback_hours`, `overclock_last` in
  [shared-settings.md](shared-settings.md)) or, for the price, the save's grid mix. The
  shared defaults are 0 h and off, so nothing changes until a setting is moved. A plan that
  inherits changes `plan_id` when the default changes, because its numbers change.
- Writers may send `"default"` for null. Anything else out of range, a boolean for a number,
  NaN or text is `InvalidOp`.
- M1 as for every scalar: the same value from both sides is `same`, a different one a
  conflict on its own key, and it merges with any other field. Undo is the inverse `set`.
- Words: `payback 10 h`, `payback: shared default`, `overclock last machine: on`,
  `power price 360 pts/MWh`, `power price: grid mix`.
- `recall.PLAN_DEFAULTS` holds null for all three, so 0 and false are overrides a chat call
  can make, and `"default"` puts a recalled plan back on the shared value.

## 7. Showing the trade-off

`SolveResponse.power` (`payback.view`):

```
PaybackView { hours, inherited, default_hours, price, price_source, mix: PowerSource[],
              splits, reason, stops: PaybackStop[], overclock: OverclockView }
PaybackStop { hours, machines, mw_draw,              # the whole plan, as the headline counts
              extra_machines, saved_mw,             # against the plain build
              cost: [{item, amount}], area_m2, points, average_payback_h, shards }
OverclockView { on, inherited, rows: [{label, building, machines, instead, last_clock,
                shards, extra_mw}], shards, machines_saved, extra_mw, without, unused,
                shards_free, shards_craftable }
```

`splits` is false when no stop changes a row, and `reason` says why (nothing spreadable, or
power runs free on this grid). `stops` is empty when the plan does not solve.

- **Page.** The workbench's **power** row: a hint naming the price, the dashkit slider in
  hours (ends "fewer machines" and "less power"), and one live line,
  `pays back within 10 h · +11 machines · −54 MW · on average 7.3 h`. Dragging reads the
  stops; releasing commits one `set payback_hours` op. Below it, the overclock checkbox with
  its line: `−3 machines · +26 MW · 3 shards (19 in hand)`, or `would save: …` when off.
  "shared default" marks an inherited value; **use default** clears an override. The build
  list shows an overclocked row as `100%, last 150%`.
- **Chat.** `plan_factory` and `plan_layout` take `payback_hours`, `overclock_last` and
  `power_price`. The notes carry `payback 10 h at grid mix 251 pts/MWh: Fuel 5,000 MW, Coal
  2,550 MW: +11 machines, −54.1 MW against the plain build; the last pays back within 10 h, on average
  7.3 h; 20 h would change +14 machines, −33.7 MW`, and an overclock line
  (`overclock last machine: 3 row(s), 3 shard(s) (19 in hand, 411 more from slugs): −3
  machines, +25.9 MW`, or what `overclock_last=true` would save).
- **Sync.** Plan edits ride the plans SSE feed as before. A change to a shared default
  arrives as the `settings` event; the page drops its solve cache and re-solves the open
  plan.

## 8. Migration (D6a)

`power_priority` was a stored 0–4 step. It is read as the horizon where a Refinery's best
clock equals the old cap at Fuel's 360 pts/MWh: `H = K / (r · (e − 1) · P · c ** e)`.

| Old step | Cap | `payback_hours` |
|---|---|---|
| 0 | 100% | null (inherit) |
| 1 | 75% | 4 |
| 2 | 50% | 7 |
| 3 | 33% | 11 |
| 4 | 25% | 17 |

- `PlanArgs.from_dict` maps a stored `power_priority` (snapshots, create ops).
- `Commit.from_dict` maps an old `set power_priority` op, `was` included, so replay, undo,
  merge and history read it as `set payback_hours`. The log file keeps the old op unchanged.

## 9. Worked example

Fixture save, grid mix 251 pts/MWh, closed-form readout. Cells are machines / MW (best clock).

| Row | 0 h | 5 h | 20 h | 100 h | Overclock last at 0 h |
|---|---|---|---|---|---|
| Plastic plan: 10.0 + 1.667 Refineries | 12 / 347.1 | 16 / 317.1 (80%) | 42 / 231.7 (28%) | 140 / 157.3 (8%) | Residual Fuel 1 at 167%, 2 shards, 58.9 MW |
| Smelter 5.5 | 6 / 21.4 | 9 / 18.8 (61%) | 26 / 13.3 (21%) | 87 / 9.0 (6%) | 5, last 150%, 1 shard, 22.8 MW |
| Blender 7.5 | 8 / 550.9 | 8 / 550.9 | 8 / 550.9 | 22 / 397.8 (34%) | 7, last 150%, 1 shard, 578.2 MW |

The Blender's Heavy Modular Frames are scarce in this save, so it holds until 100 h.

## 10. Choices made here (open for review)

| # | Choice | Alternative |
|---|---|---|
| C1 | Phase 1 keeps `machine_cost_mw`; only phase 2 and the column clocks see the horizon | Replace the flat MW price in `max_mw`/`min_power` phase 1 with `K_b / (H · r)` |
| C2 | The tier memory lives per process, so the web server and the MCP server can disagree on a tier at a boundary after a save moved | Store the last tiers beside the plan log |
| C3 | The overclock candidate spends only free shards in hand | Count craftable slugs too |
| C4 | Stops compare with the plain build (0 h, no overclock) | Compare with 0 h under the plan's own overclock setting |
