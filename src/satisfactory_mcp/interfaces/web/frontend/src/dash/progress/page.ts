/* The Progress page, `dash=progress[/<section>]`: how to reach the next phase and what to do
 * next. The roadmap band on top, then gate | next steps, alternates | buy now, the reference
 * folded. docs/frontend_kit.md, "Progress"; the server side is docs/progress_roadmap.md. */

import "./progress.css";
import { loadOne } from "../../app/load";
import { settingOn } from "../../app/settings";
import { state } from "../../app/state";
import { html, mount, type Safe } from "../../kit/html";
import { selectBox } from "../../kit/ui/controls";
import { wireExpand } from "../../kit/ui/entry";
import { emptyLine } from "../../kit/ui/text";
import { hashFor } from "../../map/map";
import { requestRender } from "../actions";
import { pending, q, read, wireRetry } from "../factories/data";
import { milestones, mam, phase, visible } from "./feeds";
import { gateSection, nextSection, type SectionParts } from "./gate";
import { alternatesSection, buySection, referenceSection } from "./lists";
import { buyLists, DEFAULT_HORIZON, HORIZONS, phaseNumber, progressSection, roadOf, type AltSort, type Section } from "./model";
import { roadBand } from "./road";

import type { AlternatesView, GateView } from "../../api/shapes";

const TITLES: Record<Section, string> = {
  gate: "phase",
  next: "what to do next",
  alternates: "alternates worth getting",
  buy: "buy now",
  reference: "reference",
};

/* What the page keeps for its session: the horizon, the sort, open rows and folds. */
const view = { horizon: DEFAULT_HORIZON, altSort: "machines" as AltSort, opened: new Set<string>(), closed: new Set<string>(), folds: new Set<string>() };
let scrolledTo = "";
let askedIn = -1;

/* A deep link can land before the live wave reaches the progress reads; ask for them now. */
function askFeeds(): void {
  if (askedIn === state.epoch || !state.world) return;
  askedIn = state.epoch;
  [milestones, mam, phase].forEach((f) => {
    if (!f.data && !f.failed) loadOne(f.path);
  });
}

function section(key: Section, title: string, g: SectionParts): Safe {
  const n = g.n === undefined ? "" : html` <span class="n">· ${g.n}</span>`;
  return html`<section class="${"psec ps-" + key}"><div class="sec-head" id="${"progress-" + key}"><h2${g.tip ? html` title="${g.tip}"` : ""}>${title}${n}</h2>${g.figs ? html`<span class="sec-figs">${g.figs}</span>` : ""}${g.tools ? html`<span class="tools">${g.tools}</span>` : ""}</div>${g.body}</section>`;
}

function band(): Safe {
  const p = phase.data;
  const m = milestones.data;
  if (!p || !m) return html`<div class="road road-wait">${emptyLine(phase.failed || milestones.failed ? "the phases could not be read" : "loading the phases…")}</div>`;
  const delivered = phaseNumber(p.current_phase) ?? 0;
  const target = phaseNumber(p.target_phase);
  const total = Math.max(delivered, target ?? 0, ...p.phases.map((r) => phaseNumber(r.phase) ?? 0));
  const road = roadOf(delivered, target, total, visible(m.tiers), settingOn("spoilers"));
  return roadBand(road, hashFor(state.dash) + "&settings=spoilers");
}

/** The gate rows open: the first short part unless closed, and any opened. */
function openKeys(g: GateView | null): Set<string> {
  const keys = new Set(view.opened);
  const first = g?.parts.find((p) => p.short > 0);
  if (first && !view.closed.has("gate-" + first.item)) keys.add("gate-" + first.item);
  return keys;
}

function wire(root: HTMLElement): void {
  wireRetry(root);
  wireExpand(root, function (key, open) {
    (open ? view.opened : view.closed).add(key);
    (open ? view.closed : view.opened).delete(key);
  });
  root.addEventListener("change", function (event) {
    const t = event.target as HTMLSelectElement;
    if (t.id === "pg-h") view.horizon = t.value;
    else if (t.id === "pg-alt") view.altSort = t.value as AltSort;
    else return;
    requestRender();
  });
  root.addEventListener(
    "toggle",
    function (event) {
      const d = event.target as HTMLDetailsElement;
      if (!d.id) return;
      if (d.open) view.folds.add(d.id);
      else view.folds.delete(d.id);
    },
    true
  );
  root.addEventListener("click", function (event) {
    const target = event.target as Element;
    if (target.closest("a, button, input, select, label, summary")) return;
    const row = target.closest<HTMLElement>(".entry[data-href]");
    if (row && root.contains(row)) location.hash = row.dataset.href!;
  });
}

function scrollToSection(root: HTMLElement, subject: string): void {
  const key = progressSection(subject);
  if (!key) {
    scrolledTo = "";
    return;
  }
  if (scrolledTo === key) return;
  const head = root.querySelector<HTMLElement>("#progress-" + key);
  if (!head) return;
  scrolledTo = key;
  window.requestAnimationFrame(() => head.scrollIntoView({ block: "start" }));
}

export function renderProgress(body: HTMLElement, subject: string): void {
  askFeeds();
  const h = Number(view.horizon);
  const gatePath = q("/api/progress/gate", { horizon_h: view.horizon });
  const altPath = q("/api/progress/alternates", { horizon_h: view.horizon });
  const gate = read<GateView>(gatePath);
  const alts = read<AlternatesView>(altPath);
  const gateWait = pending(gate, "the gate", gatePath);
  const ms = milestones.data;
  const research = mam.data;
  const lists = ms && research ? buyLists(visible(ms.milestones), visible(research.research)) : null;
  const feedWait = milestones.failed || mam.failed ? emptyLine("milestones or research could not be read") : emptyLine("loading milestones and research…");

  const g = gateSection(gate.data, gateWait, h, openKeys(gate.data));
  g.tools = html`<label class="sort">deliver in ${selectBox("pg-h", HORIZONS, view.horizon, "deliver the phase in")}</label>`;
  const gateTitle = gate.data && gate.data.phase !== null ? TITLES.gate + " " + gate.data.phase : "the next phase";
  const parts = {
    gate: section("gate", gateTitle, g),
    next: section("next", TITLES.next, nextSection(gate.data, gateWait)),
    alternates: section("alternates", TITLES.alternates, alternatesSection(alts.data, pending(alts, "the alternates", altPath), view.altSort)),
    buy: section("buy", TITLES.buy, buySection(lists, view.folds.has("pg-almost"), feedWait)),
    reference: section("reference", TITLES.reference, referenceSection(ms ? visible(ms.tiers) : null, ms ? visible(ms.milestones) : [], research ? visible(research.research) : null, view.folds.has("pg-all"))),
  };
  const root = document.createElement("div");
  root.className = "prog";
  mount(root, html`<h1 class="dk-hidden">Progress</h1>${band()}<div class="pstop">${parts.gate}${parts.next}</div><div class="psupp">${parts.alternates}${parts.buy}</div>${parts.reference}`);
  wire(root);
  body.appendChild(root);
  scrollToSection(root, subject);
}
