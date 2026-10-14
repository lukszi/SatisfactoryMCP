/* The map-only page (docs/map/sharing.md): the base map, its mode switch and the sun. Nothing
 * here reads a save, a setting from the server, or anything else under /api. */

import "leaflet/dist/leaflet.css";
import "./app/base.css";
import "./map/layer-control.css";
import "./map/live-light.css";
import "./share/share.css";

import { BOOT } from "./app/state";
import { fail } from "./kit/toast";
import { fitWorld, map, writeHash } from "./map/map";
import { loadBaseMap } from "./map/tiles";
import { loadSite } from "./share/site";

map.on("moveend zoomend", writeHash);
// The full page's prefix advertises right-click inspect, which this page does not wire.
map.attributionControl.setPrefix("").removeAttribution("map data from the save &middot; Leaflet").addAttribution("Leaflet");
if (!("z" in BOOT)) fitWorld();
loadSite()
  .then(loadBaseMap)
  .catch(function (reason) {
    fail("the map could not load: " + String(reason));
  });
