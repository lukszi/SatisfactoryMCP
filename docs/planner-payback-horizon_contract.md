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
  count and minimises `Σ (K_b + H · r · draw) · v` in points. For `max_item`, `min_raw` and
  `min_machines`, phase 1 (the goal) is untouched, so recipe choice moves only among routes
  that tie on the goal.
- **Power goals (F1a, 3b).** `max_mw` and `min_power` already trade machines against MW in
  phase 1. From **5 h** (`optimize.POWER_GOAL_BUILD_COST_FROM_H`) each column's machine price
  there is its building's `K_b / (H · r)` MW (`optimize.machine_mw`) instead of the flat
  `machine_cost_mw`. Below 5 h, at 0 h, or with no running price, the flat 5 MW stays; the
  horizon still sets column clocks and phase 2. At 0 h the LP is the one from before F1a.
  Why a threshold (ruling 3b): a short horizon on a power plan means a temporary build, and a
  dismantle refunds its materials in full, so its build cost is not a cost yet. Why 5 h:
  measured on the fixture's Spire Coast plan (`max_mw`, Plastic 600 and Rubber 250, grid mix
  250.6 pts/MWh), the build-point price lost MW against the flat price at every horizon up to
  4.5 h (−495 MW at 1 h, −89 MW at 4 h, −19 MW at 4.5 h) and gained from 5 h on (+42 MW at 5 h,
  +409 MW at 10 h). From 5 h it also builds for fewer points, so it is a win on both
  counts. 5 h is the first stop past the crossover. Short horizons price machines high:
  the 32 Blenders cost 146,162 points each, because Heavy Modular Frames are scarce, which is
  ~580 MW a machine at 1 h.

  | Horizon | Before 3b: MW / machines | After 3b: MW / machines |
  |---|---|---|
  | 0 h | 28,252.5 / 286 | 28,252.5 / 286 (unchanged) |
  | 1 h | 27,757.1 / 396 | 28,252.5 / 286 |
  | 2 h | 27,819.1 / 399 | 28,252.5 / 286 |
  | 5 h | 28,447.7 / 544 | 28,447.7 / 544 |
  | 10 h | 29,139.7 / 796 | 29,139.7 / 796 |
  | 20 h | 29,724.1 / 1,219 | 29,724.1 / 1,219 |
  | 100 h | 30,754.7 / 3,682 | 30,754.7 / 3,682 |

  Machines are the built rows. Before 3b the plan dropped the Blenders on Diluted Fuel for
  Diluted Packaged Fuel in Refineries plus Packagers from 1 h on; now it keeps them below 5 h
  and switches at 5 h, where the switch also gives more MW.
  Normal plans are unchanged: they keep their build cost at every horizon (§10).
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

All derived per save in `planning/solver/prices.py` and cached per projection
(`prices.prices_for`). Nothing here is stored in the plan log; only the last scarcity tiers
are stored, beside it (§3.1).

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
  it (√2 past the snapping boundary). The memory is per world, stored beside the plan log in
  `<plans>/<world>/tiers.json` (`{"schema": 1, "tiers": {"<building>|<item>": tier}}`) and
  read and written under its file lock (`prices.tiers_path`, F2a). The web server and the MCP
  server therefore put a material in the same tier. The file is written only when a tier
  moves. An unreadable file starts afresh, and a file from a newer version is used as empty
  and left untouched.
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
overclock at all: neither the plan's switch nor any row's own choice (§5.1). **Every stop is
compared with that plain build (F4a)**, never with 0 h under the plan's own overclock, so
"+11 machines" always means "against building it plainly". With overclock on, the 0 h stop
can read −1 machine for that reason.

Moving the slider re-solves, and the recipes may then change (§2), so the solved figures can
differ from the preview. **When the re-solve after a release switches recipes (F5a)**, the
power row shows one line naming the switch, `switched to Alternate: Diluted Packaged Fuel,
Packaged Water, Unpackage Fuel, from Alternate: Diluted Fuel`, or `dropped …` when a recipe
only goes. The page compares the build list's recipes before the release with the first
result after it. The line stays until the next result and appears only after a slider
release; a chat edit or another control does not produce it.

**The horizon stays capped at 100 h (F6a).** Beyond that the 1% clock floor starts to bind,
and the builds stop being ones a player would make. Writers past 100 are `InvalidOp`.

## 5. Overclock the last machine (D4, P3a)

A row of `units` with a fraction `f > 0` becomes `floor(units)` machines: all at 100% but the
last, which runs at `(1 + f)`. 4.2 Refineries are 3 at 100% and 1 at 120%. Because `f < 1`,
the last machine never exceeds 199.9% and never needs more than **2 Power Shards**.

- **Eligible rows:** the spreadable rows of §2 whose building can overclock that far, with at
  least one whole machine.
- **Weighed, not forced.** Its cost `K_b · n + H · r · power` is compared with the row's
  spread at the same horizon, and it is used only where it is cheaper. At 0 h it always wins
  (power is free there); past a row's break-even it does nothing and the notes say so.
- **Shards in hand plus craftable (F3b).** Candidates are taken greedily by points saved per
  shard while the save's shards last: the **free** shards in hand plus those **craftable**
  from slugs in hand (`scenario.shard_stock`). The rest keep the spread and are listed as
  `without`. Words: `3 shards (19 in hand + 411 craftable)`.
- **What "craftable" assumes.** Each Blue, Yellow and Purple Power Slug carried, in storage
  containers or in the Dimensional Depot counts at its single-ingredient shard recipe's
  yield (1, 2 and 5 shards), but **only when that recipe is unlocked in this save** (MAM
  Power Slugs research). Crafting is manual and takes time; the plan assumes it is done
  before building. Slugs lying in crates on the ground do not count, and Synthetic Power
  Shard, a production chain, is never counted. `power_shards` reports craftable without the
  unlock check, so the two figures can differ on a save that has not researched every
  slug recipe.
- The LP never sees it. The row carries `last_clock`; `clock` stays the average
  (`machines × clock` is still the throughput), the exact MW includes the overclock, and the
  shard bill charges only the last machine.
- The pick is made whether or not the switch is on, so the page can say what it would save.

### 5.1 A row's own choice (F3 extra)

Each eligible row can override the plan's switch: **overclock last** (`"last"`) or **one more
underclocked machine** (`"spread"`). Absent, the row follows `overclock_last`.

- **Stored** as `row_overclock`, a map field keyed by **recipe class id**: `put row_overclock
  [Recipe_ResidualFuel_C] "last"`, and `del` to follow the plan again. Writers resolve names
  to ids, as for `required`. Any other value is `InvalidOp`.
- **Merge (M1).** Each row is its own key, `row_overclock[<id>]`: the same value from both
  sides is `same`, a different one is a conflict on that row only, and it merges with every
  other field, the plan switch included. Undo is the inverse `put`/`del`.
- **Words** (`describe_op`, with recipe names): `Residual Fuel: overclock last`, `Residual
  Fuel: one more underclocked machine`, `Residual Fuel: follows the plan's overclock
  setting`. A conflict reads `overclock on Residual Fuel: you set …`.
- **Solve.** `"last"` builds the overclocked candidate whatever it costs at the horizon, and
  takes shards before any other row. It still needs them: with too few, it keeps the spread
  and is listed as `without`. `"spread"` is never a candidate. The plain build (§4) ignores
  both. `plan_id` hashes the map when it is not empty.
- **Readout.** Every row that could carry an overclocked last machine has
  `SolveRow.overclock_option` (§7), whether or not it is built that way.
- **Page.** The build list's clock cell has a small select on those rows: `plan: overclock
  last` or `plan: one more machine` (what the plan's switch gives that row), `overclock
  last`, `one more underclocked`. Its tooltip gives both builds, e.g. `overclock last: 11
  machines, the last at 166.7%, 2 shards (+12 MW) · one more underclocked: 12 at 97.2%`.
  Picking writes one op; the page and chat stay in sync over the plans SSE feed like any
  other edit.
- **Chat.** `plan_factory` and `plan_layout` take `row_overclock={"Residual Fuel": "last",
  "Plastic": "default"}`. A call names only the rows it changes (`recall.merge_rows`), and
  `"default"` drops a row's choice. An unknown recipe is refused before anything is solved.

## 6. The plan state and the shared defaults

| Field | Type | Default | Kind | Maps to kwarg |
|---|---|---|---|---|
| `payback_hours` | float 0–100 \| null | null | scalar | same |
| `overclock_last` | bool \| null | null | scalar | same |
| `power_price` | float 0–100,000 \| null | null | scalar | same |
| `row_overclock` | dict[recipe id, `"last"` \| `"spread"`] | {} | map | same (§5.1) |

- **null follows the shared setting** (`payback_hours`, `overclock_last` in
  [shared-settings.md](shared-settings.md)) or, for the price, the save's grid mix. The
  shared defaults are 0 h and off, so nothing changes until a setting is moved. A plan that
  inherits changes `plan_id` when the default changes, because its numbers change.
- Writers may send `"default"` for null. Anything else out of range, a boolean for a number,
  NaN or text is `InvalidOp`.
- M1 as for every scalar: the same value from both sides is `same`, a different one a
  conflict on its own key, and it merges with any other field. Undo is the inverse `set`.
  `row_overclock` merges per row (§5.1).
- Words: `payback 10 h`, `payback: shared default`, `overclock last machine: on`,
  `power price 360 pts/MWh`, `power price: grid mix`.
- `recall.PLAN_DEFAULTS` holds null for all four, so 0 and false are overrides a chat call
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
                shards, extra_mw, pinned, applied}], shards, machines_saved, extra_mw,
                without, unused, pinned_last, pinned_spread, shards_free, shards_craftable }
SolveRow.overclock_option { pinned, applied, machines, last_clock, shards, extra_mw,
                spread_machines, spread_clock, without, unused } | null
```

`splits` is false when no stop changes a row, and `reason` says why (nothing spreadable, or
power runs free on this grid). `stops` is empty when the plan does not solve. The
`OverclockView` totals are what the plan's switch builds when on; each row's `applied` says
whether it is built that way now, and `pinned` is the row's own choice or null.

- **Page.** The workbench's **power** row: a hint naming the price, the dashkit slider in
  hours (ends "fewer machines" and "less power"), and one live line,
  `pays back within 10 h · +11 machines · −54 MW · on average 7.3 h`. Dragging reads the
  stops; releasing commits one `set payback_hours` op, and a recipe switch it causes gets
  its own line (§4). Below it, the overclock checkbox with its line: `−3 machines · +26 MW ·
  3 shards (19 in hand + 411 craftable)`, or `would save: …` when off. With rows set on
  their own, the line leads with them: `1 row(s) overclocked by their own setting: −1 machine
  · +12 MW · 2 shards (10 in hand + 427 craftable) · the switch would save −1 machine · +10
  MW · 1 shard`. "shared default" marks an inherited value; **use default** clears an
  override. The build list shows an overclocked row as `100%, last 150%`, with the row's
  own choice beside it (§5.1).
- **Chat.** `plan_factory` and `plan_layout` take `payback_hours`, `overclock_last`,
  `power_price` and `row_overclock`. The notes carry `payback 10 h at grid mix 251 pts/MWh:
  Fuel 5,000 MW, Coal 2,550 MW: +11 machines, −54.1 MW against the plain build; the last pays
  back within 10 h, on average 7.3 h; 20 h would change +14 machines, −33.7 MW`, and an
  overclock line (`overclock last machine: 3 row(s), 3 shard(s) (19 in hand + 411
  craftable): −3 machines, +25.9 MW`, or what `overclock_last=true` would save). Rows set on
  their own read `overclock last set on its row: Residual Fuel: 2 shard(s) (…), −1
  machines, +11.8 MW` and `one more underclocked machine set on 1 row(s), whatever
  overclock_last says`.
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

## 10. Follow-up decisions (answered 2026-10-05)

Every question this section once held is answered. Each answer is built and tested
(`tests/test_payback_followups.py`).

| # | Question | Answer | Where |
|---|---|---|---|
| F1a | Should `max_mw`/`min_power` phase 1 keep the flat `machine_cost_mw`? | **No.** From 5 h each machine costs `K_b / (H · r)` MW there; below it the flat 5 MW stays, so today's results hold | §2 |
| 3b | Should build cost count for power plans at short horizons? | **No.** Only from 5 h: a short horizon means a temporary build, and a dismantle refunds its materials. Threshold measured in §2 | §2 |
| 3b extra | Does the refund argument apply to normal plans' build cost? | **No.** A short horizon on a power plan implies a temporary setup that is dismantled, so its materials come back; a normal plan is a factory that stays, and its materials stay tied up. Normal plans keep their build cost at every horizon | §2 |
| F2a | Where does the scarcity-tier memory live? | **Beside the plan log**, `<plans>/<world>/tiers.json`, under a file lock, so page and chat agree | §3.1 |
| F3b | Which shards may overclock-last spend? | **In hand plus craftable** from slugs in hand whose shard recipe is unlocked; shown as `N shards (H in hand + C craftable)` | §5 |
| F3 extra | Can one row differ from the plan's switch? | **Yes**: `row_overclock`, "overclock last" or "one more underclocked machine" per row, through the op log, from the page and from chat | §5.1 |
| F4a | What do the stops compare with? | **Always the plain build**: 0 h with no overclock, not even a row's own | §4 |
| F5a | What does a re-solve that switches recipes show? | **One line** naming the switch after a slider release | §4 |
| F6a | Should the horizon go past 100 h? | **No**, it stays capped at 100 h | §4 |
