/* The status strip: the selection, one-line vitals that each open their section, and a
 * changed save. See docs/frontend_vision.md §2.1 and §16. */

import { onStale } from "./api";
import { button, link } from "./dashkit";
import { toMap } from "./dashboard";
import { el, keepFocus, make } from "./dom";
import { count } from "./format";
import { reload } from "./load";
import { onVitals, showCircuit, showFactory, showPoint, vitals } from "./panel";
import { faultCount, faultsOf } from "./power-tab";
import { ratedWorld, readNow } from "./powerview";
import { onProgress, readyMilestones } from "./progress";
import { showRef } from "./finder";
import { onSelect, select, selected, selectionRef } from "./selection";
import { state } from "./state";
import { actionTone, statesOf } from "./states";
import { counted, W } from "./words";

import type { Selection } from "./selection";

var KIND_WORD = {
  factory: W.factory,
  circuit: "circuit",
  point: "point",
  node: W.node,
  field: W.field,
  conduit: W.run,
  pickup: "pickup",
};

function fly(s: Selection): void {
  var go = function () {
    if (s.kind === "factory") showFactory(s.key);
    else if (s.kind === "circuit") showCircuit(+s.key);
    else if (s.kind !== "point") showRef(selectionRef(s), s);
    else if (s.x_m !== undefined && s.y_m !== undefined) showPoint(s.x_m, s.y_m, { label: s.label });
  };
  if (state.dash) toMap(go);
  else go();
  renderStatus();
}

function selectionPart(s: Selection): HTMLElement {
  var part = make("span", "status-sel");
  part.appendChild(make("span", "status-k", "selected " + KIND_WORD[s.kind]));
  var dash = s.kind === "factory" ? "factories/" + s.key : s.kind === "circuit" ? "power/" + (+s.key + 1) : "";
  var name = dash && dash !== state.dash ? link(dash, s.label, "status-name") : make("span", "status-name", s.label);
  name.title = s.label;
  part.appendChild(name);
  part.appendChild(button("map", function () { fly(s); }, { map: true, title: "fly the map to it", label: "show " + s.label + " on the map" }));
  part.appendChild(button("clear", function () { select(null); }, { title: "clear the selection" }));
  return part;
}

function item(parent: HTMLElement, dash: string, text: string, tone?: string): void {
  parent.appendChild(link(dash, text, "status-item" + (tone ? " " + tone : "")));
}

function vitalsPart(parent: HTMLElement): void {
  var v = vitals();
  if (v.health) {
    var rows = v.health.factories;
    var todo = 0;
    rows.forEach(function (r) {
      todo += r.actionable;
    });
    item(parent, "factories", count(todo) + " " + W.needAction, todo ? actionTone(statesOf(rows)) : "");
  }
  if (v.circuits) {
    var faults = faultCount(faultsOf(v.circuits));
    item(parent, "power", count(faults) + " " + W.powerProblems, faults ? "bad" : "");
    var now = readNow(ratedWorld(v.circuits));
    item(parent, "power", W.headroomNow + " " + now.value, now.bad ? "bad" : "");
  }
  var ready = readyMilestones();
  if (ready !== null) item(parent, "progress", counted(ready, "milestone", "milestones") + " affordable");
}

export function renderStatus(): void {
  var strip = el("status");
  keepFocus(strip, function () {
    strip.textContent = "";
    if (state.stale) {
      var changed = make("span", "status-stale");
      changed.appendChild(make("span", "bad", "save changed"));
      changed.appendChild(button("refresh", function () { reload("reading the new save…"); }, { title: "read the save on disk now" }));
      strip.appendChild(changed);
    }
    var s = selected();
    if (s) strip.appendChild(selectionPart(s));
    if (state.noSaves) {
      strip.appendChild(make("span", "status-item", W.noSaves));
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
