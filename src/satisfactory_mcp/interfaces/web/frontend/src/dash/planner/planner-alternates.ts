/* The alternates drawer: every recipe for one item, what requiring it would change, and
 * require / ban / let the solver choose as one version each. See docs/planner-p3_contract.md F2. */

import { button, chip, empty, error, loading, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count, mw, perMin, signed } from "../../kit/format";
import { dashParts, go } from "../../app/nav";
import { loadAlternates, showAlternates } from "./planner-reads";
import { bench } from "./planner-state";
import { applyOps, hasPushInFlight } from "./planner-writes";
import { counted, WORDS } from "../../kit/words";

import type { Column } from "../../kit/dashkit";
import type { DeltaRow, PlanOpBody, SwapOption } from "../../api/shapes";
import type { Op } from "./planner-state";

var NONE = "–";

/** The [recipes] button that opens the drawer for `item`; `where` tells its openers apart. */
export function recipesButton(item: string, name: string, where: string): HTMLButtonElement {
  const ctl = "alt:" + where + ":" + item;
  const open = !!bench.alternates && bench.alternates.item === item;
  const opener = button(
    WORDS.recipes,
    function () {
      const switching = dashParts().rest[1] === "alt";
      showAlternates(item, ctl);
      if (!switching) bench.alternatesCloseGoesBack = true;
      go("planner/" + bench.key + "/alt/" + item, switching);
    },
    { title: "every recipe for " + name + " and what requiring each would change", label: "recipes for " + name }
  );
  opener.setAttribute("data-ctl", ctl);
  opener.setAttribute("aria-expanded", String(open));
  return opener;
}

/** An option's ops that carry an op name, typed as the ops a gesture pushes. */
function namedOps(list: PlanOpBody[]): Op[] {
  return list.filter(function (o): o is Op {
    return typeof o.op === "string";
  });
}

function hasComparableDelta(option: SwapOption): boolean {
  return option.solved && !!option.delta && option.delta.comparable;
}

function machinesDelta(option: SwapOption): string {
  if (!option.solved || !option.delta) return NONE;
  if (!option.delta.comparable) return option.delta.text;
  return signed(option.delta.machines, count);
}

function powerDelta(option: SwapOption, field: "mw_draw" | "mw_net"): string {
  if (!hasComparableDelta(option)) return NONE;
  return mw(option.delta![field], { signed: true });
}

/** The two largest raw input changes, with the full list in the title. */
function rawInputDeltaCell(option: SwapOption): string | HTMLElement {
  if (!hasComparableDelta(option)) return NONE;
  const moved = option.delta!.inputs.filter(function (r: DeltaRow) {
    return Math.abs(r.delta) >= 0.05;
  });
  if (!moved.length) return "0";
  const cell = make("span", "");
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

function recipeCell(option: SwapOption): HTMLElement {
  const cell = make("span", "", option.name);
  if (option.status === "locked" && option.granted_by.length) cell.appendChild(make("span", "dash-sub", "granted by " + option.granted_by.join(", ")));
  return cell;
}

/** The drawer has re-solved the current head and no push is on its way. */
function alternatesCurrent(): boolean {
  const drawer = bench.alternates;
  return !!drawer && !!drawer.data && !drawer.asked && !hasPushInFlight() && !!bench.plan && drawer.data.rev === bench.plan.rev;
}

/* A burst of clicks lands one version: an option acts only once the drawer is current again. */
function applyIfCurrent(list: Op[], requires?: boolean): void {
  if (alternatesCurrent()) applyOps(list, requires && bench.alternates && bench.alternates.data ? bench.alternates.data.item : undefined);
}

function optionActions(option: SwapOption): HTMLElement {
  const box = make("span", "dash-acts");
  if (option.status === "locked") return box;
  if (option.banned_by && !option.solved) {
    box.appendChild(make("span", "dash-sub", "banned by “" + option.banned_by + "”: edit the banned list to change it"));
    return box;
  }
  const require = namedOps(option.require_ops);
  const ban = namedOps(option.ban_ops);
  const free = namedOps(option.free_ops);
  if (!option.required && require.length) {
    box.appendChild(
      button(
        "require",
        function () {
          applyIfCurrent(require, true);
        },
        { title: "make every " + (bench.alternates && bench.alternates.data ? bench.alternates.data.name : "unit") + " in this plan with " + option.name, label: "require " + option.name }
      )
    );
  }
  if (!option.banned && ban.length) {
    box.appendChild(
      button(
        "ban",
        function () {
          applyIfCurrent(ban);
        },
        { title: "keep " + option.name + " out of this plan", label: "ban " + option.name }
      )
    );
  }
  if (free.length) {
    box.appendChild(
      button(
        WORDS.letSolverChoose,
        function () {
          applyIfCurrent(free);
        },
        { title: "drop " + option.name + " from the required and banned lists", label: WORDS.letSolverChoose + " for " + option.name }
      )
    );
  }
  const live = alternatesCurrent();
  box.querySelectorAll("button").forEach(function (b) {
    if (!live) b.setAttribute("aria-disabled", "true");
    b.setAttribute("data-ctl", "alt:" + option.recipe_id);
  });
  return box;
}

function optionTable(options: SwapOption[]): HTMLElement {
  const columns: Column<SwapOption>[] = [
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
      render: machinesDelta,
    },
    {
      key: "draw",
      label: "Δ MW draw",
      align: "right",
      render: function (o) {
        return powerDelta(o, "mw_draw");
      },
    },
    {
      key: "net",
      label: "Δ MW net",
      align: "right",
      render: function (o) {
        return powerDelta(o, "mw_net");
      },
    },
    {
      key: "raw",
      label: "Δ raw",
      align: "right",
      title: "change in raw inputs per minute, the two largest first",
      render: rawInputDeltaCell,
    },
    { key: "acts", label: "", render: optionActions },
  ];
  return table(columns, options, {
    rowClass: function (o) {
      return o.status === "locked" ? "plan-locked" : "";
    },
    caption: "recipes and what requiring each would change",
  });
}

export function renderAlternates(parent: HTMLElement, close: () => void): void {
  const drawer = bench.alternates;
  if (!drawer) return;
  const data = drawer.data;
  const name = data ? data.name : "";
  const heading = name ? "recipes for " + name : "recipes";
  const aside = make("aside", "dash-card plan-drawer");
  aside.setAttribute("aria-label", heading);
  const head = make("div", "dash-title");
  head.appendChild(make("h2", "dash-h", heading + (data ? " · v" + data.rev : "")));
  if (drawer.asked) head.appendChild(make("span", "plan-status", "solving v" + drawer.asked + "…"));
  const shut = button("×", close, { title: "close the recipes (Escape)", label: "close the " + heading });
  shut.setAttribute("data-ctl", "alt-close");
  head.appendChild(shut);
  aside.appendChild(head);
  if (drawer.error) {
    error(aside, "the recipes", drawer.error, loadAlternates);
  } else if (!data) {
    loading(aside, "the recipes");
  } else {
    const body = make("div", alternatesCurrent() ? "" : "plan-stale");
    if (!data.head_feasible) body.appendChild(make("p", "plan-warning", "v" + data.rev + " is not solvable"));
    if (!data.options.length) empty(body, "no recipe makes " + name);
    else body.appendChild(optionTable(data.options));
    if (data.hidden) body.appendChild(make("p", "dash-note", WORDS.lockedHidden(data.hidden) + " (Settings, spoilers)"));
    aside.appendChild(body);
  }
  parent.appendChild(aside);
}
