/* The workbench: one plan at its head, every control a versioned gesture. */

import { COPY_ATTR, COPY_CLASS, make } from "./dom";
import { hashFor } from "./map";
import {
  age,
  applyArgs,
  bench,
  changed,
  createPlan,
  dismissStrip,
  dropChip,
  gesture,
  inbox,
  rateName,
  redoLast,
  status,
  undoLast,
  undoRev,
} from "./planner-core";
import { banOps, button, recipeName, renderResult } from "./planner-result";
import { fail, friendly } from "./toast";

import type { Op, Selection } from "./planner-core";

var OBJECTIVES: [string, string][] = [
  ["max_mw", "max MW"],
  ["max_item", "max item"],
  ["min_raw", "min raw"],
  ["min_machines", "min machines"],
  ["min_power", "min power"],
];

var CLOCKS = [1, 1.5, 2, 2.5];

export function go(dash: string): void {
  location.hash = hashFor(dash);
}

export function back(parent: HTMLElement, text: string): void {
  var a = make("a", "dash-back", text);
  a.setAttribute("href", hashFor("planner"));
  parent.appendChild(a);
}

function input(ctl: string, value: string, type: string, commit: (raw: string) => void, placeholder?: string): HTMLInputElement {
  var box = make("input", type === "number" ? "dash-number" : "dash-name plan-text");
  box.type = type;
  box.value = value;
  box.defaultValue = value;
  box.setAttribute("data-ctl", ctl);
  if (placeholder) box.placeholder = placeholder;
  if (type === "number") box.step = "any";
  box.onchange = function () {
    box.defaultValue = box.value;
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
  bench.chips.forEach(function (chip) {
    if (field.indexOf(chip.field) < 0) return;
    chipLine(row, chip.id, chip.text);
  });
  parent.appendChild(row);
  return body;
}

function chipLine(parent: HTMLElement, id: number, text: string): void {
  var line = make("div", "plan-conflict");
  line.appendChild(make("span", "", text));
  line.appendChild(
    button("keep theirs", "leave the plan as it is now", function () {
      dropChip(id, false);
    })
  );
  line.appendChild(
    button("use mine", "push your change again on top of the new version", function () {
      dropChip(id, true);
    })
  );
  parent.appendChild(line);
}

function chips(parent: HTMLElement, field: string, members: unknown[], words: (m: unknown) => string): void {
  members.forEach(function (m) {
    var chip = make("span", "plan-chip", words(m));
    var x = make("button", "plan-chip-x", "×");
    x.type = "button";
    x.title = "remove";
    x.onclick = function () {
      gesture([{ op: "remove", field: field, member: m }]);
    };
    chip.appendChild(x);
    parent.appendChild(chip);
  });
}

function adder(parent: HTMLElement, ctl: string, placeholder: string, ops: (text: string) => Op[]): void {
  var box = input(
    ctl,
    "",
    "text",
    function (raw) {
      if (raw) gesture(ops(raw));
    },
    placeholder
  );
  box.className = "dash-name plan-add";
  parent.appendChild(box);
}

function goalItem(): string {
  var args = bench.plan!.args;
  var keys = Object.keys(args.export_minimums);
  if (keys.length) return keys[0]!;
  var named = args.exports.filter(function (e) {
    return e !== "MW";
  });
  return named[0] || "";
}

function itemOps(from: string, to: string, rate: number | null): Op[] {
  var args = bench.plan!.args;
  var ops: Op[] = [];
  if (from) {
    if (args.exports.indexOf(from) >= 0) ops.push({ op: "remove", field: "exports", member: from });
    if (from in args.export_minimums) ops.push({ op: "del", field: "export_minimums", item: from });
  }
  if (args.exports.indexOf(to) < 0) ops.push({ op: "add", field: "exports", member: to });
  if (rate !== null) ops.push({ op: "put", field: "export_minimums", item: to, value: rate });
  return ops;
}

function number(raw: string): number | null {
  if (raw === "") return null;
  var n = Number(raw);
  return isFinite(n) ? n : NaN;
}

function goal(parent: HTMLElement): void {
  var args = bench.plan!.args;
  var body = section(parent, ["objective", "export_minimums", "target_item"], "goal");
  var pick = make("select", "dash-select");
  pick.setAttribute("data-ctl", "objective");
  OBJECTIVES.forEach(function (o) {
    var option = make("option", "", o[1]);
    option.value = o[0];
    pick.appendChild(option);
  });
  pick.value = args.objective;
  pick.onchange = function () {
    gesture([{ op: "set", field: "objective", value: pick.value }]);
  };
  body.appendChild(pick);
  var item = goalItem();
  var rate = item && item in args.export_minimums ? args.export_minimums[item]! : null;
  body.appendChild(
    input(
      "goal-item",
      item,
      "text",
      function (raw) {
        if (!raw || raw === item) return;
        gesture(itemOps(item, raw, rate));
      },
      "item, e.g. Heavy Modular Frame"
    )
  );
  body.appendChild(
    input("goal-rate", rate === null ? "" : String(rate), "number", function (raw) {
      var n = number(raw);
      if (!item) {
        fail("name the item first");
        changed();
      } else if (n === null) {
        if (rate !== null) gesture([{ op: "del", field: "export_minimums", item: item }]);
      } else if (!(n > 0)) {
        fail("a rate is a positive number per minute");
        changed();
      } else {
        gesture([{ op: "put", field: "export_minimums", item: item, value: n }]);
      }
    })
  );
  body.appendChild(make("span", "dash-muted", "/min"));
  if (args.objective === "max_item") {
    body.appendChild(
      input(
        "target-item",
        args.target_item || "",
        "text",
        function (raw) {
          gesture([{ op: "set", field: "target_item", value: raw || null }]);
        },
        "item to maximise"
      )
    );
  }
}

function lists(parent: HTMLElement): void {
  var args = bench.plan!.args;
  var ex = section(parent, ["exports"], "exports");
  if (!args.exports.length) ex.appendChild(make("span", "dash-muted", "MW (the default)"));
  chips(ex, "exports", args.exports, String);
  if (args.exports.length && args.exports.indexOf("MW") < 0) {
    ex.appendChild(
      button("+MW", "export power too; the plan then may not draw from the grid", function () {
        gesture([{ op: "add", field: "exports", member: "MW" }]);
      })
    );
  }
  adder(ex, "exports-add", "+ export", function (t) {
    return [{ op: "add", field: "exports", member: t }];
  });

  var src = section(parent, ["sources"], "from");
  if (!args.sources.length) src.appendChild(make("span", "dash-muted", "the whole map"));
  chips(src, "sources", args.sources, String);
  adder(src, "sources-add", "+ selector, e.g. region:Grass Fields", function (t) {
    return [{ op: "add", field: "sources", member: t }];
  });

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

function supply(parent: HTMLElement): void {
  var args = bench.plan!.args;
  var body = section(parent, ["water_extractors", "sloops", "extractor_clocks"], "supply");
  body.appendChild(make("span", "plan-sub", "water extractors"));
  body.appendChild(
    input(
      "water",
      args.water_extractors === null ? "" : String(args.water_extractors),
      "number",
      function (raw) {
        var n = number(raw);
        if (n !== null && !(Number.isInteger(n) && n >= 0)) {
          fail("water extractors is a whole number, or blank for auto");
          changed();
          return;
        }
        gesture([{ op: "set", field: "water_extractors", value: n }]);
      },
      "auto"
    )
  );
  body.appendChild(make("span", "plan-sub", "sloops"));
  body.appendChild(
    input("sloops", String(args.sloops), "number", function (raw) {
      var n = number(raw);
      if (n === null) n = 0;
      if (!(Number.isInteger(n) && n >= 0)) {
        fail("sloops is a whole number");
        changed();
        return;
      }
      gesture([{ op: "set", field: "sloops", value: n }]);
    })
  );
  body.appendChild(make("span", "plan-sub", "extractor clocks"));
  CLOCKS.forEach(function (c) {
    var on = args.extractor_clocks.some(function (x) {
      return x === c;
    });
    body.appendChild(
      button(
        c * 100 + "%",
        on ? "stop offering this extractor clock" : "offer extractors at this clock (needs power shards above 100%)",
        function () {
          gesture([{ op: on ? "remove" : "add", field: "extractor_clocks", member: c }]);
        },
        on ? "on" : ""
      )
    );
  });
}

function stripRows(parent: HTMLElement): void {
  if (!bench.strip.length) return;
  var box = make("div", "dash-card plan-strip");
  var title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", "changed while you had this open"));
  title.appendChild(
    button("dismiss", "clear this list", function () {
      dismissStrip(null);
    })
  );
  box.appendChild(title);
  bench.strip.forEach(function (row) {
    var line = make("div", "dash-issue");
    if (row.chat) line.appendChild(make("span", "plan-tag", "chat"));
    line.appendChild(make("span", "dash-what" + (row.undone ? " dash-muted" : ""), row.text + (row.undone ? " (undone)" : "")));
    if (!row.undone) {
      line.appendChild(
        button("undo", "undo v" + row.rev + " as a new version", function () {
          undoRev(row.rev);
        })
      );
    }
    box.appendChild(line);
  });
  parent.appendChild(box);
}

function summaryOf(args: Record<string, unknown> | null): string {
  if (!args) return "";
  return Object.keys(args)
    .map(function (k) {
      var v = args[k];
      return k + " " + (typeof v === "object" ? JSON.stringify(v) : String(v));
    })
    .join(" · ");
}

function chatName(args: Record<string, unknown> | null, fallback: string): string {
  var mins = args && (args.export_minimums as Record<string, number> | undefined);
  var items = mins ? Object.keys(mins) : [];
  if (items.length) return rateName(items[0]!, mins![items[0]!]!);
  return fallback.slice(0, 40) || "chat solve";
}

export function renderCard(parent: HTMLElement): void {
  var entry = inbox.card;
  if (!entry) return;
  var card = make("section", "dash-card plan-strip");
  var title = make("div", "dash-title");
  title.appendChild(make("span", "plan-tag", "chat"));
  title.appendChild(make("h2", "dash-h", entry.actor.display + " solved, nothing saved"));
  card.appendChild(title);
  card.appendChild(make("p", "", entry.text));
  var args = entry.args;
  if (args) card.appendChild(make("p", "dash-note", summaryOf(args)));
  var acts = make("div", "dash-acts");
  if (bench.plan && args && !bench.gone) {
    var from = entry.plan === bench.key && typeof entry.rev === "number" ? entry.rev : undefined;
    if (from === undefined) {
      card.appendChild(make("p", "dash-note", "chat did not solve from this plan: applying replaces its whole request"));
    }
    acts.appendChild(
      button(
        "apply to this plan",
        from === undefined
          ? "replace this plan's arguments with chat's request, as one new version"
          : "apply what chat changed from v" + from + ", merged with edits made since",
        function () {
          inbox.card = null;
          applyArgs(args!, entry!.id, from);
        }
      )
    );
  }
  if (args) {
    acts.appendChild(
      button("new plan from it", "save chat's request as a new plan and open it", function () {
        inbox.card = null;
        changed();
        createPlan(chatName(args, entry!.text), args!, entry!.id)
          .then(function (key) {
            go("planner/" + key);
          })
          .catch(function (error) {
            fail(friendly(error));
          });
      })
    );
  }
  acts.appendChild(
    button("dismiss", "forget this card", function () {
      inbox.card = null;
      changed();
    })
  );
  card.appendChild(acts);
  parent.appendChild(card);
}

function header(parent: HTMLElement): void {
  var plan = bench.plan!;
  var head = make("div", "dash-title");
  head.appendChild(make("h1", "", "plan “" + plan.name + "”"));
  var st = status();
  head.appendChild(make("span", "plan-status" + (st === "conflict" ? " bad" : ""), "v" + plan.rev + " · " + st));
  if (bench.last) head.appendChild(make("span", "dash-where", "last: " + bench.last.who + " " + age(bench.last.ts) + " ago"));
  var call = 'plan_factory(plan="' + plan.name + '")  # base_rev=' + plan.rev;
  var copy = make("button", "dash-map " + COPY_CLASS, "copy as tool call");
  copy.type = "button";
  copy.title = call;
  copy.setAttribute(COPY_ATTR, call);
  head.appendChild(copy);
  head.appendChild(button("undo", "undo your last change on this plan (Ctrl+Z)", undoLast));
  head.appendChild(button("redo", "undo that undo (Ctrl+Shift+Z)", redoLast));
  parent.appendChild(head);
}

export function renderBench(root: HTMLElement, select: (s: Selection) => void): void {
  back(root, "‹ all plans");
  if (bench.error) {
    root.appendChild(make("p", "dash-note", "this plan could not be read: " + bench.error));
    return;
  }
  if (!bench.plan) {
    root.appendChild(make("p", "dash-note", "loading…"));
    return;
  }
  header(root);
  if (bench.gone) {
    root.appendChild(make("p", "plan-warning", "this plan was forgotten; its history is kept, and plan_log can restore it"));
  }
  renderCard(root);
  stripRows(root);
  bench.chips.forEach(function (chip) {
    if (["objective", "export_minimums", "target_item", "exports", "sources", "required", "banned", "water_extractors", "sloops", "extractor_clocks"].indexOf(chip.field) < 0) {
      chipLine(root, chip.id, chip.text);
    }
  });
  var controls = make("section", "dash-card plan-bench");
  goal(controls);
  lists(controls);
  supply(controls);
  root.appendChild(controls);
  renderResult(root, select);
}
