/* The Power lists: problems, circuits and a circuit's factories, with their ◎ map buttons and
 * row links wired by delegation on the page root. */

import { cls, html, type Safe } from "../../kit/html";
import { acts, entry, identity, list, nameLine } from "../../kit/ui/entry";
import { chip } from "../../kit/ui/marks";
import { icon } from "../../kit/ui/text";
import { btn } from "../../kit/ui/controls";
import { counted, WORDS } from "../../kit/words";
import { mw, pct } from "../../kit/format";
import { hashFor } from "../../map/map";
import { showCircuit, showFactory } from "../../map/panel";
import { showMachine } from "../../map/map-highlight";
import { leaveDashThen } from "../actions";
import { factoryDash } from "../factories/address";
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

function noMap(): Safe {
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
      html`${identity("circuit " + (row.index + 1), ctx, href)}${utilBar(row, scale, unrated(rated))}${headroomCell(now.value, now.bad, "now", "now", now.why || WORDS.headroomNow)}${headroomCell(full.value, full.bad, "sm", WORDS.ifAllRun, full.why || "headroom " + WORDS.ifAllRun + ": " + WORDS.ifAllRunTip)}${acts(circuitMap(row))}`
    );
  });
  return list("pcirc", "stacked", entries, "nolead");
}

function whereLine(p: Problem): Safe {
  const place = p.where.dash ? html`<a href="${hashFor(p.where.dash)}">${p.where.where}</a>` : p.where.where;
  return html`${p.cause}${p.cause && p.where.where ? " · " : ""}${place}`;
}

function problemMap(p: Problem, key: string): Safe {
  const at = p.refs.find((r) => r.x_m !== null && r.y_m !== null);
  if (!at) return noMap();
  return mapBtn(key, p.building + " (" + p.kind + ")", () => showMachine(at.instance, p.building, at.x_m!, at.y_m!, { layers: ["machines"] }));
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

/** A circuit's named factories, as the factory health read has them. */
export function factoryList(names: string[], health: FactoryHealthRow[]): Safe {
  const entries = names.map(function (name) {
    const row = health.find((r) => r.name === name);
    const href = hashFor(factoryDash(name, ""));
    const link = html`<a class="name" href="${href}" title="${name}">${name}</a>`;
    const ctx = row ? mw(row.measured_mw) + " " + WORDS.drawingNow : "";
    const metric = !row
      ? html`<div class="metric none"><b>–</b></div>`
      : row.actionable
        ? html`<div class="metric bad" title="${WORDS.needAction}"><b>${row.actionable}</b><span>${WORDS.needAction}</span></div>`
        : html`<div class="metric"><b>${pct(row.uptime)}</b><span>up</span></div>`;
    const map = row && row.bbox_m ? mapBtn("factory:" + name, name, () => showFactory(name)) : noMap();
    return entry({ tone: row && row.actionable ? "bad" : "", href }, html`<div class="id">${nameLine(link, ctx)}</div>${metric}${acts(map)}`);
  });
  return list("ofac", "stacked", entries, "nolead");
}
