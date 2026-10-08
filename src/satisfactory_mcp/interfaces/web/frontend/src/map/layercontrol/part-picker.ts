/* The base map's parts, checkboxes under the mode radios: what a map drawn with live light is
 * made of. Shade today; docs/spatial-and-map.md §18. Each row is a Settings → map switch. The
 * rows show only while the base map has a light, greyed with the reason while it is not lit live. */

import { L } from "../leaflet";
import { onSetting, setSetting, settingOn, SETTINGS } from "../../app/settings";

interface PartRow {
  setting: string;
  input: HTMLInputElement;
  row: HTMLElement;
}

const PARTS: [string, string][] = [["mapShade", "shade"]];

let box: HTMLElement | null = null;
let rows: PartRow[] = [];
/* null: no light, so no rows; "": drawn live; otherwise why it is not. */
let off: string | null = null;

function hintOf(key: string): string {
  const setting = SETTINGS.find(function (candidate) {
    return candidate.key === key;
  });
  return setting ? setting.hint : "";
}

function refresh(): void {
  if (!box) return;
  box.hidden = off === null;
  rows.forEach(function (part) {
    part.input.checked = settingOn(part.setting);
    part.input.disabled = !!off;
    part.row.title = off || hintOf(part.setting);
    if (off) L.DomUtil.addClass(part.row, "layer-mode-off");
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

/** null hides the rows (this base map has no light); "" draws them live; any other text
 *  greys them, with that text as their tooltip. */
export function showParts(why: string | null): void {
  off = why;
  refresh();
}

onSetting(refresh);
