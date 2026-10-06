/* The status strip: the selection, one-line vitals that each open their section, and a
 * changed save. See docs/frontend_vision.md §2.1 and §16. */

import { onStale } from "../api/client";
import { button, link } from "../kit/dashkit";
import { leaveDashThen } from "../dash/shell";
import { el, make } from "../kit/dom";
import { keepFocus } from "../kit/focus";
import { count } from "../kit/format";
import { reload } from "./load";
import { showCircuit, showFactory } from "../map/panel";
import { showMachine, showPoint } from "../map/map-highlight";
import { faultCount, faultsOf } from "../dash/power-tab";
import { ratedWorld, readNow } from "../dash/power-ledger";
import { onProgress, readyMilestones } from "../dash/progress/progress";
import { showRef } from "../map/tools/finder";
import { onSelect, select, selected, selectionRef } from "./selection";
import { state } from "./state";
import { onVitals, vitals } from "./vitals";
import { startTrace } from "../map/tools/trace";
import { actionTone, statesOf } from "../dash/machine-states";
import { counted, WORDS } from "../kit/words";

import type { Selection } from "./selection";

var KIND_WORD = {
  factory: WORDS.factory,
  circuit: "circuit",
  machine: "machine",
  point: "point",
  node: WORDS.node,
  field: WORDS.field,
  conduit: WORDS.run,
  pickup: "pickup",
};

function onMapView(go: () => void): void {
  if (state.dash) leaveDashThen(go);
  else go();
  renderStatus();
}

function fly(s: Selection): void {
  onMapView(function () {
    if (s.kind === "factory") showFactory(s.key);
    else if (s.kind === "circuit") showCircuit(+s.key);
    else if (s.kind === "machine" && s.x_m !== undefined && s.y_m !== undefined) showMachine(s.key, s.label, s.x_m, s.y_m, { layers: ["machines"] });
    else if (s.kind !== "point") showRef(selectionRef(s), s);
    else if (s.x_m !== undefined && s.y_m !== undefined) showPoint(s.x_m, s.y_m, { label: s.label });
  });
}

function selectionPart(s: Selection): HTMLElement {
  const part = make("span", "status-sel");
  part.appendChild(make("span", "status-k", "selected " + KIND_WORD[s.kind]));
  const dash = s.kind === "factory" ? "factories/" + s.key : s.kind === "circuit" ? "power/" + (+s.key + 1) : "";
  const name = dash && dash !== state.dash ? link(dash, s.label, "status-name") : make("span", "status-name", s.label);
  name.title = s.label;
  part.appendChild(name);
  part.appendChild(button("map", function () { fly(s); }, { map: true, title: "fly the map to it", label: "show " + s.label + " on the map" }));
  if (s.kind === "factory" || s.kind === "machine") {
    part.appendChild(button("trace", function () { onMapView(function () { startTrace(selectionRef(s), "up"); }); }, { title: "draw what feeds this " + KIND_WORD[s.kind] + " on the map" }));
  }
  part.appendChild(button("clear", function () { select(null); }, { title: "clear the selection" }));
  return part;
}

function item(parent: HTMLElement, dash: string, text: string, tone?: string): void {
  parent.appendChild(link(dash, text, "status-item" + (tone ? " " + tone : "")));
}

function vitalsPart(parent: HTMLElement): void {
  const v = vitals();
  if (v.health) {
    const rows = v.health.factories;
    let todo = 0;
    rows.forEach(function (r) {
      todo += r.actionable;
    });
    item(parent, "factories", count(todo) + " " + WORDS.needAction, todo ? actionTone(statesOf(rows)) : "");
  }
  if (v.circuits) {
    const faults = faultCount(faultsOf(v.circuits));
    item(parent, "power", count(faults) + " " + WORDS.powerProblems, faults ? "bad" : "");
    const now = readNow(ratedWorld(v.circuits));
    item(parent, "power", WORDS.headroomNow + " " + now.value, now.bad ? "bad" : "");
  }
  const ready = readyMilestones();
  if (ready !== null) item(parent, "progress", counted(ready, "milestone", "milestones") + " affordable");
}

export function renderStatus(): void {
  const strip = el("status");
  keepFocus(strip, function () {
    strip.textContent = "";
    if (state.saveMovedOn) {
      const changed = make("span", "status-stale");
      changed.appendChild(make("span", "bad", "save changed"));
      changed.appendChild(button("refresh", function () { reload("reading the new save…"); }, { title: "read the save on disk now" }));
      strip.appendChild(changed);
    }
    const s = selected();
    if (s) strip.appendChild(selectionPart(s));
    if (state.noSaves) {
      strip.appendChild(make("span", "status-item", WORDS.noSaves));
      return;
    }
    vitalsPart(strip);
  });
}

export function wireStatus(): void {
  onVitals(renderStatus);
  onProgress(renderStatus);
  onSelect(renderStatus);
  onStale(renderStatus);
  window.addEventListener("hashchange", renderStatus);
  window.addEventListener("popstate", renderStatus);
  renderStatus();
}
