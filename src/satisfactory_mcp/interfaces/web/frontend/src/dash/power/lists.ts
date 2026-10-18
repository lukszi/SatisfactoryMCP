/* The Power lists: problems and circuits, with their ◎ map buttons (the circuit page shares
 * them) and row links wired by delegation on the page root. */

import { cls, html, type Safe } from "../../kit/html";
import { acts, entry, identity, list } from "../../kit/ui/entry";
import { chip } from "../../kit/ui/marks";
import { icon } from "../../kit/ui/text";
import { btn } from "../../kit/ui/controls";
import { counted, WORDS } from "../../kit/words";
import { hashFor } from "../../map/map";
import { showCircuit, showFactory } from "../../map/panel";
import { showMachine } from "../../map/map-highlight";
import { leaveDashThen } from "../actions";
import { circuitDark, factoriesOn, ratedCircuit, readHeadroomFull, readHeadroomNow, unrated } from "./readings";
import { utilBar } from "./supply";
import { utilScale, worstFirst, type Problem } from "./model";

import type { CircuitRow, FactoryHealthRow } from "../../api/shapes";

/* The map buttons of the lists drawn last, by key; redrawn with them. */
const mapActs = new Map<string, () => void>();

function mapBtn(key: string, label: string, action: () => void): Safe {
  mapActs.set(key, action);
  return btn("◎", { extra: "icon", title: "show " + label + " on the map", data: { map: key } });
}

export function noMap(): Safe {
  return html`<span class="btn-gap" aria-hidden="true"></span>`;
}

/** Row clicks follow the row's link; ◎ leaves for the map. */
export function wirePowerLists(root: HTMLElement): void {
  root.addEventListener("click", function (event) {
    const target = event.target as Element;
    const button = target.closest<HTMLElement>("[data-map]");
    if (button && root.contains(button)) {
      const action = mapActs.get(button.dataset.map || "");
      if (action) leaveDashThen(action);
      return;
    }
    if (target.closest("a, button, input, select, label")) return;
    const row = target.closest<HTMLElement>(".entry[data-href]");
    if (row && root.contains(row)) location.hash = row.dataset.href!;
  });
}

export function circuitChips(row: CircuitRow): Safe {
  const starved = row.starved.length;
  return html`${circuitDark(row) && chip(WORDS.noGenerator, "bad")}${starved > 0 && chip(counted(starved, WORDS.starvedGenerator), "bad")}${unrated(ratedCircuit(row)) && chip("output unknown", "muted", "game data cannot rate " + row.unmodellable.join(", "))}`;
}

export function circuitMap(row: CircuitRow): Safe {
  return row.bbox_m ? mapBtn("circuit:" + row.index, "circuit " + (row.index + 1), () => showCircuit(row.index)) : noMap();
}

function headroomCell(value: string, bad: boolean, kind: "now" | "sm", unit: string, title: string): Safe {
  const none = value === "–";
  return html`<div class="${cls("metric", kind, none && "none", bad && "bad")}" title="${title}"><b>${kind === "sm" ? value.replace(/ MW$/, "") : value}</b><span>${unit}</span></div>`;
}

export function circuitList(rows: CircuitRow[]): Safe {
  const scale = utilScale(rows);
  const entries = worstFirst(rows).map(function (row) {
    const rated = ratedCircuit(row);
    const now = readHeadroomNow(rated);
    const full = readHeadroomFull(rated);
    const href = hashFor("power/" + (row.index + 1));
    const tone = circuitDark(row) || now.bad || row.starved.length ? "bad" : "";
    const ctx = html`${circuitChips(row)} ${factoriesOn(row)}`;
    return entry(
      { tone, href },
      html`${identity("circuit " + (row.index + 1), ctx, href)}${utilBar(row, scale, unrated(rated))}${headroomCell(now.value, now.bad, "now", "now", now.why || WORDS.headroomNow)}${headroomCell(full.value, full.bad, "sm", "at maximum", full.why || WORDS.headroomFull + ": " + WORDS.maxConsumptionTip)}${acts(circuitMap(row))}`
    );
  });
  return list("pcirc", "stacked", entries, "nolead");
}

function whereLine(p: Problem): Safe {
  const place = p.where.dash ? html`<a href="${hashFor(p.where.dash)}">${p.where.where}</a>` : p.where.where;
  return html`${p.cause}${p.cause && p.where.where ? " · " : ""}${place}`;
}

export function problemList(problems: Problem[]): Safe {
  const entries = problems.map(function (p, i) {
    const n = p.refs.length;
    const unit = p.generator ? (n === 1 ? "generator" : "generators") : n === 1 ? "machine" : "machines";
    const title = p.refs.map((r) => r.instance).join(", ");
    return entry(
      { tone: p.fine ? "" : "bad", title },
      html`${icon(p.icon, p.building)}${identity(p.building, whereLine(p))}<div class="chips">${chip(p.kind, p.fine ? "muted" : "bad")}</div><div class="metric"><b>${n}</b><span>${unit}</span></div>${acts(problemMap(p, "problem:" + i))}`
    );
  });
  return list("prob", "stacked", entries);
}

/** ◎ for a named factory, or the empty slot when it has no extent to show. */
export function factoryMap(name: string, health: FactoryHealthRow | undefined): Safe {
  return health && health.bbox_m ? mapBtn("factory:" + name, name, () => showFactory(name)) : noMap();
}

/** ◎ for one problem group: its first placed machine. */
export function problemMap(p: Problem, key: string): Safe {
  const at = p.refs.find((r) => r.x_m !== null && r.y_m !== null);
  if (!at) return noMap();
  return mapBtn(key, p.building + " (" + p.kind + ")", () => showMachine(at.instance, p.building, at.x_m!, at.y_m!, { layers: ["machines"] }));
}
