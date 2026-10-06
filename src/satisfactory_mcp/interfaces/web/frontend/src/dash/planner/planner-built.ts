/* The "built at" line on Track: where the plan's built machines were found, its progress in
 * machines or percent, and the one-line answers. See docs/planner-p4_contract.md §5.2. */

import { send } from "../../api/client";
import { button, choice, fieldError } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { onMap } from "../../app/nav";
import { showBox, vitals } from "../../map/panel";
import { bench, changed, gesture, pickTab } from "./planner-core";
import { blankOrLong, newest, refreshLabels, refusal, wrote } from "../factories/rename";
import { choice as setting, setSetting } from "../../app/settings";
import { fail, friendly, note } from "../../kit/toast";
import { W } from "../../kit/words";

import type { NamedResponse, TrackBuiltAt, TrackBuiltCandidate } from "../../api/shapes";

type Box = [number, number, number, number];

var WORLD = "/world";
var NONE = "/none";
var CLUSTER = "cluster:";

var naming = { key: "", proposal: -1, name: "", problem: "", busy: false };
var picking = { key: "", open: false };

interface Figures {
  built: number | null;
  figure: string;
  percent: number | null;
  percent_max: number | null;
}

/** "12 / 16 machines" or "75%", as the progress setting says; "–" when not placed. */
export function progressText(f: Figures): string {
  if (f.built === null) return f.figure || "–";
  if (setting("progress") === "percent" && f.percent !== null) {
    var lo = Math.round(f.percent);
    var hi = f.percent_max === null ? lo : Math.round(f.percent_max);
    return (hi !== lo ? lo + "–" + hi : String(lo)) + "%";
  }
  return f.figure + " machines";
}

export function toggleProgress(): void {
  setSetting("progress", setting("progress") === "percent" ? "machines" : "percent");
  changed();
}

function setFactory(value: string): void {
  var plan = bench.plan;
  if (!plan || bench.gone || plan.factory === value) return;
  picking.open = false;
  gesture([{ op: "set", field: "factory", value: value }]);
}

function startNaming(c: TrackBuiltCandidate): void {
  naming = { key: bench.key, proposal: c.proposal === null ? -1 : c.proposal, name: c.name, problem: "", busy: false };
  picking.open = false;
  changed();
}

function saveName(b: TrackBuiltAt, field: HTMLInputElement): void {
  var name = field.value.trim();
  var problem = blankOrLong(name);
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
    version: newest(b.labels_version),
  })
    .then(function (reply) {
      wrote(reply.version);
      naming = { key: "", proposal: -1, name: "", problem: "", busy: false };
      note("named “" + reply.name + "”; the plan now counts it as built");
      refreshLabels();
      setFactory(reply.name);
    })
    .catch(function (failure) {
      var why = refusal(failure);
      naming.busy = false;
      if (why === "name_taken") naming.problem = "“" + name + "” is already a factory name";
      else if (why === "bad") naming.problem = friendly(failure);
      else if (why === "stale" || why === "pin") {
        naming.key = "";
        fail("the save or the factory names moved, so “" + name + "” was not written; look again");
      } else fail("naming “" + name + "”: " + friendly(failure));
      changed();
    });
}

function nameBar(parent: HTMLElement, b: TrackBuiltAt): void {
  var bar = make("div", "plan-line built-name");
  var field = make("input", "dash-name plan-text");
  field.value = naming.name;
  field.setAttribute("data-ctl", "built-name");
  field.setAttribute("aria-label", "name for this " + W.unnamedCluster);
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
    }, { disabled: naming.busy, title: "name this " + W.unnamedCluster + " and count it as this plan's" })
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
  var health = vitals().health;
  var names = health
    ? health.factories.map(function (f) {
        return f.name;
      })
    : [];
  if (current && current.charAt(0) !== "/" && names.indexOf(current) < 0) names.push(current);
  return names.sort(function (a, b) {
    return a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });
  });
}

function picker(parent: HTMLElement, b: TrackBuiltAt): void {
  var plan = bench.plan!;
  var clusters = b.candidates.filter(function (c) {
    return c.kind === "cluster" && c.proposal !== null;
  });
  var options: [string, string][] = [["", W.foundAutomatically]];
  clusters.forEach(function (c) {
    options.push([CLUSTER + c.proposal, c.name + " (" + W.unnamedCluster + ": names it)"]);
  });
  factoryNames(plan.factory).forEach(function (name) {
    options.push([name, name]);
  });
  options.push([WORLD, W.wholeWorld], [NONE, W.nothingBuiltYet]);
  var pick = choice(
    options,
    plan.factory,
    function (value) {
      if (value.indexOf(CLUSTER) === 0) {
        var n = Number(value.slice(CLUSTER.length));
        var c = clusters.filter(function (x) {
          return x.proposal === n;
        })[0];
        if (c) startNaming(c);
        return;
      }
      setFactory(value);
    },
    { label: W.countAsBuilt, disabled: bench.gone }
  );
  pick.setAttribute("data-ctl", "track-scope");
  var row = make("div", "plan-line");
  row.appendChild(make("span", "plan-sub", W.countAsBuilt));
  row.appendChild(pick);
  parent.appendChild(row);
}

function mapButton(c: TrackBuiltCandidate | undefined): HTMLButtonElement | null {
  var at = c && c.bbox_m && c.bbox_m.length === 4 ? (c.bbox_m as Box) : null;
  if (!at) return null;
  var target = at;
  return button(
    "map",
    function () {
      onMap(function () {
        showBox(target, { layers: ["machines"] });
      });
    },
    { map: true, title: "fly the map to what counts as built and outline it", label: "show what counts as built on the map" }
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

function actions(b: TrackBuiltAt): HTMLElement[] {
  var out: HTMLElement[] = [];
  if (bench.gone) return out;
  var top = b.candidates[0];
  var change = button(picking.open ? "close" : "change", function () {
    picking = { key: bench.key, open: !picking.open };
    changed();
  }, { title: "pick what counts as built for this plan" });
  change.setAttribute("aria-expanded", String(picking.open));
  if (b.mode !== "auto") {
    out.push(button("auto", function () {
      setFactory("");
    }, { title: "find what is built at the plan's site again" }));
  } else if (b.confidence === "unsure") {
    b.candidates.forEach(function (c) {
      out.push(button(c.name, function () {
        if (c.kind === "factory") setFactory(c.name);
        else startNaming(c);
      }, { title: c.kind === "factory" ? "count “" + c.name + "” as built" : "name this " + W.unnamedCluster + " and count it" }));
    });
    out.push(button(W.nothingBuiltYet, function () {
      setFactory(NONE);
    }));
  } else if (b.confidence === "likely" && top) {
    if (top.kind === "cluster") {
      out.push(button("name it", function () {
        startNaming(top!);
      }, { title: "name this " + W.unnamedCluster + " and keep it as the plan's factory" }));
    }
    out.push(button("not this", function () {
      setFactory(NONE);
    }, { title: "count nothing as built until you pick a factory" }));
  } else if (b.confidence === "no site") {
    out.push(placeButton());
  }
  var there = b.mode === "world" || b.confidence === "no site" ? null : mapButton(top);
  if (there) out.push(there);
  out.push(change);
  return out;
}

function hintLine(parent: HTMLElement, b: TrackBuiltAt): void {
  if (!b.hint) return;
  var line = make("div", "plan-line built-hint");
  line.appendChild(make("span", "dash-note", b.hint));
  var top = b.candidates[0];
  if (!bench.gone && b.mode === "none") {
    line.appendChild(button("count them", function () {
      setFactory("");
    }));
  } else if (!bench.gone && top) {
    line.appendChild(button("use it", function () {
      if (top!.kind === "factory") setFactory(top!.name);
      else startNaming(top!);
    }));
  }
  parent.appendChild(line);
}

/** The built line under the Track headline, with its answers. */
export function builtLine(parent: HTMLElement, b: TrackBuiltAt): void {
  if (picking.key !== bench.key) picking = { key: bench.key, open: false };
  if (naming.key && naming.key !== bench.key) naming.key = "";
  var line = make("div", "plan-line built-line");
  line.setAttribute("data-ctl", "built-line");
  var figure = make("button", "built-figure", progressText(b));
  figure.type = "button";
  figure.title = b.built === null ? "not placed, so no progress" : setting("progress") === "percent" ? "show machines instead" : "show percent of the planned rate instead";
  figure.disabled = b.built === null;
  figure.onclick = function (event) {
    event.stopPropagation();
    toggleProgress();
  };
  line.appendChild(figure);
  if (b.text) line.appendChild(make("span", "built-where", b.text));
  actions(b).forEach(function (el) {
    line.appendChild(el);
  });
  parent.appendChild(line);
  if (b.fallback) parent.appendChild(make("p", "dash-note", b.fallback));
  hintLine(parent, b);
  var more = b.missing.length ? ["missing: " + b.missing.join(", ")] : [];
  if (b.also_here.length) more.push("also here: " + b.also_here.join(", "));
  more = more.concat(b.foreign);
  if (b.node_owner && b.confidence !== "nothing") more.push(b.node_owner);
  if (more.length) parent.appendChild(make("p", "dash-note", more.join(" · ")));
  if (naming.key === bench.key) nameBar(parent, b);
  if (picking.open && !bench.gone) picker(parent, b);
}

/** The built column's title: "anywhere" when the plan has no site, so the count is not progress. */
export function builtColumn(b: TrackBuiltAt): string {
  return b.confidence === "no site" ? W.anywhere : "built";
}
