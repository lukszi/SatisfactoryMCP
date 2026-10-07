/* The side panel's building blocks: a row's name button, its "map" button, a power ledger and
 * a folded list of machines. See docs/spatial-and-map.md §21. */

import { button } from "../kit/dashkit";
import { make } from "../kit/dom";
import { mw } from "../kit/format";
import { reveal } from "./labels";
import { selectAndRing } from "./map-highlight";
import {
  biomassLine,
  ledgerBar,
  readGeneration,
  readHeadroomFull,
  readHeadroomNow,
  readMeasuredDraw,
  whereOf,
} from "../dash/power-ledger";
import { WORDS } from "../kit/words";

import type { MachineRef, StarvedGenerator } from "../api/shapes";
import type { Rated, Reading } from "../dash/power-ledger";

/** How many machines a folded reference list shows; the rest are counted. */
const REF_ROWS_SHOWN = 40;

export const STARVED_HINT = "input ran dry and produced nothing in its window";

interface Placed {
  x_m: number;
  y_m: number;
}

type Ref = MachineRef | StarvedGenerator;

export function located(row: { x_m: number | null; y_m: number | null }): row is Placed {
  return row.x_m !== null && row.y_m !== null;
}

export function panelNote(body: HTMLElement, text: string): void {
  body.appendChild(make("p", "panel-note", text));
}

export function showOnMapButton(row: { x_m: number | null; y_m: number | null }, label: string): HTMLElement | null {
  if (!located(row)) return null;
  const at = row;
  return button(
    "map",
    function () {
      reveal(["machines"]);
      selectAndRing(at.x_m, at.y_m, label);
    },
    { map: true, title: "fly the map to it", label: "show " + label + " on the map" }
  );
}

export function nameButton(text: string, expanded: boolean, action: () => void): HTMLElement {
  const host = make("span", "panel-row-name");
  const pick = make("button", "panel-pick", text);
  pick.type = "button";
  pick.title = text;
  pick.setAttribute("aria-expanded", String(expanded));
  pick.onclick = function (event) {
    event.stopPropagation();
    action();
  };
  host.appendChild(pick);
  return host;
}

export function ledgerBlock(r: Rated, starved: number): HTMLElement {
  const box = make("div", "panel-ledger");
  const grid = make("div", "panel-kv");
  const pairs: [string, Reading][] = [
    [WORDS.generation, readGeneration(r)],
    [WORDS.measuredDraw, readMeasuredDraw(r)],
    [WORDS.nameplateDraw, { value: mw(r.ledger.draw_mw), bad: false, why: "" }],
    [WORDS.headroomNow, readHeadroomNow(r)],
    [WORDS.headroomFull, readHeadroomFull(r)],
  ];
  if (starved) pairs.splice(1, 0, ["of it starved", { value: mw(starved), bad: true, why: "" }]);
  pairs.forEach(function (p) {
    grid.appendChild(make("span", "panel-k", p[0]));
    const v = make("span", "panel-v" + (p[1].bad ? " bad" : ""), p[1].value);
    if (p[1].why) v.title = p[1].why;
    grid.appendChild(v);
  });
  box.appendChild(ledgerBar(r.ledger));
  box.appendChild(grid);
  const extra = biomassLine(r.ledger);
  if (extra) box.appendChild(make("p", "panel-note", extra));
  return box;
}

export function refList(title: string, rows: Ref[], hint: string): HTMLElement {
  const fold = make("details", "panel-fold-list");
  const summary = make("summary", "", title + " (" + rows.length + ")");
  summary.title = hint;
  fold.appendChild(summary);
  const list = make("ul", "panel-issues");
  rows.slice(0, REF_ROWS_SHOWN).forEach(function (r) {
    const line = make("li", "panel-issue");
    const text = make("span", "panel-issue-text");
    text.appendChild(make("span", "panel-issue-what", r.name));
    const sub = "missing" in r ? mw(r.mw) + " · " + r.cause : whereOf(r).where;
    if (sub) text.appendChild(make("span", "panel-issue-cause", sub));
    line.appendChild(text);
    const go = showOnMapButton(r, r.name);
    if (go) line.appendChild(go);
    list.appendChild(line);
  });
  const rest = rows.length - REF_ROWS_SHOWN;
  if (rest > 0) list.appendChild(make("li", "panel-more", rest + " more"));
  fold.appendChild(list);
  return fold;
}

export function badgeTone(now: Reading, starved: boolean): string {
  if (now.bad || starved) return " bad";
  return now.why ? "" : " ok";
}
