/* The map. Reads /api, draws markers, and refetches when the game writes a save.
 *
 * Two coordinate facts drive everything below.
 *
 *   1. The API already speaks metres. Nothing here divides by 100 -- if a number looks
 *      like centimetres, the bug is server-side.
 *   2. Satisfactory is +X east and +Y SOUTH, while Leaflet's CRS.Simple is +lat north.
 *      So a point is plotted at [-y, x], and that negation lives in map.ts and nowhere else.
 *
 * And one content fact: every string that reaches a popup or a label is DATA, so popup()
 * escapes everything by default and the few rows that need markup say so with html().
 *
 * This file is the entry point and draws nothing. What it holds is the ORDER of two things
 * neither of which is visible from inside a module: which map events are listened for, and
 * what happens on load.
 */

import "leaflet/dist/leaflet.css";
import "./style.css";

import { listenForCopies } from "./copy";
import { applyFloorFragment, escapeLeavesFloorMode, noteFloorChoice } from "./floors";
import { listenToFragment } from "./fragment";
import { inspect } from "./inspector";
import { declutter } from "./labels";
import { isBatching, onSettled } from "./layercontrol";
import { loadLive, loadRegions, loadStatic } from "./load";
import { map, writeHash } from "./map";
import { notePickupChoice } from "./markers";
import { noteRegionChoice, updateRegionBlend } from "./regions";
import { ROUTE_LAYERS, sinkRoutes, styleRoutes } from "./routes";
import { listen } from "./sse";
import { BOOT, state } from "./state";
import { loadBaseMap } from "./tiles";
import { loadWorlds } from "./worlds";

/* ---------------------------------------------------------------- features */

/* Every module that declares a fetch, imported for that side effect alone.
 *
 * ORDER IS NOT LOAD-BEARING HERE, unlike the listener block below: `registerFetch` takes an
 * explicit `rank` and `fetchersOf` sorts by it. A feature is one appended line, in whatever
 * place keeps this list alphabetical.
 *
 * DELETING A LINE HERE DELETES ITS LAYER, silently: each module registers what it wants
 * fetched as it is evaluated, load.ts imports none of them, and Rollup drops what nothing
 * imports -- no compile error, no runtime one. `test_architecture.py` checks this list
 * against the set of modules that call `registerFetch`, in both directions. Three of these are
 * imported by name above as well, and are repeated here anyway: a rule with exceptions in it
 * is a rule nobody can check at a glance. */
import "./crates";
import "./dashboard";
import "./header";
import "./labels";
import "./markers";
import "./panel";
import "./placements";
import "./plans";
import "./power";
import "./routes";

/* ------------------------------------------------------------------- wiring */

/* Every map listener the page adds, in one block and in this order on purpose.
 *
 * Leaflet fires listeners in registration order. Split across modules that order would be a
 * consequence of the import graph, so reordering two imports would silently reorder the
 * handlers -- and three of these events have more than one listener:
 *
 *   zoomend            writeHash, styleRoutes, declutter
 *   overlayadd         (the control's own decorator), noteRegionChoice, noteFloorChoice,
 *                      notePickupChoice, styleRoutes + sinkRoutes, declutter
 *   overlayremove      (the control's own decorator), noteRegionChoice, noteFloorChoice,
 *                      notePickupChoice, declutter
 *
 * The control's decorator is not in this list because it is registered while the control is
 * being built, which is the only moment it can be, and it therefore always comes first.
 */
map.on("moveend zoomend", writeHash);
map.on("layeradd layerremove", updateRegionBlend);
// Which of the region box's ticks were the player's, which is what makes the base map's
// default for it a default rather than an override. See regionsUnderMode.
map.on("overlayadd overlayremove", noteRegionChoice);
// The same question for floor mode -- and a layer ticked on mid-mode owes the floor filter a
// pass, which this does too.
map.on("overlayadd overlayremove", noteFloorChoice);
// ...and for the pickup rows, which the fragment carries: see notePickupChoice.
map.on("overlayadd overlayremove", notePickupChoice);
map.on("zoomend", styleRoutes);

/* A layer added long after both fetches landed is appended to the canvas' draw list, i.e. on
 * top of everything, so the sink has to run again when the player ticks the box. And so does
 * the restyle: styleRoutes skips a layer that is not on the map, so a layer ticked on carries
 * the pixel sizes of the zoom it was last drawn at. Style first, then sink -- the order both
 * draw functions already end in. */
map.on("overlayadd", function (event) {
  if (!ROUTE_LAYERS.some(function (n) { return state.layers[n] === event.layer; })) return;
  styleRoutes();
  sinkRoutes();
});

/* Same batch guard as the control decorator: this pass measures every label's screen
 * rectangle, so running it once per member of a fourteen-layer family is fourteen forced
 * layouts to reach one answer. It runs through onSettled, so the control can say "the list
 * has stopped changing" without importing the module that knows what a label is. */
map.on("zoomend overlayadd overlayremove", function () {
  if (!isBatching()) declutter();
});
onSettled(declutter);

map.on("contextmenu", inspect);

/* The listeners that are not the map's: the address bar, the one key this page binds, and the
 * click that copies a selector. The fragment one is registered BEFORE the loaders below, so a
 * fragment edited during the first fetch is not dropped on the floor. ESC goes on the document
 * rather than on the map, because floor mode is a state of the PAGE and the key has to work
 * with the keyboard in the layer control's floor picker. */
listenToFragment();
document.addEventListener("keydown", escapeLeavesFloorMode);
/* ...and the third: one delegated click for every selector on the page, which is why it is
 * here and not in whatever module last built a popup. */
listenForCopies();

/* -------------------------------------------------------------------- boot */

/* In this order and not in parallel: the base map's mode decides whether the region tint
 * starts on, so the group it decides about has to exist by then. */
loadRegions().then(loadBaseMap);

loadWorlds().then(function () {
  // With no world there is nothing to fetch: firing the loaders anyway would bury the
  // persistent "no readable saves" line under six toasts and a fake header.
  if (state.worlds.length) {
    loadStatic();
    loadLive();
    /* ...and the fragment's floor half, which cannot be applied before there is a world to
     * apply it to. Fired here rather than waiting for the two waves: `/api/floors` is its own
     * request and the view owes a flight until the concrete arrives. */
    applyFloorFragment(BOOT.floor);
  }
  listen();
});
