# Planner P4: what was built

Phase P4 of [planner_vision.md](planner_vision.md) §8: track. [planner-p4_contract.md](planner-p4_contract.md)
is the specification; this file says what the backend does, where it departs, and what was
measured. The page's half is added by the frontend phase.

## Backend

| Piece | Where | What it does |
|---|---|---|
| Startup headroom | `domain/planning/planlog.py` | `headroom_mw` joins `PLAN_SCALARS`: optional number, > 0 and ≤ 1,000,000 MW, `null` clears. `PlanState.headroom_mw` (absent and unreadable values read as `None`, schema stays 1), in `to_dict`/`from_dict` and the `create` body. Merge key `headroom_mw`; undo, restore and `describe_op` (`startup headroom 2,000 MW` / `startup headroom: nameplate`) work as for any scalar. Not part of `plan_id` |
| Shared words | `domain/planning/commission.py` | `ENERGISED_CAVEAT`, `RANGE_CAVEAT` and `NO_MONITOR` moved here from the text presenter, which re-exports them. `Stage.describe()` (the stage phrase), `Tracking.headline(brief=)` (the text's headline; `brief` is the page's `you are in stage 2 of 4: 76% built (13/17), 13 proven running`), `partition_id(tracking)`, `machine_states(report, …)` (the one health pass). `StageRow` gains `key` and `instances`; `Stage` gains `fill_s` and `waits_for_fill` from its wave |
| One solve | `domain/planning/diff_service.py` | `build_diff_report` takes `stored` (scope, siting and plan_id come from that `PlanState`) and `headroom_mw`, keeps `run` (the `Commissioning`) and `health`. `match_scope` is the scoped diff both services call. A drift note is no longer printed for a plan saved without a plan_id |
| Commission | `domain/planning/commission_service.py` | `build_commission_report(stored=)`: with no `headroom_mw` it uses the plan's (`stored on the plan`), and it matches the waves against the save under the plan's scope so chat gets the same partition as the page |
| Track view | `domain/planning/track.py` | `track_view(g, st, state, biomass=)` → `TrackResponse` from one `build_diff_report`. Rows in the diff's order with `job:` ids, verbs lower-cased, ranges kept, `running` null when no matched machine is monitored, `act`/`targets` ≤ 50 with metre positions, `bbox_m` over act (else matched) plus targets, `selectors` act, else build targets, else matched. Stages with rows, states, fill floor and the box of their allotted machines. `feeders_view` wraps `live_feeders` |
| Asks | `domain/planning/asks.py`, `config.asks_dir()` | `asks/<world>.json` under `filelock.held` with `atomic.write_text`; `version`, per-ask `rev`, numbers never reused, 200 live, text ≤ 200, label ≤ 120, ref ≤ 200. `AboutMissing` (404) beside the contract's `AskError`, `AskMissing`, `AskStale`. `row`, `state_of` and `live` build `AskRow`; `plan_name` resolves at read time |
| Routes | `routers/planner.py`, `routers/asks.py`, `routers/planlog.py` | `GET /api/plan/track`, `GET /api/plan/feeders`; `GET/POST /api/asks`, `DELETE /api/asks/{n}`; `PlanStateBody.headroom_mw`. `asks.router` is appended last to `ALL_ROUTERS` |
| Tools | `tools/planning.py`, `mcp/app.py` | `diff_vs_save(plan=)` passes the recalled `PlanState` and its headroom; `commission_plan(plan=)` uses the stored headroom when none is given. Both journal `plan.view` with `args.view = "track"` and print the renumber note (§6.2). `ui_context` lists asks, marks them seen, takes `answered=`. `INSTRUCTIONS` mention `ask:` and `pin:` ids |
| Journal | `routers/asks.py`, `tools/planning.py`, `journal.KINDS` | `ask.add`, `ask.drop` from the web; `ask.seen`, `ask.answered` from chat. No new SSE event name |

## Departures from the contract

| # | Contract says | Built | Why |
|---|---|---|---|
| B1 | `stage_text` = `Tracking.headline()` | `Tracking.headline(brief=True)`; the text tool keeps the long form | F2 shows the short sentence; the text output had to stay unchanged |
| B2 | `?biomass=` a bool | accepts `true`/`false` and `include`/`exclude` | The page's `powerview` helper already sends `biomass=include|exclude` to every power route |
| B3 | Headroom source `"nameplate from the save"` | used by Track and `diff_vs_save`; `commission_plan` keeps printing `power_report, nameplate` | The commission text is unchanged; its measured-headroom note keys on that source |
| B4 | `AskError`, `AskMissing`, `AskStale` | adds `AboutMissing(AskError)` and `asks.row`/`asks.state_of` | A 404 for an unknown `about.plan` needs its own type; the routes and the tool build rows the same way |
| B5 | `scope_error` text | `“<name>” has no machines in this save` | The F4 wording, so the page can show it as it comes |
| B6 | Renumber note on every `commission_plan(plan=)` | not when the call passes its own `headroom_mw` | That call deliberately partitions differently; recording it would announce a renumbering on the next ordinary read |

## Measured (2026-09-28, copy of the user-data backup, newest save, warm process)

| Call | Measured | Budget |
|---|---|---|
| `track_view`, 3 stored plans, nameplate / 2,000 / 6,372 MW | median 10.0–14.4 ms, max 15.6 ms | – |
| `GET /api/plan/track` over HTTP | warm median 30–32 ms, max 41 ms (a bare `GET /api/asks` is 31 ms on the same client) | p95 ≤ 60 ms |
| Track payload | 7.6–11.2 kB at nameplate (no stages), up to 22.5 kB with 3 stages (`north oil rig`, 11 rows) | ≤ 40 kB |
| stages on the reference save | nameplate 116 MW: none (minimum slices 392–644 MW); 2,000 MW: 2–3; 6,372 MW: 1–2 | – |
| `feeders_view` | 0.69–0.87 s (19 feeders) | on demand only |
| `push_ops set headroom_mw` | 26–40 ms | – |
| `POST /api/asks` / `DELETE` | 58 / 35 ms | – |
| chat `ui_context` → `ask.seen` on a second SSE stream | 0.41 s | ≤ 1 s |
| `ui_context(answered=)` → `ask.answered` on the stream | 0.51 s | ≤ 1 s |
| `diff_vs_save(plan=)` → `plan.view` on the stream | 0.50 s | ≤ 1 s |

## Open

- `manage.duplicate` builds the copy field by field and does not carry `headroom_mw`; a
  duplicate starts at the nameplate.
- `?biomass=` on `/api/plan/feeders` is accepted and ignored: which extractors feed running
  generators does not depend on it.
- The Track render budget (A20) and every page check in contract §13 belong to the frontend and
  verifier.
