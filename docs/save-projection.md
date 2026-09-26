# The save projection, and the parser under it

Part of the [SatisfactoryMcp design spec](../DESIGN.md) — §6 and §6.9–§6.11 are what the sidecar
emits and how each fact in it was established; §13a and §13b are the record of replacing the
vendored GPL parser with `src/pioneersav`, which cannot be re-run now that the library is deleted.
Section numbers are continuous with the rest of the spec; [DESIGN.md](../DESIGN.md) indexes it.

The byte-level format — the derivation, the overlap predicates, the parity table and what stays
unknown — is next door in [savparse-notes.md](savparse-notes.md). This file is the design-level
half: what the projection carries and why the agreement is believable.

---

## 6. Save projection

The sidecar emits a flat JSON projection — never the parse tree. Verified property names:

| fact | object (`typePath` substring) | property |
|---|---|---|
| unlocked recipes ★ | `FGRecipeManager` | `mAvailableRecipes` (405) |
| purchased schematics | `BP_SchematicManager_C` | `mPurchasedSchematics` (226) |
| game phase ★ | `BP_GamePhaseManager_C` | `mCurrentGamePhase`, `mTargetGamePhase`, `mTargetGamePhasePaidOffCosts`. `mGamePhaseCosts` is **deprecated and frozen** — see [§6.4](#64-space-elevator-phases--two-records-and-only-one-is-alive) |
| hard-drive offers ★ | `BP_ResearchManager_C` | `mUnclaimedHardDriveData`, `mLastUsedHardDriveID` |
| research in progress | `BP_ResearchManager_C` | `mSavedOngoingResearch` — **seconds remaining**, absent when empty |
| misc unlocks | `BP_UnlockSubsystem_C` | `mIsMapUnlocked`, `mIsBuildingOverclockUnlocked`, `mNumTotalInventorySlots`, … |
| depot contents | `FGCentralStorageSubsystem` | `mStoredItems` |
| lifetime stats | `FGStatisticsSubsystem` | `mItemsPickedUp` — **nested**, see below |
| machine recipe | manufacturers | `mCurrentRecipe` |
| clock speed | clockable buildings | `mCurrentPotential`, `mPendingPotential` |
| installed shards ★ | every buildable | `InventoryPotential` component's `mInventoryStacks` — see [§6.5](#65-power-shards--committed-is-read-never-derived) |
| paused | buildings | `mIsProductionPaused` |
| extractor → node | extractors | `mExtractableResource` → node `instanceName` |
| pipe network fluid | `FGPipeNetwork` | `mFluidDescriptor` (19 objects) |

Normative gotchas:

- **`ComponentHeader` has no `typePath`.** Always `getattr(h, 'typePath', '') or ''`.
- **Bools are uint8 and `16` means True.** Observed `{16: 1489, 1: 84, 0: 32}`. Use `bool(v)`, never `v == 1`.
- **UE omits empty `TArray` SaveGame properties** — absent means empty, not missing. Default to `[]`.
- **`mItemsPickedUp` is a `MapProperty` keyed by player state**, whose value is a struct containing an
  inner map. Two levels of nesting; aggregate across players or co-op pickups vanish.
- **`ObjectReference` defines `__str__` but not `__repr__`**, so `repr(o.properties)` renders every
  reference as `<object at 0x...>`. Any substring search over `repr()` silently finds nothing.
- **`mPendingPotential` equals `mCurrentPotential` on all 46 clocked machines** in this save, so the
  applied-vs-slider distinction is *unconfirmed* and must not be asserted.
- `mCurrentProductionBoost` / `mPendingProductionBoost` are **guessed names** — they appear neither in the
  save nor in `Docs.json`. `[UNVERIFIED]`.
- **Extractor→node resolution is partial: 40/66.** Oil pumps 13/13, Miner Mk2 20/21, Miner Mk1 7/9, and
  **all 23 water pumps point at `FGWaterVolume*`**, which aren't purity-table keys. Three miners have no
  `mExtractableResource` at all. A coordinate-proximity fallback is required, and unresolved extractors
  must be reported as unknown rather than dropped.
- **Fluid buffer contents are not readable.** Tank fill level is; the *fluid type* is not (no serialized
  tank→network link). "Crude is backing up" is an inference, never a measurement.

**Building spatial grouping.** Cluster buildings by position (single-linkage) to form "sites", so
"your northern oil site" is addressable. Note `FGLightweightBuildableSubsystem` holds 17 additional
`Build_*` classes that appear in **no** actor header — 103 classes are built, not the 86 visible via
headers alone.

### 6.1 The factory graph — a first-class structure

Schema **6** adds a `graph` block to the projection, interned to keep it small (553 KB total for
6,100 actors / 11,554 material edges / 1,276 power edges):

```json
"graph": {
  "actors":   ["Build_ConstructorMk1_C_2147441119", ...],   // short instance names
  "roles":    ["Output1", "Input1", "ConveyorAny0", ...],
  "material": [[i, j, role_i, role_j], ...],
  "power":    [[i, j], ...]
}
```

Three edge kinds, kept separate because **no single one identifies a factory**:

| layer | count | what it is | why it is not enough |
|---|---|---|---|
| material | 11,554 | belts and pipes; orientable, since the connector role says `Output*` vs `Input*` | a mature base is one belt web → 35 components, 19 of them fragments |
| power | 1,276 | wires, with **poles** and **towers** distinguished (tower wires median 208 m vs 34 m for poles, and carry no machines) | dropping towers separates outposts but leaves the base as **one 476-machine island** |
| transport | 0 here | trains, drones, trucks | modelled from the start: a transport link is a deliberate connection **between** factories, so it belongs on a boundary |
| structure | 8,372 pieces | foundations, ramps, walls, catwalks — full transforms from `FGLightweightBuildableSubsystem` | 128 of 563 machines are ground-built and sit on no slab at all |

**Slabs are the sharpest signal we have** (§6.2a), but only the fourth one — not a replacement.

`build_graph` seeds nodes from `machines`/`extractors`/`generators` as well as from the edge list.
The interned actor list is derived from *edges*, so the **6 of 563** machines wired to nothing would
otherwise be absent from the graph entirely — and an isolated machine is exactly what a coverage
report exists to surface.

### 6.1a What the power layer can and cannot say about power

`mHasPower` and `mCircuitID` are on **0 of 44,634 objects** — `UFGPowerInfoComponent` carries no
`SaveGame` specifier at all, checked against Headers.zip (§13). So the file states nothing about
whether a machine is *running on* power. What it does state is the **wire**, and reachability over
the wires supports exactly one claim, in two shapes:

| what `factory_health` reports | what it means | why the save supports it |
|---|---|---|
| `no power connection` | the actor is in no power edge | degree zero in `graph["power"]`, and `build_graph` seeds every machine record as a node so an isolated one is a node with no edges rather than an absence |
| `no generator on its circuit` | wired, but no `generators` record is reachable over the wires | a breadth-first walk out from every generator; anything unreached has no source that could feed it, however the grid is loaded |

**Everything else is refused.** A brownout, a tripped grid, a coal plant out of coal, a machine
whose circuit is at 0 MW — none of these are in the file, and a machine some generator *can* reach
is therefore never called unpowered no matter what the report otherwise says about it. An **open
power switch** is the one known blind spot and it fails in the safe direction: the switch is a node
joined to both sides, so a genuinely dark sub-circuit behind one reads as reached and is not
reported. Under-report, never over-report.

Two absences are also refused as findings. No graph supplied, no verdict. And **no generator
anywhere in the projection**, which would make every actor in the world unreachable: that is a
statement about the save — an early world, a hand-cut fixture — not a diagnosis of any one machine,
so the whole check stands down and only the degree-zero half still reports.

Measured over all **98 saves** on the author's machine, across three worlds and four years:
**34 of them** carry at least one machine on a circuit no generator stands on, up to 32 at once.
The clearest case is `HL_BUFFER_A` through `D`, where ten Oil Refineries in two rows of five are
wired to each other and one pole and nothing else, and are wired into the grid by `HL_BUFFER_E`.
The **committed fixture and the current save have none** — every wired machine there is on a circuit
a generator stands on, which is the sharper sentence and the one the tool prints. The degree-zero
half stands at 7 on the fixture and 8 on the current save.

### 6.2a Foundation slabs — the fourth signal

A player builds a platform, then fills it. Belts and wires cross between platforms freely
— that is what they are for — but a foundation is only ever placed against another one
deliberately. Snapping is a build-time UI concept and is **not serialized**, so adjacency
is recovered geometrically: two 8 m foundations that touch have centres 800 cm apart.

Three link rules, each measured against the player's twelve named factories. *Purity* is
the share of a label's machines landing on its single dominant slab; a *collision* is one
slab claimed by two different factories.

| bridging rule | slabs | purity | collisions |
|---|---|---|---|
| nothing | 90 | 0.68 | none |
| walls only | 71 | 0.99 | none |
| ramps + stairs | 42 | 0.99 | none |
| **ramps + stairs + walls** | **41** | **0.99** | **none** |
| catwalks only | 85 | 0.78 | none |
| plus catwalks | 46 | 0.90 | tier 1&2 welded to the tor factory |

**Bridges must be chained, not tested pairwise.** Asking whether a *single* piece touches
two slabs finds nothing — the real shape is `slab → wall → wall → slab`. Tested as single
pieces, 0 of 1,937 walls touch two slabs; tested as chains, they join four pairs. Bridging
pieces therefore enter the union as nodes in their own right.

**Catwalks are excluded, and that is the whole trick.** Ramps connect the floors of one
structure; catwalks are the long walkways a player runs *between* distant platforms.
Chaining catwalks scores well and is still wrong, because the one thing it merges is two
genuinely separate factories. An over-segmented slab can be merged by naming; an
over-merged one cannot be split.

`LINK_Z = 1600` cm (four storeys) is a measured knee, not a guess: purity runs 0.84 at
450 cm, 0.85 at 900, 0.94 at 1200, 0.99 at 1600, with no collision at any of them. Past
1600 purity stops improving and only merge risk grows. This is what merges a factory
built on stacked decks — before it, the tor factory read as three platforms that merely
shared a footprint, and the speedwire factory as two.

**`slab:n` means the slab's own index**, the one `factory_map` prints. Slabs are numbered
by tile count while their machine groups sort by machine count; indexing the wrong list
silently returns a different platform.

**Walls are included on the same evidence.** Alone they take 90 slabs to 71; added to
ramps and stairs, 42 to 41, purity unchanged and still no collision. They buy little here
because ramps already cover most of the same joins, but they are structurally the right
kind of edge and cost nothing.

**Slabs are not the arbiter.** 128 of 563 machines stand on no foundation — the concrete
setup and the copper setup are built straight on the ground and have no slab at all.

### 6.2 Identifying factories — measured, not assumed

Checked against the player's own list of what they built on a 316-hour save:

- **material components** → 35 pieces. Splits one Christmas factory into a Tree Branch line and a
  Candy Cane line. Too fine.
- **power islands, towers removed** → 9, one holding 476 machines across 2,603 m. Separates outposts
  cleanly, does not subdivide the base at all. Too coarse.
- **spatial clustering alone** → chains through shared infrastructure; merged an oil plant 900 m out
  into the base. Wrong shape.

What works is the signal the first two lack: **what a machine makes**. Steel (50 machines, 95 m) and
Tier 1&2 (98 machines, 307 m) are one belt-connected mass and one power island, but they sit 600 m
apart and make different things. A grown-together base defeats topology; it does not defeat geometry
plus recipe.

So bases and lines are offered as **candidates**, never as an answer — `factory_map` prints both and
flags where they disagree. Product alone over-collects too: 17 machines make Concrete, 15 of them a
construction feed inside the steel site.

### 6.2b One coherence score over every signal

Each signal fails alone, so combine them: score every machine pair on shared slab,
proximity, belt component, shared product and supply link, then agglomerate. Validated
**leave-one-factory-out** — weights fitted on eleven factories, the twelfth scored:

> **precision 1.000, recall 0.945.** Ten of twelve recovered exactly. Precision was 1.000
> on *every* fold: it never merges two factories, it only splits one.

Clustering all 563 machines rather than only the labelled ones: **precision 1.000, recall
0.972**, 40 proposals, 0.3 s.

**The weights are barely load-bearing, and saying so matters more than the score.**
Ablated on the 382 labelled machines:

| variant | clusters | precision | recall | F1 |
|---|---|---|---|---|
| fitted log-odds | 15 | 1.000 | 0.973 | 0.986 |
| round numbers | 15 | 1.000 | 0.973 | 0.986 |
| every weight = 1 | 20 | 1.000 | 0.926 | 0.961 |
| **slab weight = 0** | 15 | 1.000 | 0.973 | 0.986 |
| random ±50%, worst of 12 | — | — | — | 0.967 |
| no power-island veto | 15 | 1.000 | 0.973 | 0.986 |
| **no distance cap** | 14 | **0.776** | 0.973 | 0.863 |
| **single linkage (same score)** | 8 | **0.353** | 0.995 | **0.521** |

Two results against intuition:

1. Rounding the weights changes nothing, perturbing them 50 % costs 0.02 F1, and deleting
   the *strongest* signal costs nothing — the others separate the same pairs. The weights
   are kept for the evidence report, not because the arithmetic needs them.
2. **The linkage rule and the distance cap are everything.** The identical score under
   single linkage collapses to F1 0.521: one adjacent pair chains a base into a blob.
   Complete linkage requires *every* cross pair to clear the bar. The power-island veto
   was measured redundant and is not applied.

`MAX_SPAN_M = 250` caps *linkage* distance, so a genuinely sprawling factory is proposed
in pieces. That is the deliberate trade for precision 1.000.

**Exclusive dependents are absorbed in a second pass**, because exclusivity is a property
of a cluster and no pairwise score can express it. The coal plant is the proof: its water
pumps sit 21–184 m away, well inside the span cap, and 94 % of everything their pipes
reach is that plant — but the plant runs on **two separate pipe networks**, so every pump
against a generator in the *other* network scores negative, and complete linkage takes the
minimum over cross pairs. One blind pair vetoed the merge.

A cluster is absorbed when ≥ `MIN_EXCLUSIVITY` (0.8) of the machines it reaches over
material edges lie in one other cluster, **and** it is at most `MAX_DEPENDENT_RATIO` (0.5)
of that cluster's size, **and** at most `MAX_DEPENDENT_RECIPES` (2) of its machines run a
recipe at all.

That third guard is what size cannot express. The player's space-elevator-parts area is 15
machines feeding a 110-machine host almost exclusively — inside both the exclusivity and
the size guard — but 3 of those 15 manufacture (Automated Wiring, Computer) and the other
12 are the biomass burners and miners powering them. Infrastructure runs *no* recipe:
every correctly absorbed dependent measured on the reference save has 0 or 1. Something
that manufactures stands on its own.

The size guard is not optional either:

| variant | clusters | precision | recall |
|---|---|---|---|
| no attachment | 40 | 1.000 | 0.972 |
| exclusivity ≥ 0.9, no size guard | 26 | **0.709** | 0.972 |
| exclusivity ≥ 0.8, no size guard | 21 | **0.711** | 0.979 |
| **exclusivity ≥ 0.8, ratio ≤ 0.5** | **30** | **1.000** | 0.972 |

Without it, two large factories that mostly feed each other are welded together. With it,
the coal plant becomes `32 + 6 + 6 + 2 + 1` — its generators, both water-pump farms, its
miners and a stray constructor. Recall against the twelve labels cannot move, because none
of the absorbed machines was ever labelled; the evidence is that precision holds at 1.000
and every merge is qualitatively right.

**Exclusivity alone cannot attribute a remote mine**, so there is a second way to
qualify. The four mines feeding the steel factory reach it in 26–43 hops and the tor
factory in 88–123 — but steel and tor are belt-connected to *each other* downstream, so
counting every reachable machine dilutes exclusivity to 0.55 and the mine is orphaned.
First arrival is unambiguous: a dependent is absorbed by the cluster it reaches first when
that cluster is `NEAREST_MARGIN` (2×) nearer in hops than the runner-up. Measured margins
were 42–69 hops. This takes 31 proposals to 26 with precision still 1.000, the steel
factory reclaiming its 8 miners and the second oil site its pump.

The margin has a readable meaning: it fires only when the two factories are farther from
*each other* than the mine is from the nearer one. A mine genuinely between two consumers
stays unattributed, which is the honest answer — an orphan in the coverage report beats a
wrong attribution.

Attachment runs *after* linkage, so an absorbed dependent may sit beyond the span cap — a
miner feeding a plant from 400 m is still that plant's.

**What stays unattributed is now a real finding, not a gap.** Five extractor clusters
reach *no* machine at all: their belt or pipe ends in a container. Nothing in the material
graph can attribute those, and guessing by proximity would be invention.

**Index selectors are snapshot-scoped.** `base:`, `line:`, `slab:` and `proposal:` are
positions in size-ordered lists rebuilt from the save on every call, so building anything
reshuffles them. A stale index once re-anchored the speedwire factory onto the aluminium
site. Every tool that prints an index now says so. A *label* is durable because it holds
machine ids — the index is only ever a way of pointing at them once.

**`proposal:n` closes the loop.** The workflow is propose-then-name, so a proposal has to
be selectable. Reconstructing one by hand from a centroid and a radius does not work: on
the real save, `near:-442,-1406@120` around a 15-machine proposal picked up **137**
machines, 82 of them belonging to the factory next door.

**A proposal is not a claim that something is a factory.** The space-elevator-parts area is
a temporary setup the player throws up to hand-build parts; it is neither part of tier 1&2
nor a factory in its own right. Keeping it a separate proposal is the correct outcome for
exactly that reason — the tool proposes, the player names, and what goes unnamed stays
visible in the coverage report rather than being silently filed somewhere.

Naming is also how a player records that something is *deliberately* not a factory. The
label carries `notes`, so "kept around to hand-build parts and mess with — idle is expected
here" is a durable statement that survives into every later report. `factory_health` will
need exactly that distinction: 304 of 580 machines are idle on this save, and idleness in a
scratch area is not a fault.

**Caveat no internal cross-validation removes:** one save, one player's building style.
LOO tests generalisation across *that player's* factories, not across players. The
insensitivity to weights is the real reassurance — a result that survives ±50 % on every
parameter is not resting on a fit.

### 6.2c Querying a factory

One tool, not eight: every question shares the same two steps — resolve a machine set,
then read something off it. `factory_query(factory, show=...)` takes a label name, a
selector, or a proposal index, and `show` accepts several aspects at once.

```
factory_query("steel factory", show="summary,balance")
  -> makes: Steel Ingot 405/min, Steel Beam 81/min, Steel Pipe 60/min, EIB 48/min
     needs: Coal 975/min, Iron Ingot 840/min, Concrete 288/min, Iron Ore 135/min
```

The **balance** table is what earns the tool. Production minus consumption across the set,
where the sign is the answer:

- **positive** — surplus: it leaves, or it backs up
- **negative** — has to be fed in from outside
- **zero with non-zero production** — made *and* consumed inside, the signature of a
  self-contained line

A per-machine listing says a Foundry runs Solid Steel Ingot. Only the balance says the
steel factory needs 975 Coal/min fed in.

Aspects: `summary`, `balance`, `inputs`, `outputs`, `internal`, `machines`, `recipes`,
`buildings`, `power`, `nodes`, `links`, `issues`. `internal` is the third row of the list
above given its own view — what a factory makes and eats entirely within itself, which is
the difference between a finished line and one still on somebody else's belts. `power`
prints nameplate **and** measured side by side, never blended: measured weights each
machine's rated draw by its own 300 s productivity window, and the machines that keep no
monitor are charged in full, because unknown utilisation must not read as idle.

Two implementation notes that were both bugs first:

- **The boundary walk must pass through logistics.** A material edge runs
  machine → belt → machine, so a walk that stops at the first non-machine finds no links
  at all and every factory looks isolated. `links` counts *machines reached on the far
  side*, not edges, and is asymmetric on purpose: from a 15-machine copper setup you reach
  16 tor-factory machines on the shared belt web, but walking back the first copper machine
  blocks the rest.
- **`links` is only as good as the labels.** Before re-anchoring, every factory reported
  its neighbours as `(unlabelled)` — 92 boundary links in total — because the labels
  under-covered their own sites (the steel *label* was 50 machines against a 108-machine
  site). Re-anchoring the eight clean cases to their proposals took coverage from 397 to
  **511 of 563** machines and unlabelled links from 92 to **3**. Three labels were
  deliberately left alone: `biofuel setup` (its proposal lumps 3 biofuel machines with a
  separate 12-machine iron line), `aluminium setup` (split across two proposals whose union
  already equals the label), and the three that already matched exactly.
- **Rates are nameplate at each machine's saved clock**, applied per machine, never to a
  factory total. Paused machines contribute no flow but are still members and are listed
  under `issues`. Anything whose building class cannot be resolved is reported rather than
  silently contributing 0 MW — an understated draw with no explanation is worse than an
  error.

This is explicitly **not** throughput. A starved factory reports its full rate; measuring
what actually flows needs the productivity fields, and conflating the two would make a
starved factory look healthy.

### 6.2d Health — the only measured numbers in this MCP

Every manufacturing buildable keeps a **productivity monitor**. Schema 8 emits it, plus
per-machine input/output/fuel buffers. Field semantics, verified rather than assumed:

| field | finding |
|---|---|
| `mLastProductivityMeasurementDuration` | **300.00 s on all 580** carriers — a fixed window, so the ratio needs no normalisation |
| `mLastProductivityMeasurementProduceDuration` | **absent when zero**; UE omits defaults, so missing is a real zero (377 of 580 idle) |
| `mCurrentProductivityMeasurement*` | a *partial* window still filling — mixing it with the last complete one compares a 3-minute sample to a 5-minute one |
| `mTimeSinceStartStopProducing` | **FLT_MAX on 256 of 580** as a "never flipped" sentinel. Not a duration; averaging it poisons any statistic. Unused, and **not** a "has never produced" marker either — on `Starved.sav` 770 of the 1,023 carriers hold it and **353 of those also hold a closed productivity window**, so it does not separate a machine that never started from one that started and never stopped. Not projected |

Uptime says a machine is stopped but never why, and the fixes are opposite. The buffers
settle it. **Every rule below was wrong before it was measured:**

- **Starvation is a missing *ingredient*, not an empty input.** Black Powder takes Coal
  and Sulfur; the assembler that motivated this held 100 Sulfur and no Coal. An
  empty-input test called it well-fed and filed eight machines as unexplained stalls.
  Comparing the buffer against the recipe names the missing item.
- **Blocked is checked before starved.** A blocked machine's input backs up too — the
  sample reads input 100/100 Iron Ingot, output 199/200 Iron Plate. Reading the input
  first calls it well-fed and misses that nothing is taking its plates.
- **A nearly full stack counts as backed up** (`FULL_FRACTION = 0.95`). Demanding exactly
  100% hides a bottleneck that has just ticked one item forward.
- **An absent intake inventory is not an empty one.** A miner draws from its node and has
  no `InputInventory` at all; treating that as "no input items" reported every idle miner
  as starved.
- **`dead node` is its own state.** An extractor whose `mExtractableResource` is *absent*
  is bound to nothing and can never produce — three miners on this save, left behind when
  a game update removed their resource node. Distinct from a water pump, whose node *is*
  set but points at an `FGWaterVolume` that is not a purity-table key; that one works fine.
- **Generators keep a `FuelInventory`, not an `InputInventory`.** Without capturing it a
  starved coal plant shows no evidence either way.

`STACK_SIZE` joins §5.6's register: Docs.json gives only the enum symbol (`SS_BIG`), so
the numbers are game knowledge. Verified against observed buffers — Wire 500 = `SS_HUGE`,
Iron Rod 200 = `SS_BIG`.

**Blocked needs action.** 319 of 563 machines on the reference save are blocked: a base
whose output nobody consumes fills its buffers and stops. That once read as a factory at
rest, and the overview's `todo` column left it out. Lukas decided on 2026-09-26 that a
blocked machine is a problem, because nothing is taking what it makes, so `todo` counts
`health.ACTIONABLE`: `dead node`, `no recipe`, `blocked`, `starved` and `stalled`. The web
dashboard counts the same tuple (docs/frontend_vision.md §8.6), and so does the map:
`/api/machines` sends `actionable` per row (`state in health.ACTIONABLE`), and
`frontend/src/placements.ts` keeps no list of its own.

**How the map marks a machine.** The fill stays the kind's hue (machines blue, extractors
amber, generators red); the outline carries the state:

| mark | meaning |
|---|---|
| solid box, thin outline in the kind's hue | running, or not in `ACTIONABLE` (`intermittent`, `saturated`, `unmonitored`) |
| hollow box, dashed outline | `paused` |
| hollow box, thick **red** outline | stopped, needs action: `dead node`, `no recipe`, `starved`, `stalled` |
| solid box, thick **yellow** outline | `blocked`: output full, runs again once emptied |

Lukas decided the colours on 2026-09-26: red means broken, and blocked is not red. A blocked
machine runs again as soon as its output is emptied, so it is waiting on downstream rather
than broken. It still counts as `actionable`.

The red is the page's problem colour, `--warn` in style.css, which is also the generator hue
(`#d9534f`). A stopped generator therefore keeps a red outline, only thicker and hollow.

The yellow is `placements/blocked`, `#ffd000`, a new colour: the palette had no yellow free.
The ore and pickup yellows are node dots, and an equal hex in another module is dE 0 and
fails the audit. Measured CIE76 distances: gold dot 23.4, yellow slug 26.9, sulfur 27.0,
extractor amber 33.0 (same owner, so the audit does not compare it; measured by hand) and
loot cache 46.7. A darker ochre landed within 16–19 of the extractor amber, and a paler butter
yellow within 10–15 of gold and sulfur.

Every `actionable` machine gets the thick outline, red unless it is blocked, so a state added
to `ACTIONABLE` needs no frontend change. The popup says "blocked — output full, runs again
once emptied" and adds a "needs action" row. The marker key lists both outlines.

The side panel and the dashboard use the same yellow for anything that names `blocked`: the
state chips, the machine-row labels, the per-state table cell and its bar. `placements.ts`
publishes the declared colour as the CSS variable `--blocked`, and `stateTone()` picks the
class, so all three surfaces read one token. The other actionable states stay red. On the
panel background (`#1f2228`) the yellow has a contrast ratio of about 11:1.

### 6.3 Labels — anchor sets matched by recall

A label stores the **set of machine instance ids** it was created from (verified stable: 365/365 kept
id and position across two saves). Matching is **recall**, `|anchors ∩ candidate| / |anchors|`, not
Jaccard — Jaccard punishes growth, and extending a factory is the most common thing that happens to
one. Match at ≥ 0.5, re-anchor at ≥ 0.8 with no competing label.

Labels attach to **arbitrary machine sets**, not to a base or a line, because a real factory is
sometimes several components (Christmas) and sometimes part of one (steel inside the base).

Persisted per world under `saveIdentifier` in `user_data_dir/labels/`, deliberately **not** under
`cache_dir` (which `cache_prune` wipes) and **not** in the repo.

Selection uses a small query language (`factories/select.py`), tabulated in
[selectors.md](selectors.md) alongside the node selectors it shares a spelling with. Terms are ANDed,
commas inside one term are ORed, a leading `-` excludes. `machine:` is checked against the graph, so an
unknown id is an error rather than a selection of nothing. Intersection rather than union because carving is
subtractive in practice — the player starts from something too big and narrows it.

Two orthogonal modifiers, because a factory is delimited from either end:

- `split` keeps only the largest spatial cluster — the escape hatch when one product is made in
  several places (17 concrete machines across 3 sites).
- `expand` grows the result to whole material components — the escape hatch when a factory is defined
  by **what feeds it**. The concrete setup is one limestone miner → storage → constructor → storage,
  a self-contained 10-actor component that no product or radius term describes.

Exclusions apply **after** expanding, or `-label:x` would be silently undone by the expansion
following it.


#### The label file is an interface

Labels are the one thing this server holds that a **player authored by hand**, so the
file is published rather than kept private. Location, and the same JSON served as an MCP
resource:

```
user_data_dir/satisfactory-mcp/labels/<saveIdentifier>.json
satisfactory://factories/labels          # same content, plus its own path
```

```json
{ "schema": 1, "world_id": "<saveIdentifier>", "session_name": "Han Solo",
  "labels": [ { "id": "steel-factory", "name": "steel factory",
                "anchors": ["Build_FoundryMk1_C_2147082409", "..."],
                "notes": "...", "centroid": [x_cm, y_cm],
                "signature": {"Build_FoundryMk1_C": 24},
                "created": "<save filename>", "last_matched": "<save filename>" } ] }
```

`anchors` is what makes a label portable: machine instance names were verified stable
across saves (365 of 365 kept id and position between two files), so a consumer joins
them against **its own** read of the same save and needs nothing else from this server.
`schema` is an integer so a reader can refuse a shape it does not know.

`centroid` is in **centimetres** — save units, not the metres every tool prints — because
it is stored data rather than presentation. `signature` is a building-class census kept
as a re-match hint after a full rebuild; it is advisory and never used to match
automatically.

`list_factories` prints the path, because reverse-engineering platformdirs to find it is
not a reasonable ask.

### 6.4 Space Elevator phases — two records, and only one is alive

`phase_requirements` exists because the obvious field is a trap. The save carries **two** accounts of
Space Elevator progress and they disagree.

| record | property | status |
|---|---|---|
| live | `mCurrentGamePhase`, `mTargetGamePhase` (→ `UFGGamePhase` assets), `mTargetGamePhasePaidOffCosts` | authoritative |
| legacy | `mGamePhaseCosts`, keyed by the `EGamePhase` enum | **deprecated and frozen** |

`FGGamePhaseManager.h` is unambiguous about the second one. The enum is *"The old enum that defined the
phases of the game. Replaced by UFGGamePhase. **DEPRECATED Only kept for save compatibility**"*, and the
array is *"**DEPRECATED Only kept for save compatibility**"* too.

> **Measured: the legacy array is not merely deprecated, it is dead.** Parsed across all **29 parseable
> saves of the reference world**, 180 h to 316 h of play, `mGamePhaseCosts` is byte-identical in every
> one — including across the session (between 244.0 h and 251.0 h) where `mCurrentGamePhase` advanced
> `Phase_2 → Phase_3` and `mTargetGamePhase` `Phase_3 → Phase_4`. Completing an entire Space Elevator
> phase moved nothing in it. It still bills the player **500 Modular Engine and 100 Adaptive Control
> Unit** for a phase they finished ~70 hours of play ago.

Deliveries go to the **target** phase, not the current one — `PayOffOnTargetGamePhase`,
`GetTargetGamePhaseCosts` — so "what do I owe" is the target's cost minus `mTargetGamePhasePaidOffCosts`.
On the reference save that array is **absent, i.e. empty**: nothing at all has been delivered toward
Phase 4.

**The EGP_* → GP_Project_Assembly_Phase_N mapping.** The legacy array is still the *only* source of
per-phase item lists, because the `UFGGamePhase` assets that hold `mCosts` do not ship in Docs.json, so
the keys have to be mapped. Establishing that mapping was the hard part:

- **Not in Docs.json.** `"GP_Project"` occurs **0 times** in the 10 MB dump, and the only `"EGP_"` string
  in it is one `EGP_Victory` schematic dependency. The field that *would* join them,
  `UFGGamePhase::mGamePhase` (`Category=Legacy`), lives on those unshipped assets.
- **Not joinable in the save either.** `BP_GamePhaseManager_C` carries exactly three properties. The
  manager's own legacy scalar `mGamePhase` is **absent**, i.e. UE-default `EGP_NA`, whose declaration
  comment reads *"Added N/A to have a state that indicates we have migrated the save"*.
- **But one anchor is measurable.** At 180–244 h the same world reads `mTargetGamePhase = Phase_3` with
  `mTargetGamePhasePaidOffCosts = {Desc_SpaceElevatorPart_2_C: 2500}` — exactly one item settled. The
  `EGP_EndGame` row of the legacy array describes those same three items with that same one at zero
  remaining, and nothing can be paid into a phase that was never the target. **`EGP_EndGame` →
  `GP_Project_Assembly_Phase_3`, from the save.**
- The other three follow **by enum order** (`EarlyGame 0 < MidGame 1 < LateGame 2 < EndGame 3 <
  FoodCourt 4`, declared in the shipped header) anchored on that pin, the four stored keys being
  contiguous in it. Corroborated but not relied on: the vendored wiki-derived `PROJECT_ASSEMBLY_COSTS`
  table lists Phase 1–4 item sets matching these four keys exactly and in order.

So `EGP_MidGame → 1`, `EGP_LateGame → 2`, **`EGP_EndGame → 3` (measured)**, `EGP_FoodCourt → 4`. The
three derived ones are labelled `[UNVERIFIED]` in the tool output, and an unrecognised key is reported
as `unmapped` rather than dropped — a silently missing phase reads as a phase with nothing outstanding.

**Frozen does not mean wrong for every row.** A phase that has never been delivered into cannot have
drifted, so its snapshot still equals its full cost. That is a checkable condition, not an assumption,
and it is what makes the Phase 4 numbers (Assembly Director System 4000, Magnetic Field Generator 4000,
Thermal Propulsion Rocket 1000, Nuclear Pasta 1000) usable while the Phase 3 numbers are not. Every row
is emitted with a `trust` column — `complete` / `usable` / `derived` / `stale` / `unmapped` — rather
than filtered.

**And a delivery must not destroy the row.** `usable` used to expire on the player's first delivery
into the target and never come back, so *"what does Phase 4 still need"* — the question this tool is
for — died the moment they started answering it. The target phase is the only one deliveries can reach
(`PayOffOnTargetGamePhase`), so it is the only row with a live counter to take off, and its remainder
is the frozen cost **minus** `mTargetGamePhasePaidOffCosts`: `derived`. Two things prove a snapshot
froze *after* a delivery and fall back to `stale`, an item at zero in it and a live figure larger than
it still bills for; neither can happen to a full cost. A partial payment below the remainder cannot be
detected, so the subtraction can only **understate** what is owed and `derived 0` means "nothing left
that this can see". Untestable on any save of this world — `mTargetGamePhasePaidOffCosts` is empty in
all 29 — so it is pinned on constructed projections in `tests/test_phase_and_shards.py`.

### 6.5 Power Shards — committed is read, never derived

Every buildable carries an **`InventoryPotential`** component holding the shards physically slotted into
it. 447 exist on the reference save and 41 are non-empty. That component is the only faithful record of
a spent shard, and schema 9 emits it per machine as `potential_slots`.

> **A shard raises the *maximum* clock; it does not set the clock.** The player slots shards and then
> drags the slider anywhere below the new ceiling, so installed ≥ required. Measured: 39 of the 41
> overclocked buildings hold exactly `ceil((clock − 1) / 0.5)` shards, and **two hold 3 while running at
> clock 2.0** — a filled slot with the slider pulled back. Deriving committed shards from clocks gives
> **95**; reading the slots gives **97**. The 2 are really spent and really unavailable.

> **The 97 shards in `inventories["machine"]` are not shards on hand.** Every one of them is in an
> `InventoryPotential`, i.e. already installed. Reading that bucket as the free pool overstates it by
> more than **4×**: the player can actually spend **22**, all in the Dimensional Depot. `stock()` already
> excludes machine inventories for exactly this class of reason (§ its docstring), so free/committed/owned
> are 22 / 97 / 119.

Cross-check that the two halves agree: 41 buildings have `clock > 1.0`, 41 hold shards, and they are the
same 41 — no underclocked building holds one, and 5 of the 46 clocked buildings are *under*clocked (down
to 0.333), so treating any `clock != 1.0` as an overclock would invent shards for them.

**Why a tool and not an aspect.** `factory_query` is factory-scoped by construction and needs a selector;
"how many shards do I have, and can I afford to overclock twelve machines" is world-scoped and takes a
*hypothetical* (`plan_machines`, `plan_clock`) that no existing tool's signature accommodates, while
`world_summary` and `power_report` are fixed parameterless dashboards. A shard budget is a planning
question, so it gets its own entry point.

---

### 6.9 Capability gates, and absence as evidence

`plan_factory(sloops=N)` spends Somersloops, and a Somersloop cannot enter a machine until
**Production Amplifier** is researched in the MAM. Planning against it while locked prints
a plan that cannot be built, so the gate has to be readable.

**The first answer here was wrong, and the mistake is the useful part.** Probing a save
taken before the research found no key containing "Boost", "Amplif" or "Sloop" anywhere in
its 44,307 objects, and this section originally concluded that the game records no flag.
It does: `BP_UnlockSubsystem_C.mIsBuildingProductionBoostUnlocked` appears the moment the
research completes. UE omits a SaveGame property still at its default, so **absent means
false** — the same rule §6 already states for empty TArrays, applied to a bool. *"Not in
this file"* and *"no such field"* are different claims and only the first was evidence.
The fix was to research it and look again; the reference save now carries the flag, and
schema 10 extracts it.

So the flag is authoritative when present, with a `CAPABILITY_SCHEMATICS` register
(capability → gating schematic) as fallback. That fallback is not redundant: a projection
written before schema 10 looks exactly like a world that never did the research. The
register also answers the other half — *which research to do and what it costs* — from
Docs.json and `stock()`.

**The same absence fooled the sloop budget, in the same direction.** `sloop_budget` claimed
committed Somersloops were unreadable. They are not: they sit in `InventoryPotential`, the
*same component* as Power Shards, which the sidecar had been reading into `potential_slots`
all along. `mArbitrarySlotSizes` shows the shape — `[1, 1, 1, 2]` on an Assembler is three
shard slots plus one somersloop slot holding two. The count is now exact, and
`mPendingProductionBoost` gives an independent cross-check: **1.5 on an Assembler with one
of two slots filled, exactly `boost_for(1)`**. Reading the count from the slot is still
right and the multiplier is the worse source, since inverting it needs the building's base
and step and rounds.

On the reference save this closes a real gap: production boost was **not researched**, so
every sloop plan produced so far was unbuildable and nothing said so. `plan_factory` now
says it, with the bill:

```
! sloops=16 but PRODUCTION AMPLIFIER IS NOT RESEARCHED, so no somersloop can go in a
  machine yet and this plan is not buildable as printed. Research Production Amplifier in
  the MAM (1 Somersloop, 100 SAM Fluctuator, 50 Circuit Board) -- you can afford that now.
```

It **warns rather than refuses**, because planning ahead of cheap research is legitimate —
the same reasoning that makes `must build first:` a note and not an error. It fires only
when `sloops > 0`, since a plan spending none is buildable today and a standing warning
would be noise.

`mam_research` exposes the whole tree: status (DONE / READY / short / BLOCKED), cost,
what you are short of, prerequisites, and a `LOCKS <capability>` marker on the rows that
gate a feature rather than merely adding a recipe. Costs are checked against spendable
stock only — carried, storage containers and the Depot — never machine buffers and never
the crates on the ground, per § 6.

**The HUB ladder is the same walk, and `milestones` is it.** Status, bill, shortfall and
prerequisites over `EST_Milestone` instead of `EST_MAM`, from one `SchematicLadder` priced
against one stock pool, so a status means the same thing in both — a second vocabulary for
the same four facts is the failure this avoided. It is a tool rather than a mode because
the surface names tools after what they answer, and `mam_research(track=…)` would have
been a tool whose name was false for half its arguments. One thing it deliberately cannot
say: a HUB **tier** is opened by delivering to the Space Elevator, and no milestone
schematic in Docs.json carries a dependency of any kind, so `READY` is a statement about
the bill and the note under it points at `phase_requirements` for the other half.

### 6.10 Pushing back on "not modelled"

Four claims of unknowability were audited after three of them turned out false in one
session (§6.9, §8.5c). The pattern each time: a failed search reported as a missing field.

**"Fuel supply and uptime are not modelled here" — false, and expensive.** `power_report`
returned pure nameplate while the 300 s productivity monitor sat in the projection on
**520 of 566 records**. Weighting each machine by it:

| | nameplate | measured |
|---|---|---|
| draw | 6,839 MW | **1,516 MW** |
| headroom | 711 MW | **6,034 MW** |

An 8.5× error in the number `commission_plan` sizes a startup against. Both are now
reported, because both are true and they answer different questions: nameplate is what
everything built would draw *running at once* — the safe bound, since energising a block
can un-starve idle machines and the fuse blows on demand rather than on averages —
while measured is what is free *now*. Commissioning still defaults to nameplate and names
the other, because being wrong in that direction trips a grid.

Two rules keep it honest. A machine with **no monitor is charged in full** on both figures:
unknown utilisation must not read as idle. And **generation is capacity on both**, because
generators burn to meet demand rather than at a rate of their own — weighting them would
double-count the idleness already seen on the draw side.

**OQ5's "water pumps carry no geometry" — a third right, and the conclusion wrong.** The
volume's *shape* is level geometry and genuinely absent from the save, so how many pumps a
body of water holds stays unknowable. But its *identity* is in every pump's
`mExtractableResource`, which the sidecar had been storing in `node` all along, and it
groups this save's 23 pumps into **three distinct bodies (13 / 6 / 4)**. Sea level falls
out of the same rows: every pump at **−17.4 m, spread 0.24 m**, which turns "water must be
drawn at sea level" from a rule of thumb into a number deck ordering can be checked
against.

**Still genuinely unknown**, and left alone: terrain (there is no heightmap in any input,
which is why layout draws no coordinates and trunk runs are lower bounds), water-volume
capacity, and belt/pipe length without a route.

### 6.11 The removed-actor list — the only record of what was collected

**The world is not saved.** Every power slug, mushroom, Mercer sphere, somersloop, shrine,
crashed drop pod and rock is placed by the map, and a `.sav` never mentions the ones still
standing. What it records is the **negative**: which map-placed actors are *gone*. So there is
no field anywhere that says how many slugs exist, and the only way to answer *"how many have I
picked up"* or *"which crash sites have I looted"* is to count what has been removed. A count
here **is** a collected count — not a proxy for one.

That list was the last region of the body still stepped over rather than read. Schema **11**
reads it and adds the projection's twentieth key, `removed`:

```json
"removed": {
  "cells":     ["0O2UIH8ZOBYWRN8PY7727SVBT", ...],   // interned partition cells
  "instances": [[cell_ix, "BP_Crystal_mk3_C_2146"], ...],
  "counts":    {"BP_Crystal_mk3": n, ...}            // approximate class -> count
}
```

Both engines produce it — `pioneersav` merges the three lists into `destroyed_actors`, the vendored
parser exposes them separately — and the projection **sorts before emitting**, so the two are
byte-identical rather than merely equivalent. The order in the file is an artefact of which list
a parser walks first and means nothing.

The save keeps **three** such lists — one trailing each level's header block, one in each
sub-level's trailer, one closing the body — and they must be **merged and deduplicated**, which
is measured rather than assumed: the header-block list alone falls 870 → 836 across a game
update while the merged union never falls, and the trailer list carries internal duplicates on 6
of the 31 saves that the vendored parser reproduces exactly. Format, overlap predicates and the
oracle parity are in `docs/savparse-notes.md`.

`WorldState.removed_actors(group=None)` groups by class-name prefix and `collected_from_world`
prints it. On the reference save, **889 actors over 284 cells**: flora 185, dropped_pickup 170,
slug_blue 163, mercer_shrine 80, artifact_unsplit 65, crash_site 55, debris 51, slug_yellow 50,
slug_purple 37, mercer_sphere 27, somersloop 6. Both the census and the per-group listing are
built from the instance names, so they agree group for group -- they did not always, and
`test_the_census_and_the_listing_agree_for_every_group` is why they cannot drift apart again.
Over all 31 saves the series and the union are **non-decreasing**, and the actor set is strictly
nested — each save is a superset of every earlier one — which is what a
"collected" reading predicts and a "currently despawned" reading would not.

**Two limits ride on every answer.** The first is printed on every response: these are *absolute
counts, not fractions*, because the denominator would need the map's own table of where every slug
is, which this project does not ship — and the obvious source for it is GPL-3.0 build-time data,
which makes it a licence decision rather than a feature (see `docs/savparse-notes.md`,
*Opportunities*). The tool also prints that `dropped_pickup` is loot the player dropped and
re-collected rather than a map collectible, so its 170 says nothing about the world.

The second limit is **not** printed and belongs here: the refs carry **no class path**, only an
instance name, of which only 32% spell the class out with `_C`; the rest have an instance number
glued straight onto the class with no separator. Harmless for slugs
(`BP_Crystal_mk21_23` still shows its `mk2`) and lossy for artifacts, where `BP_WAT1`
(somersloop) and `BP_WAT2` (Mercer sphere) differ in **exactly the glued digit** and `BP_WAT60`
fits neither — which is why 65 of the reference save's 98 artifacts are reported as
`artifact_unsplit` rather than guessed at. That bucket demonstrably holds somersloops: only 6
names spell `BP_WAT1_C` unambiguously, while the player holds **11 somersloops in the Dimensional
Depot plus 4 slotted in machines** — so at least nine of the unsplit names are sloops, on the one
game-behaviour premise that a somersloop is only ever picked up off the map.

One inconsistency in `removed_actors()` is known and stated rather than hidden: `groups` is built
from `counts`, whose keys have already lost their `_C`, so the two `strict` groups can never
match there and `groups["somersloop"]` / `groups["mercer_sphere"]` are absent on **31 of 31
saves** even though `removed_actors("mercer_sphere")["actors"]` returns 27 entries. 65 + 6 + 27 =
98, so nothing is lost, but the census and the listing disagree about whether the split exists.
Details in `docs/savparse-notes.md`.

### 6.12 Power wires — the save publishes the drawn line, not just the pair

Schema 17. `graph["power"]` has carried this world's **1,297 power edges** as interned actor-index
pairs since schema 11, and it says who is joined to whom and nothing about where. The obvious way
to draw them — a line from one actor's origin to the other's — is wrong, because a wire is strung
between CONNECTORS and a connector is a component at a fixed offset on its owner: the endpoint on
a Mk1 pole is 7 m above the pole's origin, the endpoint on a constructor is 2.1 m forward and 4.7 m
to one side of its centre. Origin-to-origin puts every wire through the middle of the machine it
feeds.

**It does not have to be reconstructed.** `Build_PowerLine_C` carries `mWireInstances`, an array of
`FWireInstance`, and each one holds `Locations[2]` — the two endpoints **in absolute world
coordinates** — beside `CachedRelativeLocations[2]`. So schema 17 needs no table of per-class
connector offsets at all; it reads the two points the game itself drew between.

That these are the wire is measured rather than assumed, against `mCachedLength`, which the actor
also stores and which nothing in the projection derives:

| check | n | result |
|---|---|---|
| `\|dist(loc0, loc1) − mCachedLength\|`, single-instance lines | 1,162 | median **0.000031 cm**, max **0.000484 cm** |
| `\|dist(poleA.origin, poleB.origin) − mCachedLength\|`, Mk1↔Mk1 wires | 399 | max **0.000484 cm** |
| `\|endpoint − (pole origin + 700 cm z)\|`, every Mk1 endpoint | 1,166 | max **0.000000 cm** |

The third row has no free parameter and is what settles the frame: world, unrotated, unscaled. Mk2
is +760 cm, Mk3 +885, the wall outlet −80, each with zero spread. Rotated back into each owner's own
body frame the same measurement gives a fixed local offset per class with **zero spread** across
hundreds of instances — 205 constructors at (210, −470, 687.2), 93 smelters at (220, −310, 480) —
which incidentally verifies the yaw convention of [§ `yaw_of`] at the same time, since a flipped
sign would scatter them.

Two details the reference save forced:

- **135 lines carry two `FWireInstance`s**, and every one of them is a Power Tower strung to another
  Power Tower. The second is the parallel conductor: two strands 12.2 m apart spanning the same two
  towers, each `mCachedLength` long. The projection takes the first — one real strand, rather than
  an average line neither occupies.
- **The save's endpoint order is not the edge's.** Over the 1,297 wires, the order the two
  `PowerConnection` components were serialised in agrees with the order the two `Locations` are
  stored in **687 times and disagrees 608** — a coin flip. `extract._power` therefore assigns each
  end to the nearer of the two actors in plan, and the evidence that the assignment is right is
  that only under it do the per-class offsets above collapse to a constant.

The key is `power` = `{poles: {classes, instances}, wires}`, geometry only, and `wires[i]` is the
span of `graph["power"][i]` — **one pass writes both**, which is the only thing making the
positional join true. **+78,671 bytes on the reference projection, +5.27%** (poles 24.3 KB over 701
rows, wires 54.4 KB over 1,297).

### 6.13 Crates — the inventory that is an event, not a place

Schema 18. A death crate and a dismantle crate were the one thing in this world the projection
could not see at all: not a buildable, so absent from `building_counts`; not a machine; not a
lightweight piece; and not in schema 15's `storage`, which joins a written-down list of container
classes that `BP_Crate_C` is not on. All the projection has ever said about their contents is
that they are somewhere inside `inventories["machine"]` — the schema-11 bucket rule sees a
component named `Inventory` on a non-player, non-storage owner and files it with the smelter
buffers.

They get their **own key, not more `storage` rows**, because the two answer different questions.
A container is infrastructure: the player built it, it stays put, and it answers *"where did I
leave the steel"*. A crate is a **situation** — it did not exist until somebody died or
dismantled something with a full inventory, it self-destructs the moment it is emptied, and it
answers *"what did I lose and where"*. Two rows that are events among 151 that are places would
make any client filter by class to get its own question back.

```json
"crates": [
  {"cls": "BP_Crate_C", "instance": "...", "pos": [x, y, z], "yaw": 90.0,
   "kind": "dismantle",                        // "death" | "dismantle" | "none"
   "items": [["Desc_IronPlate_C", 15], ...],   // biggest first, ties by class
   "slots": 4}                                 // off the component: sized to what went in
]
```

**One class, and the game says so.** `AFGCrate` (`FGCrate.h`) is the whole family — "containers
of items that are spawned on demand when the contents do not fill into the player's inventory,
or when the player dies" — and the two kinds are one actor with a different `mCrateType`, which
is why `CRATE_CLASSES` is a list of one and the kind is read off a property rather than off a
name. All **170 crates across the 67 saves** on the reference machine are `BP_Crate_C`. Not the
item pickups lying beside them: `FGItemPickup_Spawnable` is map-placed loot, and that is the
collectibles layer's business.

**`kind: "none"` is an answer, not a parse failure.** `mCrateType` is a `SaveGame` property, so
UE omits it while it sits at the class default of `CT_None` — and `FGCrate.h` says what that
default means, naming the sibling the enum replaced: `mMapText` is "name of the crate on the map
(before distinction between dismantle and death crates was added)". Measured rather than
assumed: the property does not appear at all below build 433351, and two of the reference
world's own crates were made under builds 201717 and 186638 and still read `none` under 495413.
**125 of the 170 crates** on this machine carry no type and never will, so "unknown kind" is a
permanent, ordinary state of an old crate, and reporting it as a death crate would be inventing
the one fact the save withheld.

**What the save does NOT say, recorded because the obvious question is "is it mine".**
`mCrateType` is `AFGCrate`'s only `SaveGame` property — no owning player, no timestamp, no cause
of death — checked against the install's own `Headers.zip` and against every crate in all 67
saves. Single-player, every death crate is the player's by construction; co-op, this projection
cannot say. An owner field invented here would look exactly like an answer.

**The inventory join is case-folded, and that is not tidiness.** The same component is spelled
`.inventory` on some save versions and `.Inventory` on others — **89 and 81 times** on this
machine — so a case-sensitive test would report every crate present and every one of them empty.
The join itself is by owner instance name, the one `_storage` uses, with the same guard: a
player pawn and 93 crashed drop pods also own a component named `Inventory`, and they go
unclaimed because they are not crate actors.

**An addition, not a correction, and the parity bank says so in one line.** `crates` joins
`POST_11_ADDITIONS["keys"]` and owes no `_unfix`: `inventories` is left exactly where schema 11
put it, so that banked key goes on being compared unfiltered and a crate this parser started
miscounting would still move it on all 31 saves — the difference between 18 costing one line
where 16 cost a function. The rows also join the null-yaw census on the terms schema 17's poles
did: all 170 crates read their rotation, which is why a banked key could take them without
moving. Dict rows rather than a `rows.py` iterator, on the storage precedent's size argument —
**2 of them against 3,085 belt pieces**.

Cost: the fixture diff is one added key and the version number, **480 bytes of 1,571,349**. The
reference world holds 2 — a dismantle crate of 15 Iron Plate, 4 Encased Industrial Beams, 3 SAM
and 2 Rotors in 4 slots, and one of unknown kind holding 17 Coal and 7 Concrete — and the pair
is the whole argument for `kind` existing: one says what it is and one cannot.

### 6.14 Schema 19 — a crate's contents are their own inventory bucket

Schema 19, and the second **correcting** bump after 16 — it adds no key, it moves values
inside one: `inventories` gains a fourth bucket, `crate`, and the death- and dismantle-crate
contents that had summed into `inventories["machine"]` since schema 11 sum into it instead.

The schema-11 bucket rule files any component named `Inventory` on an owner that is neither a
player nor a storage class with the machine buffers, and a crate is exactly such an owner — so
a dead pioneer's pockets counted as material that *exists and cannot be spent*, anonymously,
beside the smelter buffers. Schema 18 gave crates their own `crates` key and deliberately left
the bucket alone (moving a banked value was a separate decision from being able to see a crate
at all); schema 19 is that decision made. The bucket test is membership of `CRATE_CLASSES`
read off the owner's instance name plus the case-folded role `inventory` — the same class list
and the same case fold the `crates` key's own join uses, so the two cannot disagree about what
a crate is. The player pawn and the crashed drop pods, which also own a component by this
name, land where they always did (`player` and `machine` respectively).

What the bucket **means**: recoverable stock lying on the ground. It is deliberately in
*neither* aggregate — `stock()` still excludes it (a crate deletes itself when emptied and
exists because something went wrong; a build plan must not quietly depend on walking back to
where you died), and `machine_buffers()` no longer includes it, which makes that name honest
for the first time. On the reference save the move is 48 items over 6 classes — the two
crates' contents exactly, verified item for item against the `crates` rows.

Because `inventories` is a banked schema-11 key, 19 owes the parity filter a reconstruction:
`_unfix_19` in `tests/test_savparse_parity.py` folds `crate` back into `machine` (a pure
integer addition — cheaper than `_unfix_16`'s re-routing, and with no cancellation blindness,
since nothing is subtracted) so the banked digests still compare on all 31 saves. A pickle
written under 18 disagrees about `machine` and lacks `crate`, so the cache key had to move
with the number, as every correcting bump's must.

### 6.15 Conduit runs — naming them, and why geometry was refused

`domain.world.logistics` contracts the 3,597 conduit actors of `graph["material"]` into 2,198
node-to-node runs in ~15 ms, by actor identity rather than by geometry. `domain.world.conduits`
builds a parallel set of runs from the *drawn line* — `chain:<n>` per belt chain, `pipe:<row>`
per pipeline piece — and those are the ids `search_conduits` prints and `resolve_origin`
accepts. Two views of the same conduit, and **schema 20 is the column that joins them.**

**Pipes joined from the start.** `pipes["segments"]` carries `actorIndex` (schema 14), a
position in `graph["actors"]`, so every one of the reference world's 503 pipe pieces maps to
the actor the physical graph knows it by. A contracted pipe run names itself with the lowest
`pipe:<row>` on it, and following that id lands on a piece of that very run.

**Belts did not, and the geometric substitute was measured before being refused.**
`belts["segments"]` used to be `[chainIndex, classIndex, points, spans]` with no actor:
`extract._belts` read each piece's instance name only to recover its class and then threw it
away. The two groupings are nearly the same size — 1,909 chains against 1,916 contracted belt
runs — but nothing paired them, so the only available join was geometric:

| how the chain was matched to the run | runs matched uniquely (of 1,916) |
| --- | --- |
| nearest placement to each chain end, then paired by that pair of instances | 1,432 (75%) |
| candidate chains within each endpoint building's own port reach | 1,120 (58%) |

That residue was not noise. 360 runs had both endpoint actors placed and still no chain whose
ends resolved to that pair, because a belt end sits nearer a neighbour's centre than its own
machine's; 96 endpoints were `Build_TreeGiftProducer_C`, which is in no placement table at
all. **A 75%-accurate id printed as a fact is a confident wrong claim about which belt to go
and look at**, which is worse than not naming it — so `Link.ident` stayed empty for every
belt, and `factory_health` and `trace_upstream` named the far-end ACTOR instead.

#### Schema 20 — the belt row's actor index

The fix is one column, interned from the same `actor_ix` `_pipes` already reads: the belt row
becomes `[chainIndex, classIndex, points, actorIndex, spans]`, the pipe layout exactly. The
chain names its pieces by INSTANCE and the graph interns that same instance, so this is **not
a match at all** — there is no distance, no tolerance and nothing to tune. The measurements
that matter are therefore not accuracy but coverage and injectivity:

| | reference world |
| --- | --- |
| belt rows resolving to an actor | 3,083 of 3,085 |
| two rows claiming one actor | 0 |
| rows naming a non-conveyor actor | 0 |
| contracted runs carrying an ident | **2,187 of 2,198** — belts 1,905/1,916, pipes 282/282 |
| ident collisions across both media | 0 |
| idents naming no drawn run | 0 |

**The residue, named.** Two belt rows carry `-1`: a parallel pair of Mk3 belts for which the
save records no coupling at either end, so they are in `graph["actors"]` nowhere — absent from
the graph, not mismatched in it, and a run they are not part of cannot want their id. Eleven
contracted runs carry no ident for the mirror reason: their pieces ARE in the graph, with real
couplings, but the game builds them without an `FGConveyorChainActor`, so the projection holds
no drawn line to name. All eleven hang off the FICSMAS gift trees — the same `Build_Tree
GiftProducer_C` the geometric attempt above also fell over. **Every one of the 13 is an
absence the save itself states, not a join that failed.**

**Where it is spent.** All 12 starved machines on the reference world are belt-fed, so
`factory_health`'s "arrives by / at the far end" section previously named a far-end actor for
every real case and a run for none — a feature paid for and not collected. It now names a run
on 16 of the 18 feed rows, covering 11 of the 12 machines. The other two rows are verdict
`NOTHING`: no conveyor arrives at that machine at all, so there is no run in existence to name
and an id there would be an invention. `trace_upstream`'s crossed-run list names belt runs
beside the pipes, which matters because those walks are nearly all conveyor.

**What the bump cost.** The change is confined to `belts`, which the parity filter drops
whole, so it owes no `_unfix_20` — and that was verified rather than assumed: all 31 banked
saves were replayed through `as_schema_11` before and after, and **not one of the 20 keys'
digests moved on any save.** The projection grew 14,780 bytes on 1,571,480 (+0.94%) and the
re-cut takes 3.5 s. The one thing to notice on review is that 20 does not only append: putting
the actor at column 3 to match the pipe layout MOVES schema 15's tangents from column 3 to
column 4, so every reader of a raw belt row had to move with it. Inside a dropped key that is
free; on a banked row it would have owed a reconstruction.

---

## 13a. Replacing the vendored parser

The save parser WAS vendored GPL-3.0, which reached the whole project. It is now deleted and
`src/pioneersav` is the only parser; this section records how that was done and what the
agreement between the two measured, because the diff cannot be re-run. Replacing it started
with knowing what was actually used, and the answer was small: **three entry points**.

| what | used by | replaces |
|---|---|---|
| `readSaveFileInfo(path)` | `header_info` — 9 fields | ✅ `pioneersav.read_info` |
| `readFullSaveFile(path)` | `iter_objects` — levels, headers, objects, properties, destroyed actors | ✅ `pioneersav.read_full_save` — all 20 projection fields exact on all 31 readable saves |
| `ParseError` | one `except` | ✅ `pioneersav.ParseError` |

All three are **wired in and selectable**: `SATISFACTORY_SAVPARSE=own|vendor`, resolved at
import, **defaulting to `vendor`**. Nothing changes for users yet. What the switch buys is
that "the two parsers agree" is a diff someone can run rather than a claim — see *Both ways,
diffed* below.

Its 6,800 lines of `sav_data/` tables are **build-time only** — `tools/gen_*.py` uses them to
produce committed artifacts. Different question, different answer — and one that got harder,
not easier, when the destroyed-actor lists were read: four of those tables (`slug.py`,
`somersloop.py`, `mercerSphere.py`, `crashSites.py`) are location lists for exactly the
collectibles §6.11 can now count, so there is a real feature arguing for GPL data at the moment
the rest of it is being removed. That is the user's call and is written up as one in
`docs/savparse-notes.md` rather than assumed either way.

### What "cleanroom" can and cannot mean here

A strict cleanroom needs an implementer who has never seen the original, and the library is
vendored in this repo. What is done instead is a reimplementation **of the file format** —
a fact about what the game writes, not a creative work — verified black-box: same file in,
same values out. That is the ordinary interoperability route and it is written down here
rather than implied, because the distinction matters and is not mine to certify.

### The header, done

Field order was derived by walking the bytes and checking values against what the game
shows. It is a linear sequence with no offsets to seek by, so `save_header_type` is the
only thing that would announce a change.

The load-bearing detail is the string encoding: an int32 length then bytes, where **the
sign of the length is the encoding** — positive is one byte per character, negative is
UTF-16LE at two — and the count includes the terminator either way.

**Verification, across 67 saves on disk:** exact agreement on all **31** the old parser can
read, and failure on exactly the same **36** — pre-1.0 saves (`saveVersion` 52 and older)
that it also refuses. Same answers, same limits.

The header ends by asserting Unreal's `PACKAGE_FILE_TAG` (`0x9E2A83C1`) is the next four
bytes. Every field is positional, so one wrong width silently rereads the rest and returns
plausible nonsense; landing on the tag is the proof it did not. A patch that inserts a
field is caught there and the error names the versions it was reading.

Tests run against a committed 2 KiB fixture, so they survive the vendored library's removal
and pass on a machine with no game install.

### The compressed body, done

Everything after the header is a run of independently zlib-compressed blocks, each with a
**49-byte preamble**: tag, max chunk size, a one-byte compressor id, then the compressed
and uncompressed sizes **written twice, identically**. The duplication is real on every
chunk of every save checked, and both copies are read and compared — it is a free
integrity check on a format that has no other one.

Verified on all 31 readable saves: **1,194 MB of body inflated in 1.39 s**, none failed. The
reference save is 2.94 MB on disk and 44.4 MB inflated, in 0.047 s. All **9,125** chunks of all
31 saves declare a max chunk size of 131072 with both of their sizes inside it — which is why
that field is checked as a self-contradiction rather than against the constant, since the block
size is the writer's choice and not the format's.

The tag is checked **per chunk**, not once. This project reads autosaves, which are
rewritten every few minutes, so a file torn mid-write is routine — it now fails on the
chunk where the tear is, with the offset, instead of inflating garbage into the object walk
and failing somewhere unrelated.

### The two layers that carry the complexity

The body walk and the tagged property serialiser are 1,907 of the package's 2,969 lines
and all of the format that could not be read off a hex dump. They have a section of their
own, [§13b](#13b-the-object-walk-and-the-property-serialiser), because the argument that
they are *right* is a different kind of argument from the one above: the header and the
chunk stream are proved by a tag landing where it must, and these two are proved by a
length check balancing 2,269,824 times.

### Both ways, diffed

The composition is one module, `pioneersav/save.py`, and it holds two decisions rather than
glue. **The inflated body is retained** — every object slice and every `extra_offset` is an
absolute index into it, nothing is copied, and that is what keeps a 44 MB body at a fifth of
a second. And **there is deliberately no `actorSpecificInfo` attribute**, because
`_lightweight` and `_structures` read it through `getattr(..., None)`: its absence is what
makes them return empty, which is a *visible* gap. A partial decode there would turn an
undercount into a silent one, and a foundation census reporting 40 slabs where 8,347 pieces
are built reads exactly like a real answer.

Getting there also collapsed two exception types into one. `ParseError` moved below `Reader`,
because the commonest failure of all — a walk running off the end of a file the game is
rewriting — was raised by the primitive layer as a bare `ValueError`, fell through the
sidecar's single `except`, and was reported as `{"error": "ValueError"}` with no offset for a
file that was merely mid-write.

**The result, over all 67 files in the save folder, JSON compared leaf by leaf:**

* **31 readable and 36 refused, by both, the same 36**, each reported as `parse_error` on both
  sides. (One of the 36 is not a save at all: `ServerManager_V2.sav` opens `MSGF`.)
* **All 20 projection keys are identical on all 31 saves**, and identical means leaf by leaf,
  not key by key: **zero leaves anywhere where the two disagree on a value, and none present
  under one parser and absent under the other.** The last two to fall were `lightweight_counts`
  and `structures` — **224,530 structure instances in 488 classes**, which came out empty until
  the lightweight blob was decoded. The twentieth key, `removed` (§6.11), was identical on both
  engines from its first run: they reach the same three destroyed-actor lists under different
  attribute names, and the projection sorts before emitting so the byte order cannot differ.
* `n_objects` agrees on every save, 29,734 to 44,643. `Han Solo_270726-215626`: **44,307**
  objects, **566** machine/extractor/generator records, **11,554** material edges, 8,347
  structure instances, `schema_version` **11** — both parsers.
* `--list` over the folder: same 31/36 split, and **zero differences in any header field**.
* Whole sidecar including interpreter start, summed over the 31 readable saves: **vendor
  75.7 s, own 59.2 s** — 1.28×. The per-layer split is in
  [§13b](#13b-the-object-walk-and-the-property-serialiser); the short version is that
  inflation is 2% of a parse and the property bodies are 87%.

Every figure above was re-measured on the finished tree rather than carried forward. That
matters here for a specific reason: the package was edited by several agents in parallel, so
numbers taken mid-way describe code that no longer exists, and a parity claim about code that
no longer exists is not a parity claim.

### Torn files, fuzzed

Autosaves rewrite the save in place every few minutes, so a file read mid-write is routine.
About **60,000 mutations** of two real saves, each classified by outcome rather than eyeballed:
1,750 truncation points, 600 single-bit flips in the chunk stream, every byte of three chunk
preambles, every length field the body walk reads in both committed fixtures, ~30,000 mutations
inside real property blocks, and every int32 of two real headers. **Nothing hung** once the
container-count bound in [§13b](#13b-the-object-walk-and-the-property-serialiser) was in place —
no case took longer than one clean parse — and, after the four fixes below, nothing raises
anything but `ParseError`.

Two results worth keeping. **The realistic tear is caught by one int64**: a file half-rewritten
is a prefix of the new chunk stream followed by a suffix of the old, every chunk of it
individually valid — right tag, matching size copies, clean adler32 — and the only thing
between this project and a confidently-reported chimera of two factories is the body's own
`len(body) - 8`. Spliced at 14 chunk boundaries between two real autosaves, refused every time.
And **everything the property serialiser reads sits behind a per-chunk adler32**, so silently
corrupting the inflated body takes a deliberate re-compression rather than a tear: of 600 bit
flips, 599 were refused and the survivor landed in a deflate block's unused padding bits and
inflated to byte-identical output.

Four defects, now fixed with tests in `tests/test_savparse_robustness.py`:

* **Nested `StructProperty` tags raised `RecursionError`, not `ParseError`.** 29 KB of crafted
  payload was enough; through the sidecar it came out as `{"error": "RecursionError"}` with a
  traceback and no offset — the report `errors.py` exists to abolish, arriving through the one
  door it did not cover. Property lists now count their own depth against 32; the deepest real
  one is 4.
* **A property list terminating early was absorbed in silence.** Every property is size-checked
  but nothing looked at the payload as a whole, so the bytes after the `"None"` terminator
  absorbed anything: overwriting one property's name with the terminator made a
  `Build_ConstructorMk1` read as 2 properties instead of 13 and the sidecar emit **exit 0 and a
  complete projection** — 438 machines, one of them with no recipe and no inventories. Now
  bounded by what 1,243,288 objects actually do: no trailer is shorter than 4 bytes, and a
  *component's* is exactly 8 on 562,556 of them and 4 on the other 5,300, never anything else.
  Injecting the terminator into every component of two real saves is refused 104 times of 104
  and was silent 104 of 104 before. An **actor's** stays unchecked, because an actor may
  legitimately carry any amount and bounding it needs the eight-class whitelist that belongs to
  whoever decodes those bytes — refusing a whole save because a patch taught a ninth class to
  carry data is a worse failure than the one prevented. That is the one stated gap here, and it
  is defence in depth rather than urgent: every byte involved sits behind a per-chunk adler32,
  so only a parser misread or a deliberate forgery can produce it, never a tear.
* **The chunk preamble's `max chunk size` was read and discarded** — 99 of 588 preamble
  mutations undetected, all of them its eight bytes. Checked now as a contradiction (a maximum
  below the sizes beside it), not as the constant 131072, which is the writer's choice.
* **The `PACKAGE_FILE_TAG` proof was skipped when the file ended at the header**, so a save cut
  to exactly its 453 header bytes returned a full `SaveInfo` and failed two layers down at "an
  offset into a body that does not exist".

### Opportunities this turned up

* **Header reads need no subprocess and no decompression.** `read_info` takes a 64 KiB
  prefix. Save discovery and world grouping — which today spawn the sidecar — could run
  in-process.
* **`saveDataHash` is in the header**: two int64s, read without inflating anything. Cache
  validity currently keys on `mtime_ns`, so a rewritten-but-identical autosave invalidates.
  A content hash would not.
* **The grid table is a cell→size map of the whole world** that nothing reads. Seven grids on
  the reference save, and only three hold cells: `MainGrid` at 12800 uu with **1,288** cells,
  `ExplorationGrid` at 20480 with 758, `ExplorationGridFar` at 20480 with 43;
  `LandscapeGrid` (51200), `FoliageGrid` (26500) and `HLOD0_256m_1023m` (25600) are declared
  and empty. `cell_size` is in CENTIMETRES, so `MainGrid` cells are **128 m**, not 12.8 km
  worth something to spatial work.

### The lightweight buildables — where every foundation lives

Foundations, walls, ramps and catwalks are not actors. One `FGLightweightBuildableSubsystem`
carries all of them in the class-specific bytes trailing its empty property list, and
`pioneersav/lightweight.py` (185 lines) decodes it: **224,530 instances in 488 classes** across
the 31 readable saves, 8,347 on the reference save alone. This is the whole of `structures` and
`lightweight_counts`, and it was the last projection field needing the vendored parser.

The record is 162 bytes plus two length-prefixed reference paths — a rotation quaternion, a
position, a scale, seven references of which only the paint swatch and the recipe are ever
populated, two override colours, and three small trailing fields. Its length was derived from
the **stride between repetitions of the recipe path** (a constant 375 bytes over 4,616
consecutive foundations) and its field boundaries from a **per-byte variability map** over those
records: the two colour alphas are the only non-zero floats in the region, which places both
`FLinearColor`s exactly. Full layout and the honest account of what the method cannot settle —
how the always-zero runs are grouped — is in `docs/savparse-notes.md`.

**Two blob versions, and the trap.** saveVersion 52 writes version 2, saveVersion 60 writes
version 4, differing by exactly a trailing `(uint8, int32)`: 370 bytes per foundation against
375. Version 2 is **25 of the 31 readable saves** — the common case, not the legacy one. The
first pass knew only version 4 and *refused* those 25 rather than guessing, and the whole-folder
diff surfaced it immediately as 25 files where the parsers disagreed about readability. Reading
a 370-byte record as 375 would instead have desynchronised thousands of records later, which is
the argument for refusing an unknown version everywhere in this parser.

### What is left, and the verdict

**Nothing is left, and that is now a claim about bytes and not only about classes.** All eight
classes that write trailing bytes are decoded — `pioneersav/lightweight.py` for the foundations,
`pioneersav/trailers.py` (192 lines) for the conveyor chains and their three `RepSize` variants,
power lines, and the circuit and player-state subsystems. Across the 31 saves that is **88,097
records, every one consuming its declared bytes exactly**: 224,530 foundations, 688,282 items
riding on belts, 225,686 belt spline points, 36,773 power lines, and one circuit list and account
id per save.

And the last region that was *stepped over* rather than read — the destroyed-actor list at the
tail of every level's table of contents — is read too, which takes the byte budget from 99.8% to
**100%**: 97,250 of the reference save's 44,376,211 body bytes, 0.22%, and 2,414,398 bytes over
the folder. What that list turns out to be is not a formality; it is the only record of what the
player has collected, and it has a section of its own at §6.11.

Decoding is **lazy**, which is measured rather than stylistic: reading every chain costs 0.46 s on
top of a 2.10 s parse — 22% — which no projection field touched at the time and which schema 12's
`belts` key now pays deliberately, so `actorSpecificInfo` decodes on first access and caches. `_attach_trailer` runs for actors only; calling it on all 1.24 M
objects cost another 5%.

Knowing all eight closed the hole this section used to end on: **an actor's trailer is now
length-checked** the way a component's always was. An actor that is neither one of the eight nor
leaving a plain 4 or 8 bytes warns with its class and byte count — which is what a property list
that stopped early looks like, and it was previously silent. Measured before it was written: over
the 31 saves, 500,350 actors leave 4 bytes, 87,016 leave 8, 88,066 are one of the eight classes,
and nothing is left over.

What the new data is worth: not throughput, which the planner derives from recipes and clocks, but
**where each item physically sits** — a backed-up line becomes directly visible instead of inferred
from machine state as `factory_health` does now — and **the actual routed path of every belt**,
225,686 spline points that nothing in this project has had before.

**Is the own parser ready to be the default? Yes on the evidence, and the decision is still the
user's.** The acceptance test set out here is met: **20 of 20 projection keys leaf-identical on
all 31 readable saves**, no key present under one parser and missing under the other, the same 36
files refused for the same reasons, `n_objects` equal everywhere, 1.16× faster end to end
(vendor 78.2 s, own 67.5 s of sidecar wall over the folder), and 918 tests passing under both
engines. No input has been found — real, torn, fuzzed or synthesized — on which it returns a
plausible-but-different factory.

`vendor` stays the default regardless, and `test_the_sidecar_still_defaults_to_the_vendored_parser`
fails if that moves. **The flip buys nothing by itself:** the licence exposure comes from the
library being in the repository, not from which branch of an `if` executes at runtime. The
decision that matters is the deletion, and the flip belongs to it.

**To delete `sidecar/vendor/`:** flip the default and the tests that pin it; re-diff *after* the
flip, over the whole folder rather than a sample, because the same measurement is the acceptance
test — and bank the vendored parser's projection for every save first, since after the deletion
that comparison can never be run again; strip the last references outside `sidecar/vendor/`
(`extract_save.py`'s switch, `tests/test_savparse_save.py`'s default pin, and prose in
`README.md`, this file and `docs/savparse-notes.md`); and settle `sav_data/`'s licence
separately, since it is build-time input to `tools/gen_*.py` and not on this path — noting that
its four collectible location tables would now buy a real feature, which makes that the harder
half rather than the leftover half. The deletion itself is the user's call and nothing here
should make it for them.

What stays genuinely unknown is in `docs/savparse-notes.md`, and the conveyor chain is no longer
most of it: an adversarial pass over all 31 saves explained the per-segment float, derived the item
ring's capacity from the geometry (`floor(length/120) + 2*segmentCount + 1`, on 51,200 of 51,200),
identified the two remaining ints as ring indices with `-1` as their sentinel, and established that
offsets increase *along* the direction of travel — the reverse of what this file previously said —
leaving only the item's `state` int32 unexplained. What remains is the player state's two leading
bytes, two fields in the lightweight blob, and two questions about the destroyed-actor lists that
no save on this disk can settle: which of the three lists a given actor lands in, and which class
an instance name like `BP_WAT112` belongs to.

## 13b. The object walk and the property serialiser

The two layers `pioneersav` spends its lines on: `objects.py` (747 lines) walks the inflated
body into levels, object headers, one property-block slice per object and the three
destroyed-actor lists, and `properties.py` (1,160) turns a slice into the `[name, value]` pairs
the projection reads. Together they are 64% of the package and all of the format that could not
be read off a hex dump.

They belong in one section because they share one safety argument, and it is not the one the
header and the chunk stream use. Those two are proved by a constant landing where it must —
`PACKAGE_FILE_TAG`, the chunk tag, two copies of a size. These two are proved by
**self-describing lengths agreeing with each other**: every level, every block, every
property and every container declares its own byte count, and the reader is required to land
exactly on each declared end. Nothing is recognised by pattern-matching and nothing is
searched for. That is why an unknown property type costs one property rather than the file,
and it is why the reader can be confident about bytes it does not understand.

### The body, walked by its own lengths

The body opens with an int64 that equals `len(body) - 8`, then a world-partition grid table,
then one record per level. Each record holds a **header block and a property-blob block
separately** — parallel lists, not interleaved — so an object's header and its properties are
matched by index, which is a fact about the format rather than a convenience. Each block is
length-prefixed; each object entry inside the second block declares its own size; each
property inside that declares its own size again. Four levels of nesting, four independent
lengths, and the walk cross-checks them against each other.

Two facts cost the most to establish. **There are two body layouts:** saveVersion 60 opens
with an archive version header (26 fixed bytes plus the branch string: 59 bytes on 1.2.0, 70 on
the anniversary build) and a count-terminated array of `(GUID, int32)` custom
versions (13 of them), and saveVersion 52 — **25 of the 31 readable saves** — goes straight to
the grid table and has no per-level archive headers either. And **object save versions are per
object**: 36, 52 and 60 all occur in one file, because an untouched world-partition cell keeps
the bytes it was written with, and a version-60 entry carries an extra int32 that its
predecessors do not.

Where that int32 sits could not be settled by arithmetic. As a fourth header field or as a
trailer it makes an entry `16 + size` bytes either way, and both readings balance every length
in the file. It took parsing 18,369 version-60 actors' reference lists and asking where the
first property name begins. That is the character of this layer: the lengths have several
self-consistent readings and only the payload's own content picks one.

**The object header identified its own flags word.** Both kinds open with an int32 (1 for an
actor, 0 for a component), three strings, and an unexplained uint32 — which takes **eight**
distinct values over 1,243,288 objects, not the two this file used to claim; `0x280008` and
`0x2C0008` cover only about half of each kind, and differ by `0x40000`,
`RF_DefaultSubObject`. That is UE's `EObjectFlags`, and knowing it is what confirmed the two
header shapes were being told apart correctly rather than coincidentally. Actors then carry
a transform (quaternion, position, scale as `x y z w` / three / three floats) and components a
parent actor name.

One deliberate naming choice: the bytes **do** name a component's class, and it is exposed as
`class_path` and not `typePath`, because `iter_objects` reads `getattr(header, "typePath", "")`
and the projection's class-based branching depends on a component resolving to `""`. Exposing
it under the obvious name would have silently changed which objects every downstream census
counts.

Two claims from the earlier working notes were **wrong and are corrected**: there is no 774 KB
partition table — that figure measured to the first literal `Persistent_Level` in the bytes;
the grid table is ~71 KB and ends at body offset 71578 — and the reference save has **44,634**
objects, not 44,307, which is a different autosave of the same world.

**Measured, over every readable save:** 31 bodies, **86,403 levels and 1,243,288 objects walked
in 7.77 s**, zero warnings, and **zero unparsed bytes** — and nothing stepped over either. The
reference save's 44 MB body walks in **0.231 s**.

The last thing that *was* stepped over is the destroyed-actor list at the tail of each level's
table of contents, and it is now read: **79,363 lists across the 31 saves, 21,038 references,
2,414,398 bytes** (97,250 on the reference save, 0.22% of its body). Two shapes — a bare
`[i32 count][refs]` on a sub-level and one grouped by partition cell on the persistent level —
and nothing in the file announces which, so the reading is proved by **landing**: after the list
the cursor must sit exactly on the header block's declared end, and it does **79,363 times out of
79,363, with 0 mislandings**. Two independent lengths agreeing, the block's and the list's own
counts, is the same argument the rest of this layer runs on. It costs **1–3 ms per save, under
1.1% of the walk and ~0.08% of a parse**, measured by interleaved A/B against a version that
jumps to the block's end. What the list means, and why it is the only record of what the player
has collected, is §6.11; the format, the overlap predicates and the longitudinal check are in
`docs/savparse-notes.md`.

`SaveBody.skipped_toc_bytes` still reports those byte counts. Its name is now historical: it is a
number a caller can see is nonzero, not a confession.

**Verified black-box on all 31 readable saves:** identical level counts, identical per-level
header *and* object counts, identical `typePath` multisets. Object by object on the reference
save — 23,821 actors and 20,813 components over 3,124 levels, the actors covering 184 distinct
class paths — `instanceName`, the actor/component split and `position` agree, **0 differences in
44,634**. The one divergence anywhere is the persistent level's name, `None` in the vendored
parser and `"Persistent_Level"` here; nothing reads level names.

### The property serialiser

Every property announces its own length before its bytes, and that one field is what makes
this layer tractable: an unrecognised type costs that property and nothing else. Two tag
layouts occur, keyed by the *object's* version — UE5 writes a **type-name tree**
(`ArrayProperty(StructProperty(InventoryStack(/Script/FactoryGame)))`, each node a name plus a
parameter count), UE4 writes the same information as fixed tag-data fields. Both are
normalised into one tree, so there is one value reader per type instead of two.

**The flags byte on a version-60 tag is the most useful field in the format**, and it
identified itself from data. `0x10` is set on a `BoolProperty` whose declared size is 0 and
which therefore has nowhere else to keep its value — so that bit *is* the bool, and it is
exactly the "16 means True" that `extract_save.truthy` has always documented without knowing
why. `0x08` is set on precisely the structs whose payload is raw numbers (`Box`, `Vector`,
`Guid`, `InventoryItem`) and clear on the ones written as nested property lists
(`InventoryStack`, `FeetOffset`, `FactoryCustomizationData`).

**The per-property size check is the whole safety argument.** After reading a value the cursor
must be exactly `size` past the payload start. That found every format detail worth recording
here, including two that were expensive: the version-60 terminator is a **bare name with no
type after it** (reading a type tree first turned the four trailing bytes into a string length
and broke 16,445 objects), and `InventoryItem` writes one extra int32 on object version **36
only, not 52** — the single place those two versions disagree, worth 87 pickups.

**Struct bodies are decided by name, not by the flag.** The flag is not reliably per-element:
the foliage subsystem's `mSaveData` is one MapProperty with `0x08` set whose keys are native
`IntVector` and whose values are property lists. Nine native structs cover every save —
`Vector` (three **doubles**, on version-52 objects too, because the width follows the writer and
every readable save is UE5-written), `Quat`, `Box`, `LinearColor`, `Guid`, `IntVector`,
`FluidBox`, `ClientIdentityInfo`, `InventoryItem`. `PlayerInfoHandle` and `UniqueNetIdRepl` are
kept as raw bytes on purpose, so that a genuinely new struct shows up as a warning instead of
hiding among them, and the other 36 struct names are nested property lists.

**What the layer actually faces**, on the reference save: 82,660 top-level properties declaring
**208,548 tags at every depth** (a struct's fields and a container's elements are tags too) in
**19 distinct types**, and **47 distinct struct names** — 9 native, 2 opaque, 36 nested lists.
The top-level distribution is led by `ObjectProperty` 34,139, `StructProperty` 22,301,
`ArrayProperty` 18,260 and `IntProperty` 17,091; `DoubleProperty` occurs only nested, never at
the top. Eight types occur fewer than 30 times in the whole save, which is the argument for
deriving from a census rather than from the first object that parses.

**One honest limit.** UE4's tag data for a map or a set names the element's *property* type and
not the struct behind it, so a struct element on a version-36/52 object is genuinely ambiguous.
Reading it as a property list and keeping the result **only when it lands exactly on the
declared end byte** recovers `mItemsPickedUp`, `mActorsBuiltCount` and
`mItemsManuallyCraftedCount`, and correctly refuses `mSaveData` and two `Guid` sets, which are
skipped by declared size. That is three warnings per saveVersion 52 save and zero on a
saveVersion 60 one — **75 over the whole folder**, in data nothing above reads.

**The escape hatch had a hole, and it was where two things could not be told apart: an
element's length and its container's.** An untagged map element with no reader was skipped to
the *map's* declared end. With pairs still to read, the next key then came out of the bytes
after the map and the next skip pulled the cursor back onto that end — so the size check
balanced and the object parsed, with fabricated keys and `None` values and no error at all.
`FText` inside an array had the same shape. Skipping *forwards* to a declared end is honest and
skipping backwards never is, so that is now the rule, and the last element of a container —
where the container's remaining length really is the element's — still recovers. In the same
family: a container's element count is bounded by the bytes left in its block rather than by a
flat ten million, because the count lives *inside* the payload where the tag's size check
cannot reach it, and a planted count of 9,000,000 read 36 MB of the following objects and took
13.4 seconds to fail. None of the three changed a value: the same 2,269,824 properties, the
same digest, before and after.

### Parity, property by property

**Verified black-box on all 31 readable saves, both parsers in one process:** **1,243,288
objects, 2,269,824 properties, zero objects whose property names or order differ.** Values are
identical on 2,168,837. The 100,987 that differ all fall in one of five classes with a stated
cause — 81,358 in the unread second element of an inventory `Item`, 19,492 in a plain byte's
enum name (`None` against the literal string UE4 writes), 75 skipped and warned about (the same
75 as the warnings, one for one), 31 in a `Guid` spelling and 31 in an empty soft-object
sub-path. Nothing is unexplained, nothing is in a field the projection reads, and the table is
in `docs/savparse-notes.md`.

**Two shape differences have to be normalised before any of that can be compared**, and both
are the ones `extract_save.struct_fields` already documents: a struct value is
`[values, propertyTypes]` here and sometimes bare `values` in the vendored parser, and a
`propertyTypes` entry's tail past `(fieldName, typeName)` is a rendering choice. So the
comparison drops types from the value tree and then compares the `(fieldName, typeName)`
sequence **separately** — 123 properties of 2,269,824 differ there, and **0** where a field both
parsers name is given a different type. Stripping something and not checking it is how a
cosmetic difference hides a real one, and both directions of that mistake happened here: a
first comparison called all 21,247 struct-bearing properties mismatched, and a second, which
compared only the top-level value, buried 2,904 real `Item` differences as "unexplained"
because the disagreement lives three lists deep at `.0.0.1.1`.

### What it costs

One fresh process, reference save (2.94 MB on disk, 44.4 MB inflated, 44,634 objects):

| stage | time | share |
|---|---|---|
| read + header | 0.001 s | — |
| inflate 44.4 MB | 0.047 s | 2% |
| level and object-header walk | 0.231 s | 11% |
| every object's properties | 1.791 s | 87% |
| **total** | **2.07 s** | against the vendored parser's **2.4–2.5 s** |

So the old note that "decompression is not the cost" holds, and its second half needed
splitting: the *walk* is 11% and the *property bodies* are 87%. A third measurement is the
interesting one — **0.53 s of that 1.791 s is retention, not parsing.** Reading each property
block and discarding it costs 1.257 s; keeping all 44,634 costs 1.791 s and about 136 MB. The
projection makes exactly one pass over the objects, so a streaming `read_full_save` would pay
for itself twice over. Not taken: it changes the shape the trailing-bytes work builds on, and
this is a measured opportunity rather than a guess.

End to end, including interpreter start, over the 31 readable saves: **vendor 75.7 s, own
59.2 s.**

