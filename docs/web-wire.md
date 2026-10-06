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

`/api/events` sends six event names. `save` and `notes` are triggers: they say a file moved
and the page decides what to refetch. `plans` and `activity` carry data, because they are
tailed line by line from the plan logs and the activity journal every 0.5 s rather than
stat-compared every 3 s. `settings` carries data too: the tail stats the shared settings file.
`maps` is published by the map job runner rather than found by a poll.

| Event | Source | Data |
|---|---|---|
| `save` | newest `*.sav` under the save root | `{filename, mtime, save_token}` |
| `notes` | newest `labels/**/*.json`, or the legacy top-level `plans/<world>.json` | `{filename, mtime}` |
| `plans` | new commits in one `plans/<world>/<key>/ops.jsonl`, one event per plan per tick | `{world, key, name, rev, from_rev, actors, text, ts, forgotten}` |
| `activity` | each new line of `activity/<world>/<writer>.jsonl` | `{world, id, ts, actor, kind, plan, rev, text, args}` |
| `settings` | `settings.json` in the user data dir, when its mtime or size moves | `SettingsResponse {version, values, stored, updated, by}` |
| `maps` | the map job runner: every job state change, at most one progress update per 2 s, and every registry write | `{job: MapJobBody \| null, queued: [job id], registry_version}` |

- `actors` and `actor` are `ActorBody` (`kind`, `client`, `pid`, `display`). `text` is the
  newest commit's `describe_commit` words; `from_rev` is the rev before the first new commit.
- The tail's first pass only records where each file ends, so a server start announces
  nothing old. A file that appears later is read from its start, which is how a new plan's
  `create` arrives. A line without its newline yet waits for the next tick.
- A new stream is replayed the newest event of each kind, in the order above. The page treats
  a replayed `save`/`notes` event as news only when its `mtime` is newer than page open
  minus 2 s.
- `?since=` is the page's open time in epoch seconds, and the page always sends it. A `plans`
  or `activity` event whose `ts` is at or before it is history: the stream withholds it from
  the replay and from the live fan-out alike. Live matters as much as replay, because the tail
  can publish an entry up to 0.5 s after it was written, and a new chat process's journal file
  is read from its start the first time the tail sees it. Without `since` (the default 0) every
  event goes out. No grace applies: an `activity` entry can move the page, and a find chat made
  a second before the page opened must not. A reconnect's replay of missed entries is
  `GET /api/activity?since=` from the newest entry heard, or page open, whichever is later.
- Only the served instance tails (`create_app(tail=True)`), and it is also what names the
  process the `web` journal writer. Test apps leave both off. The same flag decides whether the
  map job runner reads and re-adopts the jobs on disk at start.
- `maps` is state, not a journal: `since` does not apply, and the replay of the newest one is
  how a reloaded page sees a running job. A `registry_version` other than the page's means the
  list changed and the page refetches `/api/maps`.
- Pin writes (`/api/pins`, Planner P3) add no event name: each appends one journal entry,
  `pin.add`, `pin.edit` or `pin.drop`, which reaches every page as `activity` within the 0.5 s
  tail. `plan` is the plan key for plan and process pins, else null. Chat's
  `show_on_map(pin=True)` appends `pin.add` to its own journal file, so a pin chat makes
  reaches the page the same way. A `plans` event for a plan that a plan or process pin names
  also refetches pins, so a plan forgotten from chat turns its pin gone at once.
- Ask writes (`/api/asks`, Planner P4) add no event name either: the web appends `ask.add` and
  `ask.drop`, and chat's `ui_context` appends `ask.seen` and `ask.answered` to its own journal
  file, so every change reaches every page as `activity`. `diff_vs_save(plan=)` and
  `commission_plan(plan=)` journal `plan.view` with `args.view = "track"`.
- `GET /api/activity` keeps one row for a run of the same `plan.view` (same plan, actor, view
  and item, stage or section), the newest, before it applies `limit`.
- Advisory writes (`/api/advice/hidden`) add no event name either: the web appends
  `advice.hide` and `advice.restore`, and chat's `ui_context(dismissed=)` appends `advice.hide`
  to its own journal file. Any `advice.*` entry refetches `/api/advice` on every page.

## World

- `hidden_spoilers` is sent only by `/api/collectibles`, and it counts **categories** (pickup
  kinds) dropped by `spoilers=0`, never placements. The node routes take no `spoilers`: a
  locked node is always sent with `spoiler: true` and the page fades it.
- The finder tools (`search_resource_nodes`, `rank_build_sites`, `search_conduits`,
  `whereami`, `collected_from_world`) journal `world.find` with `args {view, params}`: the
  World view and its address parameters, the ones the page's own filters write. A page set to
  follow chat opens `world/<view>?<params>`; "toasts only" offers it instead.
  `rank_build_sites` sends `view: "rank"`.
- `GET /api/activity` keeps one row for a run of `world.find` by one actor (kind and pid), the
  newest, before it applies `limit`. Every row carries `count`: the entries it stands for,
  1 unless collapsed. A collapsed `plan.view` run counts the same way.

## Terrain

- `/api/inspect` `elevation.terrain_cave` is `none`, `below` or `inside`
  (spatial-and-map.md §23). Under `below`, `terrain_m` is the surface and
  `terrain_cave_note` is the line the page shows beside it. Under `inside`, `terrain_m` is
  null, the note travels as `terrain_note` and `terrain_cave_note` is null. A map click
  carries no z, so the inspector sees `below` at most.
- On rock, `terrain_m` is the collision surface from `rocks.npz` when the field has one
  (spatial-and-map.md §24): the highest of the landscape and the rock collision, never an arch
  or a boulder. No field was added or renamed. `inside` with a cave floor found keeps
  `terrain_m` and sends the floor line as `terrain_cave_note`; the inspector cannot reach that
  case without a z.
- The site preview's `terrain.cave_pct` is the pad's share with a cave under it, 0 without
  cave masks. The page prints it when it is not 0.

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

`Flow`, `MachineSpot` and their builders `_flow` and `_machine_spots` live in `serial.py`
because `routers/naming.py` (candidates, amend) and `routers/factory_graph.py` (`/api/factories/graph`,
`/api/factories/machines`) both send them.

## Track and asks

`GET /api/plan/track?key=&rev=&biomass=&headroom=` (`routers/plan_track.py`) builds its whole reply from one
solve (`domain/planning/progress/track.py`). Not feasible, empty, and a count-as-built factory with no
machines left are all 200s that say so (`feasible`, `empty`, `scope_error`) with empty lists;
a 400 is only a solve that refuses its arguments. `biomass` is `include` or `exclude`, the
spelling every power route uses. `headroom` is `measured` (the default) or `nameplate`: the
save's figure a plan with no stored headroom is staged against. The page sends the shared
*stage headroom* setting, and `biomass` the shared biomass setting; the routes keep their own
defaults when the query is absent. `written_ago` is the save's age alone (`12 min ago`, null with no mtime); `age_note`
keeps the full line for a tooltip. `GET /api/plan/feeders` is the ~0.7 s
extractor walk, never run per save.

`/api/asks` (`routers/asks.py`) follows the pins rules: the guard on every method, a delete
carries the `rev` it read and a different one is a 409 `AskStaleResponse {error, stale: true,
ask}`, a newer asks file is a 503 `{error, newer_schema: true}`. `about.plan` must be a live plan
key (404 otherwise). Ask numbers are never reused. Unlike pins, the store has writers in every
MCP process (seen, answered), so every write holds the file lock.

## Advice

`/api/advice` (`routers/advice.py`) follows the asks rules: the guard on every write, a write
carries the `rev` it read (0 for a row never hidden) and a different one is a 409
`AdviceStaleResponse {error, stale: true, row}` with the row as it stands, a newer file is a 503
`{error, newer_schema: true}`. A hide names the row by `key`, a restore by `adv:` id in the
path. [advisors_contract.md](advisors_contract.md) is the specification.

## Settings

`/api/settings` (`routers/settings.py`) reads and writes the settings the page and chat share:
`GET` sends `SettingsResponse`, `PATCH {values, version?, only_unset?}` changes some. A write
passes the guard. A different `version` is a 409 `SettingsStaleResponse {error, stale: true,
settings}` and writes nothing, a newer file is a 503 `{error, newer_schema: true}`, and the route
takes no `?save=`/`?world=`. Every write arrives on every page as the `settings` event.
[shared-settings.md](shared-settings.md) is the specification.

`PlanStateBody.headroom_mw` is the stored startup headroom, `null` for the save's own figure
(measured by default, see `headroom` above).
`SolveResponse.power` is the payback view: the same recipes read out at every horizon stop,
the price used and the overclock-last pick
([planner-payback-horizon_contract.md](planner-payback-horizon_contract.md) §7).
`SolveRow.last_clock` is set on a row whose last machine is overclocked.

## Maps

`/api/maps` (`routers/maps.py`) lists the base map types and runs the jobs that make them;
[maps_contract.md](maps_contract.md) is the specification. The writes (`PUT /api/maps/default`,
`PATCH` and `DELETE /api/maps/{id}`, `POST /api/maps/adopt`, `POST` and `DELETE /api/maps/jobs…`,
`DELETE /api/maps/cache`) pass the guard. A registry write carries the list `version` it read;
another is a 409 `MapsStaleResponse {error, stale: true, version}` and nothing is written. The
registry writes answer with the whole `MapsResponse`, so the page redraws from the reply. A job
is queued with 202; a full queue is 409 and a short disk 507. Routes take no `?save=`/`?world=`.
`/api/maptiles/{layer}/…` takes a registry id as `layer`; `map`, `terrain` and `satellite` are
served even before the manifest exists. A type's `title` is the one name the page and chat show
it by ("Painted · 6 Oct ★"), composed by the server; `name` stays the technical name from its
axes, and the page never derives a title from it (maps_contract.md §3.5).
