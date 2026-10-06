# Advisors: the contract

Roadmap phase 12 of [frontend_vision.md](frontend_vision.md) §6, rebuilt as a cheap list:
"worth a look", a short list of things on the current save that are worth acting on. Each row
is **one measured fact plus where to look**. Nothing is scored, nothing has an ETA, and the
order is a fixed tuple of kind and size. The slow advisors of the original phase 12
(`rank_unlocks`, `advise_hard_drive_pick`) stay on-demand buttons and never enter this pass.

This file says what was built and what binds it. The decisions behind it were taken on
2026-10-05 (§11).

**Binding:** the pass only reads; a dismiss or a snooze is the only write, and it is the
player's (or chat's, when the player asks) statement, kept as user data. Red stays for broken
or stopped things; advisors never use it. No hard-drive advisory exists at all. Blocked
machines are evidence, never a row of their own.

Backend paths are relative to `src/satisfactory_mcp/`; frontend paths to
`src/satisfactory_mcp/interfaces/web/frontend/src/`.

---

## 1. Modules

| Module | Does |
|---|---|
| `domain/advice/rules.py` | The pass: `compute(st, biomass=, headroom=, box_fed=, spoilers=)` returns every firing row, ranked; `with_ids`, `key_for`, `ids_for`, `capped` |
| `domain/advice/store.py` | Dismissed and snoozed rows, one file per world: `read`, `hide`, `restore`, `split`, `worse` |
| `domain/advice/__init__.py` | `current(st, …)`: the shared settings read, the rows cached, the store applied. The page and chat both read through it |
| `interfaces/web/routers/dashboard/advice.py` | `GET /api/advice`, `POST /api/advice/hidden`, `DELETE /api/advice/hidden/{adv_id}` |
| `presenters/text/advice.py` | The `ui_context` line, the `world_summary` block, the `dismissed=` grammar |
| `advice.ts` | The store, the card (`adviceCard(parent, {factory}?)`), the snooze split button, restore, the `new` marks |

`domain/advice` may import `domain.factories`, `domain.power`, `domain.planning`,
`domain.spatial` and `domain.collectibles`; nothing in `domain` imports it.

## 2. Kinds

An advisory exists only when its rule fires on the current save, and reads measured state only.
Its **subject** is a named factory, a region for machines outside every factory
(`regions.label_for` at the machine), a stored plan, or the world. One row per (kind, subject).

| Kind | Severity | Fires when | Page line |
|---|---|---|---|
| `unconnected` (K1) | act | A starved machine where every missing input has nothing arriving: every feed `NOTHING`, an `UNFED` pipe, or a fluid whose rung is `connection` | `nothing brings Crude Oil to 1 Refinery in “north oil rig”` |
| `dead_node` (K2) | act | `state == "dead node"` | `2 Miners Mk.1 in “blackpowder factory” stand on no node` |
| `starved` (K3) | act | Starved machines K1 and K6 did not take, not box-fed, not generators. Items rawest first (§2.1). Evidence: blocked machines holding the item, here and elsewhere | `3 Constructors in “speedwire factory” starve of Caterium Ingot · 6 blocked here hold it` |
| `power` (K4) | act | Any starved generator, or any machine on no power wire. One row for the world | `8 machines on no power wire (215 MW)` |
| `underclock` (K6) | consider | An extractor below 100 % whose item a machine in the **same named factory** starves of | `Miner Mk.2 at 50% while 1 Foundry in “steel” starves of Iron Ore` |
| `headroom` (K5) | consider | Headroom under 5 % of generation. The figure is the shared *stage headroom* setting's: measured (default) or nameplate; biomass follows its setting | `headroom now is 116 MW of 7,550 MW` |
| `no_recipe` (K7) | consider | `state == "no recipe"` | `12 machines in “tier 1&2” have no recipe` |
| `plan` (K8) | consider | A stored plan that no longer solves, whose source selector drifted (`field N->M`), or whose nodes another factory taps (`BuiltAt.node_owner`). **`world moved` alone never fires** | `plan “spire-coast-e1”: its 3 crude oil nodes already feed “north oil rig”` |
| `pickups` (K9) | note | Uncollected pickups within `PICKUP_REACH_M` (500 m) of the saved position; spoilers follow the page's switch, off for chat; hard drives and drop pods never count | `6 pickups within 500 m of where you saved · somersloop 417 m` |
| `box_empty` | note | Only with the shared setting `advice_box_fed` on (default off): a starved machine fed only from a storage box | `1 Constructor in … emptied the box that feeds it` |

Left out on purpose: blocked alone (377 of 586 machines on the reference save), stalled with no
cause, paused (player intent), any pending hard-drive choice (hoarded on purpose), box-fed
starvation by default (hand-fed boxes are temporary setups), and "unlocking X changed plan Y"
(K10, phase 12b: it needs the timeline's first consumer).

### 2.1 Words and limits

- Page text ≤ 90 characters, cut with `…`; chat text (`tool_text`) ≤ 160; ≤ 3 evidence lines.
- "Rawest first" is the number of standard-recipe steps from a raw resource (`Item.is_resource`
  is 0), then how many machines starve of it, then the name.
- K3's evidence item is the first item, rawest first, with blocked holders in the same subject;
  otherwise the rawest. Its seed (what **trace** starts from) is a machine starved of the rawest.
- `next_call` is a real call: `trace_upstream seed=<leaf>`, `factory_health factory="<name>"`
  (a `bbox:` selector for a region), `power_report`, `diff_vs_save plan="<name>"`,
  `collected_from_world show=nearest near=me`.
- A row sends at most 50 places (`machines`); `members` (every machine leaf, or every pickup's
  `category:name`) stays server-side for the store.

## 3. Order, caps, keys, ids

1. Order is `(severity, kind, -weight, subject)`. Severity is act, consider, note; kind order
   is the table above. Weight is machines, MW for K4 and K5, the line count for K8 and the
   pickup count for K9. The same save always gives the same list.
2. The card shows 5 rows and at most 3 of one kind (`rules.capped`; `advice.ts` applies the
   same rule). The rest fold into `+N more (2 starved, power, …)`, a link that expands in place.
3. `key` = `<kind>|<subject kind>:<subject>`, a `|` inside the subject written `%7C`; world rows
   are `<kind>|world`. A plan's subject is its name.
4. `id` = `adv:` + the first 4 hex of sha1(key). Ids are assigned over the firing keys and the
   stored ones together; keys sharing 4 hex get 6 each.

## 4. Dismiss and snooze

| Gesture | Effect | Comes back when |
|---|---|---|
| snooze *n* h (0.5 to 24) | Hidden for *n* hours of **play time** (`header.play_duration_s`) | Play time passes the mark, or it gets worse |
| until it gets worse (dismiss) | Hidden | It gets worse: a machine not in the hidden set joins, the severity rises, or (with no members, or past 200 ids) the weight grows by half |
| restore | Shown again | – |

A row that comes back because it got worse is sent `back: true` and the page marks it **back**.
A row that stops firing has nothing to show; if the same machines fire again it stays hidden.

`store.py` writes `config.advice_dir()/<world>.json` (`advice/` beside `asks/` in the user data
dir), sanitised like the asks file. The web process and every MCP process write it, so every
write holds `filelock.held` and uses `atomic.write_text`; readers take no lock.

```json
{"schema": 1, "version": 3,
 "hidden": {"starved|factory:tor factory":
   {"state": "snoozed", "ids": ["Build_OilRefinery_C_2147178149", "…"], "weight": 10.0,
    "severity": "act", "until_play_s": 1204199.0, "hours": 1.0,
    "by": {"kind": "page", "client": "", "pid": 33244}, "at": 1791183031.0, "rev": 2}}}
```

- `version` counts writes to the file, `rev` writes to one entry. A write naming another `rev`
  is `AdviceStale` (409 on the wire), `rev` 0 meaning "not hidden yet". Chat writes with no
  `rev`: last writer wins.
- `schema` above 1 is `NewerSchema`: read and write refuse and leave the file as it is. A torn
  file reads empty.
- On every write, entries whose key is not firing and that were written more than 30 days ago
  are dropped. That keeps the file small; it is not a behaviour rule.
- The pass writes nothing: an autosave never touches the file.

## 5. Routes

| Handler | Method, path | Body / query | 2xx | Errors |
|---|---|---|---|---|
| `advice_list` | GET `/api/advice` | `biomass?: include\|exclude` (default: the shared setting), `spoilers?: 0\|1` | `AdviceResponse {save_token, play_s, version, active, hidden}` | 404 save unreadable; 503 `{error, newer_schema: true}` |
| `hide_advice` | POST `/api/advice/hidden` | `AdviceHideBody {key, mode: dismiss\|snooze, hours?, rev?}`, `?spoilers=` | `AdviceRow` as it now stands | 400 bad hours; 404 key not firing; 409 `AdviceStaleResponse {error, stale, row}`; 422 bad mode; 503 lock or schema |
| `restore_advice` | DELETE `/api/advice/hidden/{adv_id}` | `AdviceRestoreBody {rev}` | `AdviceRestored {ok, id}` | 404 not firing or not hidden; 409; 503 |

- `active` is every row not hidden, ranked; the page applies the caps. `AdviceRow.tone` is
  `blocked`, `mid` or `muted`; `state` is `active`, `dismissed` or `snoozed`; `rev` is the
  hidden entry's, 0 when never hidden; `until_play_s` is a snooze's mark; `by` the last
  writer's kind; `reveal` names the map layers the row's **map** action turns on.
- Writes pass the guard (Host always, Origin on writes). GET passes the `as_of` middleware.
- Each write appends one journal entry (§7).
- The payload on the reference save is 12.8 kB for 15 rows.

## 6. Chat

- **`ui_context`** prints, after the `asks` line, `advice (14, 1 hidden): adv:01ae unconnected:
  … · adv:7bd7 no node: … · … (+11 more: world_summary)`: the first three rows the card shows,
  each ≤ 160 characters, then one hint line naming `ui_context(dismissed=[...])`. The reply stays
  under `CONTEXT_BUDGET` (3,800).
- **`ui_context(dismissed=[...])`** hides rows: `"adv:3f9a"` dismisses (until it gets worse),
  `"adv:3f9a snooze"` snoozes 1 h of play, `"adv:3f9a snooze 4h"` or `"… 30m"` that long. It
  writes as chat (no `rev`), journals `advice.hide` with chat as the actor, prints
  `hidden on the page: adv:3f9a (dismissed)` after the header, and refuses an unknown id on its
  own line. The description says to hide one only when the user asks. The page can **restore**.
- **`world_summary`** ends with a `## worth a look` block: `adv:id · kind · subject · tool_text ·
  next: <next_call>` for every active row, at most 12, then `(+N more)`. Its `biomass=` reaches
  K4 and K5. The whole stays under `test_surface.BUDGET` (4,000); 2,923 characters on the
  reference save.
- **Asks about an advisory**: `asks.ABOUT_KINDS` gains `advice`, with `ref` the key and
  `label` the page text. `ui_context` prints `about advice "…" (adv:77b8)`.
- No tool was added; `INSTRUCTIONS` are unchanged.

## 7. Journal and the page

| Journal kind | Writer | `args` | `text` |
|---|---|---|---|
| `advice.hide` | web, chat | `{id, key, mode}` | `snoozed adv:5150 starved “speedwire factory” for 1 h of play`; chat: `chat dismissed adv:77b8 unconnected: …` |
| `advice.restore` | web | `{id, key}` | `restored adv:01ae` |

The page refetches `/api/advice` on: the save wave (a registered live fetch, rank 45, with the
spoiler query), an `activity` entry whose kind starts `advice.`, a `notes` event (labels are in
the answer), a `plans` event (plan revs are), a settings change (biomass, stage headroom,
`advice_box_fed`, spoilers), and a reconnect. Each refetch is a `latest("advice")` ticket, so
the newest reply wins. A chat dismissal reached an open page in 0.7 s (§10).

## 8. The page

- **Overview:** the card `worth a look` sits first under the tile row. **Factory detail:** the
  same card, filtered to that factory, under its tiles; no second fetch. Nothing else: no tile,
  nothing in the status strip, nothing on the map panel.
- A row is one line at desktop width: chip (`chip(word, tone)`), `new` (an id not seen in this
  browser before, kept in `localStorage` `advice.seen`; nothing is new on a first visit),
  `back`, the text, then actions. Clicking the line (a button with `aria-expanded`) opens its
  evidence lines; a hidden row's first line says how it is hidden and for how much more play.
- Actions: **map** (one machine: fly and pin it; more: fly to the box and outline it; the row's
  `reveal` layers turn on), **trace** (K1, K3, K6, box: what feeds the seed), **power** (K4, K5,
  a link), **open plan** (K8, a link), **ask** (queues `about advice`; the ask bar opens inside
  the card), and the split button **snooze 1 h ▾**. The ▾ opens a menu: 30 min, 1 h, 4 h,
  10 h, until it gets worse. Arrow keys move in it, Escape closes it and returns focus to ▾,
  a click outside closes it. A hidden row has **restore** instead of ask and snooze.
- `+N more (…)` and `N hidden · show` are text links. The card is never hidden: an empty list
  says `nothing worth a look in this save` (or `in this factory`).
- Tones: act → `blocked` yellow, consider → `mid`, note → `muted`. No red; the verifier counts
  zero `--bad` colours in the card.
- Below 600 px a row wraps: chip and text first, actions on the next line, every control at
  least 44 px tall. No sideways scroll at 390 px.
- Settings › advisors › *note emptied hand-fed boxes* is the shared `advice_box_fed` switch.

## 9. Cost (measured)

Reference autosave: 450 machines, 74 extractors, 62 generators, 16 named factories, 5 plans.
Median of 5, main repo `.venv`, the worktree's `src`.

| Step | Measured | Budget |
|---|---|---|
| Pass, facets built (warm) | **9.7 ms** (10.2 max) | ≤ 25 ms |
| Pass on a fresh projection, plans already known | **88 ms** (129) | ≤ 150 ms |
| Of which K8, 5 plans solved (per (plan, rev, save, labels), then cached) | ~80 ms | – |
| Pass on a fresh projection, nothing derived yet (pays proposals, ~0.55 s, which the map's factories layer shares) | 686 ms | – |
| `GET /api/advice`, cached | 6–8 ms | – |
| Chat dismissal → gone from an open page | 0.67 s | ≤ 1 s |

Rows are cached per (projection, game, labels version, plan heads, settings, spoilers) in a
singleflight of eight; the head-lift model per projection. A route and a tool asking at once
compute once.

## 10. Verified

Headless Chrome over CDP on the reference autosave, user data a scratch copy, port 8734:

1. Overview at 1440 × 900: the card is the first card under the tiles, 5 rows, one line each,
   `+10 more (3 starved, power, 2 no recipe, 3 plan, pickups)`, zero red, no `null` / `NaN`.
2. The ▾ opens the five-item menu; ArrowDown moves, Escape closes and refocuses ▾.
3. *until it gets worse* hid the first row; `1 hidden · show` listed it with **restore**;
   restore brought it back. Both journalled.
4. **ask** opened the ask bar in the card and queued `ask:1` about the advisory.
5. Chat's `ui_context(dismissed=["adv:77b8 snooze"])` removed the row from the open page in
   0.67 s; the hidden list said `snoozed by chat, 1 h of play left`.
6. Factory detail (`tor factory`, `north oil rig`, `copper setup`): the filtered card, with
   `1 hidden` where chat had snoozed one and the empty line where nothing fires.
7. At 390 × 844 the same, two-line rows, 44 px controls, `scrollWidth == innerWidth`.
8. **map** on the pickups row turned on the three pickup layers and outlined the box;
   **trace** on the tor factory row drew the refinery's supply.

On the reference save 15 rows fire: unconnected 2, no node 2, starved 4, power 1, no recipe 2,
plan 3 (all node owners), pickups 1.

## 11. Decisions (2026-10-05)

- A1a: advice, as a measured fact plus where to look; fixed order, no score, no ETA.
- A2a: chat may hide advisories, journalled with the actor; the page restores.
- A3: one-click snooze of 1 h of play time, plus a picker (30 min, 1 h, 4 h, 10 h, until it gets
  worse). Play-time clock. A hidden row comes back early when a new machine joins or severity
  rises.
- A4a: blocked appears only as evidence.
- A5: no hard-drive advisories at all; drop pods and hard drives are left out of K9 too.
- A6a: no box-fed advisory by default; a shared Settings switch turns it on.
- A7a: K10 "unlocking X changed plan Y" is phase 12b.
- A8a: the Overview card and the factory-detail card only.
- A9a: 5 rows visible, 3 per kind; K5 under 5 % on the shared stage-headroom figure.

## 12. Open

- K8 is computed on the first read of a save rather than on the save prewarm. Its five solves
  are cached per (plan, rev, save, labels); the first read after a save pays them (~80 ms here)
  and, on a fresh process, the proposals K8's built detection needs.
- The `new` mark is per browser and per data version; a server-side "new since the last save"
  needs the timeline (phase 12b).
- Play time moves only with a new save, so a snooze ends at the first save past its mark.
