/* The map. Reads /api, draws markers, and refetches when the game writes a save.
 *
 * Coordinates (metres, plotted at [-y, x]) are map/map.ts's header.
 *
 * This file is the entry point and draws nothing. What it holds is the ORDER of two things
 * neither of which is visible from inside a module: which map events are listened for, and
 * what happens on load.
 */

import "leaflet/dist/leaflet.css";
import "./style.css";

import { listenForCopies } from "./kit/copy";
import { el } from "./kit/dom";
import { listenForFinds } from "./map/tools/finder";
import { applyFloorFragment, escapeLeavesFloorMode, noteFloorChoice } from "./map/floors/floors";
import { listenToFragment } from "./app/fragment";
import { inspect } from "./map/inspector";
import { declutter } from "./map/labels";
import { isBatching, onSettled } from "./map/layercontrol/control";
import { loadLive, loadOne, loadRegions, loadStatic } from "./app/load";
import { rememberTick } from "./map/layers";
import { fitWorld, map, padPopups, writeHash } from "./map/map";
import { listenForEmptyClicks } from "./map/mapclick";
import { markHiddenRows, notePickupChoice } from "./map/drawn/markers";
import { render as renderPanel, showSelector } from "./map/panel";
import { listenForPins } from "./chat/pins";
import { noteRegionChoice, updateRegionBlend } from "./map/regions";
import { restyleRoutesForZoom, ROUTE_LAYERS, sinkRoutes } from "./map/drawn/route-passes";
import { wireSearch } from "./app/search";
import { syncSharedSettings } from "./app/shared-settings";
import { listen } from "./app/sse";
import { BOOT, BOOT_GARBLED, garbledNote, state } from "./app/state";
import { wireStatus } from "./app/status";
import { loadBaseMap } from "./map/tiles";
import { onTone } from "./map/map-tone";
import { fail } from "./kit/toast";
import { listenForTraces } from "./map/tools/trace";
import { loadWorlds } from "./app/world-picker";

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
import "./chat/advice";
import "./map/drawn/belts";
import "./map/drawn/crates";
import "./app/header";
import "./dash/inventory";
import "./map/labels";
import "./map/drawn/markers";
import "./map/panel";
import "./chat/pins";
import "./map/drawn/pipes";
import "./map/drawn/placements";
import "./map/drawn/plan-sitings";
import "./map/drawn/power-wires";
import "./dash/progress/progress";
import "./dash/recipes/recipes";
import "./dash/world/world";

/* ------------------------------------------------------------------- wiring */

/* Every map listener the page adds, in one block and in this order on purpose.
 *
 * Leaflet fires listeners in registration order. Split across modules that order would be a
 * consequence of the import graph, so reordering two imports would silently reorder the
 * handlers -- and three of these events have more than one listener:
 *
 *   zoomend            writeHash, restyleRoutesForZoom, declutter, markHiddenRows
 *   overlayadd         (the control's own decorator), noteRegionChoice, noteFloorChoice,
 *                      notePickupChoice, restyleRoutesForZoom + sinkRoutes, declutter
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
map.on("zoomend", restyleRoutesForZoom);

/* A layer added long after both fetches landed is appended to the canvas' draw list, i.e. on
 * top of everything, so the sink has to run again when the player ticks the box. And so does
 * the restyle: restyleRoutesForZoom skips a layer that is not on the map, so a layer ticked on
 * carries the pixel sizes of the zoom it was last drawn at. Style first, then sink -- the order
 * both draw functions already end in. */
map.on("overlayadd", function (event) {
  if (!ROUTE_LAYERS.some(function (n) { return state.layers[n] === event.layer; })) return;
  restyleRoutesForZoom();
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
map.on("zoomend", markHiddenRows);

/* The layers whose colours follow the base map's tone and keep no copy of their data to repaint
 * from; the node dots and pickups repaint themselves. */
onTone(function () {
  if (!state.worlds.length) return;
  loadOne("/api/belts");
  loadOne("/api/power");
});

map.on("preclick contextmenu", padPopups);
map.on("contextmenu", inspect);
listenForEmptyClicks();
map.on("overlayadd overlayremove", rememberTick);

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
wireSearch();
listenForTraces();
listenForFinds();
listenForPins();
wireStatus();
if (BOOT_GARBLED.length) fail(garbledNote(BOOT_GARBLED));
el("world").addEventListener("change", fitWorld);

/* -------------------------------------------------------------------- boot */

/* In this order and not in parallel: the base map's mode decides whether the region tint
 * starts on, so the group it decides about has to exist by then. */
loadRegions().then(loadBaseMap);
syncSharedSettings();

if (!("z" in BOOT)) fitWorld();

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
    if (BOOT.show) showSelector(BOOT.show);
  } else renderPanel();
  listen();
});
