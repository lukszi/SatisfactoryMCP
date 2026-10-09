/* The base map's parts, checkboxes under the mode radios: what a map drawn with live light is
 * made of. Shade, and the trees on a map that draws them; docs/spatial-and-map.md §18. Each row
 * is a Settings → map switch. The rows show only while the base map has a light, greyed with
 * the reason while a part cannot be switched: the light is not drawn live, or the render keeps
 * its trees in its colour. */

import { L } from "../leaflet";
import { onSetting, setSetting, settingOn, SETTINGS } from "../../app/settings";

import type { LightControls } from "../suncontrol";

interface PartRow {
  setting: string;
  input: HTMLInputElement;
  row: HTMLElement;
}

const PARTS: [string, string][] = [
  ["mapShade", "shade"],
  ["mapTrees", "trees"],
];

/** Why the trees of a render drawn before they came apart cannot be switched. */
export const TREES_IN_COLOUR = "this render keeps its trees in its colour: render it again to switch them";

let box: HTMLElement | null = null;
let rows: PartRow[] = [];
/* null: no light, so no rows. */
let controls: LightControls | null = null;

function hintOf(key: string): string {
  const setting = SETTINGS.find(function (candidate) {
    return candidate.key === key;
  });
  return setting ? setting.hint : "";
}

/** null hides a part's row; "" draws it live; any other text greys it, with that tooltip. */
export function partState(setting: string, light: LightControls | null): string | null {
  if (!light) return null;
  if (setting !== "mapTrees") return light.off;
  if (!light.trees && !light.apart) return null;
  return light.off || (light.apart ? "" : TREES_IN_COLOUR);
}

function refresh(): void {
  if (!box) return;
  box.hidden = controls === null;
  rows.forEach(function (part) {
    const why = partState(part.setting, controls);
    part.row.hidden = why === null;
    part.input.checked = settingOn(part.setting);
    part.input.disabled = !!why;
    part.row.title = why || hintOf(part.setting);
    if (why) L.DomUtil.addClass(part.row, "layer-mode-off");
    else L.DomUtil.removeClass(part.row, "layer-mode-off");
  });
}

/** The rows' box, built once; the mode section keeps it under its radios. */
export function partsBox(): HTMLElement {
  if (box) return box;
  box = L.DomUtil.create("div", "layer-parts");
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", "base map parts");
  const into = box;
  rows = PARTS.map(function (part) {
    // Leaflet's own row shape, as the radios above have it.
    const row = L.DomUtil.create("label", "layer-part", into);
    const holder = L.DomUtil.create("span", "", row);
    const input = L.DomUtil.create("input", "", holder) as HTMLInputElement;
    input.type = "checkbox";
    L.DomUtil.create("span", "", holder).textContent = " " + part[1];
    L.DomEvent.on(input, "change", function () {
      setSetting(part[0], input.checked);
    });
    return { setting: part[0], input: input, row: row };
  });
  refresh();
  return box;
}

/** The base map's light, or null for one without: its rows follow it. */
export function showParts(light: LightControls | null): void {
  controls = light;
  refresh();
}

onSetting(refresh);
