/* The alternates drawer: every recipe for one item, what requiring it would change, and
 * require / ban / let the solver choose as one version each. See docs/planner-p3_contract.md F2. */

import { button, chip, empty, error, loading, table } from "./dashkit";
import { make } from "./dom";
import { count, mw, perMin, signed } from "./format";
import { bench, gesture, loadAlternates, pushing } from "./planner-core";
import { counted, W } from "./words";

import type { Column } from "./dashkit";
import type { DeltaRow, PlanOpBody, SwapOption } from "./api-shapes";
import type { Op } from "./planner-core";

var NONE = "–";

function ops(list: PlanOpBody[]): Op[] {
  return list.filter(function (o): o is Op {
    return typeof o.op === "string";
  });
}

function shown(o: SwapOption): boolean {
  return o.solved && !!o.delta && o.delta.comparable;
}

function machines(o: SwapOption): string {
  if (!o.solved || !o.delta) return NONE;
  if (!o.delta.comparable) return o.delta.text;
  return signed(o.delta.machines, count);
}

function power(o: SwapOption, field: "mw_draw" | "mw_net"): string {
  if (!shown(o)) return NONE;
  return mw(o.delta![field], { signed: true });
}

function raw(o: SwapOption): string | HTMLElement {
  if (!shown(o)) return NONE;
  var moved = o.delta!.inputs.filter(function (r: DeltaRow) {
    return Math.abs(r.delta) >= 0.05;
  });
  if (!moved.length) return "0";
  var cell = make("span", "");
  cell.title = moved
    .map(function (r: DeltaRow) {
      return signed(r.delta, perMin) + " " + r.name;
    })
    .join("\n");
  moved.slice(0, 2).forEach(function (r: DeltaRow) {
    cell.appendChild(make("span", "plan-raw", signed(r.delta, perMin) + " " + r.name));
  });
  if (moved.length > 2) cell.appendChild(make("span", "plan-raw dash-sub", counted(moved.length - 2, "more input", "more inputs")));
  return cell;
}

function recipeCell(o: SwapOption): HTMLElement {
  var cell = make("span", "", o.name);
  if (o.status === "locked" && o.granted_by.length) cell.appendChild(make("span", "dash-sub", "granted by " + o.granted_by.join(", ")));
  return cell;
}

function settled(): boolean {
  var alt = bench.alt;
  return !!alt && !!alt.data && !alt.asked && !pushing() && !!bench.plan && alt.data.rev === bench.plan.rev;
}

function act(list: Op[]): void {
  if (settled()) gesture(list);
}

function acts(o: SwapOption): HTMLElement {
  var box = make("span", "dash-acts");
  if (o.status === "locked") return box;
  if (o.banned_by && !o.solved) {
    box.appendChild(make("span", "dash-sub", "banned by “" + o.banned_by + "”: edit the banned list to change it"));
    return box;
  }
  var require = ops(o.require_ops);
  var ban = ops(o.ban_ops);
  var free = ops(o.free_ops);
  if (!o.required && require.length) {
    box.appendChild(
      button(
        "require",
        function () {
          act(require);
        },
        { title: "make every " + (bench.alt && bench.alt.data ? bench.alt.data.name : "unit") + " in this plan with " + o.name, label: "require " + o.name }
      )
    );
  }
  if (!o.banned && ban.length) {
    box.appendChild(
      button(
        "ban",
        function () {
          act(ban);
        },
        { title: "keep " + o.name + " out of this plan", label: "ban " + o.name }
      )
    );
  }
  if (free.length) {
    box.appendChild(
      button(
        W.letSolverChoose,
        function () {
          act(free);
        },
        { title: "drop " + o.name + " from the required and banned lists", label: W.letSolverChoose + " for " + o.name }
      )
    );
  }
  var live = settled();
  box.querySelectorAll("button").forEach(function (b) {
    if (!live) b.setAttribute("aria-disabled", "true");
    b.setAttribute("data-ctl", "alt:" + o.recipe_id);
  });
  return box;
}

function optionTable(options: SwapOption[]): HTMLElement {
  var columns: Column<SwapOption>[] = [
    { key: "recipe", label: "recipe", render: recipeCell },
    {
      key: "status",
      label: "status",
      render: function (o) {
        return chip(o.status);
      },
    },
    {
      key: "building",
      label: "building",
      render: function (o) {
        return o.machine || NONE;
      },
    },
    {
      key: "machines",
      label: "Δ machines",
      align: "right",
      title: "machines in the whole plan with this recipe required, against now",
      render: machines,
    },
    {
      key: "draw",
      label: "Δ MW draw",
      align: "right",
      render: function (o) {
        return power(o, "mw_draw");
      },
    },
    {
      key: "net",
      label: "Δ MW net",
      align: "right",
      render: function (o) {
        return power(o, "mw_net");
      },
    },
    {
      key: "raw",
      label: "Δ raw",
      align: "right",
      title: "change in raw inputs per minute, the two largest first",
      render: raw,
    },
    { key: "acts", label: "", render: acts },
  ];
  return table(columns, options, {
    rowClass: function (o) {
      return o.status === "locked" ? "plan-locked" : "";
    },
    caption: "recipes and what requiring each would change",
  });
}

export function renderAlternates(parent: HTMLElement, close: () => void): void {
  var alt = bench.alt;
  if (!alt) return;
  var data = alt.data;
  var name = data ? data.name : alt.item;
  var drawer = make("aside", "dash-card plan-drawer");
  drawer.setAttribute("aria-label", "recipes for " + name);
  var head = make("div", "dash-title");
  head.appendChild(make("h2", "dash-h", "recipes for " + name + (data ? " · v" + data.rev : "")));
  if (alt.asked) head.appendChild(make("span", "plan-status", "solving v" + alt.asked + "…"));
  var shut = button("×", close, { title: "close the recipes (Escape)", label: "close the recipes for " + name });
  shut.setAttribute("data-ctl", "alt-close");
  head.appendChild(shut);
  drawer.appendChild(head);
  if (alt.error) {
    error(drawer, "the recipes", alt.error, loadAlternates);
  } else if (!data) {
    loading(drawer, "the recipes for " + name);
  } else {
    var body = make("div", settled() ? "" : "plan-stale");
    if (!data.head_feasible) body.appendChild(make("p", "plan-warning", "v" + data.rev + " is not solvable"));
    if (!data.options.length) empty(body, "no recipe makes " + name);
    else body.appendChild(optionTable(data.options));
    if (data.hidden) body.appendChild(make("p", "dash-note", W.lockedHidden(data.hidden) + " (Settings, spoilers)"));
    drawer.appendChild(body);
  }
  parent.appendChild(drawer);
}
