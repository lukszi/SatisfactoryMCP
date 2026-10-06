# Planner P5: site drag, the contract

The build contract for phase P5 of [planner_vision.md](planner_vision.md) §8, *site*, with
vision §4.7 (site on the map) and §5 (map integration). It builds on P1
([planner_slice_contract.md](planner_slice_contract.md)), P3
([planner-p3_contract.md](planner-p3_contract.md)) and P4
([planner-p4_contract.md](planner-p4_contract.md), §17 built detection) and forks none of them.
Every write is one `site` op through `PlanLog` and merge rule M1.

Where this file and the vision note disagree, this file wins for P5. Departures are in §12;
open questions are in §13.

---

## 1. Decisions (2026-10-05)

| Id | Decision |
|---|---|
| P5-1a | Touch: **crosshair mode** (the map pans under a fixed pad, then **[drop here]**). Desktop: drag handles, plus **[move by panning]**. Keyboard works too |
| P5-2a | A drop outside the **map square** is refused: a 400 on the write, a refusal line on the page. Outside the playable content box it is only a muted line |
| P5-3 | Snapping is a **shared setting** `site_snap`: `fine` (1 m, 15° steps, the default) or `grid8` (8 m world grid, 90° steps; owner ruling). Shift moves freely |
| P5-4 | The ground height comes from a **provider** (§6). Until one is installed `z` stays null and the page says `terrain height: pending` |
| P5-5b | Moving a plan off machines that count as built asks first, inline, never a browser dialog: `28 of 133 built here → 0 at the new spot · [move anyway] [cancel]`. Ctrl+Z still undoes the move |
| P5-6a | W×D is typed. No edge handles |
| P5-7a | `site_plan(preview=True)` shows a ghost pad on the page with **[use it]** and writes nothing |
| P5-8a | The pad never moves the plan's sources. **[fit pad to “…”]** covers the half-built case |
| P5-9 | Plan first. An empty pad with no plan (spot first) is later |

---

## 2. Scope

| In | Out (later, or open) |
|---|---|
| A fourth result tab, **build list · graph · track · site**, at `#dash=planner/<key>/site` | Resize by handle (P5-6a) |
| Move and turn a stored plan's pad: handles, crosshair, keyboard, typed fields | Routes over terrain; trunks stay straight-line lower bounds |
| A throttled live preview while the pad moves (§4) | Snapping to pins (design §6), see §13 |
| One `site` op per gesture: undo, the 409 chip, chat sees it | `allocate` for overlapping plans; overlaps are only flagged |
| **[place]** on Track's `not placed` line opens the site tab | Spot first (P5-9) |
| Chat: `site_plan(preview=True)` and a ghost pad; `site_plan` writes on the shared snap | Chat dragging anything live |

---

## 3. User flows

### F1 Open the site tab

- From the tab bar, from **[place]** on Track's built line (a plan with no site), or from
  **[move]** in a pad's popup on the main map.
- The dashboard narrows to a column on the right (`body.site-on`, 440 px or 46 vw) and the
  page's own map shows on the left. Below 700 px the column becomes a bottom sheet, 48 vh.
  Leaving the tab (any address change) drops the class, the handles and the ghost.
- The map frames the pad (and chat's ghost, when there is one). The bench controls are hidden
  on this tab; the header, versions, strip and conflict chips stay.
- A never-sited plan starts at its `near:` centre, else its nodes' centroid, else the map
  square's centre, sized by the layout square (`site_preview.initial_siting`). The card says
  `not placed yet`, and a drop there is the first placement.

### F2 Move on a desktop

- **Move handle**: a 16 px square at the origin, cursor `move`. **Turn handle**: a 16 px circle
  1.5 × the half-depth out along the pad's local +Y, cursor `grab`. Both are Leaflet markers on
  the pane `sitedrag` (z 640); no new dependency.
- Every frame redraws the outline from `footprintCorners`, snapped (§5). Shift turns the snap
  off. The card's head line and fields follow at once; the feedback lines follow the preview.
- **Escape** while dragging puts the pad back and writes nothing.
- Release commits (F6).

### F3 Move by keyboard

- The move handle is focusable: `aria-label="pad of “<plan>”, move with arrow keys, turn with
  [ and ]"`, `data-ctl="site-move"`.
- Arrows nudge 8 m and land on the snap; Shift+arrows nudge 1 m freely; `[` and `]` turn one
  snap step (15° on `fine`, 90° on `grid8`) to the next step angle; Shift+`[`/`]` turn 15° freely.
  North is up (−y).
- A burst is one gesture: it commits 600 ms after the last key, on Enter, or on blur. Escape
  restores the pad from before the burst.

### F4 Crosshair mode (touch, and [move by panning] on a desktop)

- On a coarse pointer the handles are not drawn and the card offers **[move]**.
- The pad jumps under a fixed cross at the centre of the **visible** map (the part the column
  or the sheet does not cover). Panning the map moves the pad; each `move` event is a step.
- **[⟲ 15°] [⟳ 15°]** turn it (the label and the step read **90°** on `grid8`), landing on the
  next step angle; **[drop here]** commits; **[cancel]** puts it back. Buttons are
  44 px on a coarse pointer.

### F5 Typed fields

- x, y, yaw, W and D commit on Enter or blur, unsnapped. A typed W or D records the footprint as
  `given`.

### F6 The drop

1. Same as the stored pad: nothing.
2. Any corner outside the map square: refused on the page (`outside the map: the drop is
   refused`), the pad goes back, nothing is sent.
3. Otherwise a full-resolution preview of the drop spot (`full=1`). When it reports `loses`
   (§4.3), the card shows the inline confirm and nothing is written until **[move anyway]**.
   **[cancel]** puts the pad back.
4. One op: `POST /api/plans/{key}/ops {"base_rev", "ops": [{"op": "site", "value": …}]}` with
   `origin_m [x, y, null]`, `yaw_deg`, `footprint_m`, `footprint_source` (kept, or `given` after
   a typed size or a fit), `origin_label` (`map`, `chat preview`, or `built “<name>”`).

It is an ordinary own commit, so Ctrl+Z undoes it, a chat move in between gives the P1
conflict chip, and the history list words it (§5.3).

### F7 Fit pad to built

When detection at the pad sees a candidate whose machines the pad does not cover, the card
offers **[fit pad to “<name>”]** (up to 3). The value comes from the server (`fits[].value`):
the candidate's box centre, unturned, the box plus 16 m rounded up, `given`. It skips the
confirm, since it moves the pad onto what is built.

### F8 Chat looks at a spot

- `site_plan(plan, at=…, preview=True)` resolves `at` as the write would, snaps it, answers in
  words (§7) and journals `plan.view` with `args {"view": "site", x_m, y_m, yaw_deg, w_m, d_m}`.
- Follow on: the page opens that plan's site tab, draws a **ghost pad** (dashed, `--ink-hi`) and
  says `chat is looking at x, y · [use it] [dismiss]`. Toasts: a toast with **[open]**. Off:
  nothing.
- **[use it]** runs F6 with the ghost's pad and `origin_label: "chat preview"`. A new head that
  equals the ghost, a use or a dismiss clears it.
- A ghost that arrives mid-gesture is drawn but the map does not move until the gesture ends.

---

## 4. The preview

### 4.1 Route

`GET /api/plan/site-preview?key=&rev=&x_m=&y_m=&yaw_deg=&w_m=&d_m=&first=&full=&biomass=&headroom=`
→ `SitePreviewResponse` (`routers/plan_site.py`).

- Any of x, y, yaw, w, d left out comes from the stored site, else from `initial_siting`.
- `first=1` adds `nodes` (the plan's chosen nodes) and `content_bbox_m`.
- `full=1` reads the terrain at 1 m; otherwise the window is capped at 40,000 texels.
- Unknown key or rev: 404. A pad with any corner outside the map square: 200 with
  `in_map: false` and only `region` filled. A plan that does not solve: 200 with `failure`.
- A per-process LRU of 8 sessions keyed `(world, key, rev, save token, biomass, headroom)`
  holds the solve, the partition and the figure at the stored site, so a step only measures.

### 4.2 Fields

| Field | Meaning |
|---|---|
| `x_m y_m yaw_deg w_m d_m source sited` | The pad asked about; `source` is the footprint's, `sited` whether the plan has a site |
| `in_map in_content region` | Map square (hard edge), playable box (warning), region label |
| `z_m z_note` | The provider's ground height (§6), else null and `terrain height: pending` |
| `terrain terrain_note` | `Field.window` over the pad's box: z min/median/max, slope, roughness, submerged %, stride, and water distance and drop; `no terrain field on this machine` or `terrain not read` |
| `slabs on_pad planned` | Foundation slabs the pad meets; the class census on the pad against the plan |
| `trunks placeless` | `plan_trunks` toward the pad: `run_m` node to node, **`to_site_m`** the leg to the pad, `lift_m` the last node over the pad's ground, `pumps` for pipes |
| `built now` | `built.detect` + diff + stages with the pad **here**, and at the stored site |
| `basis` | First placement: `counted on its pad from now (was: <old area>)` |
| `loses` | `{now, here, total, text}` when auto detection counts fewer built here than at the stored site |
| `fits overlaps` | §F7; names of other plans whose pads meet this one |

### 4.3 Losing progress

`loses` is set when the stored site counts `now.built > 0`, the plan is in auto mode, and the
pad here counts fewer. A picked factory, `/world` or `/none` never loses: the pad does not
change their count, and the built line says so.

### 4.4 Throttle

One request in flight; the newest pending pad replaces older ones; the next request waits
`clamp(3 × last round trip, 120 ms, 400 ms)` after the last one went out. A reply that no longer
matches the pad is still shown, dimmed. Steps never write.

---

## 5. The `site` op

### 5.1 Normalise (`siting.normalise_record`, called from `planlog.ops._canonical_op`)

| Field | Rule |
|---|---|
| `schema` | Absent or 1; a newer one is refused |
| `origin_m` | `[x, y]` or `[x, y, z]`, finite; x, y inside `geo.MAP_SQUARE_M` (= `tiles.DEFAULT_MAP_BOUNDS_M`): `the site is outside the map (x must be -3,247…4,253 m)` |
| `yaw_deg` | Finite, normalised to [0, 360) |
| `footprint_m` | `[0, 0]` (origin only) or both 8…2,000 m |
| `footprint_source` | `given`, `layout`, `default` or `""` |
| `origin_label`, `when` | Text of at most 80 and 40 characters |

The output is the canonical record; unknown keys are dropped. A null `z` is filled from the
ground provider (§6). The page's push, chat's `site_plan` and `restore_to` all pass through it,
so they cannot store different shapes. Undo does not: it replays the stored inverse.

### 5.2 Snap (`siting.snap`, `sitedrag.ts` `snap`)

- `fine`: the centre rounds to 1 m.
- `grid8`: the pad's west and north edges land on the 8 m world grid (`x − w/2` and `y − d/2`
  are multiples of 8).
- Yaw rounds to 15° on `fine` and to 90° on `grid8` (`siting.yaw_step`, `sitedrag.ts` `yawStep`).
  On `grid8` yaw snaps first; at 90° or 270° the pad's extent along x is its depth, so the
  grid rule uses `x − d/2` and `y − w/2`. Shift (page only) skips the snap.
- A stored yaw off the `grid8` lattice (set on `fine`, or typed) moves to the nearest quarter
  turn on the next snapped write, including a chat write that does not name a yaw.
- Chat's `site_plan` writes and previews are snapped with the shared value; the page's typed
  fields are not.

### 5.3 Words (`describe_op`, `_action_words`)

`site set at 1,476, -2,098 (Rocky Desert)` · `site moved 1,503 m west` · `site moved 212 m
north-east, turned 30°` · `site resized to 200×120 m` · `site cleared`. A record that does not
parse falls back to `site set` / `site moved`.

---

## 6. Ground height hook

```python
from satisfactory_mcp.domain.planning import siting
siting.set_ground_z(provider)   # provider(x_m, y_m, yaw_deg, width_m, depth_m) -> float | None
```

- `siting.ground_z` calls it; an exception or a non-finite answer reads as None.
- `normalise_record` fills a null `z` with it on every write; the preview shows it as `z_m`, and
  the trunk lift uses it before the pad's median ground.
- Nothing installs a provider yet. The terrain-height work wires it with that one call where the
  web app and the MCP server start, and the page then shows `ground height … m` instead of
  `terrain height: pending` with no page change.

---

## 7. Chat

- `site_plan` gains `preview: bool` (one parameter, one line). With `preview=True` it needs no
  `base_rev`, writes no op, journals `plan.view`, and answers with the same sections as the
  card: where, height, terrain, water, floors, on the pad now, trunks with to-site legs, built
  here against built at the stored site, the stage, a `loses progress` line, fits and overlaps.
- Its last line says how to keep it: `site_plan … base_rev=N`, or **[use it]** on the page.
- `settings()` lists `site_snap` like any shared setting (docs/shared-settings.md).

---

## 8. Frontend

| Module | Does |
|---|---|
| `sitedrag.ts` (new) | Handles, crosshair, keyboard bursts, snap, node lines, the ghost. No fetch: it reports `step` and `commit` |
| `planner-site.ts` (new) | The card, the throttled preview loop, the drop with its confirm, fit, [use it], the split class |
| `planner-core.ts`, `planner.ts`, `planner-result.ts`, `planner-bench.ts` | `ResultTab` `"site"`, the address, follow for `plan.view view=site`, the tab |
| `planner-built.ts` | **[place]** opens the site tab |
| `plans.ts` | **[move]** in a pad's popup; `PLAN_COLOUR` exported for the drag outline |
| `settings.ts`, `words.ts`, `style.css` | `siteSnap` (shared `site_snap`); `W.site`, `W.dropHere`, `W.moveByPanning`, `W.fitPad`; `.site-*` rules from tokens |

---

## 9. Performance (measured 2026-10-05)

Reference world, newest autosave, warm process, terrain field present.

| What | Measured |
|---|---|
| One step, `spire-coast-e1` (184 m pad, auto) | browser round trip 9–16 ms, most 10–13 ms |
| One step, `north oil rig` (432 m pad, picked) | browser round trip 18–46 ms |
| First reply (session open, solve, partition) | 45 ms |
| Release at full resolution | 41–49 ms |
| Fixture world through the test client | 12–14 ms per step; p95 under 60 ms is a test |

One fix was needed to get there: `built._cluster_names` named every proposal on every call
(about 300 ms on the fixture); it now keeps the names per state and labels version.

---

## 10. Tests

- `tests/test_site_drag.py`: the check and its refusals, the map square against the tile
  router, a refused op writes nothing, playable-box edge allowed, words, undo, the `site` vs
  `site` conflict, no-op drops, both snaps, fit to built, the ground provider, trunk legs, and
  the preview (built moves with the pad and writes nothing, picked does not follow, outside the
  map, first reply, no terrain, fits, first placement basis, overlaps, the 60 ms budget).
- `tests/test_web_site.py`: the route's shape, `first=1`, 404s, a 400 drop outside the map,
  one version per drop and undo, `loses` on the new head, the p95 budget, chat preview → journal
  → page push as one version, and chat writes on both snaps.
- `tests/test_planlog.py`: site ops in the M1 and replay tests now carry valid sitings.

---

## 11. Acceptance (verified in headless Chrome, 2026-10-05)

At 1440 × 900:
- `spire-coast-e1` dragged about 1,500 m west: the built line read `nothing … · 0 / 133` and
  then `built at “oil setup” · 28 / 133` while dragging; the release made one version.
- Dragging it back east showed `28 of 133 built here → 0 at the new spot · [move anyway]
  [cancel]`; cancel wrote nothing, move anyway made one version, Ctrl+Z restored it in one step.
- Escape mid-drag wrote nothing. Three arrow keys and `]` made one version (`site moved 17 m
  east, turned 15°`); Escape mid-burst wrote nothing.
- A typed x of 4,300 was refused on the page; a typed W made `site resized to 120×184 m`.
- **[fit pad to “oil setup”]** made one version labelled `built “oil setup”`.
- `grid8` in Settings: a drag landed with both edges on the 8 m grid. **[place]** on Track opened
  the site tab of an unsited plan.
- An out-of-process `site_plan(preview=True)` opened the site tab with the ghost in about
  0.2 s; **[use it]** asked first (it lost progress) and then made one version.

At 390 × 844 with touch: no handles, **[move]** opened the crosshair at the visible centre,
a swipe moved the pad, **[⟳ 15°]** turned it, **[drop here]** gave the confirm. No sideways
scroll at either width.

---

## 12. Departures from the design

- The drag's stored outline in the `plan sitings` layer stays where it is during a drag (it
  shows where the pad was); the design hid it.
- A reply that no longer matches the pad is shown dimmed rather than dropped, so the card does
  not sit empty during a long drag.
- The throttle adapts to the measured round trip (§4.4) instead of a fixed 250 ms.
- The map is not re-parented: the dashboard narrows to a column and the map shows beside it.

## 13. Open

- **Pin snap** (design §6: snap within 24 px of a located pin, label `pin:N`) is not built.
- ~~Should `grid8` also step yaw by 90°?~~ Decided: yes (owner ruling); see P5-3 and §5.2.
- Chat's writes are snapped too. A typed coordinate from chat moves by up to half a cell in
  `grid8`.
- The ground provider is a hook only; `z` stays null until the terrain-height work installs it.
- `ui_context` reports the site tab as `tab: site` but not the pad position.
