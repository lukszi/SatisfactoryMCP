# Shared settings

Some settings change what chat computes as well as what the page shows. Those live on the
server, in one file, so the page and chat always read the same value. The rest stay in each
browser. Decided 2026-09-30 (7b): *stage headroom* is shared. *Count biomass burners in
headroom* moved with it, because chat's power tools take the same switch.

## 1. What is shared, and what stays per browser

| Setting (page key) | Where | Why |
|---|---|---|
| stage headroom (`stageHeadroom`) | **server**, `stage_headroom` | `diff_vs_save` and `commission_plan` stage a plan with it. Kept per browser, the page and chat staged one plan two ways. |
| count biomass burners in headroom (`biomass`) | **server**, `biomass` | The same kind of setting: it changes generation and headroom, and `power_report`, `world_summary`, `diff_vs_save` and `commission_plan` all take it. |
| name suggestions (`naming`) | browser, **candidate** | Only the page's Detect uses it today. It becomes shared if chat's `propose_factories` ever suggests names in a style. |
| show what is not unlocked yet (`spoilers`) | browser, **candidate** | A choice about the player, not about one screen, but chat does not filter spoilers today. Share it when chat's progress tools do. Its one-time notice stays per browser either way. |
| only fed clusters (`fedOnly`) | browser | A view filter on Detect. |
| minimum machines (`minMachines`) | browser | A view filter on Detect. |
| follow chat (`follow`) | browser | How one tab reacts to chat. The page already reports it to chat through `ui_context`. |
| payback horizon, hours (`paybackHours`) | **server**, `payback_hours` | What a plan with no horizon of its own solves at, in chat and on the page ([planner-payback-horizon_contract.md](planner-payback-horizon_contract.md)). Number 0–100, default 0. |
| overclock the last machine (`overclockLast`) | **server**, `overclock_last` | The same for the overclock-last switch. Default off. |

The rule: a setting is shared when chat computes with it. A setting that only changes how
one viewer sees the page stays in the browser.

## 2. The store

`domain/settings.py`, file `settings.json` in the user data dir (`config.settings_path()`),
one file for every world.

```json
{"schema": 1, "version": 3,
 "values": {"stage_headroom": "nameplate", "biomass": true},
 "updated": 1790750397.3, "by": {"kind": "page", "client": "", "pid": 34476}}
```

- `SPECS` names every setting: kind (`choice`, `switch` or `number` with `low`/`high`),
  default and hint. Adding a setting is one entry, plus its field in the route's
  `SettingsValues`/`SettingsChanges` and a `shared:` name on the page's `SETTINGS` entry.
- `values` holds only what was set. A missing or invalid value reads as the default. A key this
  version does not know is kept on every write.
- Every write holds `core/filelock.py`'s lock on the file, re-reads inside it and bumps
  `version`, but only when a value really moved. `by` and `updated` are the last write.
- `write(changes, actor, version=None, only_unset=False)`. `None` clears a setting back to
  its default. A `version` other than the current one raises `SettingsStale` and writes
  nothing. `only_unset` skips keys that are already set (the migration, §6).
- A file with a higher `schema` raises `NewerSchema` on read and write, and is left as it is.

## 3. The route

`GET /api/settings` → `SettingsResponse {version, values, stored, updated, by}`. `stored`
lists the keys that were set rather than defaulted. `by` is an `ActorBody`, null before the
first write. `?save=`/`?world=` are ignored: the settings belong to no world.

`PATCH /api/settings {values, version?, only_unset?}` → `SettingsResponse`. It passes the
guard like every write: Host must be this server, and Origin must be this page, else 403.
Refusals:

| Status | Body | When |
|---|---|---|
| 409 | `SettingsStaleResponse {error, stale: true, settings}` | `version` is not the current one; nothing written |
| 422 | FastAPI's validation body | a value outside the schema (`Literal`, `bool`) |
| 400 | `{error}` | a value the store refuses that the schema let through |
| 503 | `{error, newer_schema: true}` | the file is from a newer version; names the settings, never the path |
| 503 | `{error}` | the lock stayed held for 10 s |

## 4. The event

The watcher's 0.5 s tail stats `settings.json`. When its mtime or size moves, it sends one
`settings` event whose data is the `SettingsResponse` as it now stands. The first look only
records the stamp, so a server start announces nothing old. A write from any process arrives
this way: the page, another tab, or chat.

## 5. Chat

- `settings(change=None)` lists every setting: its value (marked `(default)` when unset), what
  it takes, and what it means, with the last writer in the header.
  `settings(change={"stage_headroom": "nameplate"})` writes as `chat`, last writer wins, the
  same as the label tools (frontend_vision §9.6). `null` resets one. Chat may write, but the
  description says to change a setting only when the user asks. It is a player preference,
  not a knob for the assistant.
- `diff_vs_save` and `commission_plan` stage a plan with no stored headroom against
  `stage_headroom`. A plan's stored `headroom_mw` still wins, and so does
  `commission_plan(headroom_mw=)`.
- `biomass=` on `power_report`, `world_summary`, `diff_vs_save` and `commission_plan` now
  defaults to `None`, meaning the shared value. `true`/`false` still override for one call.
  It also decides whether burners count in the grid mix that prices a payback horizon.
- A plan whose `payback_hours` or `overclock_last` is unset solves with the shared value, in
  every planning tool. The page drops its cached solves when either moves.
- When the file cannot be read (newer schema, I/O), a tool uses the default and says so in a
  note (`app.shared`).

## 6. The page

- `settings.ts` marks a shared entry with `shared: "<server name>"`. It keeps a mirror of the
  server's values in `localStorage`, so the first paint uses the last known value.
- `shared-settings.ts` fetches `/api/settings` at boot and adopts it. Listeners hear only the
  settings that moved, so a changed value refetches exactly what it did before.
- **Migration.** On the first boot after this change, a browser that had set a shared setting
  itself pushes that value with `only_unset: true`, only for keys the server has not
  stored. The flag `shared-settings-pushed` in `localStorage` makes it once. After that, and
  for every key the server already had, the server wins. Without storage, the push repeats
  at each boot and `only_unset` makes it harmless.
- A change on the Settings tab is a `PATCH` carrying the version the page holds. On a 409 it
  takes the fresh version and sends the same change once more, because the click is the
  player's intent for that key. A second refusal adopts the server's state and says so.
- *Reset to defaults* clears the shared settings too (`null` for each). That reaches every tab
  and chat.
- The `settings` event is applied when its version is newer than the one the tab holds. A
  change by chat raises a note, "chat changed a shared setting". The Settings tab redraws
  when a setting changes, whoever changed it. A reconnect refetches.

## 7. Verified

Headless Chrome, two tabs and chat on one scratch user data dir (2026-09-30):
1. Tab A booted with a legacy local `stageHeadroom: nameplate`, and the server stored it
   (`by: page`, v1).
2. Tab B, with no local value, showed nameplate.
3. Chat's `settings()` read nameplate. `settings(change={"biomass": true})` wrote v2, and both
   tabs ticked the biomass switch and noted the change within 2 s.
4. Tab A picked measured, and Tab B and chat's next read both showed measured (v3).

## 8. Open

- Browsers allow six HTTP/1.1 connections per host and every tab holds one for
  `/api/events`. With six tabs open, a seventh tab's requests, this `PATCH` among them, wait
  until a tab closes. This is not new with shared settings.
- `ui_context` does not repeat the shared settings. Chat reads them with `settings()`.
