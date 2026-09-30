# Plan management: versions, restore, duplicate, result deltas, activity

Phase P2 of [planner_vision.md](planner_vision.md) §8, built on the P1 slice
([planner_slice_contract.md](planner_slice_contract.md)) and the plan log
([plan_log.md](plan_log.md)). Every write here goes through `PlanLog` and merge rule M1;
nothing edits a plan file around it.

## What was built

| Piece | Where | Behaviour |
|---|---|---|
| Versions list | workbench **[versions]** toggle; `GET /api/plans/{key}/versions` | Every commit, newest first: version, who, when, the change in words (`describe_commit`), and flags: `head`, `undone in vN`, `restores vN`, `merged over vN`. Each row has **[view]** and, except the head, **[restore]** |
| View at a version | `#dash=planner/<key>/v<rev>` | Read-only: the version's request in words, its re-solved result and build list, and the result delta from that version to the head. **[back to vN]**, **[restore vR]**, **[duplicate vR]**. The head's controls are hidden while a version is viewed |
| Restore | `POST /api/plans/{key}/restore {base_rev, rev}` → `PlanLog.restore_to` | A **new** commit whose ops turn the head back into `rev` (note `restore v<rev>`). Never a rewind: every later version stays in the log. It merges and conflicts like any write: a 409 `OutdatedResponse` when something it would change was changed since `base_rev`, a 409 `name_taken` when the old name now belongs to another plan. On the page it is an own commit, so Ctrl+Z undoes it |
| Duplicate | `POST /api/plans/{key}/duplicate {rev?, name?}` → `manage.duplicate` | A `create` at v1 of a new key, equal to `key` at `rev` (the head when omitted): arguments, notes, factory link and siting. Stamped against the save the request read. Named `<name> (copy)`, then `(copy 2)` …; a given `name` that is taken is a 409 `name_taken`. The source is untouched |
| Rename, forget, restore from forget | workbench header (built in P1) | `rename` / `forget` / `restore` ops. A forgotten plan is reached from the Activity panel's plan link, where the workbench offers **[restore]** |
| Result deltas | `GET /api/plan/delta?key=&from_rev=&to_rev=` → `manage.result_delta` | Re-solves both versions (about 2 × 15 ms) and compares: total machines, machines per building, MW draw, MW net, and raw inputs. `text` is one line, e.g. `+4 Fuel-Powered Generator · +1 Blender · +147.7 MW draw · Water +80/min`. When either side is not solvable the counts are left at zero and `comparable` is false, and the text says only `no longer solvable` / `solvable again` |
| Changed-by-chat diff | workbench strip ("changed since you opened it") | Below the per-commit lines, `result since vF: …` where `F` is the rev before the oldest listed commit and the other end is the head. It follows the head, so it also counts own edits made after chat's |
| Activity panel | Planner list view; `GET /api/activity?limit=50` | Plan commits and journal entries, newest first, filter **all / you / chat**. Each row links to its plan. A plan commit (not v1) has **[undo]**, pushed against that plan's current head; a chat solve has **[open]**, which shows the from-chat card |
| Raw inputs on a solve | `SolveResponse.inputs` | What the plan takes from the world: extractor output plus raw drawn from outside (`Solution.raw_used`), item name and per minute, largest first |
| List status (G2) | `PlanIndexRow.status` / `recorded`; `manage.plan_status` | `world moved`, `field N->M`, `broken: <Error>`. `list_plans` and `/api/plans` call the same function. An empty status with `recorded` false shows as "field not recorded" |

## G2: logic moved out of the tool bodies

- `manage.plan_status` replaces the status loop in `list_plans`.
- `manage.undone_by` replaces the tool's own `_undone_by`; `plan_log` and the versions route
  share it.
- `summary.stamp_for` is now the one stamp: the MCP tools' `_stamp` returns it.
- `manage.duplicate` and `manage.versions` are new, and domain from the start.

Still in tool bodies: the `save_as` target resolution and write (`_save_target`,
`_save_over`), the one-plan detail text (`_plan_detail`, a presenter over checks the
domain already owns), and site ranking (`rank_build_sites`), which moves with P5 when a
route needs it.

## Decisions taken here (smallest option; open for review)

- **D-P2-1. A duplicate keeps the factory link and the siting.** It is a copy of the whole
  plan state. The alternative (a copy with no pad and no factory) is one line in
  `manage.duplicate`.
- **D-P2-2. The version deep link is `dash=planner/<key>/v<rev>`**, not vision §3.6's
  `planner/<name>&v=<rev>`: a path segment fits `nav.dashParts`, and the key survives a rename.
- **D-P2-3. Viewing a version replaces the workbench body** rather than splitting the
  screen; the head is one click away.
- **D-P2-4. Result deltas are re-solved, not stored** (Q13 stays open): nothing is added to
  the log, which keeps the request-only rule.
- **D-P2-5. The Activity panel lives on the plans list only**, 50 rows, no paging.
- **D-P2-6. The versions toggle stays open across plans** for the page session.

## Open

- Q13: a stored result summary per version would make deltas free; the log still stores
  arguments only.
- Vision §2.7 outlines changed graph nodes and build-list rows for a few seconds after a
  chat edit. The backend half is built in P3: `result_delta` now carries `rows`, the process
  rows added, changed or removed, joined on `SolveRow.id` ([planner_p3.md](planner_p3.md)).
  The badges and pulse are the page's half.
- A list of forgotten plans. Today a forgotten plan is reached through Activity, or chat's
  `plan_log`; after 50 newer entries it drops out of Activity.
- The Asks column of the §2.10 wireframe: asks are built in P4 (store, routes, `ui_context`
  listing and marking; [planner_p4.md](planner_p4.md)). The page shows them as an asks card on
  the plans list and at the end of Track rather than as a column.
- Undo from Activity uses the plan's current head as `base_rev`, so it conflicts only on
  what changed after the undone version, as `plan_log undo=` does.
