# Planner P4: what was built

Phase P4 of [planner_vision.md](planner_vision.md) §8: track. [planner-p4_contract.md](planner-p4_contract.md)
is the specification; this file says what the backend and the page do, where they depart, and
what was measured.

## Backend

| Piece | Where | What it does |
|---|---|---|
| Startup headroom | `domain/planning/planlog.py` | `headroom_mw` joins `PLAN_SCALARS`: optional number, > 0 and ≤ 1,000,000 MW, `null` clears. `PlanState.headroom_mw` (absent and unreadable values read as `None`, schema stays 1), in `to_dict`/`from_dict` and the `create` body. Merge key `headroom_mw`; undo, restore and `describe_op` (`startup headroom 2,000 MW` / `startup headroom: save default`) work as for any scalar. Not part of `plan_id` |
| Shared words | `domain/planning/commission.py` | `ENERGISED_CAVEAT`, `RANGE_CAVEAT` and `NO_MONITOR` moved here from the text presenter, which re-exports them. `Stage.describe()` (the stage phrase), `Tracking.headline(brief=)` (the text's headline; `brief` is the page's `you are in stage 2 of 4: 76% built (13/17), 13 proven running`), `partition_id(tracking)`, `machine_states(report, …)` (the one health pass). `StageRow` gains `key` and `instances`; `Stage` gains `fill_s` and `waits_for_fill` from its wave |
| One solve | `domain/planning/diff_service.py` | `build_diff_report` takes `stored` (scope, siting and plan_id come from that `PlanState`) and `headroom_mw`, keeps `run` (the `Commissioning`) and `health`. `match_scope` is the scoped diff both services call. A drift note is no longer printed for a plan saved without a plan_id |
| Commission | `domain/planning/commission_service.py` | `build_commission_report(stored=)`: with no `headroom_mw` it uses the plan's (`stored on the plan`), and it matches the waves against the save under the plan's scope so chat gets the same partition as the page |
| Track view | `domain/planning/track.py` | `track_view(g, st, state, biomass=)` → `TrackResponse` from one `build_diff_report`. Rows in the diff's order with `job:` ids, verbs lower-cased, ranges kept, `running` null when no matched machine is monitored, `act`/`targets` ≤ 50 with metre positions, `bbox_m` over act (else matched) plus targets, `selectors` act, else build targets, else matched. Stages with rows, states, fill floor and the box of their allotted machines. `feeders_view` wraps `live_feeders` |
| Asks | `domain/planning/asks.py`, `config.asks_dir()` | `asks/<world>.json` under `filelock.held` with `atomic.write_text`; `version`, per-ask `rev`, numbers never reused, 200 live, text ≤ 200, label ≤ 120, ref ≤ 200. `AboutMissing` (404) beside the contract's `AskError`, `AskMissing`, `AskStale`. `row`, `state_of` and `live` build `AskRow`; `plan_name` resolves at read time |
| Routes | `routers/planner.py`, `routers/asks.py`, `routers/planlog.py` | `GET /api/plan/track`, `GET /api/plan/feeders`; `GET/POST /api/asks`, `DELETE /api/asks/{n}`; `PlanStateBody.headroom_mw`. `asks.router` is appended last to `ALL_ROUTERS` |
| Tools | `tools/planning.py`, `mcp/app.py` | `diff_vs_save(plan=)` passes the recalled `PlanState` and its headroom; `commission_plan(plan=)` uses the stored headroom when none is given. Both journal `plan.view` with `args.view = "track"` and print the renumber note (§6.2). `ui_context` lists asks, marks them seen, takes `answered=` (each id may carry a one-line answer, stored as `answer`). `INSTRUCTIONS` mention `ask:` and `pin:` ids |
| Journal | `routers/asks.py`, `tools/planning.py`, `journal.KINDS` | `ask.add`, `ask.drop` from the web; `ask.seen`, `ask.answered` from chat. No new SSE event name |

## Page

| Piece | Where | What it does |
|---|---|---|
| Track tab | `planner-track.ts`, `planner-core.ts`, `planner.ts` | Third result tab, `#dash=planner/<key>/track[/<n>]`. Re-requests on a new head, a game save (feeders cleared), a labels event when the plan has a scope, the biomass toggle, reconnect and entering the tab. The renumber notice picks its cause in the order rev → headroom → save; a fourth cause (the biomass toggle) reads `the stages changed: …`. When the current stage and the count both stay, it reads `<cause> moved machines between stages; you are still in stage N of M`. Clearing the stage filter with × focuses that stage's row (`data-ctl=track-stage-<n>`) |
| Headroom and scope | `planner-track.ts` | Two buttons, `measured` and `nameplate`. The one the *stage headroom* setting names is pressed while nothing is stored and clears the plan's headroom (`null`); the other stores its figure floored to 10 MW and is disabled when that is 0 or less; the `given` field (`data-ctl=track-headroom`) takes > 0 and ≤ 1,000,000 and shows the stored value only when it differs from the measured one. The scope select is `data-ctl=track-scope` |
| Follow | `planner.ts` | `plan.view` with `view: track` goes to `/track[/<n>]` after the gesture; `section: startup` focuses the Startup order heading (`data-ctl=track-startup`); a stage without a section scrolls that stage's row to the middle of the view once the track has loaded, unless focus is in a field |
| Asks | `asks.ts`, `asks-card.ts` | One ask bar, the first child of `.plan-root` on the list and the workbench. Ask buttons carry `data-ctl` `ask:job:<id>`, `ask:stage:<n>`, `ask:item:<cls>`, `ask:row:<solve row id>`, `ask:node:<node id>`, `ask:plan` and `ask:pin:<n>`, so focus returns to the opener. Open asks show as muted `ask:N` chips on the matching Track job, stage and short rows and on build-list rows. The card drops the `in “<plan>”` part for asks about a plan |
| Map | `nav.ts`, `panel.ts`, `pins.ts` | `nav.onMap(action)` is the one path to the map (`pins.showPin` uses it); `panel.showBox(bbox, {layers})` reveals the layers first |
| Page words | `track.py` | `site_line` gives the On-site card whole metres, `°` and `W × D m`; `page_lines` splits a startup warning into one lower-case line per sentence. Feeder rows carry `region`, and rows sharing a name and region add the last four digits of the instance. The range note on a job appears only when built is a real range |
| Types | `api-shapes.ts` | Every §5.2 name is an alias of the generated `api-schema.d.ts` |

## Departures from the contract

| # | Contract says | Built | Why |
|---|---|---|---|
| B1 | `stage_text` = `Tracking.headline()` | `Tracking.headline(brief=True)`; the text tool keeps the long form | F2 shows the short sentence; the text output had to stay unchanged |
| B2 | `?biomass=` a bool | `include`/`exclude` only | The page's `powerview.biomassQuery` already sends that spelling to every power route; one spelling, one helper |
| B3 | Nameplate is the default headroom (§15 C2) | **measured** is the default for the page and every tool: `diff_service.default_headroom`, source `measured from the save`. The *stage headroom* setting (measured / nameplate) is shared: the page sends it as `?headroom=`, and chat's tools read it ([shared-settings.md](shared-settings.md)). Every tool words the save's figures `measured from the save` / `nameplate from the save` (the old `power_report, nameplate` is gone) | Owner decision 2026-09-30: with nameplate the reference save has no stages at all (116 MW free against a 392 MW minimum slice). `commission_plan` names nameplate as the safe bound beside a measured order |
| B4 | `AskError`, `AskMissing`, `AskStale` | adds `AboutMissing(AskError)` and `asks.row`/`asks.state_of` | A 404 for an unknown `about.plan` needs its own type; the routes and the tool build rows the same way |
| B5 | `scope_error` text | `“<name>” has no machines in this save` | The F4 wording, so the page can show it as it comes |
| B6 | Renumber note on every `commission_plan(plan=)` | not when the call passes its own `headroom_mw` | That call deliberately partitions differently; recording it would announce a renumbering on the next ordinary read |
| B7 | `age_note`, `drift_note`, caveats, notes and row notes are the text tool's words | page-facing short forms (contract §5.2) | The long forms carried save ids, issue codes and property names onto the page |

## Measured (2026-09-28, copy of the user-data backup, newest save, warm process)

| Call | Measured | Budget |
|---|---|---|
| `track_view`, 3 stored plans, nameplate / 2,000 / 6,372 MW | median 10.0–14.4 ms, max 15.6 ms | – |
| `GET /api/plan/track` over HTTP | warm median 30–32 ms, max 41 ms (a bare `GET /api/asks` is 31 ms on the same client) | p95 ≤ 60 ms |
| Track payload | 7.6–11.2 kB at nameplate (no stages), up to 22.5 kB with 3 stages (`north oil rig`, 11 rows); 51 kB for a 33-job plan, where the matched-machine `selectors` of rows with no action dominate. Render stays at 3–14 ms | ≤ 64 kB (was 40 kB) |
| stages on the reference save | nameplate 116 MW: none (minimum slices 392–644 MW); 2,000 MW: 2–3; 6,372 MW: 1–2 | – |
| `feeders_view` | 0.69–0.87 s (19 feeders) | on demand only |
| `push_ops set headroom_mw` | 26–40 ms | – |
| `POST /api/asks` / `DELETE` | 58 / 35 ms | – |
| chat `ui_context` → `ask.seen` on a second SSE stream | 0.41 s | ≤ 1 s |
| `ui_context(answered=)` → `ask.answered` on the stream | 0.51 s | ≤ 1 s |
| `diff_vs_save(plan=)` → `plan.view` on the stream | 0.50 s | ≤ 1 s |

## Open

- `manage.duplicate` builds the copy field by field and does not carry `headroom_mw`; a
  duplicate starts at the default headroom.
- ~~The *stage headroom* setting is per browser.~~ Shared since 2026-09-30: the page and chat
  read one server-side value ([shared-settings.md](shared-settings.md)).
- A refused undo still words the conflict as `you <value>, <actor> set <value> in vN`, the
  grammar shared with chat; for the page both sides can be the page. A cleared headroom reads
  `save default` there, never `none`.
- The `nodes` layer passed to `showBox` for rows with targets names no layer; node layers are
  per resource and on by default.
- Not checked in a browser: a game save re-requesting Track (A11) needs a writable save
  directory, and a scope with no machines left (A7, second half) does not occur in the
  reference data.

## Verified in a browser (2026-09-28, merged branch)

Headless Chrome at 1440×900 and 390×844 against the merged server and a copy of the user-data
backup. A1–A10 and A12–A20 as in contract §13, apart from the two items above. Measured: follow
to `/track` 0.1–0.4 s after the tool returns; feeders 0.77 s; a stage pick redraws the planner
in 12–15 ms (9-job plan); `ui_context` → `seen by chat` on the page within the 1 s budget.

## How built is found

`domain/planning/built.py`, reached through `diff_service.match_scope(stored=)`; contract §17
is the specification. Detection is derived and never written.

**Position seeds; clusters decide.** A raw radius cannot tell two factories apart: 300 m single
linkage merges the oil plant and the main base, and a region is ±256 m advisory. So position
only picks **seeds**: machines whose job (building and recipe, or generator building) is in the
plan and that stand in the search area (§17.2). Each seed's coherence cluster
(`WorldState.proposals`, never merging two hand-named factories in the leave-one-out tests)
then adds its other machines on the plan's jobs. Clusters are capped at 250 m before
dependents attach and **extractors never grow a cluster**: grown from the three pumps on
`spire-coast-e1`'s nodes, the rule pulled in eleven refineries 1.4 km away. Extractors count on
the plan's own nodes when their owner is one of the counted clusters or a cluster that makes
nothing; water pumps count when they belong to a counted cluster.

**Owners and the one hard rule.** Every counted machine has an owner: its named factory, else
its cluster. Owners are ranked by fit (rate overlap with the plan over the union of the two,
so a big neighbour with the same recipes loses to the small factory that is the plan). A
machine another named factory holds **never counts**; the line names it instead
(`8 matching machines nearby belong to “temporary iron line”`). A new factory is a new
factory: a plan does not grow into an existing named one unless the plan is pinned to it.

**Confidence.**

| | Rule |
|---|---|
| nothing | no seed at the site; `node_owner` says who taps the plan's nodes |
| unsure | the top owner is a named factory holding under 10% of the plan's rate, or another named factory holds at least 20% with half the top's fit, or the excluded machines cover more of the rate than the counted ones |
| sure | the top owner is named, holds at least 80% of the counted rate, nothing was excluded, and the area is not only nodes |
| likely | everything else (an unnamed cluster, a nodes-only area, excluded neighbours) |

Unsure counts a range: low is the counted machines no factory name holds, high adds the top
and rival factories.

**Rate.** Jobs count by rate (docs/planning.md, "Counting by rate"); the progress figure is
machines at the plan's clock, capped per job at the need, and the percent is the covered share
of the planned rate.

### Measured (2026-09-30, newest autosave of the reference world, 15 named factories)

Each named factory stood in for a plan wanting exactly its machines, sited at its centroid:

| Rule | Exact (precision = recall = 1) | Misses |
|---|---|---|
| clusters grown from every seed, extractors included | 10 / 15 | tier 1&2, tor, copper, scratch area, concrete, temporary iron line |
| extractors never grow | 11 / 15 | tier 1&2 0.93, tor 0.96, scratch area 0.92, temporary iron line 0.17 |
| **plus: another named factory never counts** | **14 / 15** | tor factory 0.96: three unnamed machines inside its own cluster, on its recipes |

Recall is 1.00 on all 15. `tests/test_built.py` holds this on the committed fixture world, and
tests the same-spot pair (tier 1&2 around temporary iron line) with the small factory's name
removed, so the rule is not checked against the label it reads.

A 30% partial build was found whole from a site on its centroid for 14 of 14 factories with
three or more machines, 11 of 14 at 100 m off, 3 of 14 at 200 m: the site must be where the
plan is built, which is how plans are built here (at the pin, one plan one spot).

The stored plans: `spire-coast-full` (a region) is not placed; `spire-coast-e1` finds nothing
at its site, and its three crude oil nodes already feed an unnamed cluster; `north oil rig`
(13 nodes) is unsure between “oil setup” (3.6% of its rate) and an unnamed cluster, 15–43 of
801 machines. Detection costs 0.3–0.6 ms per plan once the clusters exist (about 0.4 s once
per save, shared with the map), plus 30 ms when an unnamed cluster needs its suggested name;
`GET /api/plan/built` for the three plans is about 90 ms cold, cached after.

### Open

- Two plans on one site are not allocated between each other (design §3.8): a machine can
  count for both.
- `ui_context`'s focus line does not repeat the built line; `plan_log` shows the new
  `count as built: …` words.
- The somersloop boost on a machine is not in its rate (one record, field unverified).
