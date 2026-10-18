# Frontend kit and shell

The design system the dashboard pages are being rebuilt on, page by page. Paths are relative to
`src/satisfactory_mcp/interfaces/web/frontend/src/`. No UI framework: vanilla TypeScript, one
escaping markup builder, a route table and a handful of parts.

## Rendering: one escaping builder

`kit/html.ts` exports `html`, a tagged template. Every hole is escaped unless it is a `Safe`
value, which only `html` itself (or `trusted()`) produces. So data is escaped by default and
markup composes: a part returns `Safe`, a page nests parts in `html` and puts the result in the
DOM with `mount(el, safe)` (or `toElement` for one node).

- Holes take strings, numbers, `Safe`, arrays of those, and `null` / `undefined` / `false`
  (which render nothing, so `cond && html\`…\`` works).
- `trusted()` is for markup the caller wrote itself; never pass data through it.
- `marked(text, query)` wraps search hits in `<mark>`, everything escaped.
- Behaviour is wired by event delegation on the page's root (`data-act`, `data-seg`,
  `data-chev` attributes), not per element, because pages redraw by replacing markup.

Why a string builder rather than element factories: it is testable in node with no DOM, a page
reads like its markup, and one function (`hole`) is the only place escaping can be forgotten.
`kit/dashkit.ts` (element factories) stays for the old pages until each is ported.

## Tokens and type roles

`kit/styles/tokens.css` holds every token, dark only. Rules use tokens by name, never raw values.
Text uses role tokens `--t-<role>-size / -w / -lh`: title 18, section 14, group 11 caps, name 12
bold, number 12 bold tabular, body 12, meta 11, figure 18. `kit/styles/kit.css` styles the parts;
phones are one `max-width: 600px` media query per stylesheet.

- **Radii**: three values only: `--r` (3 px; `--r-card` is the same), `--r-chip` (8 px pill),
  `--r-round` (circle).
- **Strokes**: hairlines are a literal `1px`; `--ring` (2 px) is the focus outline and its
  offset, `--mark-w` (2 px) the selected-tab underline, a picked row's edge and marker lines;
  `--edge` (3 px) the status edge.
- **Squares**: `--ic-s` / `--ic-m` / `--ic-l` (20 / 24 / 32) size icons and glyph buttons,
  `--dot` a status dot. **Bars**: `--bar-s` / `-m` / `-l` (4 / 6 / 8). **Heights**: `--ctl-h`
  (a control: line height plus two hairlines), `--row-h` (a list row), `--row-h-card`,
  `--touch-h` (a phone sheet row, and the least hit area of a control on phones; see Tap
  targets).
- **List columns** (`--col-*`): a list's `--cols` template is built from these, so the same kind
  of column has the same width on every page: `icon` 24, `icon-l` 32, `act` 24 (one glyph
  button), `short` 56 (a count, a percentage, a distance), `num` 72 (a number with its unit),
  `num-l` 96 (a long figure or a state word), `label` 112 (a bar, or a figure over a bar),
  `text` 150 (a short phrase), `name` 180 (the least a flexible name column gets). The flexible
  column is `minmax(0, 1fr)`. `--card-min-w` is the narrowest card in an auto-fill grid.
- **Action slots**: `--act-w` (112) is the one text-button slot; `--col-acts` is that slot plus
  the ⋯ slot (`--col-act`), `--col-acts-pin` adds a leading ◎ slot. See Action slots below.
- **Chips**: a fixed box, `--chip-h` (18) on its own and `--chip-h-in` (14) inside a text line
  (`.ctx`, `.nl`), the text centred by flex with `text-box` trimmed to the x-height; a
  line-height box alone sat lowercase words low.
- **Motion**: `--dur` with `--ease` for state changes, `--dur-fade` for a busy fade,
  `--flash-turn` for one beat of a node flash; spinners and sweeps keep `linear`.
- A value that belongs to one page only (a map block's height, a roadmap node) is a custom
  property declared at the top of that page's stylesheet on its root class, built from tokens
  where it can be.

## Parts

| module | parts |
|---|---|
| `kit/ui/text.ts` | `pageHead` (object-page header: breadcrumb, title, chips, figures, note, the object's own tabs), `dashH` (heading · n), `blockHead` + `fig` / `figSub` (at most two headline figures), `toolrow`, `note`, `emptyLine`, `icon` (game icon or monogram; a failed load becomes the monogram) |
| `kit/ui/entry.ts` | `list(name, mode)` — the list's column template is `.l-<name> .entry { --cols: … }` in the page's CSS — `entry`, `identity`, `nameLine`, `metric`, `facts`, `chips`, `acts`, `actSlots`, `chevron` + `expandBody` + `wireExpand`, `group` |
| `kit/ui/marks.ts` | `chip` / `tagChip`, `pill` / `flow`, `bar` / `mixBar`, `jobBar`, `slotTile` |
| `kit/ui/controls.ts` | `btn`, `busyBtn`, `glyphBtn`, `moreBtn`, `seg`, `selectBox`, `sortSelect`, `filterField`, `filterChip`, `switchBox`, `check` |
| `kit/ui/forms.ts` | `frow` (label column + one control column), `form` (groups split by a soft rule), `fnum`, `ftext`, `fadd`, `frange`, `checks` |
| `kit/overlay/*` | `openMenu` / `wireMoreMenus` (the ⋯ menu), `modal` (native `<dialog>`), `sidePanel`, `toast` |
| `kit/ui/states.ts` | `loadingLine` / `loadingInto` and `failedInto` (what could not be read, why, try again): a read in place of a page or block |
| `kit/busy.ts` | `busy(el, work)` and `busyButton(button, work)` |

### Action slots

A list's actions column is fixed slots that line up from row to row: `actSlots(action, { pin,
more })` draws an optional ◎ slot, the one text-button slot (the button fills it), then the ⋯
slot, always last and kept when empty. Two related actions may share the button slot as equal
halves (`{ split: [a, b] }`, the running map job's log | cancel); every other row keeps one
button. A state chip may stand in the button slot, never beside a button. Inline confirms reuse
the slots: confirm in the button slot, × (keep) in the ⋯ slot. Everything else goes into ⋯.
The list's template ends in `--col-acts` (`--col-acts-pin` with ◎, `--act-w` with `more:
false`), so the buttons sit at one x on every row.

### Tap targets (phones)

Under 600 px every control keeps its look and gets a hit area of at least `--touch-h` (44 px),
set once in `kit.css`, never per page. It covers `.btn` (glyph buttons, ⋯ and ◎ included),
`.chev`, `.fchip`, segments, `.otabs` links, the × of a `.pchip`, link pills and chips, the
switch, selects and text, search and number inputs.

- **Buttons and links** get a transparent `::after`, positioned on the control
  (`position: relative`), so neither layout nor the drawn box changes.
- **Inputs and selects** have no `::after`: their top and bottom borders become the hit area
  (transparent, with a negative margin of the same size), and the frame is an inset
  `box-shadow` in `--frame` (`--bad` when `aria-invalid`). Focused, the border goes back to
  nothing, so the focus ring sits where it does on desktop.
- **Neighbours**: the hit area grows up and left only by half the gap to the neighbour there,
  `--hit-gap` (column gap) and `--hit-gap-y` (row gap); the rest goes down and right. There it
  passes under the next control, which comes later in the page and so paints over it: no hit
  area covers another control's box, and a shared gap is split. The kit's rows declare their
  gaps (`.acts`, `.chips`, `.pills`, `.fchips`, `.kv`, `.checks`, `.tools`, `.title`,
  `.toolrow`, `.frow`, `.fadd`, `.btn-row`; `.seg` and `.otabs` have none); a page row with
  its own gap declares it the same way (`.qrow` on World). Labels are positioned too, so a
  label on the line below keeps its own taps.
- **Bars** cap the height at their own (`--hit-h`): the top bar at `--hdr-h`, the status and
  job strips at `--strip-h`. The top bar clips sideways, and `#dash` hides horizontal
  overflow, so a hit area at the right edge never makes the page scroll.
- **Clipping**: a segmented control drops its `overflow: hidden` and rounds its own ends; the
  tab row, which scrolls and so clips, has room above its rule and below the tabs for the hit
  areas; a slot button clips its label with `overflow: clip` and an `overflow-clip-margin`
  past the hit area.
- One look differs by a pixel: a select's arrow sits 1 px further right, because Chrome places
  it by the border edge, not the padding.

The hidden route `#kit` (`dash/kit-gallery/`) renders every part in its states for screenshot
checks.

## Loading rule

`busy()` sets `aria-busy` at once, adds `.is-busy` (spinner, hairline) only after 300 ms, and once
shown keeps it at least 500 ms. `busyButton` also disables the button and adds "… 1.2 s" after a
second. Long jobs show a 2 px determinate bar; the job strip under the top bar shows the running
map job on every page.

## Shell and addresses

The address is still the page's fragment (`map.ts` `hashFor`); `app/shell/router.ts` reads its
`dash=` value into a page, a subject and the lit top tab, and lists the old addresses that now
land elsewhere. A bare first word is a shorthand (`#maps` is `#dash=maps`).

- Top bar (`app/shell/topbar.ts`): Map · Overview · Factories · Power · Progress · Items · World ·
  Planner; tabs that do not fit go into "more". Phones get a bottom bar (Map, Overview,
  Factories, Planner, More).
- Jump search (`app/shell/jump.ts`, `/` or Ctrl+K): factories, items and recipes from
  `/api/search`, plans and circuits from what the page holds.
- Settings is a dialog (`app/settings-dialog/`), not a page: `settings=<section>` in the fragment
  opens it over the current page, so Back closes it. `#dash=settings` opens it over the page shown
  last; `#dash=settings/maps` goes to `#maps`.
- Map types, jobs, the generate form and inputs are the map studio, `#maps` (`dash/maps/`).

## Navigation

Back undoes where am I, never how it's shown. Every move goes through `app/nav.ts`; the
decisions are pure in `app/shell/history.ts`.

| Move | History |
|---|---|
| top-level page, open object (factory, plan, circuit, item sheet), object tab, version view, jump-search pick, Map tab, world or save switch | push |
| open a drawer or dialog (settings, alternates) | push |
| filters, sorts, picks, segmented controls, floor step, settings section, map pan/zoom, redirects, landing tab, renames | replace |
| close (×, Esc, "back to …") | Back if this entry was opened from that parent, else replace to the parent |
| breadcrumb | Back if the entry before is that parent, else push the parent |

- Each entry carries a stamp in `history.state`: its id, the dash it was opened from and the
  last `#dash` scroll (saved on click and when scrolling stops). A link the browser followed
  is stamped on arrival; `writeHash` keeps the stamp.
- Back or Forward restores the entry's scroll; a new move starts at the top. A fresh deep link
  has no earlier entry of this site, so Back leaves the site; no entries are ever inserted.
- Links keep a real href for middle-click; `data-nav="up"`, `"close"` or `"replace"` on the
  anchor makes a plain click follow the rule (crumbs, × and "back to" links, `seg` links).

## Overview

`dash/overview/` is the Overview (`#dash=overview`, and where an unknown `dash=` lands): five
glanceable blocks, each clicking through to the page with the detail. Headline figures sit in
the block heading at heading size; no list runs past three rows. Desktop has two independent
column stacks (factories and map left, about two thirds; power, do next and progress right);
phones get one column in the order do next, factories, power, progress, map, factory cells
two across. The order is the DOM order on both, picked at render by a 600 px media query.

- **Factories**: machines and need action in the heading; one cell per named factory, worst
  first (need action, then blocked, then most machines, lowest uptime): the factory's icon
  (`icon-choice.ts`), name, the need-action count or uptime, the state mix bar.
- **Power**: headroom now as the figure and headroom at maximum consumption under it (`headroomFigs`); the
  three worst circuits as label, utilisation bar (`utilBar`, one shared scale) and headroom
  now, as on Power, then "+N more".
- **Do next** (`model.ts` `todoOf`): power problems that stop machines (no wire, no
  generator), then machine groups needing action (`machine-health.ts` `issueGroups`), starved
  generators, blocked machines; most machines first within a rank. Three rows, "+N more" to
  Factories when a factory row is left out, else Power. A power row opens its factory's power
  aspect or its circuit; a machine row its factory's machines.
- **Progress**: the phase gate as held of owed (each part counted up to what is owed) from
  `/api/progress/gate` at the default horizon (the same read the Progress page makes), the
  first next step, and the plans as machines built of planned (`/api/plan/built`, plans whose
  figure counts machines at their current version).
- **Map**: a real Leaflet map from `map/mini-map.ts` (`overview-map.ts`), kept across renders
  and refitted only when the factories or the player move. One dot per named factory at its
  centroid, coloured only when it needs action, the player from `/api/world/here`. It does not
  pan or zoom; a dot opens the factory, anywhere else opens the main map there. Its picture
  is the World map's (`copyMode`, see "World"), never the biome cells while one exists.
- The "worth a look" advice card left the Overview; it stays on a factory's page. The old
  tiles (`dashkit` `tile`) and tables are gone.

## Power

`dash/power/` is the Power page (`#dash=power`, `page.ts`) and a circuit's page
(`#dash=power/<n>`, `circuit.ts`), both from `/api/power/circuits`; the circuit page also reads
factory health, and `/api/power/generators` when its circuit is short.

- **Words**: the game's circuit terms, lowercase (`kit/words.ts`): **production capacity**
  (rated generation less what starved generators cannot run), **power consumption** (measured;
  machines without a monitor count in full), **maximum consumption** (every machine flat out at
  its saved clock; "max consumption" in a row's second line and on factory cards). Headroom
  stays ours: **headroom now** and **at maximum consumption**.
- **Starved generation never counts.** The server leaves it out of `capacity_mw` and both
  headrooms; the capacity line, overdraw, the circuit bars' 100% and the worst-first rank use
  it. The capacity bar keeps the rated length with each type's starved part hatched.
- **Supply and demand** (`supply.ts`): three bars on one scale, the largest of rated
  generation and both consumptions. Production capacity is segmented by generator type; a
  white line marks it, so the gap to it is the headroom, and consumption past it turns red.
  Under the bars, "not counted" chips with tooltips name what the figures leave out: hand-fed
  biomass, starved MW, unmeasured machines, paused buildings, what stands on no wire, and
  generator types game data cannot rate (no line then).
- **Problems** (`model.ts` `problemsOf`): one building with one fault at one place is one row;
  no wire and no generator first, starved generators next, generators on no wire last and
  muted (they are not a fault, just left out).
- **Circuits**: worst first (no generator, short now, a starved generator, then least
  headroom per MW of capacity). The bar's 100% is that circuit's own production capacity, and
  every bar shares one overflow room (`utilScale`, up to 150%), so overdraw reads alike.
- **Circuit page**: a ledger, no tabs. A slim header ("circuit N", the numbering caveat as its
  tooltip, at most one chip, ◎, one note line of counts); the supply-and-demand card with
  headroom in its heading and both consumption rows split per named factory (`CircuitRow.factories`,
  each factory's draw on this circuit only), the remainder "outside named factories" last;
  "do next" (feed a starved generator, wire machines on no generator, build the biggest
  generator type the world can run when consumption passes rated generation); then generators |
  draw by factory, 2 : 3 on desktop, stacked on phones. Hover or focus on a consumption segment,
  or on the build row's name, opens a popover (`seg-tip.ts`): a factory's two consumptions with
  their share of the circuit and its paused and need-action machines; or every generator type,
  biggest first, with its verdict. A grid with batteries gets a fourth row, "power storage"
  (`storageRow`): the charge held against its capacity, MWh, on its own scale and below the
  capacity line, which does not cross it. It stays out of headroom.
- `readings.ts` reads a ledger for every surface (the header, the map's side panel, the
  planner); `ledger-bar.ts` is the compact DOM bar the side panel uses.

## Factories

`dash/factories/` is the Factories page (`#dash=factories`) and a factory's page
(`#dash=factories/<name>[/<aspect>][?floor=<n|all>]`).

- **List** (`list.ts`, `clusters.ts`): the unnamed clusters first, compact (name field, what
  they make, machine count, ◎, name, ⋯ graph / skip); then the named factories as equal-height
  cards (status edge, icon, name, "N machines" with power chips, need-action count or uptime,
  power consumption and max consumption, the state mix bar on the card's bottom edge, an expand
  with the worst machines). The sort select (need action, name, uptime, power consumption) groups into "need
  action" and "running" when sorting by need action.
- **Icon** (`icon-choice.ts`): the main product the health read names (`product_icon`: of the
  surpluses a recipe makes, the highest sink value, then the larger surplus; an extraction-only
  factory shows its main resource; a tie or nothing is no icon, an empty slot, never letters;
  `domain/factories/product.py`). "change icon" in ⋯ picks any item or none; the choice is the
  label's `icon` in the world's label file (`POST /api/labels/icon`, the health row's `icon`),
  so it follows every rename and shows wherever the factory does: overview cells, the list and
  page, a circuit's factories and the jump search. A choice an older page kept in this
  browser's localStorage moves to the server once, unless the label already has one
  (`local-icons.ts`).
- **Page** (`page.ts`, `ctx.ts`): the header carries need action (x of machines) and uptime as
  figures and the aspect tabs. The plan sits left (sticky; on top on phones), the aspect right;
  flows takes the whole width. `ctx.ts` joins the reads once per render: the factory's rows of
  `/api/machines` (label owner, or listed by `/api/factories/aspects`), grouped by building and
  recipe; its floors from `/api/floors?factory=label:<name>` (bands holding its machines, merged
  over platforms by ordinal, top down); the floor shown (asked in the address, else the floor
  with most machines needing action, ties to the largest deck; "all" below two floors).
- **Plan** (`plan.ts`, `plan-geometry.ts`): machines as their outlines and height bands
  (`shapes`, band veils from `map/drawn/outline-geometry.ts`), decks from the bands'
  `deck_rows` into `/api/structures`, belts from `/api/belts` by the chains `/api/floors` files
  on each floor (or, without floors, those crossing the machines' box). The page is turned by the
  most common deck yaw modulo 90 so the grid is square. A floor shows its deck, belts and
  machines, with the floor below as a dashed ghost. Outlines take a status colour only when the
  machine needs action. Outline styles are inline in the symbols, because stylesheet rules do
  not reach `<use>` copies; custom properties do.
- **Linking**: plan marks carry keys (`g<group> m<id> fl<floor> s<site> st-<state>`), rows one
  of them in `data-hl`; hovering either lights the other, a click picks (again or Esc clears).
  On overview and flows a machine click opens machines with its group picked; a floor row shows
  that floor. The pick lives for the page session.
- **Flows** (`flows.ts`, `graph.ts`): the production graph from `/api/factories/graph` on the
  shared canvas in its card style (`NodeStyle "card"` in `prodgraph/draw.ts`: building disc,
  recipe and ×n beside it, the measured ring round the disc, line width in four rate steps), at
  1:1 and scrolled rather than fitted; the balance per item below, grouped by verdict. A floor
  shown elsewhere is only noted. Hovering a balance row or a line lights that item. On phones
  the graph keeps scrolling inside its frame (a double tap fits it), on purpose, as the plan's
  flows do.
- **Worth a look** (`chat/advice.ts`), under the overview aspect: the advisories about this
  factory, one line each (kind chip, "new" or "back", the text; a hidden one says for how
  long), ◎ to the machines, the evidence in the row's expand, and ⋯ for trace, power, the plan,
  ask chat, snooze (30 min to 10 h of play) or hide until it gets worse, restore when hidden.
  Five rows, at most three of a kind, then "+N more"; hidden ones fold under the list.
- **Machines** per recipe (count, worst state, clock as "100% clock", shards and sloops from the
  progress reads when any are held) or per machine (state, clock, uptime; six per group, then
  "more"). Power is the draw rows of the supply block and the circuit links. Nodes, links,
  floors and sites are compact lists; floors and sites light theirs on the plan.

## Items

`dash/items/` is the Items page, one page for what is held and what exists:
`#dash=items?view=held|all|containers&item=<slug>&box=<id>&rec=<recipe>&locked=1`
(`address.ts`). `item` and `rec` take a name's slug or a class id. A pick lives in the address;
a second click on its slot, × or Esc clears it. Picking does not move the scroll
(`scrollKey` in the router keys scroll by view).

- **Views** (`grid.ts`): square slots (`slotTile`), the held total compact under the icon.
  **held** (default) groups parts, raw, fluids, boosts, other; **all** is the catalogue from
  `/api/gamedata/items`, grouped by category; **containers** shows `/api/stock` places, the
  region and a fill bar in each slot (crates have no fill), grouped storage / fluid buffers /
  crates, by region, or one group by fill. Filter left of the sort select; sorting by count or
  name merges the item groups and keeps fluids and boosts apart. Filter and sort stay for the
  page session, not in the address.
- **Spoilers:** a catalogue item is `locked` when recipes make it and the save has none of
  them. Locked items are hidden while spoilers are off; "show locked" (needs spoilers on)
  shows them faded. A hard drive on hand that offers a recipe keeps that recipe and its item
  in view either way, with a "drive N" chip (`/api/progress/harddrives`).
- **Sheet** (`sheet.ts`): beside the grid, sticky; nothing picked means no sheet. An item:
  header (held, sink points, solid or fluid); **have** when any is held (piles as fixed rows;
  Power Shard and Somersloop as free / craftable / in machines from the progress reads, slugs
  add "makes shards"; the places holding it, biggest first, five then "+N more", ◎ flies to
  one); **made by** from `/api/gamedata/alternates` (default first, a flow row per recipe:
  inputs, the machine's icon and MW, outputs); **used by** from
  `/api/gamedata/recipes?consumes=` (main product icon, recipe, machine, rate). A pill or row
  picks that item. A container: region, distance, fill, contents. On phones the sheet is a
  bottom sheet above the bottom bar and the grid takes four slots a row.
- **Links in:** old addresses redirect (`inventory[/<item>]` to held, `recipes…`,
  `recipes/item/<cls>`, `recipes/recipe/<cls>`, `item-<slug>`, `recipe-<slug>` to all). A
  recipe without an item opens its main product with that row marked. `itemDash()` in
  `stock.ts` links an item to held when any is held, else to all; the jump search uses it.
- **Categories** are worked out on the server (`core/gamedata/categories.py`), since the
  game data has none: fluids by form; boosts (the shard descriptor class and the slugs that
  craft shards); raw by the resource flag; space elevator parts by class; equipment,
  ammunition, consumables and packaged fluids are other; ingots by name; then the AWESOME
  Shop's category of the item (minerals, oil products, standard parts, industrial parts,
  electronics with communications); nuclear for fuel rods, their wastes, what default
  recipes make from those and what goes into a rod; the rest is other. Raw, other and the
  catalogue groups carry a dotted underline: worked out, not sent by the save.

## World

`dash/world/` is the World page, one page where there were seven tabs:
`#dash=world?show=nodes|conduits|pickups&res=<class>&near=<place>&group=…&sort=…&mode=…&kind=…&radius=…&sel=<key>`
(`address.ts`; defaults are left out, so the bare address is the nodes nearest the player).

- **Strip**: where the player stood at the save (position, height, region and grid, the nearest
  building), one line and no card, from `/api/world/here` in the live wave (`here.ts`). A save
  with no pawn says so, and "near me" then means nothing: nodes and pickups are read without a
  place, conduits ask for a factory.
- **Finder** (left, about 40%): the query card's first row picks what to show and, for nodes,
  the resource; the second the place (me or a factory; any other place an old link carried shows
  as its own segment), the grouping, the view's own controls and one sort select. Filters an old
  address carried that have no control (purity, status, within, pure only, a network) show as
  chips with ×. Rows have fixed columns (lead, name, chips, figure, distance); extractor, region
  and grid sit in the row's tooltip. Lists show 30 rows; "show more" doubles that for the page
  session.
  - nodes: grouped free / tapped / locked, or by region with the region names on the map;
    fields as hulls of their member nodes (the places come from the map's `/api/nodes`, the box
    when a node is unknown); "best rank" (fields, one resource) lists `/api/world/sites`, whose
    distance the page measures (dotted underline).
  - conduits: runs (by length or distance) drawn as lines, or fluid networks as a ring at their
    centre, within a radius, belts and/or pipes.
  - pickups: collected of placed per kind as tiles (a tile filters to its kind), then remaining
    (`mode=nearest` from the player, so the server measures) or collected (measured on the page).
- **Map** (right, sticky, full height; 40vh on top on phones): a real Leaflet map, one instance
  kept for the page's life and moved into each render (`world-map.ts`). `map/mini-map.ts` makes
  it on the main map's terms: its CRS and frame from `map.ts`, a base picture as a plain copy
  (`baseCopy` in `tiles.ts`: baked light, and a failing tile there changes nothing on the main
  map), and the biome cells (`regionCells` in `regions.ts`) only when no picture exists at all;
  they are opaque, so the picture arriving clears any drawn before it.
  The picture (`copyMode`, rule in `copy-mode.ts`) is the main map's, else the default the main
  map would open on, else the artwork, else any probed one. A main map still unopened (a page
  loaded straight onto the dashboard) or set to plain still gets a picture: a mini map is for
  orientation. It follows every mode change and probe (`onBaseChange`), not only its renders. Node and pickup colours and sizes are the main map's (`markers.ts`, `pickups.ts`).
  It draws only the rows listed, plus the player and the factory searched near, and refits only
  when the results change; a legend of glyphs and single words and the zoom buttons are its only
  chrome.
- **Linking**: rows and marks share a key (`model.ts` `keyOf`). Hovering or focusing a row lights
  its mark in `--ink-hi` and the reverse; a click on either picks it (`sel=` in the address;
  again or Esc clears; the other marks fade), makes it the page's selection (what the chat
  sees), and a mark click scrolls its row into view. Picking keeps the scroll (`scrollKey`).
- **Links in**: the old `world/<view>?…` addresses (here, nodes, fields, rank, regions,
  conduits, pickups) land on the matching query (`legacyWorldDash`); the map's finder card, the
  inspector and the chat's world finds link through the same mapping (`fromLegacy`). The
  regions table (area and direction per region) is gone; grouping nodes by region replaces it.

## Planner

`dash/planner/plans/` is the Planner page (`#dash=planner`); `dash/planner/plan/` is a plan's own
page (`#dash=planner/<key>[/…]`, see "Plan page"). `planner.ts` mounts either, and holds a redraw
while a field there has an unsaved value.

- **Cards** (`cards.ts`, `model.ts`), two across, "+ new plan" first: an item (from the game
  data's names) and a rate make a plan named "<item> <rate>/min" (least raw, then power) and
  open it. Each card: the plan's icon, name, goal chip, version, the factory it is tied to (only
  a named factory links), export pills (icon and rate, the name in the tooltip; a power plan
  with no item exports shows "MW"), and the built line from `/api/plan/built` for that version:
  "x / y built" with a bar, or percent of the planned rate (a click switches, as the "progress"
  setting), else "no site", "which factory?" (to Track) or why it does not solve. At most one
  status chip: the first of `status` (all of them in the tooltip), else "done", uncoloured.
  Sort: last change, name, built (unplaced last).
- **Icon**: the first export's item, unless "change icon" in ⋯ chose one; the choice is plan
  state (`set icon`, so it is a version and undo takes it back; the index row's `icon`), with
  the same picker as factories. The jump search shows it too. A choice an older page kept in
  this browser moves to the plan once, as for factories. The other ⋯ actions: rename in place, duplicate (opens the copy), pin,
  forget. Renames and forgets go against the version the list read; if the plan moved on
  meanwhile the write is refused and the list says to try again.
- **Activity** (`timeline.ts`): `/api/activity` (last 50), newest first, grouped by day in the
  browser's zone, on one rail: a "you" dot filled, chat hollow. One line per event: time, who,
  what, the plan; repeated finds and views fold into one line. Undo on plan versions after v1
  (a new version, from the plan's head); open on chat's solves (the solve card above the
  cards) and finds (the World query). A filter shows all, you or chat. Sticky beside the cards;
  under 1100 px and on phones it goes below them, cards one per row. On phones the who column
  goes: the text takes its room and wraps, and who leads a meta line under it (the plan, ×n);
  the versions tab does the same.
- **Chat** (`chat-lists.ts`): chat's last solve sits above the cards as one row (what chat
  asked for, its settings as chips, "not saved", then open its plan, new plan from it, ×).
  Pins and asks follow the cards as lists, one line per row: the `pin:N` or `ask:N` chip and
  the label or question, what it is about inline, one state chip (gone; waiting, seen,
  answered), ◎ or → for a pin's place, the rest in ⋯ (rename in place, copy, ask chat,
  delete). Phones keep the name and ⋯ on the first line and the chip under them. The plan
  page shows chat's solve the same way, with "apply to this plan" (or "replace this plan's
  request" when chat did not solve from it).

## Plan page

`dash/planner/plan/` is one plan, shaped as a bill: the factory page's header card and tab row,
then the tab. The reads and writes are the planner's own (`reads.ts`, `writes.ts`, the bench in
`state.ts`); the page only draws them.

- **Addresses** (`routes.ts`, pure): `planner/<key>/<tab>` with the tabs machines, flows, site,
  build (to build, `?stage=<n|all>`, `&pick=1` for "which factory?") and versions; `v<n>` a
  version read-only on machines, `alt/<item>` the alternates drawer on machines, `place` the
  pad editor beside the main map. A bare `planner/<key>` lands on the plan's step once the plan
  and its built figure are read (`settleLanding` in `planner.ts`, rule in `plan/model.ts`): no
  site and no named factory → machines; every machine built → flows; else to build. The
  address then names that tab. Old `graph` and `track[/<n>]` links redirect (`legacyPlannerDash`).
- **Header** (`head.ts`): breadcrumb Planner, the plan's icon (as on its card) and name, at most
  one status chip, ◎ to the pad on the map when sited, ⋯ (rename in place, change icon,
  duplicate, copy id `plan:<name>`, delete, which forgets; restore when forgotten); one note
  line: goal, version, the named factory, and "pushing…" or "conflict" while a write is out.
  No figures. "to build" carries the machines left to place (`to_build`, else built of total).
  Strips above every tab: forgotten, conflict lines (keep theirs or use mine), chat's solve,
  and the versions others made since the page opened the plan, each with undo.
- **Machines** (`machines.ts`): the result band (inputs → one pill per building with its count
  → exports, an MW export signed; net MW as its figure; "targets" folds the form open) or the
  server's cause when it does not solve, then the last solvable version greyed. The targets form
  (`targets.ts`) holds every request field, one version per change, plus the payback slider, the
  overclock-last switch and the plan's own stage headroom. Build rows grouped "step N" by
  `depth`, or sorted by building, machines or power: building icon, recipe (required, chat,
  pin and ask chips), in → out pills at the planned rate, "n × 100%" with the last machine's
  clock under it, signed MW, alternates and ⋯ (ban, pin, ask chat, the row's overclock choice).
  A row click picks it for chat. The drawer (`alternates.ts`) lists every recipe for the item
  with Δ machines, Δ draw and Δ raw pills, and require, ban or let the solver choose.
- **Flows** (`flows.ts`): the factory's graph canvas in its card style, from `SolveResponse.graph`,
  cards dragged to place them (stored as `place` ops, "reset layout" puts them back); the
  balance per item under it grouped in, out, leftover, internal. A card opens machines with its
  row picked; hovering a row or line lights that item.
- **Site** (`site.ts`): a mini map (`map/mini-map.ts`) of the stored pad, the nodes its trunks
  draw from (dots by the node's purity, which the preview now sends) and straight lines from
  them to the pad; beside it the pad's facts and the trunks, all from one
  `/api/plan/site-preview?first=1`. "move the pad on the map" opens `place`.
- **To build** (`build.ts`, `/api/plan/track`): built x of y and headroom now as the heading's
  figures, where it counts as built with a "change" link, then the stages on the Progress
  band's rail (state, starved, built of machines, draw, made and headroom after), the jobs of
  the stage shown (to do, then standing: tick, building, the verb and its count, have of need,
  ◎), and what is short. No site: a link to place the pad. Unsure where it is built, or `pick`:
  "which factory?" with the candidates and a scope select; an unnamed cluster is named first.
- **Versions** (`versions.ts`): the Planner timeline for one plan, with each version's delta
  against the one before (`/api/plan/delta`, the newest 30, read one at a time); view and
  restore.

## Map studio

`dash/maps/` is the map studio, `#maps` (breadcrumb Map): which pictures the map can draw and
which it opens on, the render jobs, a new picture, and the inputs. All of it is `GET /api/maps`
(`app/map-types.ts` holds it), refreshed by `maps` events; job progress redraws the jobs and the
rendering cards' wells in place.

- **Address** (`address.ts`, pure): `maps?gen=render|artwork|inputs` opens the generate form at
  that preset, `&type=<id>` picks a type (accent outline, scrolled into view once). Both
  replace; opening a card's picture on the map pushes.
- **Header** (`page.ts`): one meta line, maps on disk · free · raster cache, and "clear cache"
  (async). A setup problem or unregistered pyramids add one line each with their fix.
- **Jobs** (`jobs.ts`, `model.ts`): running, queued and the last one that ended in the past
  hour, on one grid: a state chip of the column's width | the type's title (linking `?type=`)
  and one meta line | the time in one format ("32 min left", "3 min", "1 h 42 min") | action
  slots. Running is the split slot log | cancel; queued remove; done make default, with "make
  default and delete …" in ⋯ when it replaces a type; failed show log. The job bar, the cancel
  confirm and the log tail run under the row from the name column.
- **Map types** (`cards.ts`): a card grid (`--mt-w`, four across on a desktop page). The picture
  is the type's own z1 tiles (`/api/maptiles/<id>/1/{x}/{y}`, 2 × 2), a click opens the map on
  it; a type without tiles is a plain `--bg-deep` well with its render's percent, bar and time
  left, never stand-in art. Then the title (no ★: the "default" chip says it), size in GB, a
  chip line (default, building, older data / older build with the reasons as tooltip, newer
  palette, re-render available, not in switcher) and "32,768 px · 15 Oct". Foot: regenerate or
  re-render when freshness offers it, else set as default, then ⋯ (rename in place, show or
  hide in the switcher, delete). Delete confirms in the same slots.
- **Generate** (`form.ts`): the "generate" toggle writes `?gen=`; the form card sits under the
  heading, then one estimate line from `GET /api/maps/estimate`.
- **Inputs** (`inputs.ts`): name | version, build and date (a "queued" chip leads it) | the
  number of types whose axes list the input | rebuild.

## Progress

`dash/progress/` is the Progress page (`#dash=progress[/<section>]`, sections gate, next,
alternates, buy, reference; a section scrolls to its heading). It advises: how to reach the
target phase and what to do next. The reads are `/api/progress/gate` and
`/api/progress/alternates` at the chosen horizon (docs/progress_roadmap.md) and the milestone,
MAM and phase feeds (`feeds.ts`).

- **Band** (`road.ts`): start and each phase as stops on one rail, the tiers each phase opens
  under it (done of total, a thin bar; a tier still shut says which phase opens it), "you are
  here" after the last delivered phase, the target bigger. While spoilers are off it stops at
  the target and folds the rest into "+N later". On phones it scrolls sideways, opening at the
  target end.
- **Gate** (`gate.ts`): one row per owed part with one header row: a tick once held, held of
  owed with a bar, where it is made (named factories with their surplus, the rest of the world
  as "elsewhere", or "not made anywhere"), the rate that delivers it in the horizon, the time
  the rate made now takes to cover it, and the chain's machines. A locked recipe carries a
  "locked" chip naming its unlock. Inputs sit in the row's expand in the same columns; the first
  short part opens by default. The heading carries parts short and recipes locked, and the
  "deliver in" select (10, 20, 50 h).
- **What to do next**: at most five numbered steps on one line, each with one reason chip and
  the full reason in its tooltip. Item steps link to the Planner's new-plan card filled with
  the item and rate (`planner?item=…&rate=…`); unlock steps link to the reference.
- **Alternates worth getting** (`lists.ts`): grouped offered by a drive on hand (the drive's
  other option in the chip's tooltip), not on a drive yet, unlocked already; five per group,
  sorted by machines, power or raw cut. Never a count of drives to choose.
- **Buy now**: affordable milestones (open tiers) and MAM nodes mixed, tagged by system and
  tier or tree, each naming what it unlocks; "almost" (short of exactly one item) folded.
- **Reference**, folded: the tiers (done tiers as one row) and the MAM trees with open nodes,
  the running node with its elapsed share.
- **Moved out**: shards and somersloops live in the Items boosts group and a factory's
  machines. Old addresses redirect (`model.ts` `legacyProgressDash`): milestones and mam to
  reference, elevator to gate, drives to alternates, shards and sloops to the Power Shard and
  Somersloop sheets on Items; `progress-<section>` lands on that section.
- The status strip's "affordable" count reads `summary.ts`.
