# Roadmap: what to build next

38 candidates from five lenses, three judges, generated 2026-08-05. After deduplication: 26
distinct items, in the order to actually work them. Defects live in [backlog.md](backlog.md);
this is the register of things that are not broken and might still be worth building. The parked
features it sequences against are §21 and §22 of [parked.md](parked.md).

**Status, 2026-08-06.** Section 1 is history — all eight shipped within a day of being written,
and their entries below are kept for the constraints they attached, every one of which the
shipped code carries. Each of the three big bets in §2 had a spike land its primitive; none has
its consumers. §3 through §7 are still live, and §5 — what is deliberately not being built — is
the section most likely to be re-proposed by someone who has not read it.

The one structural finding, one layer down from the backlog's: **a domain function is not
finished until something calls it.** Nine of the strongest candidates were the same shape, a
correct computation reaching nobody — `measured_share` in the flow loop, `_SOURCE_OF_TYPE`
outside `byproducts.py`, `installed_build()` with no runtime caller, `GameData.warnings`,
`water_volumes()` behind a 30-pump gate, `Field.at()` where `Field.window()` belonged. None grew
the tool count. That is why they crowded the top, and it is why `domain/world/logistics.py` and
`domain/world/timeline.py` — landed yesterday, called by nothing — are the shape to watch next.

---

## 1. Done: the eight "do next" items, all shipped 2026-08-05/06

Kept as a record because each carries an objection that is now an invariant of the shipped code,
not an argument about whether to ship.

| # | What it answers | Shipped as | The constraint it carries |
|---|---|---|---|
| 1.1 | "What is the next HUB milestone, can I afford it, what am I short of." Scored 9/9/9 — the only unanimous top, from three unrelated axes. | `2877560`, `SchematicLadder` in `domain/progression/ladder.py`, walked by the HUB and MAM tools alike | It is the third view of one ladder. It landed inside the progression family with one shared view vocabulary, which is what kept it from being a fifth `kind=`. |
| 1.2 | What a factory is actually making per minute, beside what it could. Arrived independently from two lenses — the strongest duplicate signal in the set. | `dca4191`, plus `ea11dae` for the rule | The safe direction FLIPS between power and output: charging an unmonitored machine in full is conservative for one and optimistic for the other. Unmonitored rate is parked in its own `unmonitored_` bucket (`domain/factories/query.py:78`), never folded in, so measured production is a floor. |
| 1.3 | "What does Phase 4 still need", asked again after the first delivery into the elevator. | `ac0f4ae` | The subtraction is unsound wherever `paid_off` was already non-empty when the snapshot froze, so it is scoped to the target and says so. It could not be validated on the live save — the synthetic fixtures, including the case that must NOT be subtracted, are `tests/test_phase_and_shards.py:139-235`. |
| 1.4 | What grants a locked recipe, wherever LOCKED already appears. | `17015c4`, `3ffc3cf`; `core/gamedata/unlocks.py` | A recipe can be granted by several schematics, and silently picking one is the same half-truth being fixed. `granted_by_label` prints `"<name> (first of N)"` (`unlocks.py:97`). |
| 1.5 | Where the broken machine is, on the map, instead of a table of instance ids. | `4fa7e0f` | `blocked` is not a fault on a mature base — 326 machines are blocked world-wide. Painting them the same red as starved cries wolf, so the two carry different weights. |
| 1.6 | Water, without being told there is none. `search_resource_nodes(resource="Water")` used to answer "0 free and reachable" on a map that is 32.4% water. | `c97853e` | Open water carries no node, and the answer has to say that rather than imply a missing table. Part (b), a `water_m`/`lift_m` column, was correctly held for the terrain window and arrived with it (`cfee408`). |
| 1.7 | A coal plant with a broken water pipe, told from one running flat out. | `a6305d8` | A load-following generator legitimately reads below 1.0, so the empty-tank test leads and uptime only corroborates. All 52 generators read 1.0 today; the value is naming *which* plant on the evening one does not. |
| 1.8 | Whether the pad you reserved clears the belt and fits between the cliffs. | `898e77f`; `routers/plans.py`, `frontend/src/plans.ts` | Worth a lot the evening a siting is stored and nothing the other four. `siting.contains_cm` was already written as the explicit inverse of the frontend's `footprintCorners` — the two halves agreed about the rectangle and had never met. |

---

## 2. Big bets: every primitive landed, no consumer did

**2.1 Read the heightfield as an AREA** — *synergy + prior-art lenses independently; subsumes
three other candidates.*

- BUYS: the only build-site answer in the ecosystem grounded in the game's own 1 m terrain.
  Roughness under a pad in `rank_build_sites`, a terrain line on a Siting's footprint, water
  distance and lift as real columns.
- SHIPPED (`214facf`): `Field.window(x0,y0,x1,y1)` over the already-lazily-decoded rasters,
  returning z-range, slope, %submerged and %no-data. Two consumers so far —
  `domain/spatial/ranking.py:119` and `domain/world/water.py:149`. Building the primitive first
  is what stopped three callers minting three area reads.
- LEFT: the terrain line on a Siting's footprint, and **siting by the whole bill**
  (`rank_build_sites(plan=)`), which must ship as a SECOND TABLE of the plan's other raw items
  and their nearest fields, explicitly not folded into the score.
- STANDING CONSTRAINT: buildable ≠ flat. Stilts are normal play and cliffs are sometimes
  desirable, so every term is published as a raw component with a small default weight, never as
  a veto. And the derived water-extractor COUNT is the one output the field cannot support:
  "water is 340 m away and 46 m below" is a measurement, "ten Mk1 pumps" is a placement claim.
- PARTLY DISCHARGED: this was pitched as deleting `WATER_EXTRACTOR_CAP_ASSUMED = 200`, a
  hardcoded constant inside every solved plan whose own comment admitted it stood in for a site
  the model could not see. `cfee408` measures the cap where the plan actually stands and makes
  the constant the fallback that says it is assumed. The constant is not gone; it is now honest,
  and finishing the job means the measurement becoming the default.

**2.2 §21, the physical logistics graph.**

- Both sequencing constraints this exploration added were honoured, and one of them changed the
  project. **Constraint A, land the hypertube `role` tag first**, shipped as
  `core/saveio/ports.py` (`86d466c`): 126 hypertube connections sat in `graph["material"]`
  indistinguishable from belts, with exactly one consumer that knew to filter them, and §21's
  join would have inherited that lie.
- The spike then **disproved §21's premise**. The port-level geometric join it prescribed was
  measured at 28.7% wrong; the contraction that replaced it is exact. The prescription has been
  struck from parked.md §21 rather than annotated, because a top-down reader would have built it.
- SHIPPED: `domain/world/logistics.py`. LEFT: everything that consumes it — `trace_upstream`'s
  physical mode and `factory_health`'s evidence line.
- **Constraint B, head lift is §21's first slice, not a separate project**, still stands and is
  now cheap: `trunks.pumps()` does the arithmetic for imagined pipes while `conduits.py` carries
  z, via-devices and the `FGPipeNetwork` id for real ones, and the network-wide walk it needed is
  what landed. Note the counter-argument in its favour: §21 will NEVER find this failure, because
  there connectivity is exactly what is fine. AGAINST head lift generally: it is a lower bound —
  no friction, no pipe head, no backflow — and must say so, or it declares a working pipe broken.

**2.3 §22, the timeline** — *worth more than when it was parked.*

- Measured rates (1.2) changed its value and have now shipped, so the argument is live: a 300 s
  window per save is a reading; a series of them is a rate. Sink points-per-minute, "at this
  rate, N hours", and "when did this stop" become honest only once there is a second sample.
- SHIPPED: `domain/world/timeline.py` — the row, the identity key that survives autosave
  rotation, the index file, the pairwise comparison. Measured at 112 s to index 50 saves cold,
  for 101 kB. LEFT: a tool, and the window note every answer must print with it.
- THE HARD COROLLARY, undischarged: **do not build ETA-from-a-300 s-window before §22 is
  consumed.** That is a forecast dressed as a reading, and it is the exact class of
  confidently-wrong answer this project exists to avoid.

---

## 3. Cheap wins while passing through

One of seven is done. The rest are unchanged and still cheap.

| What | Effort | Why now | Status |
|---|---|---|---|
| Hypertube `role` tag on material edges | hours | Precondition for §21. Fixed a live lie to five consumers. | DONE `86d466c` |
| Warnings channels reach a reader | hours | `GameData.warnings` reaches a bare `len()` (`tools/resources.py:32`); `projection["warnings"]` has zero consumers anywhere. Two note blocks in `world_summary` and the save resource. | open |
| Unread-class census | hours | Fifteen lines mirroring the null-yaw census. **Must land in the same commit as the warnings readers or it is invisible by construction.** Needs a hand-built "seen and dismissed" allow-list or its first run is 4,300 berry bushes. | open |
| Node-table forward build check | hours | `skew_from_meta` returns `None` unconditionally on the shipped table — the guard is structurally incapable of firing on the event it exists for. `installed_build()` returns the exact pin string every artifact records and still has no runtime caller. Ten lines closes it for five pinned artifacts. | open |
| "These 8 machines are wired to nothing" in `factory_health` | hours | The one salvage from the killed electrical-islands pair. `stalled` said "power, or a monitor that lies" and could not check which. | DONE `deadb0b`, widened here from degree zero to *no generator reachable over the wires* — the claim and its two refusals are [save-projection.md](save-projection.md) §6.1a |
| Comment-budget failure message | 15 min | The cheapest remedy for a failing ratchet is deleting an explanation, in a codebase whose prose is the design record. Change the message to name the intended remedy. Note: caps are per-*directory*, so "raise this file's cap" is not currently available. | open |
| `docs_path()` raises instead of returning `G:\SteamLibrary` | hours | A function lying rather than failing (`config.py:25`). | open |

---

## 3b. Split off the fluid work, 2026-08-06

Both came out of reading the FICSIT Plumbing Manual (public domain, on the wiki) while
designing head lift. Neither belongs in the head-lift work, and both are worth doing.

**Pipe build review — a build-advice tool, not a diagnosis.** Two checks the manual names as
common mistakes, both decidable from the contracted runs and machine positions we already
have, and neither of which any tool in the ecosystem performs:

- *A feed pipeline built below its machine inputs.* Manual, Lesson 8: always build the feed
  line level with or above the inputs, because head lift is the same for every pipe in a
  network and no machine is fed until levels equalise — so a feed line below its machines
  starves all of them the moment the level drops.
- *Pumps stacked with no vertical separation between them.* Pump head lift does NOT stack
  pump-to-pump (three Mk2 pumps in a row give 50 m, not 150); it stacks only with gravity
  gained after the pump. Pumps within no vertical distance of each other are concrete spent
  for nothing.

Output is advice about what was built, not a fault, and it wants its own tool rather than a
column on a health report. Effort: hours once the head-lift model exists, because both reuse
its geometry.

**Byproduct remedies in `explain_byproducts`.** Manual, Lesson 9: the aluminium water loop
is the canonical case, and the LP is mathematically right about it while being physically
naive — it balances the byproduct as an equality and has no idea the second refinery clogs
when its output is fed back to the first. The manual ranks four remedies: underclock the
extractors and cap them with a valve (only stable at 100% efficiency), dedicate refineries
to the byproduct (most stable), feed it to other machines, or package and sink it (worst).
`explain_byproducts` should carry them, so a plan that strands a byproduct says what players
actually do about it. This is a planner change and has nothing to do with the head-lift
model; it is listed here so it is not lost.

---

## 4. Risks to defuse, by probability × damage

Two are defused. Four are live, and their ordering is unchanged.

1. ~~**Eleven concurrent save parses every autosave.**~~ **DEFUSED.** Measured at 11 concurrent
   GETs, every one 3.9–4.35 s. Closed by single-flight around the cache miss (`c5e2aae`,
   `core/singleflight.py`) holding the lock over bookkeeping only and never across `communicate`,
   plus the paired win nobody proposed: **`WorldState`'s five derived views memoised by
   projection identity** (`domain/world/state.py:_DERIVED`), which was ~0.8 s per layer per
   request of graph, structures, pipe flow, conduit runs and proposals. The whole study is
   [residency.md](residency.md).
2. ~~**`phase_requirements` goes blind on the next elevator delivery.**~~ **DEFUSED** by 1.3.
3. **Node table silently stale on the next game patch.** p ≈ twice a year, damage = an evening of
   confidently wrong node answers, which is the standing "map updates move nodes" failure. See §3.
4. **Both invariant channels unread.** p = whenever a patch breaks normalisation; damage =
   publishing a smaller world and calling it the world, silently. See §3.
5. **`/api/worlds` publishes the Windows username and SteamID64, and the server accepts any Host
   header.** **DEFERRED 2026-08-09**, on the maintainer's own environment: the machine sits behind
   NAT on a household network with two people on it, so the exposure that matters there is not
   present. Recorded rather than closed, and with one honest asterisk — **that reasoning is one
   network's, not the code's.** The repo is public, and a stranger who clones it inherits the
   same behaviour without inheriting the NAT, so this stays on the list for the stranger-facing
   cluster in §5 and should be revisited before the project is advertised anywhere.
   **Partly closed 2026-09-27:** every non-GET request now passes a Host/Origin guard
   (frontend_vision.md §9.2), added with the first write routes. Reads still accept any Host.
   Low p in any case — it needs a hostile page open while the map runs, and DNS rebinding
   defeats the absent CORS header. Fix is a Host allowlist with an env override. Caveat:
   dropping `path` is costlier than pitched, because `routers/world.py:41` documents it as the
   pin `?save=` takes back verbatim, so schema, frontend and the filename resolver's
   cross-world ambiguity move together.
6. **`cohere.propose` is between quadratic and cubic.** 0.35 s at 581 machines, 10.92 s at 2,324,
   extrapolating to ~90 s at 4,600. p of hitting it this year: low, and risk 1's memo means it is
   paid once per save rather than per request. What remains is a scale assertion with 10×
   headroom — not paging.

---

## 5. Explicitly NOT worth building

Unchanged, and the section most worth reading before proposing anything.

- **Electrical islands / per-grid power** (two candidates, one from the synergy lens and one from
  prior art). **Measured false.** `machine_components("power")` with towers IN returns 3
  components sized 563/5/5 — one grid. The "six islands" both pitches cite is `identity.py`'s
  towers-*removed* clustering heuristic, a factory-identity choice, not an electrical one. A
  per-island ledger would print one row and two strays. Salvage the single line it does expose
  (§3, item 5) — now done, and it turns out **the two strays were the finding**: five and five
  are the two rows of Oil Refineries on `HL_BUFFER_A..D` that are wired to each other and to no
  generator. A ledger would have printed them as circuits drawing 0 of 0 MW; `factory_health`
  names them as a build to finish. save-projection.md §6.1a.
- **The starved→producer chain as a new report.** "What feeds this" would then have four answers:
  `trace_upstream`'s recipe walk, `factory_health.starved_of`, this chain, and §21's physical
  graph. Build instead one health column on the walk `trace_upstream` already does. No new tool,
  no candidate list a reader must be taught to distrust.
- **`assess_fit` per-floor free cells.** The deliverable is not a column; it is reconciling
  floors' 4-connected flood fill against fit's slab keying, and DESIGN §16b already ruled the
  other way (one slab spans 287 m of Z). Until that unit question is settled, this produces a
  free-cell count off the wrong deck — worse than the hedge it replaces.
- **Blueprints as a coherence signal.** The projection key and a `blueprint:<name>` selector are
  ordinary. Adding a sixth term to `cohere.py` means re-running the measured F1 evaluation that
  settled complete linkage, and a blueprint is a *copy*, not a factory — four copies 40 m apart
  are one factory. That is the failure mode the clusterer already has.
- **Map markers as a map layer.** Six markers on a 329 h world, four still called "New Marker".
  The idea's own evidence concedes it cannot carry a feature. If anything, emit the key and let
  `describe_location` name a nearby marker.
- **`mActorsBuiltCount` as a work item.** `Current` reads −137 for ConveyorPole against 795 in
  headers; 58 of 118 classes disagree. This is one caveat and one test inside the
  statistics-subsystem commit, not a deliverable. Standalone it is a test guarding a ledger
  nobody built.
- **Viewport paging.** Weeks across every router and the frontend, for a page that is 0.54 s warm
  at 581 machines.
- **CI.** Changes no answer the player gets. Contributor infrastructure for a repo with one contributor.
  (Counter, recorded: it is the only thing that makes a stranger's PR mean anything, and 44 of
  111 test files are integration-marked. Revisit only if the repo gets contributors.)
- **The wiki/MCP directory listing.** On the player-value axis this is negative: it spends the maintainer's
  evenings on other people's modded and Linux installs. If ever done, it must come after the
  modded-save note and a README install step, per its own risk section.
- **Co-op / dedicated-server support**, **the README transcript**, **the README's `data/local`
  honesty step.** All correct, all stranger-facing, none changes an evening. Do them the week
  before the repo is advertised, not before.

---

## 6. Where the judges disagreed, and what about

**The big one: electrical islands, scored 10 and 2.** The coherence judge scored it top of the
entire set — an existing function called without its skip-set, repairing three consumers. The
player-value judge *ran it* and got 563/5/5. The disagreement is really: **is the existence of a
call site evidence that the answer is interesting?** It is not. Measurement wins. This is the
sharpest argument in the set for scoring against the live save rather than against the code, and
the §21 spike then made it twice over: a prescription that had survived two write-ups died to one
measurement.

**Honesty and insurance work: 9/9 from feasibility and coherence, 2–3 from player value.**
Warnings channels, the unread-class census, the node-table forward check, modded-save honesty.
The disagreement is about who the user is: the one player tonight, or the codebase in six months.
Resolution: they are cheap enough that the objection does not bite — do them as passing work,
never as a sprint. **Except modded-save honesty**, where the objection is decisive: `is_modded` is
`False` on this header, so it protects strangers only, and belongs with §5's stranger-facing
cluster. (One correction to its evidence: `power_report` *does* publish unknown generator classes
as `unmodellable`; the silent skip is machines and extractors only.)

**Head lift: 7 / 5 / 8.** Really an argument about how close §21 was. The feasibility judge named
it the highest abandonment risk in the set, because it needed §21's end-joining walk. The
prior-art judge is right that it is the one thing nothing else in the world can compute. Resolved
by sequencing rather than by choosing — and the walk has since landed, which retires the
feasibility judge's objection without settling the value question.

**The starved-item chain: 8 / 7 / 4.** Repair-loop value against surface count. Both are right;
the column-on-`trace_upstream` version is the answer to both.

**Siting by the whole bill: 6 / 4 / 7.** The feasibility judge is correct that as pitched it is a
multi-resource siting engine sold as a column, depending on two unbuilt things. One of the two is
now built (§2.1). The second-table form is what survives.

**Unanimity is worth naming too:** the milestone ladder scored 9/9/9 from three different axes,
and measured rates arrived independently from two lenses. Independent discovery from unrelated
angles was the strongest signal in this dataset, and both shipped inside a day.

---

## 7. Sequencing against the parked work and the deferral

- ~~**Hypertubes before §21.**~~ Discharged: the role tag landed first, as it had to.
- **Head lift and the starved chain are slices of §21**, not competitors. Head lift is the fluid
  slice §21 would otherwise never reach, because there connectivity is fine.
- **Measured rates before §22, and before `supplied_from`.** The first half is done. The second
  is not: `build_scenario(supplied=)` exists and nothing fills it, and filling it from a
  *nameplate* surplus feeds the planner a rate a starved factory does not deliver. Same reason as
  the ETA rule: no forecast from a single window until §22 supplies a second sample.
- **Terrain window before water columns, before siting-by-bill, before the extractor cap.** One
  primitive, four readers, in that order. The primitive and the first two readers are in; the
  last two are not.
- **Transport networks:** nothing found here raises their priority. Trains, drones and trucks did
  not appear in a single one of 38 candidates. But note the asymmetry — hypertubes are the one
  transport network already fully present in the save, and their treatment was a defect in a
  shared layer rather than a network feature. Fixing them did not reopen the deferral.
