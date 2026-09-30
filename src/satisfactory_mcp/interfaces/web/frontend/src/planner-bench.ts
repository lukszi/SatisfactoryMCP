/* The workbench: one plan at its head, every control a versioned gesture. */

import { askButton } from "./asks";
import { button, chip, choice, copyButton, empty, error, link, loading, pressed } from "./dashkit";
import { make } from "./dom";
import { perMin } from "./format";
import { go } from "./nav";
import {
  age,
  applyArgs,
  bench,
  changed,
  createPlan,
  dismissStrip,
  dropChip,
  forgetPlan,
  gesture,
  inbox,
  itemList,
  knownItem,
  NAME_MAX,
  NOTES_MAX,
  openPlan,
  redoLast,
  renamePlan,
  restorePlan,
  status,
  toggleVersions,
  undoLast,
  undoRev,
} from "./planner-core";
import { duplicateButton, renderVersions, renderView } from "./planner-history";
import { loadList, planTitle } from "./planner-list";
import { powerRow } from "./planner-power";
import { banOps, recipeName, renderResult } from "./planner-result";
import { pinFor, pinThis } from "./pins";
import { fail, friendly } from "./toast";
import { counted, OBJECTIVES, objectiveText, W } from "./words";

import type { Op, Selection } from "./planner-core";

var CLOCKS = [1, 1.5, 2, 2.5];
var FIELDS = ["objective", "export_minimums", "target_item", "exports", "sources", "required", "banned", "water_extractors", "sloops", "extractor_clocks", "power_priority", "notes"];
var POWER = /^(mw|power|__mw__)$/i;

var invalid: Record<string, string> = {};
var kept: Record<string, string> = {};
var renaming = { on: false, fresh: false };
var shown = "";

function name(id: string): string {
  return (bench.plan && bench.plan.names[id]) || id;
}

function flag(ctl: string, text: string, raw?: string): void {
  invalid[ctl] = text;
  if (raw !== undefined) kept[ctl] = raw;
  changed();
}

function input(ctl: string, value: string, type: string, commit: (raw: string) => void, placeholder?: string): HTMLInputElement {
  var box = make("input", type === "number" ? "dash-number" : "dash-name plan-text");
  box.type = type;
  box.value = value;
  box.defaultValue = value;
  box.setAttribute("data-ctl", ctl);
  if (placeholder) box.placeholder = placeholder;
  if (type === "number") box.step = "any";
  if (invalid[ctl]) box.setAttribute("aria-invalid", "true");
  box.onchange = function () {
    box.defaultValue = box.value;
    delete invalid[ctl];
    delete kept[ctl];
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

function section(parent: HTMLElement, field: string[], label: string): HTMLElement {
  var row = make("div", "plan-row");
  row.appendChild(make("span", "plan-label", label));
  var body = make("div", "plan-controls");
  row.appendChild(body);
  bench.chips.forEach(function (c) {
    if (field.indexOf(c.field) < 0) return;
    conflictLine(row, c.id, c.who, c.text);
  });
  parent.appendChild(row);
  return body;
}

function problems(parent: HTMLElement, ctls: string[]): void {
  ctls.forEach(function (ctl) {
    if (!invalid[ctl]) return;
    var line = make("p", "plan-invalid", invalid[ctl]);
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

function removable(parent: HTMLElement, text: string, ops: Op[], ctl?: string): void {
  var c = make("span", "plan-chip", text);
  var x = make("button", "plan-chip-x", "×");
  x.type = "button";
  if (ctl) x.setAttribute("data-ctl", ctl);
  x.title = "remove";
  x.setAttribute("aria-label", "remove " + text);
  x.onclick = function () {
    gesture(ops);
  };
  c.appendChild(x);
  parent.appendChild(c);
}

function chips(parent: HTMLElement, field: string, members: unknown[], words: (m: unknown) => string): void {
  members.forEach(function (m) {
    removable(parent, words(m), [{ op: "remove", field: field, member: m }], field + ":" + String(m));
  });
}

function adder(parent: HTMLElement, ctl: string, placeholder: string, ops: (text: string) => Op[] | null, items?: boolean): void {
  var box = input(
    ctl,
    kept[ctl] || "",
    "text",
    function (raw) {
      if (!raw) return;
      var made = ops(raw);
      if (made) gesture(made);
    },
    placeholder
  );
  box.className = "dash-name plan-add";
  box.maxLength = NAME_MAX;
  box.setAttribute("aria-label", placeholder.replace(/^\+ /, ""));
  if (items) box.setAttribute("list", "plan-items");
  parent.appendChild(box);
}

function number(raw: string): number | null {
  if (raw === "") return null;
  var n = Number(raw);
  return isFinite(n) ? n : NaN;
}

function item(ctl: string, raw: string): string | null {
  if (POWER.test(raw)) return "MW";
  var hit = knownItem(raw);
  if (!hit) flag(ctl, "no item is called “" + raw + "”; pick one from the list", raw);
  return hit;
}

function goal(parent: HTMLElement): void {
  var args = bench.plan!.args;
  var body = section(parent, ["objective", "target_item"], "goal");
  var goals = Object.keys(OBJECTIVES).map(function (key): [string, string] {
    return [key, OBJECTIVES[key]!];
  });
  var pick = choice(
    goals,
    args.objective,
    function (value) {
      gesture([{ op: "set", field: "objective", value: value }]);
    },
    { label: "goal" }
  );
  pick.setAttribute("data-ctl", "objective");
  body.appendChild(pick);
  if (args.objective === "max_item") {
    var target = input(
      "target-item",
      args.target_item || "",
      "text",
      function (raw) {
        var hit = raw ? item("target-item", raw) : "";
        if (hit !== null) gesture([{ op: "set", field: "target_item", value: hit || null }]);
      },
      "item to maximise"
    );
    target.setAttribute("list", "plan-items");
    target.setAttribute("aria-label", "item to maximise");
    body.appendChild(target);
  }
  problems(parent, ["target-item"]);
}

function exportRow(parent: HTMLElement, id: string, rate: number | null): void {
  var line = make("div", "plan-export");
  var label = name(id);
  line.appendChild(make("span", "plan-export-name", label));
  var ctl = "rate:" + id;
  var box = input(
    ctl,
    rate === null ? "" : String(rate),
    "number",
    function (raw) {
      var n = number(raw);
      if (n === null) {
        if (rate !== null) gesture([{ op: "del", field: "export_minimums", item: id }]);
      } else if (!(n > 0)) {
        flag(ctl, "a rate is a positive number per minute, or blank for any");
      } else {
        gesture([{ op: "put", field: "export_minimums", item: id, value: n }]);
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
  var x = make("button", "plan-chip-x", "×");
  x.type = "button";
  x.title = "stop exporting " + label;
  x.setAttribute("aria-label", "remove export " + label);
  x.onclick = function () {
    gesture(ops);
  };
  line.appendChild(x);
  parent.appendChild(line);
  problems(parent, [ctl]);
}

function exportsRow(parent: HTMLElement): void {
  var args = bench.plan!.args;
  var body = section(parent, ["exports", "export_minimums"], "exports");
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
    var toggle = pressed(
      "export MW",
      power,
      function () {
        gesture([{ op: power ? "remove" : "add", field: "exports", member: "MW" }]);
      },
      { title: power ? "stop exporting power; the plan may then draw from the grid" : "export power too; the plan then may not draw from the grid" }
    );
    tail.appendChild(toggle);
  }
  adder(
    tail,
    "exports-add",
    "+ export an item",
    function (t) {
      var hit = item("exports-add", t);
      return hit ? [{ op: "add", field: "exports", member: hit }] : null;
    },
    true
  );
  body.appendChild(tail);
  problems(body, ["exports-add"]);
}

function sources(parent: HTMLElement): void {
  var args = bench.plan!.args;
  var body = section(parent, ["sources"], "from");
  if (!args.sources.length) body.appendChild(make("span", "dash-muted", "the whole map"));
  var groups: Record<string, string[]> = {};
  var order: string[] = [];
  args.sources.forEach(function (member) {
    var key = member.indexOf("node:") === 0 && name(member) !== member ? name(member) : "";
    if (!key) {
      removable(body, member, [{ op: "remove", field: "sources", member: member }]);
      return;
    }
    if (!groups[key]) order.push(key);
    (groups[key] = groups[key] || []).push(member);
  });
  order.forEach(function (resource) {
    var members = groups[resource]!;
    removable(
      body,
      counted(members.length, resource + " node"),
      members.map(function (m): Op {
        return { op: "remove", field: "sources", member: m };
      })
    );
  });
  adder(body, "sources-add", "+ selector, e.g. region:Grass Fields", function (t) {
    return [{ op: "add", field: "sources", member: t }];
  });
}

function recipes(parent: HTMLElement): void {
  var args = bench.plan!.args;
  var rec = section(parent, ["required", "banned"], "recipes");
  rec.appendChild(make("span", "plan-sub", "required"));
  if (!args.required.length) rec.appendChild(make("span", "dash-muted", "none"));
  chips(rec, "required", args.required, function (m) {
    return recipeName(String(m));
  });
  rec.appendChild(make("span", "plan-sub", "banned"));
  if (!args.banned.length) rec.appendChild(make("span", "dash-muted", "none"));
  chips(rec, "banned", args.banned, function (m) {
    return recipeName(String(m));
  });
  adder(rec, "banned-add", "+ ban a recipe or pattern", banOps);
}

function clocks(body: HTMLElement): void {
  var chosen = bench.plan!.args.extractor_clocks;
  var on = chosen.length ? chosen : [1];
  CLOCKS.forEach(function (c) {
    var picked = on.indexOf(c) >= 0;
    var only = picked && on.length === 1;
    var ops: Op[] = [];
    if (!chosen.length && !picked) ops.push({ op: "add", field: "extractor_clocks", member: 1 });
    ops.push({ op: picked ? "remove" : "add", field: "extractor_clocks", member: c });
    var b = pressed(
      c * 100 + "%",
      picked,
      function () {
        gesture(ops);
      },
      {
        title: only ? "extractors need at least one clock" : picked ? "stop offering extractors at this clock" : "offer extractors at this clock (above 100% needs power shards)",
        disabled: only,
      }
    );
    body.appendChild(b);
  });
}

function supply(parent: HTMLElement): void {
  var args = bench.plan!.args;
  var body = section(parent, ["water_extractors", "sloops", "extractor_clocks"], "supply");
  body.appendChild(make("span", "plan-sub", "water extractors"));
  var water = input(
    "water",
    args.water_extractors === null ? "" : String(args.water_extractors),
    "number",
    function (raw) {
      var n = number(raw);
      if (n !== null && !(Number.isInteger(n) && n >= 0)) {
        flag("water", "water extractors is a whole number, or blank for auto");
        return;
      }
      gesture([{ op: "set", field: "water_extractors", value: n }]);
    },
    "auto"
  );
  water.setAttribute("aria-label", "water extractors");
  body.appendChild(water);
  body.appendChild(make("span", "plan-sub", "somersloops"));
  var sloops = input("sloops", String(args.sloops), "number", function (raw) {
    var n = number(raw);
    if (n === null) n = 0;
    if (!(Number.isInteger(n) && n >= 0)) {
      flag("sloops", "somersloops is a whole number");
      return;
    }
    gesture([{ op: "set", field: "sloops", value: n }]);
  });
  sloops.setAttribute("aria-label", "somersloops");
  body.appendChild(sloops);
  body.appendChild(make("span", "plan-sub", "extractor clocks"));
  clocks(body);
  problems(parent, ["water", "sloops"]);
}

function notes(parent: HTMLElement): void {
  var body = section(parent, ["notes"], "notes");
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
    gesture([{ op: "set", field: "notes", value: box.value }]);
  };
  body.appendChild(box);
}

function stripRows(parent: HTMLElement): void {
  if (!bench.strip.length) return;
  var box = make("section", "dash-card");
  var title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", "changed since you opened it"));
  title.appendChild(
    button(
      "dismiss",
      function () {
        dismissStrip(null);
      },
      { title: "clear this list" }
    )
  );
  box.appendChild(title);
  bench.strip.forEach(function (row) {
    var line = make("div", "plan-line");
    line.appendChild(chip(row.who, "muted"));
    line.appendChild(make("span", "dash-what" + (row.undone ? " dash-muted" : ""), "v" + row.rev + " " + row.text + (row.undone ? " (undone)" : "")));
    if (!row.undone && !bench.gone) {
      line.appendChild(
        button(
          "undo",
          function () {
            undoRev(row.rev);
          },
          { title: "undo v" + row.rev + " as a new version" }
        )
      );
    }
    box.appendChild(line);
  });
  var d = bench.stripDelta;
  if (d) box.appendChild(make("p", "dash-note", "result since v" + d.from_rev + ": " + d.text));
  parent.appendChild(box);
}

export function argsWords(args: Record<string, unknown>): string {
  var parts: string[] = [];
  var objective = args.objective as string | undefined;
  if (objective) parts.push("goal " + (OBJECTIVES[objective] || objective) + (args.target_item ? " of " + args.target_item : ""));
  var mins = (args.export_minimums as Record<string, number> | undefined) || {};
  var exported = ((args.exports as string[] | undefined) || []).slice();
  Object.keys(mins).forEach(function (k) {
    if (exported.indexOf(k) < 0) exported.push(k);
  });
  if (exported.length) {
    parts.push(
      "exports " +
        exported
          .map(function (e) {
            return e in mins ? e + " " + perMin(mins[e]!) : e;
          })
          .join(", ")
    );
  }
  var from = (args.sources as string[] | undefined) || [];
  var nodes = from.filter(function (f) {
    return f.indexOf("node:") === 0;
  }).length;
  if (!from.length) parts.push("from the whole map");
  else if (nodes === from.length) parts.push("from " + counted(nodes, "node"));
  else if (from.length < 3) parts.push("from " + from.join(", "));
  else parts.push("from " + counted(from.length, "selector"));
  var banned = ((args.exclude_recipes || args.banned) as string[] | undefined) || [];
  if (banned.length) parts.push(counted(banned.length, "banned recipe"));
  var required = (args.required as string[] | undefined) || [];
  if (required.length) parts.push(counted(required.length, "required recipe"));
  if (args.sloops) parts.push(counted(Number(args.sloops), "somersloop"));
  if (args.power_priority) parts.push("power priority " + String(args.power_priority));
  return parts.join(" · ");
}

function chatName(args: Record<string, unknown>, fallback: string): string {
  var mins = args.export_minimums as Record<string, number> | undefined;
  var items = mins ? Object.keys(mins) : [];
  if (items.length) return items[0] + " " + perMin(mins![items[0]!]!);
  return fallback.slice(0, 40) || "chat solve";
}

export function renderCard(parent: HTMLElement): void {
  var entry = inbox.card;
  if (!entry) return;
  var card = make("section", "dash-card plan-strip");
  var title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", "chat solved this; nothing is saved yet"));
  card.appendChild(title);
  card.appendChild(
    make(
      "p",
      "",
      objectiveText(entry.text)
    )
  );
  var args = entry.args;
  if (args) card.appendChild(make("p", "dash-note", argsWords(args)));
  var acts = make("div", "dash-acts");
  var elsewhere = entry.plan && entry.plan !== bench.key ? entry.plan : "";
  if (elsewhere) {
    var target = elsewhere;
    var called = planTitle(target) || entry.name;
    if (!called) loadList();
    acts.appendChild(
      button(
        "open " + (called ? "“" + called + "”" : "the plan chat used"),
        function () {
          go("planner/" + target);
        },
        { title: "open the plan chat solved from; apply it there to merge with its edits" }
      )
    );
  }
  if (bench.plan && args && !bench.gone) {
    var from = entry.plan === bench.key && typeof entry.rev === "number" ? entry.rev : undefined;
    acts.appendChild(
      button(
        from === undefined ? "replace this plan's request" : "apply to this plan",
        function () {
          inbox.card = null;
          applyArgs(args!, entry!.id, from);
        },
        {
          title:
            from === undefined
              ? "chat did not solve from this plan: replace its whole request with chat's, as one new version"
              : "apply what chat changed from v" + from + ", merged with edits made since",
        }
      )
    );
  }
  if (args) {
    acts.appendChild(
      button(
        "new plan from it",
        function () {
          inbox.card = null;
          changed();
          createPlan(chatName(args!, entry!.text), args!, entry!.id)
            .then(function (key) {
              go("planner/" + key);
            })
            .catch(function (reason) {
              fail(friendly(reason));
            });
        },
        { title: "save chat's request as a new plan and open it" }
      )
    );
  }
  acts.appendChild(
    button(
      "dismiss",
      function () {
        inbox.card = null;
        changed();
      },
      { title: "forget this card" }
    )
  );
  card.appendChild(acts);
  parent.appendChild(card);
}

function titleLine(head: HTMLElement): void {
  var plan = bench.plan!;
  if (!renaming.on) {
    head.appendChild(make("h1", "", plan.name));
    return;
  }
  var box = make("input", "dash-name plan-rename");
  box.value = plan.name;
  box.defaultValue = plan.name;
  box.maxLength = NAME_MAX;
  box.setAttribute("data-ctl", "rename");
  box.setAttribute("aria-label", "plan name");
  var done = function (keep: boolean) {
    if (!renaming.on) return;
    var wanted = box.value.trim();
    if (keep && !wanted) {
      flag("rename", "a plan name cannot be blank");
      return;
    }
    renaming.on = false;
    delete invalid.rename;
    if (!keep) box.value = plan.name;
    box.defaultValue = box.value;
    box.blur();
    if (keep && wanted !== plan.name) renamePlan(wanted);
    else changed();
  };
  box.onkeydown = function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      done(true);
    } else if (event.key === "Escape") done(false);
  };
  box.onblur = function () {
    done(true);
  };
  head.appendChild(box);
  if (renaming.fresh) {
    renaming.fresh = false;
    setTimeout(function () {
      box.focus();
      box.select();
    }, 0);
  }
}

function header(parent: HTMLElement): void {
  var plan = bench.plan!;
  var head = make("div", "dash-title");
  titleLine(head);
  var st = status();
  head.appendChild(make("span", "plan-status" + (st === "conflict" ? " bad" : ""), "v" + plan.rev + " · " + st));
  if (bench.last) head.appendChild(make("span", "plan-status", "last change: " + bench.last.who + ", " + age(bench.last.ts) + " ago"));
  head.appendChild(make("span", "plan-held"));
  parent.appendChild(head);
  problems(parent, ["rename"]);
  var acts = make("div", "dash-acts plan-acts");
  acts.appendChild(button("undo", undoLast, { title: "undo your last change on this plan (Ctrl+Z)", disabled: bench.gone || !bench.done.length }));
  acts.appendChild(button("redo", redoLast, { title: "undo that undo (Ctrl+Shift+Z)", disabled: bench.gone || !bench.redo.length }));
  acts.appendChild(
    button(
      "rename",
      function () {
        renaming.on = true;
        renaming.fresh = true;
        changed();
      },
      { disabled: bench.gone || renaming.on }
    )
  );
  if (!bench.gone) acts.appendChild(button("forget", forgetPlan, { title: "hide this plan from the list; its history is kept and restore brings it back" }));
  acts.appendChild(pressed("versions", bench.versionsOpen, toggleVersions, { title: "every version of this plan: view one, or restore it as a new version" }));
  acts.appendChild(duplicateButton());
  var key = bench.key;
  var pinned = pinFor("plan", function (ref) {
    return ref.plan === key;
  });
  acts.appendChild(
    button(
      pinned ? "copy " + pinned.id : W.pin,
      function () {
        pinThis("plan", { plan: key });
      },
      { title: "pin this plan and copy its pin:N for chat", label: pinned ? undefined : "pin this plan" }
    )
  );
  var call = "plan_factory(plan=" + JSON.stringify(plan.name) + ")  # base_rev=" + plan.rev;
  acts.appendChild(copyButton(call, "copy as tool call", { title: call }));
  if (!bench.gone) acts.appendChild(askButton({ kind: "plan", label: plan.name, ref: key, plan: key, rev: plan.rev }, "plan", W.askChat));
  parent.appendChild(acts);
}

function gone(parent: HTMLElement): void {
  var line = make("div", "plan-warning plan-gone");
  line.appendChild(make("span", "", "this plan was forgotten: restore it to edit it"));
  line.appendChild(button("restore", restorePlan, { title: "bring the plan back as a new version" }));
  parent.appendChild(line);
}

export function renderBench(root: HTMLElement, select: (s: Selection) => void, close: () => void): void {
  if (shown !== bench.key) {
    shown = bench.key;
    invalid = {};
    kept = {};
    renaming.on = false;
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
  header(root);
  if (bench.gone) gone(root);
  renderVersions(root);
  if (bench.view) {
    renderView(root, select);
    return;
  }
  renderCard(root);
  stripRows(root);
  bench.chips.forEach(function (c) {
    if (FIELDS.indexOf(c.field) < 0) conflictLine(root, c.id, c.who, c.text);
  });
  var controls = make("fieldset", "dash-card plan-bench");
  controls.disabled = bench.gone;
  goal(controls);
  exportsRow(controls);
  sources(controls);
  recipes(controls);
  supply(controls);
  powerRow(section(controls, ["power_priority"], "power"));
  notes(controls);
  root.appendChild(controls);
  renderResult(root, select, close);
}
