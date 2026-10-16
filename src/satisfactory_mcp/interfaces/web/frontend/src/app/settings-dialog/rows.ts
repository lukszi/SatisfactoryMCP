/* One settings row: label and hint left, "changed" and reset, the control in a fixed column. */

import { html, marked, type Safe } from "../../kit/html";
import { selectBox, switchBox } from "../../kit/ui/controls";
import { chip } from "../../kit/ui/marks";
import { mapRegistry } from "../map-types";
import { isChanged, settingValue } from "../settings";

import type { Setting } from "../settings";

/** The shared default base map, a row of the map section that the server keeps. */
export const BASE_MAP_ROW = { key: "baseMap", group: "map", label: "default base map", hint: "what a fresh page opens on, in every browser here; the map's own switcher still changes the view" };

export type Row = Setting | typeof BASE_MAP_ROW;

export function controlId(key: string): string {
  return "set-" + key;
}

function fallbackText(s: Setting): string {
  if (s.kind === "switch") return s.fallback ? "on" : "off";
  if (s.kind === "choice") {
    const option = s.options.filter(function (o) {
      return o[0] === s.fallback;
    })[0];
    return option ? option[1] : s.fallback;
  }
  return String(s.fallback);
}

function control(s: Setting): Safe {
  const id = controlId(s.key);
  const value = settingValue(s.key);
  if (s.kind === "switch") return switchBox(id, value === true);
  if (s.kind === "choice") return selectBox(id, s.options, String(value));
  return html`<input type="number" id="${id}" min="${s.min}" max="${s.max}" step="1" value="${String(value)}">`;
}

function baseMapControl(): Safe {
  const body = mapRegistry.body;
  if (!body) return html`<span class="note">${mapRegistry.failed ? "map list unreadable" : "loading…"}</span>`;
  const options: [string, string][] = body.types
    .filter(function (type) {
      return type.status === "ready";
    })
    .map(function (type): [string, string] {
      return [type.id, type.title + (type.freshness.stale.length ? " (stale)" : "")];
    });
  options.push(["plain", "plain: no imagery"]);
  return selectBox(controlId(BASE_MAP_ROW.key), options, body.default || "plain");
}

function isSetting(row: Row): row is Setting {
  return "kind" in row;
}

export function settingRow(row: Row, query: string): Safe {
  const changed = isSetting(row) && isChanged(row);
  const tags = changed
    ? html`${chip("changed", "accent")}<button class="btn" type="button" data-reset="${row.key}" aria-label="${"reset “" + row.label + "” to " + fallbackText(row)}" title="${"back to " + fallbackText(row)}">reset</button>`
    : "";
  const shared = isSetting(row) && row.shared ? chip("chat uses this too", "muted") : "";
  return html`<div class="srow">
    <label class="st" for="${controlId(row.key)}"><span class="kl"><span class="k" title="${row.label}">${marked(row.label, query)}</span>${shared}</span><span class="d" title="${row.hint}">${marked(row.hint, query)}</span></label>
    <span class="sx">${tags}</span>
    <span class="sc">${isSetting(row) ? control(row) : baseMapControl()}</span>
  </div>`;
}
