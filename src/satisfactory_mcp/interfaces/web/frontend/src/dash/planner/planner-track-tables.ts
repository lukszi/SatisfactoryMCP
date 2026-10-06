/* Track's tables: the jobs of the plan, the items it is short of and what stands on site, with
 * the row helpers the stages table shares. See docs/planner-p4_contract.md §2 F2–F4. */

import { askButton, openAsksAbout } from "../../chat/asks";
import { copyText } from "../../kit/copy";
import { button, chip, empty, idChip, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count, countRange, formatNumber, signed } from "../../kit/format";
import { recipesButton } from "./planner-alternates";
import { boxMapButton, builtColumn } from "./planner-built";
import { pickStage } from "./planner-reads";
import { bench, changed } from "./planner-state";
import { tone } from "../machine-states";
import { fail, notify } from "../../kit/toast";
import { counted, TRACK_VERB, WORDS } from "../../kit/words";

import type { Column } from "../../kit/dashkit";
import type { AskAbout, FocusSelection, TrackCost, TrackResponse, TrackRow, TrackSiteRow, TrackState } from "../../api/shapes";

export var TRACK_TABLE_NARROW = window.matchMedia("(max-width: 899px)");

var refocusCtl = "";

TRACK_TABLE_NARROW.addEventListener("change", function () {
  if (bench.tab === "track") changed();
});

export function stageCtl(n: number): string {
  return "track-stage-" + n;
}

/** The stage row the jobs card's × asked to focus after the redraw, once. */
export function takeRefocusCtl(): string {
  const ctl = refocusCtl;
  refocusCtl = "";
  return ctl;
}

export function askAbout(kind: string, label: string, ref: string): AskAbout {
  const plan = bench.plan!;
  return { kind: kind, label: label, ref: ref, plan: bench.key, rev: plan.rev };
}

export function appendAskMarks(cell: HTMLElement, kind: string, ref: string): void {
  openAsksAbout(bench.key, kind, ref).forEach(function (ask) {
    cell.appendChild(idChip(ask.id, ask.text));
  });
}

/** A chip per machine state that is not running fine. */
export function stateChips(parent: HTMLElement, states: TrackState[]): void {
  states.forEach(function (machineState) {
    if (!machineState.count) return;
    const stateTone = tone(machineState.state);
    if (stateTone === "ok") return;
    parent.appendChild(chip(count(machineState.count) + " " + machineState.state, stateTone));
  });
}

function copyIds(row: TrackRow): HTMLButtonElement | null {
  const ids = row.selectors
    ? row.selectors.split(",").filter(function (selector) {
        return selector.trim() !== "";
      })
    : [];
  if (!ids.length) return null;
  return button(
    "copy ids",
    function () {
      copyText(ids.join(",")).then(
        function () {
          notify("copied " + counted(ids.length, "id"));
        },
        function () {
          fail("could not copy the ids: the browser refused");
        }
      );
    },
    { title: "copy the " + counted(ids.length, "selector") + " of this job for a tool call", label: "copy ids of " + row.process }
  );
}

function jobActionText(row: TrackRow): string {
  const build = TRACK_VERB.build + " " + countRange(row.build, row.build_max);
  const then = row.build > 0 || (row.build_max || 0) > 0 ? ", then " + build : "";
  if (row.verb === "unpause") return TRACK_VERB.unpause + " " + count(row.count) + then;
  if (row.verb === "setrecipe") return TRACK_VERB.setrecipe + " " + count(row.count) + then;
  if (row.verb === "build") return build;
  return TRACK_VERB.ok || "–";
}

function stagesWord(stages: number[]): string {
  if (!stages.length) return "–";
  return (stages.length === 1 ? WORDS.stageUnit + " " : WORDS.stages + " ") + stages.join(", ");
}

function nextLine(d: TrackResponse): string {
  const parts: string[] = [];
  if (d.unpause) parts.push(TRACK_VERB.unpause + " " + count(d.unpause));
  if (d.setrecipe) parts.push(TRACK_VERB.setrecipe + " " + count(d.setrecipe));
  if (d.to_build || d.to_build_max) parts.push(TRACK_VERB.build + " " + countRange(d.to_build, d.to_build_max));
  return parts.length ? parts.join(" · ") : "nothing to do: every job of the plan stands";
}

function jobActions(row: TrackRow): HTMLElement {
  const acts = make("span", "dash-acts");
  const nodeNames = row.targets.map(function (target) {
    return target.node;
  });
  const there = boxMapButton(row.bbox_m, row.process, nodeNames);
  if (there) acts.appendChild(there);
  const ids = copyIds(row);
  if (ids) acts.appendChild(ids);
  if (!bench.gone) {
    if (row.kind === "recipe" && row.item && row.recipe_id) acts.appendChild(recipesButton(row.item, row.process, "track"));
    acts.appendChild(askButton(askAbout("process", row.process, row.id), "job:" + row.id));
  }
  return acts;
}

function processCell(row: TrackRow): HTMLElement {
  const cell = make("span", "plan-recipe", row.process);
  if (row.new_building) cell.appendChild(chip("new building", "muted", "this building is not unlocked or not yet on the ground anywhere"));
  appendAskMarks(cell, "process", row.id);
  return cell;
}

function stageFilter(parent: HTMLElement, n: number, total: number): void {
  const line = make("div", "plan-line");
  const filter = make("span", "plan-chip", "only " + WORDS.stage(n, total));
  const x = make("button", "plan-chip-x", "×");
  x.type = "button";
  x.title = "show the jobs of every stage";
  x.setAttribute("aria-label", "show the jobs of every stage");
  x.onclick = function () {
    refocusCtl = stageCtl(n);
    pickStage(n);
  };
  filter.appendChild(x);
  line.appendChild(filter);
  parent.appendChild(line);
}

function caveats(card: HTMLElement, d: TrackResponse): void {
  if (!d.caveats.length) return;
  const more = make("details", "track-caveats");
  more.appendChild(make("summary", "dash-sub", "about built and running"));
  d.caveats.forEach(function (caveat) {
    more.appendChild(make("p", "dash-note", caveat));
  });
  card.appendChild(more);
}

/** The jobs table's columns, in the order the width allows; `where` only when a job is staged. */
function jobColumns(d: TrackResponse, rows: TrackRow[]): Column<TrackRow>[] {
  const lead: Column<TrackRow> = { key: "process", label: "process", render: processCell };
  const built: Column<TrackRow> = {
    key: "built",
    label: builtColumn(d.built_at),
    align: "right",
    className: "dash-nowrap",
    title: "matching machines in the save; a range where the save cannot tell them apart",
    render: function (r) {
      return r.have_min === null ? count(r.have) : countRange(r.have_min, r.have);
    },
  };
  const action: Column<TrackRow> = { key: "action", label: "action", className: "dash-nowrap", render: jobActionText };
  const building: Column<TrackRow> = {
    key: "building",
    label: "building",
    render: function (r) {
      return r.building;
    },
  };
  const need: Column<TrackRow> = {
    key: "need",
    label: "need",
    align: "right",
    render: function (r) {
      return count(r.need);
    },
  };
  const running: Column<TrackRow> = {
    key: "running",
    label: "running",
    align: "right",
    title: "matched machines a productivity monitor proves running; – where none is monitored",
    render: function (r) {
      return r.running === null ? "–" : count(r.running);
    },
  };
  const where: Column<TrackRow> = {
    key: "where",
    label: "where",
    className: "dash-nowrap",
    title: "the startup stages this job is switched on in",
    render: function (r) {
      return stagesWord(r.stages);
    },
  };
  const noteCol: Column<TrackRow> = {
    key: "note",
    label: "note",
    className: "dash-sub track-note",
    render: function (r) {
      return r.note || "";
    },
  };
  const acts: Column<TrackRow> = { key: "acts", label: "", render: jobActions };
  const columns = TRACK_TABLE_NARROW.matches
    ? [lead, built, action, building, need, running, where, noteCol, acts]
    : [lead, building, need, built, running, action, where, noteCol, acts];
  const staged = rows.some(function (r) {
    return r.stages.length > 0;
  });
  if (staged) return columns;
  return columns.filter(function (column) {
    return column !== where;
  });
}

export function jobsCard(parent: HTMLElement, d: TrackResponse, select: (selection: FocusSelection) => void): void {
  const card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "jobs · " + counted(d.rows.length, "job")));
  const next = make("p", "plan-facts");
  next.appendChild(make("b", "", "next: "));
  next.appendChild(document.createTextNode(nextLine(d)));
  card.appendChild(next);
  const stage = bench.track.stage;
  if (stage) stageFilter(card, stage, d.count);
  const rows = stage
    ? d.rows.filter(function (r) {
        return r.stages.indexOf(stage) >= 0;
      })
    : d.rows;
  if (!rows.length) {
    empty(card, stage ? "no jobs in stage " + stage : "the plan has no jobs");
  } else {
    card.appendChild(
      table(jobColumns(d, rows), rows, {
        onRow: function (r) {
          bench.picked = r.id;
          select({ kind: "process", label: r.process, ref: r.id });
        },
        rowClass: function (r) {
          return bench.picked === r.id ? "plan-picked" : "";
        },
        caption: "jobs",
      })
    );
  }
  caveats(card, d);
  if (d.neighbours.length) {
    card.appendChild(
      make(
        "p",
        "dash-note",
        "nearby, not in the plan: " +
          d.neighbours
            .map(function (neighbour) {
              return neighbour.label + " ×" + count(neighbour.count);
            })
            .join(", ")
      )
    );
  }
  parent.appendChild(card);
}

export function shortItemsCard(parent: HTMLElement, cost: TrackCost[]): void {
  if (!cost.length) return;
  const card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "short · " + counted(cost.length, "item")));
  const columns: Column<TrackCost>[] = [
    {
      key: "item",
      label: "item",
      render: function (c) {
        const cell = make("span", "", c.name);
        appendAskMarks(cell, "item", c.item);
        return cell;
      },
    },
    {
      key: "need",
      label: "need",
      align: "right",
      render: function (c) {
        return formatNumber(c.need, 0);
      },
    },
    {
      key: "stock",
      label: "stock",
      align: "right",
      render: function (c) {
        return formatNumber(c.stock, 0);
      },
    },
    {
      key: "lines",
      label: "lines",
      align: "right",
      title: "machines in the save whose recipe makes this item",
      render: function (c) {
        return count(c.lines);
      },
    },
  ];
  if (!bench.gone) {
    columns.push({
      key: "acts",
      label: "",
      render: function (c) {
        return askButton(askAbout("item", c.name, c.item), "item:" + c.item);
      },
    });
  }
  card.appendChild(table(columns, cost, { caption: "short items" }));
  parent.appendChild(card);
}

export function onSiteCard(parent: HTMLElement, d: TrackResponse): void {
  const site = d.site;
  if (!site) return;
  const card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "on site"));
  if (site.text) card.appendChild(make("p", "dash-note", site.text));
  const columns: Column<TrackSiteRow>[] = [
    {
      key: "name",
      label: "building",
      render: function (r) {
        return r.name;
      },
    },
    {
      key: "planned",
      label: "planned",
      align: "right",
      render: function (r) {
        return count(r.planned);
      },
    },
    {
      key: "standing",
      label: "on site",
      align: "right",
      render: function (r) {
        return count(r.standing);
      },
    },
    {
      key: "delta",
      label: "Δ",
      align: "right",
      title: "on site minus planned",
      render: function (r) {
        return signed(r.standing - r.planned, count);
      },
    },
  ];
  card.appendChild(table(columns, site.rows, { caption: "on site" }));
  parent.appendChild(card);
}
