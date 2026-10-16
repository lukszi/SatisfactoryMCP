/* The Factories page, `dash=factories`: the unnamed clusters, compact, over the named
 * factories as equal-height cards. docs/frontend_kit.md, "Factories". */

import "./factories.css";
import { loadOne } from "../../app/load";
import { vitals } from "../../app/vitals";
import { error, loading } from "../../kit/dashkit";
import { count, mw, pct } from "../../kit/format";
import { html, mount, type Safe } from "../../kit/html";
import { openMenu } from "../../kit/overlay/menu";
import { sortSelect } from "../../kit/ui/controls";
import { acts, chevron, entry, expandBody, list, wireExpand } from "../../kit/ui/entry";
import { chip, mixBar } from "../../kit/ui/marks";
import { dashH, emptyLine, toolrow } from "../../kit/ui/text";
import { counted, WORDS } from "../../kit/words";
import { hashFor } from "../../map/map";
import { showMachine } from "../../map/map-highlight";
import { actionTone, needsAction, stateTone } from "../machine-states";
import { leaveDashThen, requestRender } from "../actions";
import { factoryDash } from "./address";
import { clusterGraph, clustersBlock, resetDetectOnNewEpoch, wireClusters } from "./clusters";
import { factoryMore, mapBtn, MENU_CARD, runFactoryAction, wireFactoryButtons } from "./factory-actions";
import { factoryLead } from "./icon-choice";
import { mixParts, sortFactories, SORTS, type FactorySort } from "./model";

import type { FactoryHealthResponse, FactoryHealthRow, MachineIssue } from "../../api/shapes";

let sortBy: FactorySort = "need";
const opened = new Set<string>();
/* The worst machines drawn last, by key, for their ◎ buttons. */
const issueAt = new Map<string, MachineIssue>();

/** The health read, or the loading or error line in its place. */
export function healthOr(body: HTMLElement): FactoryHealthResponse | null {
  const v = vitals();
  if (v.health) return v.health;
  if (v.healthError) error(body, "factory health", v.healthError, () => loadOne("/api/factories/health"));
  else loading(body, "factory health");
  return null;
}

export function powerChips(row: FactoryHealthRow): Safe {
  return html`${row.unwired > 0 && chip(WORDS.noWire + " " + row.unwired, "bad")}${row.no_generator > 0 && chip(WORDS.noGenerator + " " + row.no_generator, "bad")}${row.review && chip(row.review, "mid", "label " + row.review + ": " + row.alive + " of " + row.anchors + " anchors still stand")}`;
}

export function stateChip(state: string, n?: number): Safe {
  const tone = stateTone(state);
  const label = (n ? n + " " : "") + state;
  return chip(label, tone === "ok" ? "muted" : tone === "mid" ? "mid" : tone);
}

function issueLine(row: FactoryHealthRow, issue: MachineIssue, i: number): Safe {
  const key = row.name + "#" + i;
  issueAt.set(key, issue);
  const map = issue.x_m !== null ? html`<button type="button" class="btn icon" data-issue="${key}" title="show it on the map">◎</button>` : html`<span class="btn-gap"></span>`;
  return html`<div class="sub">${stateChip(issue.state)}<span>${issue.what}${issue.cause.length ? html` <span class="ictx">${issue.cause.join(", ")}</span>` : ""}</span><span class="r">${issue.uptime === null ? html`<span class="dim">–</span>` : pct(issue.uptime)}</span>${map}</div>`;
}

function expandOf(row: FactoryHealthRow): Safe {
  const others = row.states.filter((s) => !needsAction(s.state));
  const alive = row.alive === row.anchors ? "" : html`<p class="note">${row.alive} of ${row.anchors} anchors still stand</p>`;
  const worst = row.worst_actionable.length ? row.worst_actionable.map((m, i) => issueLine(row, m, i)) : html`<p class="note">none ${WORDS.needAction}</p>`;
  return expandBody(html`${worst}${others.length ? html`<div class="chips">${others.map((s) => stateChip(s.state, s.count))}</div>` : ""}${alive}`);
}

function factoryCard(row: FactoryHealthRow): Safe {
  const tone = actionTone(row.states);
  const href = hashFor(factoryDash(row.name, ""));
  const key = "fac:" + row.name;
  const open = opened.has(key);
  const ctx = html`${powerChips(row)} ${counted(row.machines, "machine")}`;
  const metric = row.actionable
    ? html`<div class="metric ${tone}"><b>${count(row.actionable)}</b><span>${WORDS.needAction}</span></div>`
    : html`<div class="metric"><b>${pct(row.uptime)}</b><span>uptime</span></div>`;
  const facts = html`<div class="facts"><span><em>${mw(row.measured_mw)}</em> ${WORDS.drawingNow}</span><span title="${WORDS.ifAllRunTip}"><em>${mw(row.nameplate_mw)}</em> ${WORDS.ifAllRun}</span></div>`;
  const mix = mixParts(row.states, (s) => stateTone(s));
  const foot = html`<div class="full foot">${mixBar(mix, "machines by state: " + row.states.map((s) => s.count + " " + s.state).join(", "))}</div>`;
  return entry(
    { tone, href, key, open },
    html`${factoryLead(row)}<div class="id"><a class="name" href="${href}" title="${row.name}">${row.name}</a><div class="ctx">${ctx}</div></div>${metric}${acts(html`${mapBtn(row.name, !!row.bbox_m)}${factoryMore(row.name, MENU_CARD)}${chevron(key, row.name, open)}`)}${facts}${foot}${expandOf(row)}`
  );
}

function namedBlock(rows: FactoryHealthRow[]): Safe {
  const head = toolrow(dashH("named factories", rows.length), sortSelect("fsort", SORTS, sortBy));
  if (!rows.length) return html`${head}${emptyLine("no factories named yet", "detect them above, or ask chat to name one")}`;
  const sorted = sortFactories(rows, sortBy);
  if (sortBy !== "need") return html`${head}${list("fac", "grid", sorted.map(factoryCard))}`;
  const need = sorted.filter((r) => r.actionable);
  const fine = sorted.filter((r) => !r.actionable);
  return html`${head}${need.length ? html`${dashH(WORDS.needAction, need.length, "h3")}${list("fac", "grid", need.map(factoryCard))}` : ""}${fine.length ? html`${dashH("running", fine.length, "h3")}${list("fac", "grid", fine.map(factoryCard))}` : ""}`;
}

function wire(root: HTMLElement): void {
  wireExpand(root, (key, open) => (open ? opened.add(key) : opened.delete(key)));
  wireClusters(root);
  wireFactoryButtons(root);
  root.addEventListener("change", function (event) {
    const select = event.target as HTMLSelectElement;
    if (select.id !== "fsort") return;
    sortBy = select.value as FactorySort;
    requestRender();
  });
  root.addEventListener("click", function (event) {
    const target = event.target as Element;
    const more = target.closest<HTMLElement>("[data-factory] .more[data-menu]");
    if (more) {
      const name = more.closest<HTMLElement>("[data-factory]")!.dataset.factory!;
      openMenu(more, MENU_CARD.map((label) => ({ label, act: () => runFactoryAction(label, name, more) })));
      return;
    }
    const issue = target.closest<HTMLElement>("[data-issue]");
    if (issue) {
      const m = issueAt.get(issue.dataset.issue || "");
      if (m && m.x_m !== null && m.y_m !== null) {
        const x = m.x_m;
        const y = m.y_m;
        leaveDashThen(() => showMachine(m.instance, m.what, x, y, { layers: ["machines"] }));
      }
      return;
    }
    if (target.closest("a, button, input, select, label, .expand")) return;
    const row = target.closest<HTMLElement>(".l-fac .entry[data-href]");
    if (row && root.contains(row)) location.hash = row.dataset.href!;
  });
}

export function renderFactories(body: HTMLElement): void {
  resetDetectOnNewEpoch();
  const health = healthOr(body);
  if (!health) return;
  issueAt.clear();
  const root = document.createElement("div");
  root.className = "facs";
  mount(root, html`${clustersBlock()}${namedBlock(health.factories)}`);
  clusterGraph(root);
  wire(root);
  body.appendChild(root);
}
