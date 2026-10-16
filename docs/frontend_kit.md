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

## Parts

| module | parts |
|---|---|
| `kit/ui/text.ts` | `pageHead` (object-page header: breadcrumb, title, chips, figures, note, the object's own tabs), `dashH` (heading · n), `blockHead` + `fig` / `figSub` (at most two headline figures), `toolrow`, `note`, `emptyLine`, `icon` (game icon or monogram; a failed load becomes the monogram) |
| `kit/ui/entry.ts` | `list(name, mode)` — the list's column template is `.l-<name> .entry { --cols: … }` in the page's CSS — `entry`, `identity`, `nameLine`, `metric`, `facts`, `chips`, `acts`, `chevron` + `expandBody` + `wireExpand`, `group` |
| `kit/ui/marks.ts` | `chip` / `tagChip`, `pill` / `flow`, `bar` / `mixBar`, `jobBar`, `slotTile` |
| `kit/ui/controls.ts` | `btn`, `busyBtn`, `glyphBtn`, `moreBtn`, `seg`, `selectBox`, `sortSelect`, `filterField`, `filterChip`, `switchBox`, `check` |
| `kit/ui/forms.ts` | `frow` (label column + one control column), `form` (groups split by a soft rule), `fnum`, `ftext`, `fadd`, `frange`, `checks` |
| `kit/overlay/*` | `openMenu` / `wireMoreMenus` (the ⋯ menu), `modal` (native `<dialog>`), `sidePanel`, `toast` |
| `kit/busy.ts` | `busy(el, work)` and `busyButton(button, work)` |

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
  Planner; tabs that do not fit go into "more". Items opens the inventory page for now, with a
  held / recipes switch to the old Recipes page. Phones get a bottom bar (Map, Overview,
  Factories, Planner, More).
- Jump search (`app/shell/jump.ts`, `/` or Ctrl+K): factories, items and recipes from
  `/api/search`, plans and circuits from what the page holds.
- Settings is a dialog (`app/settings-dialog/`), not a page: `settings=<section>` in the fragment
  opens it over the current page, so Back closes it. `#dash=settings` opens it over the page shown
  last; `#dash=settings/maps` goes to `#maps`.
- Map types, jobs, the generate form and inputs are the `#maps` page (`dash/maps/`).

## Power

`dash/power/` is the Power page (`#dash=power`) and a circuit's page (`#dash=power/<n>`), both
from `/api/power/circuits` (circuit pages also read factory health for the factory rows).

- **Supply and demand** (`supply.ts`): three bars on one scale, the largest of generation and
  both draws. Generation is segmented by generator type, each type's starved MW hatched; "if
  all run" is nameplate draw (every machine flat out at its saved clock), "drawing now" the
  measured draw. A white line marks generation, so the gap to it is the headroom; draw past it
  turns red. Under the bars, "not counted" chips with tooltips name what the figures leave
  out: hand-fed biomass, starved MW, unmeasured machines (charged in full), paused buildings,
  what stands on no wire, and generator types game data cannot rate (no line then).
- **Problems** (`model.ts` `problemsOf`): one building with one fault at one place is one row;
  no wire and no generator first, starved generators next, generators on no wire last and
  muted (they are not a fault, just left out).
- **Circuits**: worst first (no generator, short now, a starved generator, then least
  headroom per MW generated). The bar's 100% is that circuit's own generation, and every bar
  shares one overflow room (`utilScale`, up to 150%), so overdraw reads alike across rows.
- `readings.ts` reads a ledger for every surface (the header, the map's side panel, the
  planner); `ledger-bar.ts` is the compact DOM bar the side panel and the old Overview tiles
  still use.

## Factories

`dash/factories/` is the Factories page (`#dash=factories`) and a factory's page
(`#dash=factories/<name>[/<aspect>][?floor=<n|all>]`).

- **List** (`list.ts`, `clusters.ts`): the unnamed clusters first, compact (name field, what
  they make, machine count, ◎, name, ⋯ graph / skip); then the named factories as equal-height
  cards (status edge, icon, name, "N machines" with power chips, need-action count or uptime,
  drawing now and if all run, the state mix bar on the card's bottom edge, an expand with the
  worst machines). The sort select (need action, name, uptime, drawing now) groups into "need
  action" and "running" when sorting by need action.
- **Icon** (`icon-choice.ts`): the main product the health read names (`product_icon`: of the
  surpluses a recipe makes, the highest sink value, then the larger surplus; an extraction-only
  factory shows its main resource; a tie or nothing is no icon, an empty slot, never letters;
  `domain/factories/product.py`). "change icon" in ⋯ picks any item or none; the choice is kept
  in this browser only (localStorage, per world) and follows a rename made here.
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
  shown elsewhere is only noted. Hovering a balance row or a line lights that item.
- **Machines** per recipe (count, worst state, clock as "100% clock", shards and sloops from the
  progress reads when any are held) or per machine (state, clock, uptime; six per group, then
  "more"). Power is the draw rows of the supply block and the circuit links. Nodes, links,
  floors and sites are compact lists; floors and sites light theirs on the plan.
