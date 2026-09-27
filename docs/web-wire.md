# The web wire rules

The rules every router in `interfaces/web/routers/` lives by. They are stated once, here;
routers point at this file instead of re-telling them.

1. **The function name is the operation_id.** FastAPI's default id is
   `{function_name}_{path}_{method}` and `frontend/src/api-schema.d.ts` is generated from
   it — renaming a handler churns the committed schema.
2. **Declaration order is wire order** for every `TypedDict` a `response_model` names: the
   keys are emitted in the order they are declared.
3. **A `response_model` FILTERS.** Keys the model does not declare are dropped from the
   response; a handler may build more than it sends. This is load-bearing — several
   endpoints deliberately send a subset of what the domain returns.
4. **Numeric types rewrite wire bytes.** Declaring `float` where the value is an int
   validates `15` into `15.0` on the wire, and vice versa fails validation. Match the real
   type of the value.
5. **A router never imports another router.** Shared shapes live in `serial.py` only when
   one function builds them for more than one router; two shapes that merely look alike stay
   separate. Enforced by `tests/test_architecture.py`.
6. **Regenerate, never hand-edit** `api-schema.d.ts`: throwaway server on a port in
   8920–8999, then `npm run typegen -- <port>` (`scripts/typegen.mjs`: `openapi-typescript`
   against that port, then `scripts/stamp-schema.mjs`). Without an argument it reads
   `SATISFACTORY_WEB_PORT`, then falls back to 8712.
7. **A write says what it refuses, in the schema.** A request body is a `TypedDict` taken as
   `Annotated[Body, Body()]` (routers may not import pydantic), and every non-2xx body the page
   branches on is declared with `responses={409: {"model": ...}}`, so it reaches
   `api-schema.d.ts` like a 200 does. The planner routes (`routers/planlog.py`) answer a
   conflict with `OutdatedResponse` and never apply part of a push. The server stamps the
   actor (`page`, its own pid); the page never sends one.

## The event stream

`/api/events` sends four event names. `save` and `notes` are triggers: they say a file moved
and the page decides what to refetch. `plans` and `activity` carry data, because they are
tailed line by line from the plan logs and the activity journal every 0.5 s rather than
stat-compared every 3 s.

| Event | Source | Data |
|---|---|---|
| `save` | newest `*.sav` under the save root | `{filename, mtime, save_token}` |
| `notes` | newest `labels/**/*.json`, or the legacy top-level `plans/<world>.json` | `{filename, mtime}` |
| `plans` | new commits in one `plans/<world>/<key>/ops.jsonl`, one event per plan per tick | `{world, key, name, rev, from_rev, actors, text, ts, forgotten}` |
| `activity` | each new line of `activity/<world>/<writer>.jsonl` | `{world, id, ts, actor, kind, plan, rev, text, args}` |

- `actors` and `actor` are `ActorBody` (`kind`, `client`, `pid`, `display`). `text` is the
  newest commit's `describe_commit` words; `from_rev` is the rev before the first new commit.
- The tail's first pass only records where each file ends, so a server start announces
  nothing old. A file that appears later is read from its start, which is how a new plan's
  `create` arrives. A line without its newline yet waits for the next tick.
- A new stream is replayed the newest event of each kind, in the order above. The page treats
  a replayed `plans`/`activity` event as news only when its `ts` is newer than page open
  minus 2 s.
- Only the served instance tails (`create_app(tail=True)`), and it is also what names the
  process the `web` journal writer. Test apps leave both off.
- Pin writes (`/api/pins`, Planner P3) add no event name: each appends one journal entry,
  `pin.add`, `pin.edit` or `pin.drop`, which reaches every page as `activity` within the 0.5 s
  tail. `plan` is the plan key for plan and process pins, else null.
- Ask writes (`/api/asks`, Planner P4) add no event name either: the web appends `ask.add` and
  `ask.drop`, and chat's `ui_context` appends `ask.seen` and `ask.answered` to its own journal
  file, so every change reaches every page as `activity`. `diff_vs_save(plan=)` and
  `commission_plan(plan=)` journal `plan.view` with `args.view = "track"`.

## Pins

`/api/pins` (`routers/pins.py`) follows the rules above: `?save=`/`?world=`, the guard on
every method, `{error}` with a 4xx. A write carries the `rev` it read; a different `rev` is a
409 `PinStaleResponse {error, stale: true, pin}` with the row as it stands, and nothing is
written. A create of an object that already has a live pin is a **200** with `existing: true`
rather than a 201. A pins file from a newer version is a 503 `{error, newer_schema: true}`
naming the pins, not the path. Pin numbers are never reused.

`PlanOpBody` lives in `serial.py` because two routers publish it (`planlog` for pushes,
`planner` for the ops an alternates option would push). The alternates route's reply is
named `PlanAlternatesResponse` because `routers/gamedata.py` already publishes an
`AlternatesResponse` and two models with one name would rename both in `api-schema.d.ts`.

## Track and asks

`GET /api/plan/track?key=&rev=&biomass=` (`routers/planner.py`) builds its whole reply from one
solve (`domain/planning/track.py`). Not feasible, empty, and a count-as-built factory with no
machines left are all 200s that say so (`feasible`, `empty`, `scope_error`) with empty lists;
a 400 is only a solve that refuses its arguments. `biomass` takes `true`/`false` or the
`include`/`exclude` spelling the power routes use. `GET /api/plan/feeders` is the ~0.7 s
extractor walk, never run per save.

`/api/asks` (`routers/asks.py`) follows the pins rules: the guard on every method, a delete
carries the `rev` it read and a different one is a 409 `AskStaleResponse {error, stale: true,
ask}`, a newer asks file is a 503 `{error, newer_schema: true}`. `about.plan` must be a live plan
key (404 otherwise). Ask numbers are never reused. Unlike pins, the store has writers in every
MCP process (seen, answered), so every write holds the file lock.

`PlanStateBody.headroom_mw` is the stored startup headroom, `null` for the nameplate.
