/* The markers the player placed on the in-game map: a dot in the marker's own colour, cased so
 * it reads on any ground, with its name beside it. On by default: the player put each one
 * there to be seen. */

import { esc, popup } from "../../kit/dom";
import { L } from "../leaflet";
import { BAND, clearedLayer } from "../layers";
import { latLngOf } from "../map";
import { declareColours } from "../palette";
import { registerFetch } from "../../app/registry";
import { casingFor } from "./marker-style";

import type { MapMarkerRow, MapMarkersResponse } from "../../api/shapes";

/* Only the casings are the page's: the fill is the save's own colour, which is data and is
 * not audited (docs/frontend_palette.md). */
const CASING = declareColours("map-markers", { "casing light": "#f6e6ff", "casing dark": "#3b1030" });

const MARKER_RADIUS_PX = 6;

/* The compass distances the reference save uses; any other value is shown as the save spells it. */
const COMPASS: Record<string, string> = {
  CVD_Near: "near",
  CVD_Mid: "mid",
  CVD_Far: "far",
};

function markerPopup(m: MapMarkerRow): string {
  return popup([
    ["marker", m.name || "unnamed"],
    ["category", m.category || null],
    ["colour", m.colour],
    ["icon", m.icon_id === null ? null : "#" + m.icon_id],
    ["compass", m.compass ? COMPASS[m.compass] || m.compass : null],
    ["at", m.x_m + ", " + m.y_m + " m"],
  ]);
}

export function drawMapMarkers(data: MapMarkersResponse): void {
  // Chrome, beside where the player stood: the player's own notes on the frame, not a building.
  const group = clearedLayer("map markers", {
    on: true,
    colour: CASING["casing light"],
    rank: [BAND.chrome, 25, "map markers"],
  });
  data.markers.forEach(function (m) {
    if (m.x_m === null || m.y_m === null) return;
    L.circleMarker(latLngOf({ x_m: m.x_m, y_m: m.y_m }), {
      radius: MARKER_RADIUS_PX,
      color: casingFor(m.colour, CASING["casing light"], CASING["casing dark"]),
      weight: 2,
      fillColor: m.colour || CASING["casing light"],
      fillOpacity: 0.95,
    })
      .bindTooltip(esc(m.name || "marker"), {
        permanent: true,
        direction: "right",
        offset: [MARKER_RADIUS_PX + 2, 0],
        className: "map-marker-label",
      })
      .bindPopup(markerPopup(m))
      .addTo(group);
  });
}

/** Live: a marker is placed or moved without building anything, and the next autosave has it. */
registerFetch<MapMarkersResponse>({
  wave: "live",
  rank: 35,
  path: "/api/map-markers",
  label: "map markers",
  clears: ["map markers"],
  refilters: false,
  draw: drawMapMarkers,
});
