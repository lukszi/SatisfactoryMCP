# §24 Plumbing: what the game states, what the save carries, what is not there

The model being implemented is the FICSIT Inc. Plumbing Manual's (public domain,
satisfactory.wiki.gg). This file records which of its numbers the game's own data confirms,
which the save measures directly, and the one that is not in either — so the head-lift work
can cite rather than assume.

Measured 2026-08-06 against Docs.json `buildVersion` 495413 and the reference world
(§App. A), save version 60.

## §24.1 Every constant, against the dump

Verified means *read out of `CommunityResources/Docs/en-US.json`*, and every verified value
below is already normalized into `GameData` — no new reader was needed for any of them.

| what | manual says | dump says | field |
|---|---|---|---|
| Pipeline Pump Mk.1 head lift | 20 m, failing at 22 | 20.0, 22.0 | `mDesignPressure`, `mMaxPressure` on `FGBuildablePipelinePump` → `Building.head_lift_m`, `max_head_lift_m` |
| Pipeline Pump Mk.2 head lift | 50 m, failing at 55 | 50.0, 55.0 | same |
| Valve head lift | none | 0.0, 0.0 | same; a Valve is the same native class and lifts nothing |
| Pipeline Mk.1 flow | 300 m³/min | 300.0 | `mFlowLimit` 5.0 (per second) × 60 → `Building.flow_m3_min` |
| Pipeline Mk.2 flow | 600 m³/min | 600.0 | `mFlowLimit` 10.0 × 60 |
| Fluid Buffer capacity | — | 400.0 | `mStorageCapacity` on `FGBuildablePipeReservoir` → `Building.storage_capacity_m3` |
| Industrial Fluid Buffer capacity | — | 2400.0 | same |
| Fluid Buffer head lift when full | 8 m | 8 m | clearance box height, `mClearanceData` → `Footprint.height_m` (6 × 6 × 8 m) |
| Industrial Buffer head lift when full | 12 m | 12 m | same (14 × 14 × 12 m) |
| Junction / pump / valve internal volume | "a few m³" | 5.0 per connection | `mFluidBoxVolume` |

Two corrections fall out of the table. **The pump tolerance is 10%, not the ~12% the manual
rounds to**: 22/20 and 55/50 are both exactly 1.10, on the game's own numbers, so the failure
point is `max_head_lift_m` and never a percentage applied to the rating. And **the buffer's
head lift when full is its building height** — the manual's 8 m and 12 m are reproduced
exactly by the clearance box, which is what lets `BUFFER_BALANCE_HEAD_M` become a level in
m³ instead of a second pair of hard-coded numbers.

## §24.2 The machine rating is in the prose, and the ceiling is measured

**Superseded in both halves; `docs/fluids_model.md` is the authority and this section records
what changed.** The original claim was that a normal machine's 10 m of head lift is absent
from the dump and so is its 12 m ceiling. Half of that was a search that stopped too early
and half of it was wrong.

**The 10 m rating IS in game data — as prose.** It is true that head lift outside a pump
lives in the `FluidBox` struct and that Docs.json exports every `mFluidBox` as the empty
tuple `()`, and true that `mDesignPressure`/`mMaxPressure` appear on `FGBuildablePipelinePump`
and nowhere else in 2,868 classes. But six classes state the number in `mDescription`:
`Build_WaterPump_C`, `Build_OilPump_C`, `Build_OilRefinery_C`, `Build_Packager_C`,
`Build_Blender_C` and `Build_FrackingExtractor_C` each say `Head Lift: 10 m`. `normalize.py`
already parsed that field for belt speeds and extraction rates, and now parses this one too,
into `Building.machine_head_lift_m`. The two pump classes state theirs in prose as well, at
20 m and 50 m, which makes the parse self-checking against `mDesignPressure`.

The separator is **U+202F, a narrow no-break space** — a literal `" m"` matches nothing.

**The 12 m ceiling was not merely unreadable, it was wrong.** Measured at 11.020 m ±0.26 on a
Water Extractor; see `docs/fluids_model.md`. `MACHINE_MAX_HEAD_LIFT_M` now carries the
measurement.

So `MACHINE_HEAD_LIFT_M` survives only as the fallback for a class that states nothing, and a
crest declares which of the two it rests on: `Crest.assumed` is now true only where the
pinned figure was used, which on real data is nowhere.

Gas is the other absence, and it is a rule rather than a number: gas has no head lift at all,
pumps do not work on it and buffers cannot compensate its flow. A gas network must be
excluded from the model entirely, not modelled with a zero.

## §24.3 What the save measures

- **Pipe fill is readable, and this is the useful finding.** Every `Build_Pipeline*` actor
  carries `mFluidBox`, a float of cubic metres — not only the junctions, pumps and valves
  (`core/saveio/extract/`). Capacity is proportional to length: across the reference
  world's 503 pipes, binned by spline length in 5 m steps, the fullest pipe in every bin from
  15 m to 55 m reads **1.858 m³/m** to four significant figures. So "how full is this pipe"
  is a measurement, and the manual's central rule — a pipe only flows at its rated rate when
  it is full — stops being an inference. The projection does not carry it yet.
- **Buffer fill is carried**, as `storage[].stored_m3` in cubic metres, with `cls`
  distinguishing the two sizes. It is NOT the litre-scaled figure an inventory uses.
- **A pump's power is answerable through its wire and nothing else.** `mHasPower` and
  `mCircuitID` are on no object in the file. A powered pump owns a `PowerInput` component
  listing one wire; an unpowered one owns only its `powerInfo`. On the reference world 15 of
  22 pumps have that wire and 7 do not, which `graph["power"]` reproduces exactly.
- **A valve's user-set flow limit is NOT PROVEN either way.** `mUserFlowLimit` is written
  zero times across 18 saves holding 28 valves and 1,017 pumps — but its class default is
  −1.0 (unlimited), and UE omits a property equal to its default, so this is equally
  consistent with "no valve in any of those saves was ever adjusted". `mDefaultFlowLimit`
  *is* written, 559 times, at 5.0 against a class default of 10.0, which proves the flow
  fields on this class do round-trip. The decisive test costs one minute: set a valve's limit
  in game, save, and look again.

## §24.4 What is shipped

`domain/world/plumbing.py`, surfaced in the `factory_health` sweep over every factory —
world-wide there and scoped to no factory, because a buffer and a pump belong to no machine
set. Both are §24.1 arithmetic and neither walks the pipe graph.

- **Throttled buffers.** `capacity × 1.5 m ÷ height` is 75 m³ in a Fluid Buffer and 300 m³ in
  an Industrial one; below it the buffer outputs slower than it takes in and says nothing.
  Three of the reference world's five buffers are under it — two Industrial tanks at 54 and
  74 m³ of Fuel, and a Fluid Buffer at 42 m³.
- **Dark pumps.** Seven of the reference world's 22 pipeline pumps have no wire, all Mk.2.
  An unpowered pump passes fluid while setting the head lift past it to zero, so nothing
  downstream looks broken. The count is guarded against the graph's own blind spot: its actor
  list is cut from edges, so `building_counts` settles how many pumps were not looked at.

## §24.5 The diagnostic ladder

The manual gives a troubleshooting ORDER and puts a red box round it: check **(1) connection**,
then **(2) head lift**, and attempt **(3) flow rate** only once sure it is neither. A starved
refinery answered with its supply rates when its water cannot climb to it is the mistake the
box is about, and the three models that answer those rungs now exist separately — so
`factory_health` walks them in that order and **stops at the first rung that fires**.

The rung is per missing INGREDIENT, and only a fluid ever carries one: head lift is not a
thing that happens to Iron Ore, so a solid reads exactly as it did before and a machine short
of both prints `Iron Ore, Water (head lift)`.

| rung | fires when | answered by |
|---|---|---|
| **(1) connection** | no run of that medium arrives at all, or every one that does reaches nothing | `domain/world/logistics.py` — `NOTHING` and `OPEN` |
| **(1) connection** | a run arrives from a real fitting, and no source anywhere reaches that network | `headlift.unfed_ports` |
| **(2) head lift** | the machine is behind a crest on that fluid's network | `domain/world/headlift.py` |
| **(3) flow rate** | none of the above, so the fluid can arrive and there is not enough of it | nothing yet — the rung is named, not measured |

Two things about the shape. **Rung (1) is two different facts**, and the second is one only the
head-lift model can see: the conduit graph is perfectly satisfied by a pipe running from a
junction, and the network it belongs to may still have no producer on it anywhere. **Rung (3)
is earned rather than defaulted to** — `assess` is given the head-lift model or it reports no
rung at all, because "it must be the rates" without checking the climb is precisely the error.

### What it says on real data

Nothing, on every rung but one, and that is a fact about the world rather than about the code.
Across all 71 saves on this machine there are 802 starved machines and **not one of them is
short of a fluid**: a machine's fluid box is carried in its input inventory in litres, so the
ingredient is readable — `Desc_Water_C: 50000` on a refinery — and every starved machine here
is short of a solid. Rungs (2) and (3) therefore never fire, which follows from the head-lift
model reporting zero crests over the same sweep.

The one rung that does fire is the second form of (1): **ten refineries plumbed into a pipe
network no source reaches**, in the newest three saves. They keep no productivity monitor, so
`factory_health` calls them `unmonitored` and says nothing else about them; the sweep now names
them in a note, world-wide, beside the throttled buffers and the dark pumps. A line to finish,
and explicitly not ten head-lift failures.

**That silence is a verdict, and the perturbation shows it both ways round.** Take the
supplemental water away from every generator on the reference world and 32 of them read starved
of Water. As the base is actually wired the plumbing reaches all 32, so every one is answered at
rung (3), the rates. Cut power to every pump and the *same* 32 move to rung (2) behind the five
crests that appear — with not one of them still being told to check its supply. Pinned in
`tests/domain/factories/health/test_fluid_rungs.py`; it is the whole of phase 3 in one assertion.

### Limits

A crest names the fluid of the network it stands on, so rung (2) is attributed per ingredient.
`unfed_ports` cannot be: a network no source reaches has typically never carried a fluid and
the save records none for it, so a machine with a second, working fluid input would have that
one called a connection fault too. Measured rather than assumed — across all 71 saves every
unfed consumer is the same thing, an Oil Refinery on Alternate: Heavy Oil Residue, whose one
fluid ingredient is Crude Oil. The over-attribution has never had a case to happen in.
