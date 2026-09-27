/* How a power ledger and a circuit are drawn and named, shared by the Overview and Power tabs.
 * See docs/frontend_vision.md §8. */

import { make } from "./dom";
import { mw } from "./format";
import { W } from "./words";

import type { CircuitRow, Ledger } from "./api-shapes";

export var LEDGER = {
  generation: W.generation,
  measuredDraw: W.measuredDraw,
  nameplateDraw: W.nameplateDraw,
  headroomNow: W.headroomNow,
  headroomFull: W.headroomFull,
  poles: W.polesAndTowers,
};

function key(parent: HTMLElement, className: string, text: string): void {
  var item = make("span", "bar-key-item");
  item.appendChild(make("span", "bar-key-swatch " + className));
  item.appendChild(document.createTextNode(text));
  parent.appendChild(item);
}

export function bar(ledger: Ledger, legend?: boolean): HTMLElement {
  var track = make("div", "panel-bar dash-bar");
  var cap = Math.max(ledger.generation_mw, ledger.draw_mw, 1);
  var nameplate = make("span", "panel-bar-nameplate");
  nameplate.style.width = Math.min(100, (ledger.draw_mw / cap) * 100) + "%";
  var measured = make("span", "panel-bar-measured");
  measured.style.width = Math.min(100, (ledger.measured_draw_mw / cap) * 100) + "%";
  var generation = make("span", "panel-bar-cap");
  generation.style.left = Math.min(100, (ledger.generation_mw / cap) * 100) + "%";
  track.appendChild(nameplate);
  track.appendChild(measured);
  track.appendChild(generation);
  track.title =
    mw(ledger.measured_draw_mw) +
    " " +
    LEDGER.measuredDraw +
    ", " +
    mw(ledger.draw_mw) +
    " " +
    LEDGER.nameplateDraw +
    ", against " +
    mw(ledger.generation_mw) +
    " " +
    LEDGER.generation;
  track.setAttribute("role", "img");
  track.setAttribute("aria-label", track.title);
  if (!legend) return track;
  var wrap = make("div", "bar-keyed");
  wrap.appendChild(track);
  var keys = make("div", "bar-key");
  keys.setAttribute("aria-hidden", "true");
  key(keys, "panel-bar-measured", LEDGER.measuredDraw);
  key(keys, "panel-bar-nameplate", LEDGER.nameplateDraw);
  key(keys, "panel-bar-cap", LEDGER.generation);
  wrap.appendChild(keys);
  return wrap;
}

export function circuitName(row: CircuitRow): string {
  return "circuit " + (row.index + 1) + (row.factories.length ? " · " + row.factories.slice(0, 3).join(", ") + (row.factory_count > 3 ? " +" + (row.factory_count - 3) : "") : "");
}

export function circuitDark(row: CircuitRow): boolean {
  return row.ledger.generation_mw <= 0 && row.consumers > 0;
}

export function headroom(value: number): string {
  return mw(value, { signed: true });
}
