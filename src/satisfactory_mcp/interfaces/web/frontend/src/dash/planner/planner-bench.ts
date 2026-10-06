/* The workbench: one plan at its head, every control a versioned gesture. */

import { button, chip, empty, error, link, loading, selectBox, toggleButton } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { bench, changed, displayName, itemList, knownItem, NAME_MAX, NOTES_MAX } from "./planner-state";
import { applyOps, banOps, dropChip, openPlan } from "./planner-writes";
import { cancelRename, forgottenBanner, renderBenchHeader, stripRows } from "./planner-bench-header";
import { renderChatSolveCard } from "./planner-chat-card";
import { renderVersions, renderRevisionView } from "./planner-history";
import { powerRow } from "./planner-power";
import { renderResult } from "./planner-result";
import { counted, OBJECTIVES } from "../../kit/words";

import type { FocusSelection } from "../../api/shapes";
import type { Op } from "./planner-state";

var CLOCKS = [1, 1.5, 2, 2.5];
var BENCH_FIELDS = ["objective", "export_minimums", "target_item", "exports", "sources", "required", "banned", "water_extractors", "sloops", "extractor_clocks", "payback_hours", "overclock_last", "power_price", "notes"];
var POWER_ITEM_PATTERN = /^(mw|power|__mw__)$/i;

var fieldErrors: Record<string, string> = {};
var rejectedRaw: Record<string, string> = {};
var renderedPlanKey = "";

/** Marks a control's value as refused; `raw` is kept to show again after the redraw. */
function markInvalid(ctl: string, text: string, raw?: string): void {
  fieldErrors[ctl] = text;
  if (raw !== undefined) rejectedRaw[ctl] = raw;
  changed();
}

function benchInput(ctl: string, value: string, type: string, commit: (raw: string) => void, placeholder?: string): HTMLInputElement {
  var box = make("input", type === "number" ? "dash-number" : "dash-name plan-text");
  box.type = type;
  box.value = value;
  box.defaultValue = value;
  box.setAttribute("data-ctl", ctl);
  if (placeholder) box.placeholder = placeholder;
  if (type === "number") box.step = "any";
  if (fieldErrors[ctl]) box.setAttribute("aria-invalid", "true");
  box.onchange = function () {
    box.defaultValue = box.value;
    delete fieldErrors[ctl];
    delete rejectedRaw[ctl];
    commit(box.value.trim());
  };
  box.onkeydown = function (event) {
    if (event.key === "Escape") {
      box.value = box.defaultValue;
      box.blur();
    }
  };
  return box;
}

/** A labelled row of controls, with the conflict chips of the fields it edits above them. */
function controlRow(parent: HTMLElement, fields: string[], label: string): HTMLElement {
  var row = make("div", "plan-row");
  row.appendChild(make("span", "plan-label", label));
  var body = make("div", "plan-controls");
  row.appendChild(body);
  bench.conflictChips.forEach(function (conflict) {
    if (fields.indexOf(conflict.field) < 0) return;
    conflictLine(row, conflict.id, conflict.who, conflict.text);
  });
  parent.appendChild(row);
  return body;
}

function showFieldErrors(parent: HTMLElement, ctls: string[]): void {
  ctls.forEach(function (ctl) {
    if (!fieldErrors[ctl]) return;
    var line = make("p", "plan-invalid", fieldErrors[ctl]);
    line.setAttribute("role", "alert");
    parent.appendChild(line);
  });
}

function conflictLine(parent: HTMLElement, id: number, who: string, text: string): void {
  var line = make("div", "plan-line");
  line.appendChild(chip("conflict", "bad"));
  line.appendChild(make("span", "", text));
  line.appendChild(
    button(
      "keep " + who + "'s",
      function () {
        dropChip(id, false);
      },
      { title: "leave the plan as it is now" }
    )
  );
  line.appendChild(
    button(
      "use yours",
      function () {
        dropChip(id, true);
      },
      { title: "push your change again on top of the new version" }
    )
  );
  parent.appendChild(line);
}

function removeButton(title: string, ariaLabel: string, ops: Op[], ctl?: string): HTMLButtonElement {
  var x = make("button", "plan-chip-x", "×");
  x.type = "button";
  if (ctl) x.setAttribute("data-ctl", ctl);
  x.title = title;
  x.setAttribute("aria-label", ariaLabel);
  x.onclick = function () {
    applyOps(ops);
  };
  return x;
}

function removableChip(parent: HTMLElement, text: string, ops: Op[], ctl?: string): void {
  var tag = make("span", "plan-chip", text);
  tag.appendChild(removeButton("remove", "remove " + text, ops, ctl));
  parent.appendChild(tag);
}

function memberChips(parent: HTMLElement, field: string, members: unknown[], words: (member: unknown) => string): void {
  members.forEach(function (member) {
    removableChip(parent, words(member), [{ op: "remove", field: field, member: member }], field + ":" + String(member));
  });
}

function addInput(parent: HTMLElement, ctl: string, placeholder: string, ops: (text: string) => Op[] | null, items?: boolean): void {
  var box = benchInput(
    ctl,
    rejectedRaw[ctl] || "",
    "text",
    function (raw) {
      if (!raw) return;
      var made = ops(raw);
      if (made) applyOps(made);
    },
    placeholder
  );
  box.className = "dash-name plan-add";
  box.maxLength = NAME_MAX;
  box.setAttribute("aria-label", placeholder.replace(/^\+ /, ""));
  if (items) box.setAttribute("list", "plan-items");
  parent.appendChild(box);
}

/** A typed number: null for blank, NaN for text that is not one. */
function parseOptionalNumber(raw: string): number | null {
  if (raw === "") return null;
  var n = Number(raw);
  return isFinite(n) ? n : NaN;
}

/** The item `raw` names, "MW" for power, or null after marking `ctl` invalid. */
function resolveItem(ctl: string, raw: string): string | null {
  if (POWER_ITEM_PATTERN.test(raw)) return "MW";
  var hit = knownItem(raw);
  if (!hit) markInvalid(ctl, "no item is called “" + raw + "”; pick one from the list", raw);
  return hit;
}

function goalRow(parent: HTMLElement): void {
  var args = bench.plan!.args;
  var body = controlRow(parent, ["objective", "target_item"], "goal");
  var goals = Object.keys(OBJECTIVES).map(function (key): [string, string] {
    return [key, OBJECTIVES[key]!];
  });
  var pick = selectBox(
    goals,
    args.objective,
    function (value) {
      applyOps([{ op: "set", field: "objective", value: value }]);
    },
    { label: "goal" }
  );
  pick.setAttribute("data-ctl", "objective");
  body.appendChild(pick);
  if (args.objective === "max_item") {
    var target = benchInput(
      "target-item",
      args.target_item || "",
      "text",
      function (raw) {
        var hit = raw ? resolveItem("target-item", raw) : "";
        if (hit !== null) applyOps([{ op: "set", field: "target_item", value: hit || null }]);
      },
      "item to maximise"
    );
    target.setAttribute("list", "plan-items");
    target.setAttribute("aria-label", "item to maximise");
    body.appendChild(target);
  }
  showFieldErrors(parent, ["target-item"]);
}

function exportRow(parent: HTMLElement, id: string, rate: number | null): void {
  var line = make("div", "plan-export");
  var label = displayName(id);
  line.appendChild(make("span", "plan-export-name", label));
  var ctl = "rate:" + id;
  var box = benchInput(
    ctl,
    rate === null ? "" : String(rate),
    "number",
    function (raw) {
      var n = parseOptionalNumber(raw);
      if (n === null) {
        if (rate !== null) applyOps([{ op: "del", field: "export_minimums", item: id }]);
      } else if (!(n > 0)) {
        markInvalid(ctl, "a rate is a positive number per minute, or blank for any");
      } else {
        applyOps([{ op: "put", field: "export_minimums", item: id, value: n }]);
      }
    },
    "any"
  );
  box.setAttribute("aria-label", "minimum " + label + " per minute");
  line.appendChild(box);
  line.appendChild(make("span", "dash-muted", "/min"));
  var ops: Op[] = [];
  if (bench.plan!.args.exports.indexOf(id) >= 0) ops.push({ op: "remove", field: "exports", member: id });
  if (rate !== null) ops.push({ op: "del", field: "export_minimums", item: id });
  line.appendChild(removeButton("stop exporting " + label, "remove export " + label, ops));
  parent.appendChild(line);
  showFieldErrors(parent, [ctl]);
}

function exportsRow(parent: HTMLElement): void {
  var args = bench.plan!.args;
  var body = controlRow(parent, ["exports", "export_minimums"], "exports");
  body.classList.add("plan-stack");
  var ids = args.exports.slice();
  Object.keys(args.export_minimums).forEach(function (id) {
    if (ids.indexOf(id) < 0) ids.push(id);
  });
  ids.forEach(function (id) {
    if (id === "MW" && !(id in args.export_minimums)) return;
    exportRow(body, id, id in args.export_minimums ? args.export_minimums[id]! : null);
  });
  var tail = make("div", "plan-controls");
  if (!args.exports.length) tail.appendChild(make("span", "dash-muted", "power only (the default)"));
  else {
    var power = args.exports.indexOf("MW") >= 0;
    var toggle = toggleButton(
      "export MW",
      power,
      function () {
        applyOps([{ op: power ? "remove" : "add", field: "exports", member: "MW" }]);
      },
      { title: power ? "stop exporting power; the plan may then draw from the grid" : "export power too; the plan then may not draw from the grid" }
    );
    tail.appendChild(toggle);
  }
  addInput(
    tail,
    "exports-add",
    "+ export an item",
    function (text) {
      var hit = resolveItem("exports-add", text);
      return hit ? [{ op: "add", field: "exports", member: hit }] : null;
    },
    true
  );
  body.appendChild(tail);
  showFieldErrors(body, ["exports-add"]);
}

function sourcesRow(parent: HTMLElement): void {
  var args = bench.plan!.args;
  var body = controlRow(parent, ["sources"], "from");
  if (!args.sources.length) body.appendChild(make("span", "dash-muted", "the whole map"));
  var groups: Record<string, string[]> = {};
  var order: string[] = [];
  args.sources.forEach(function (member) {
    var resource = member.indexOf("node:") === 0 && displayName(member) !== member ? displayName(member) : "";
    if (!resource) {
      removableChip(body, member, [{ op: "remove", field: "sources", member: member }]);
      return;
    }
    if (!groups[resource]) order.push(resource);
    (groups[resource] = groups[resource] || []).push(member);
  });
  order.forEach(function (resource) {
    var members = groups[resource]!;
    removableChip(
      body,
      counted(members.length, resource + " node"),
      members.map(function (member): Op {
        return { op: "remove", field: "sources", member: member };
      })
    );
  });
  addInput(body, "sources-add", "+ selector, e.g. region:Grass Fields", function (text) {
    return [{ op: "add", field: "sources", member: text }];
  });
}

function recipesRow(parent: HTMLElement): void {
  var args = bench.plan!.args;
  var body = controlRow(parent, ["required", "banned"], "recipes");
  var recipeWords = function (member: unknown): string {
    return displayName(String(member));
  };
  body.appendChild(make("span", "plan-sub", "required"));
  if (!args.required.length) body.appendChild(make("span", "dash-muted", "none"));
  memberChips(body, "required", args.required, recipeWords);
  body.appendChild(make("span", "plan-sub", "banned"));
  if (!args.banned.length) body.appendChild(make("span", "dash-muted", "none"));
  memberChips(body, "banned", args.banned, recipeWords);
  addInput(body, "banned-add", "+ ban a recipe or pattern", banOps);
}

function clockToggles(body: HTMLElement): void {
  var chosen = bench.plan!.args.extractor_clocks;
  var offered = chosen.length ? chosen : [1];
  CLOCKS.forEach(function (clock) {
    var picked = offered.indexOf(clock) >= 0;
    var only = picked && offered.length === 1;
    var ops: Op[] = [];
    if (!chosen.length && !picked) ops.push({ op: "add", field: "extractor_clocks", member: 1 });
    ops.push({ op: picked ? "remove" : "add", field: "extractor_clocks", member: clock });
    var toggle = toggleButton(
      clock * 100 + "%",
      picked,
      function () {
        applyOps(ops);
      },
      {
        title: only ? "extractors need at least one clock" : picked ? "stop offering extractors at this clock" : "offer extractors at this clock (above 100% needs power shards)",
        disabled: only,
      }
    );
    body.appendChild(toggle);
  });
}

function supplyRow(parent: HTMLElement): void {
  var args = bench.plan!.args;
  var body = controlRow(parent, ["water_extractors", "sloops", "extractor_clocks"], "supply");
  body.appendChild(make("span", "plan-sub", "water extractors"));
  var water = benchInput(
    "water",
    args.water_extractors === null ? "" : String(args.water_extractors),
    "number",
    function (raw) {
      var n = parseOptionalNumber(raw);
      if (n !== null && !(Number.isInteger(n) && n >= 0)) {
        markInvalid("water", "water extractors is a whole number, or blank for auto");
        return;
      }
      applyOps([{ op: "set", field: "water_extractors", value: n }]);
    },
    "auto"
  );
  water.setAttribute("aria-label", "water extractors");
  body.appendChild(water);
  body.appendChild(make("span", "plan-sub", "somersloops"));
  var sloops = benchInput("sloops", String(args.sloops), "number", function (raw) {
    var n = parseOptionalNumber(raw);
    if (n === null) n = 0;
    if (!(Number.isInteger(n) && n >= 0)) {
      markInvalid("sloops", "somersloops is a whole number");
      return;
    }
    applyOps([{ op: "set", field: "sloops", value: n }]);
  });
  sloops.setAttribute("aria-label", "somersloops");
  body.appendChild(sloops);
  body.appendChild(make("span", "plan-sub", "extractor clocks"));
  clockToggles(body);
  showFieldErrors(parent, ["water", "sloops"]);
}

function notesRow(parent: HTMLElement): void {
  var body = controlRow(parent, ["notes"], "notes");
  var box = make("textarea", "dash-name plan-notes");
  var value = bench.plan!.notes;
  box.value = value;
  box.defaultValue = value;
  box.rows = 3;
  box.maxLength = NOTES_MAX;
  box.placeholder = "notes for this plan";
  box.setAttribute("aria-label", "notes");
  box.setAttribute("data-ctl", "notes");
  box.onchange = function () {
    box.defaultValue = box.value;
    applyOps([{ op: "set", field: "notes", value: box.value }]);
  };
  body.appendChild(box);
}

export function renderBench(root: HTMLElement, select: (selection: FocusSelection) => void, close: () => void): void {
  if (renderedPlanKey !== bench.key) {
    renderedPlanKey = bench.key;
    fieldErrors = {};
    rejectedRaw = {};
    cancelRename();
  }
  root.appendChild(link("planner", "‹ all plans", "dash-back"));
  if (bench.missing) {
    empty(root, bench.error, "it may belong to another world; pick one from the list");
    return;
  }
  if (bench.error) {
    var key = bench.key;
    error(root, "this plan", bench.error, function () {
      openPlan(key);
    });
    return;
  }
  if (!bench.plan) {
    loading(root, "the plan");
    return;
  }
  root.appendChild(itemList());
  renderBenchHeader(root);
  if (bench.gone) forgottenBanner(root);
  renderVersions(root);
  if (bench.viewedRev) {
    renderRevisionView(root, select);
    return;
  }
  renderChatSolveCard(root);
  stripRows(root);
  bench.conflictChips.forEach(function (conflict) {
    if (BENCH_FIELDS.indexOf(conflict.field) < 0) conflictLine(root, conflict.id, conflict.who, conflict.text);
  });
  if (bench.tab === "site") {
    renderResult(root, select, close);
    return;
  }
  var controls = make("fieldset", "dash-card plan-bench");
  controls.disabled = bench.gone;
  goalRow(controls);
  exportsRow(controls);
  sourcesRow(controls);
  recipesRow(controls);
  supplyRow(controls);
  powerRow(controlRow(controls, ["payback_hours", "overclock_last", "power_price"], "power"));
  notesRow(controls);
  root.appendChild(controls);
  renderResult(root, select, close);
}
