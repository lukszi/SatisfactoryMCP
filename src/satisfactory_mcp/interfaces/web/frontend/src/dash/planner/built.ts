/* The "built at" line on Track: where the plan's built machines were found, its progress in
 * machines or percent, and the one-line answers. See docs/planner-p4_contract.md §5.2. */

import { send } from "../../api/client";
import { button, fieldError, selectBox } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { nodeLayers } from "../../map/drawn/markers";
import { goToMapThen } from "../../app/nav";
import { showBox } from "../../map/map-highlight";
import { vitals } from "../../app/vitals";
import { pickTab } from "./reads";
import { bench, changed } from "./state";
import { applyOps } from "./writes";
import { factoryNameProblem, labelRefusal, labelsVersionToSend, recordLabelsVersion, refetchLabelledViews } from "../factories/rename";
import { setSetting, settingChoice } from "../../app/settings";
import { fail, friendlyError, notify } from "../../kit/toast";
import { WORDS } from "../../kit/words";

import type { NamedResponse, TrackBuiltAt, TrackBuiltCandidate } from "../../api/shapes";
import type { BboxM } from "../../map/geometry";

const SCOPE_WORLD = "/world";
const SCOPE_NONE = "/none";
const CLUSTER_PREFIX = "cluster:";

let naming = { key: "", proposal: -1, name: "", problem: "", busy: false };
let picking = { key: "", open: false };

interface Figures {
  built: number | null;
  figure: string;
  percent: number | null;
  percent_max: number | null;
}

/** "12 / 16 machines" or "75%", as the progress setting says; "–" when not placed. */
export function progressText(f: Figures): string {
  if (f.built === null) return f.figure || "–";
  if (settingChoice("progress") === "percent" && f.percent !== null) {
    const lo = Math.round(f.percent);
    const hi = f.percent_max === null ? lo : Math.round(f.percent_max);
    return (hi !== lo ? lo + "–" + hi : String(lo)) + "%";
  }
  return f.figure + " machines";
}

export function toggleProgress(): void {
  setSetting("progress", settingChoice("progress") === "percent" ? "machines" : "percent");
  changed();
}

function setBuiltScope(value: string): void {
  const plan = bench.plan;
  if (!plan || bench.gone || plan.factory === value) return;
  picking.open = false;
  applyOps([{ op: "set", field: "factory", value: value }]);
}

function startNaming(c: TrackBuiltCandidate): void {
  naming = { key: bench.key, proposal: c.proposal === null ? -1 : c.proposal, name: c.name, problem: "", busy: false };
  picking.open = false;
  changed();
}

function saveName(b: TrackBuiltAt, field: HTMLInputElement): void {
  const name = field.value.trim();
  const problem = factoryNameProblem(name);
  if (problem) {
    naming.problem = problem;
    naming.name = field.value;
    fieldError(field, problem);
    return;
  }
  naming.busy = true;
  naming.name = name;
  changed();
  send<NamedResponse>("POST", "/api/labels", {
    name: name,
    proposal: naming.proposal,
    as_of: b.token,
    version: labelsVersionToSend(b.labels_version),
  })
    .then(function (reply) {
      recordLabelsVersion(reply.version);
      naming = { key: "", proposal: -1, name: "", problem: "", busy: false };
      notify("named “" + reply.name + "”; the plan now counts it as built");
      refetchLabelledViews();
      setBuiltScope(reply.name);
    })
    .catch(function (failure) {
      const why = labelRefusal(failure);
      naming.busy = false;
      if (why === "name_taken") naming.problem = "“" + name + "” is already a factory name";
      else if (why === "bad") naming.problem = friendlyError(failure);
      else if (why === "stale" || why === "pin") {
        naming.key = "";
        fail("the save or the factory names moved, so “" + name + "” was not written; look again");
      } else fail("naming “" + name + "”: " + friendlyError(failure));
      changed();
    });
}

function nameBar(parent: HTMLElement, b: TrackBuiltAt): void {
  const bar = make("div", "plan-line built-name");
  const field = make("input", "dash-name plan-text");
  field.value = naming.name;
  field.setAttribute("data-ctl", "built-name");
  field.setAttribute("aria-label", "name for this " + WORDS.unnamedCluster);
  field.disabled = naming.busy;
  field.onkeydown = function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      saveName(b, field);
    } else if (event.key === "Escape") {
      naming.key = "";
      changed();
    }
  };
  bar.appendChild(make("span", "plan-sub", "name it"));
  bar.appendChild(field);
  bar.appendChild(
    button(naming.busy ? "naming…" : "name", function () {
      saveName(b, field);
    }, { disabled: naming.busy, title: "name this " + WORDS.unnamedCluster + " and count it as this plan's" })
  );
  bar.appendChild(
    button("cancel", function () {
      naming.key = "";
      changed();
    })
  );
  parent.appendChild(bar);
  if (naming.problem) fieldError(field, naming.problem);
}

function factoryNames(current: string): string[] {
  const health = vitals().health;
  const names = health
    ? health.factories.map(function (f) {
        return f.name;
      })
    : [];
  if (current && current.charAt(0) !== "/" && names.indexOf(current) < 0) names.push(current);
  return names.sort(function (a, b) {
    return a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });
  });
}

function scopePicker(parent: HTMLElement, b: TrackBuiltAt): void {
  const plan = bench.plan!;
  const clusters = b.candidates.filter(function (c) {
    return c.kind === "cluster" && c.proposal !== null;
  });
  const options: [string, string][] = [["", WORDS.foundAutomatically]];
  clusters.forEach(function (c) {
    options.push([CLUSTER_PREFIX + c.proposal, c.name + " (" + WORDS.unnamedCluster + ": names it)"]);
  });
  factoryNames(plan.factory).forEach(function (name) {
    options.push([name, name]);
  });
  options.push([SCOPE_WORLD, WORDS.wholeWorld], [SCOPE_NONE, WORDS.nothingBuiltYet]);
  const pick = selectBox(
    options,
    plan.factory,
    function (value) {
      if (value.indexOf(CLUSTER_PREFIX) === 0) {
        const n = Number(value.slice(CLUSTER_PREFIX.length));
        const c = clusters.filter(function (x) {
          return x.proposal === n;
        })[0];
        if (c) startNaming(c);
        return;
      }
      setBuiltScope(value);
    },
    { label: WORDS.countAsBuilt, disabled: bench.gone }
  );
  pick.setAttribute("data-ctl", "track-scope");
  const row = make("div", "plan-line");
  row.appendChild(make("span", "plan-sub", WORDS.countAsBuilt));
  row.appendChild(pick);
  parent.appendChild(row);
}

/** A [map] button that outlines a bounding box with the machines and these nodes' layers shown. */
export function boxMapButton(bbox: number[] | null | undefined, what: string, nodeNames: string[]): HTMLButtonElement | null {
  if (bbox?.length !== 4) return null;
  const target = bbox as BboxM;
  return button(
    "map",
    function () {
      goToMapThen(function () {
        showBox(target, { layers: ["machines"].concat(nodeLayers(nodeNames)) });
      });
    },
    { map: true, title: "fly the map to " + what + " and outline it", label: "show " + what + " on the map" }
  );
}

function placeButton(): HTMLButtonElement {
  return button(
    "place",
    function () {
      pickTab("site");
    },
    { title: "put the plan's pad on the map" }
  );
}

function builtLineActions(b: TrackBuiltAt): HTMLElement[] {
  const out: HTMLElement[] = [];
  if (bench.gone) return out;
  const top = b.candidates[0];
  const change = button(picking.open ? "close" : "change", function () {
    picking = { key: bench.key, open: !picking.open };
    changed();
  }, { title: "pick what counts as built for this plan" });
  change.setAttribute("aria-expanded", String(picking.open));
  if (b.mode !== "auto") {
    out.push(button("auto", function () {
      setBuiltScope("");
    }, { title: "find what is built at the plan's site again" }));
  } else if (b.confidence === "unsure") {
    b.candidates.forEach(function (c) {
      out.push(button(c.name, function () {
        if (c.kind === "factory") setBuiltScope(c.name);
        else startNaming(c);
      }, { title: c.kind === "factory" ? "count “" + c.name + "” as built" : "name this " + WORDS.unnamedCluster + " and count it" }));
    });
    out.push(button(WORDS.nothingBuiltYet, function () {
      setBuiltScope(SCOPE_NONE);
    }));
  } else if (b.confidence === "likely" && top) {
    if (top.kind === "cluster") {
      out.push(button("name it", function () {
        startNaming(top!);
      }, { title: "name this " + WORDS.unnamedCluster + " and keep it as the plan's factory" }));
    }
    out.push(button("not this", function () {
      setBuiltScope(SCOPE_NONE);
    }, { title: "count nothing as built until you pick a factory" }));
  } else if (b.confidence === "no site") {
    out.push(placeButton());
  }
  if (b.mode !== "world" && b.confidence !== "no site") {
    const there = boxMapButton(top ? top.bbox_m : null, "what counts as built", []);
    if (there) out.push(there);
  }
  out.push(change);
  return out;
}

function hintLine(parent: HTMLElement, b: TrackBuiltAt): void {
  if (!b.hint) return;
  const line = make("div", "plan-line built-hint");
  line.appendChild(make("span", "dash-note", b.hint));
  const top = b.candidates[0];
  if (!bench.gone && b.mode === "none") {
    line.appendChild(button("count them", function () {
      setBuiltScope("");
    }));
  } else if (!bench.gone && top) {
    line.appendChild(button("use it", function () {
      if (top!.kind === "factory") setBuiltScope(top!.name);
      else startNaming(top!);
    }));
  }
  parent.appendChild(line);
}

function progressFigureTitle(b: TrackBuiltAt): string {
  if (b.built === null) return "not placed, so no progress";
  return settingChoice("progress") === "percent" ? "show machines instead" : "show percent of the planned rate instead";
}

/** The built line under the Track headline, with its answers. */
export function builtLine(parent: HTMLElement, b: TrackBuiltAt): void {
  if (picking.key !== bench.key) picking = { key: bench.key, open: false };
  if (naming.key && naming.key !== bench.key) naming.key = "";
  const line = make("div", "plan-line built-line");
  line.setAttribute("data-ctl", "built-line");
  const figure = make("button", "built-figure", progressText(b));
  figure.type = "button";
  figure.title = progressFigureTitle(b);
  figure.disabled = b.built === null;
  figure.onclick = function (event) {
    event.stopPropagation();
    toggleProgress();
  };
  line.appendChild(figure);
  if (b.text) line.appendChild(make("span", "built-where", b.text));
  builtLineActions(b).forEach(function (el) {
    line.appendChild(el);
  });
  parent.appendChild(line);
  if (b.fallback) parent.appendChild(make("p", "dash-note", b.fallback));
  hintLine(parent, b);
  let more = b.missing.length ? ["missing: " + b.missing.join(", ")] : [];
  if (b.also_here.length) more.push("also here: " + b.also_here.join(", "));
  more = more.concat(b.foreign);
  if (b.node_owner && b.confidence !== "nothing") more.push(b.node_owner);
  if (more.length) parent.appendChild(make("p", "dash-note", more.join(" · ")));
  if (naming.key === bench.key) nameBar(parent, b);
  if (picking.open && !bench.gone) scopePicker(parent, b);
}

/** The built column's title: "anywhere" when the plan has no site, so the count is not progress. */
export function builtColumn(b: TrackBuiltAt): string {
  return b.confidence === "no site" ? WORDS.anywhere : "built";
}
