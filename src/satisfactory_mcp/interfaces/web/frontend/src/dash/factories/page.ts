/* One factory's page, `dash=factories/<name>[/<aspect>][?floor=<n|all>]`: the header with its
 * figures and tabs, the floor plan left (sticky; on top on phones) and the aspect right; flows
 * takes the whole width. docs/frontend_kit.md, "Factories". */

import "./factories.css";
import "../power/power.css";
import { adviceCard } from "../../chat/advice";
import { replaceDash } from "../../app/nav";
import { state } from "../../app/state";
import { vitals } from "../../app/vitals";
import { count, pct } from "../../kit/format";
import { cls, html, mount, type Safe } from "../../kit/html";
import { openMenu } from "../../kit/overlay/menu";
import { btn } from "../../kit/ui/controls";
import { wireExpand } from "../../kit/ui/entry";
import { emptyLine, fig, pageHead } from "../../kit/ui/text";
import { hashFor } from "../../map/map";
import { actionTone } from "../machine-states";
import { requestRender } from "../actions";
import { ASPECTS, factoryAddress, factoryDash } from "./address";
import { floorsAspect, linksAspect, machinesAspect, nodesAspect, opts, overviewAspect, powerAspect, sitesAspect } from "./aspects";
import { aspectHref, buildCtx, setPick, type FCtx } from "./ctx";
import { wireRetry } from "./data";
import { factoryMore, mapBtn, MENU_PAGE, runFactoryAction, wireFactoryButtons } from "./factory-actions";
import { flowsAspect, lightItem, mountGraph } from "./flows";
import { factoryLead } from "./icon-choice";
import { healthOr, powerChips } from "./list";
import { planPanel } from "./plan";
import { onRenamed, renamedTo } from "./rename";

import type { FactoryHealthRow } from "../../api/shapes";

/* Set when a pick came from the plan, so the list scrolls its row into view once. */
let scrollTo = "";

function header(c: FCtx): Safe {
  const row = c.row;
  const total = row.machines;
  const tone = actionTone(row.states);
  const figs = html`${fig(count(row.actionable) + " of " + count(total), "machines " + "need action", row.actionable ? tone || undefined : undefined, total ? (row.actionable / total) * 100 : 0)}${fig(pct(row.uptime), "uptime")}`;
  const acts = html`${mapBtn(row.name, !!row.bbox_m)}<span data-factory="${row.name}">${btn("trace supply", { extra: "hide-phone", title: "draw what feeds this factory on the map", data: { fact: "trace supply" } })}</span>${factoryMore(row.name, MENU_PAGE)}`;
  const tabs = ASPECTS.map((a) => ({ label: a[1], href: aspectHref(c, a[0]), on: a[0] === c.aspect }));
  return pageHead({ crumbs: [["Factories", hashFor("factories")]], name: row.name, lead: factoryLead(row, "lead"), chips: powerChips(row), acts, figs, tabs, tabsLabel: "factory sections" });
}

function aspectBody(c: FCtx): Safe {
  if (c.aspect === "flows") return flowsAspect(c);
  if (c.aspect === "machines") return machinesAspect(c);
  if (c.aspect === "power") return powerAspect(c);
  if (c.aspect === "nodes") return nodesAspect(c);
  if (c.aspect === "links") return linksAspect(c);
  if (c.aspect === "floors") return floorsAspect(c);
  if (c.aspect === "sites") return sitesAspect(c);
  return overviewAspect(c);
}

/* ---------------------------------------------------------------- linking */

function light(root: HTMLElement, key: string | null): void {
  root.querySelectorAll(".fsplit .hl").forEach((el) => el.classList.remove("hl"));
  const svg = root.querySelector(".fsvg");
  if (svg) svg.classList.toggle("has-hl", !!key);
  if (!key) return;
  root.querySelectorAll('.fsvg [data-k~="' + key + '"], .fside [data-hl~="' + key + '"]').forEach((el) => el.classList.add("hl"));
}

function pick(c: FCtx, key: string, go?: string, fromPlan?: boolean): void {
  if (!key) return;
  if (/^fl-?\d+$/.test(key)) {
    const ordinal = key.slice(2);
    location.hash = aspectHref(c, c.aspect, c.floor === +ordinal ? "all" : ordinal);
    return;
  }
  const same = c.picked === key;
  setPick(c.name, same ? "" : key);
  scrollTo = same || !fromPlan ? "" : key;
  if (go && !same && location.hash !== go) location.hash = go;
  else requestRender();
}

function stepFloor(c: FCtx, by: number): void {
  if (c.floors.length < 2) return;
  const order = [-1].concat(c.floors.map((f) => f.ordinal));
  const at = order.indexOf(c.floor);
  const next = order[Math.max(0, Math.min(order.length - 1, at + by))]!;
  if (next !== c.floor) location.hash = aspectHref(c, c.aspect, next < 0 ? "all" : String(next));
}

function wire(root: HTMLElement, c: FCtx): void {
  wireExpand(root);
  wireRetry(root);
  wireFactoryButtons(root);
  const flows = c.aspect === "flows";
  root.addEventListener("mouseover", function (event) {
    const t = event.target as Element;
    if (flows) {
      const item = t.closest<HTMLElement | SVGElement>("[data-item]");
      lightItem(root, c, item ? item.getAttribute("data-item") : null);
      return;
    }
    const hit = t.closest<HTMLElement | SVGElement>(".fside [data-hl], .fsvg [data-pk]");
    light(root, hit ? hit.getAttribute("data-hl") || hit.getAttribute("data-pk") : null);
  });
  root.addEventListener("focusin", function (event) {
    const hit = (event.target as Element).closest<HTMLElement>(".fside [data-hl]");
    if (hit) light(root, hit.dataset.hl || null);
  });
  root.addEventListener("click", function (event) {
    const t = event.target as Element;
    const more = t.closest<HTMLElement>(".phead .more[data-menu]");
    if (more) {
      openMenu(more, MENU_PAGE.map((label) => ({ label, act: () => runFactoryAction(label, c.name, more) })));
      return;
    }
    const extra = t.closest<HTMLElement>("[data-more]");
    if (extra) {
      opts.more.add(extra.dataset.more || "");
      requestRender();
      return;
    }
    const segBtn = t.closest<HTMLElement>("[data-seg='fgrain']");
    if (segBtn) {
      opts.grain = segBtn.dataset.val || "group";
      requestRender();
      return;
    }
    const mark = t.closest<SVGElement>(".fsvg [data-pk]");
    if (mark) {
      pick(c, mark.getAttribute("data-pk") || "", mark.getAttribute("data-go") || undefined, true);
      return;
    }
    if (t.closest("a, button, input, select, label")) return;
    const row = t.closest<HTMLElement>(".fside .entry[data-hl]");
    if (row && row.dataset.hl) pick(c, row.dataset.hl, row.dataset.go);
    else if (row && row.dataset.go) location.hash = row.dataset.go;
    const link = t.closest<HTMLElement>(".fside .entry[data-href]");
    if (link) location.hash = link.dataset.href!;
  });
  root.addEventListener("change", function (event) {
    const el = event.target as HTMLInputElement;
    if (el.id === "fgsort") opts.groupSort = el.value;
    else if (el.id === "fmsort") opts.machineSort = el.value;
    else if (el.id === "fmbld") opts.building = el.value;
    else if (el.id === "fboost") opts.boosted = el.checked;
    else return;
    requestRender();
  });
  root.addEventListener("keydown", function (event) {
    const t = event.target as Element;
    if (event.key === "Escape" && c.picked) {
      setPick(c.name, "");
      requestRender();
      return;
    }
    if (event.key === "Enter" && t.matches(".fside .entry[data-hl]")) {
      const row = t as HTMLElement;
      pick(c, row.dataset.hl || "", row.dataset.go);
      return;
    }
    if (!t.closest(".fsvg, .fflr")) return;
    const up = event.key === "PageUp" || event.key === "ArrowUp" || event.key === "ArrowRight";
    const down = event.key === "PageDown" || event.key === "ArrowDown" || event.key === "ArrowLeft";
    if (!up && !down) return;
    event.preventDefault();
    stepFloor(c, up ? -1 : 1);
  });
}

/* A name renamed since the page was linked: follow it to the new address. */
function followRename(body: HTMLElement, name: string, aspect: string, floor: string): boolean {
  const now = renamedTo(name);
  if (!now) return false;
  replaceDash(factoryDash(now, aspect, floor));
  renderFactory(body, factoryDash(now, aspect, floor).slice("factories/".length));
  return true;
}

export function renderFactory(body: HTMLElement, subject: string): void {
  const health = vitals().health;
  const at = factoryAddress(subject, (whole) => !!health && health.factories.some((r) => r.name === whole));
  const row: FactoryHealthRow | undefined = health ? health.factories.filter((r) => r.name === at.name)[0] : undefined;
  if (health && !row && followRename(body, at.name, at.aspect, at.floor)) return;
  if (!healthOr(body)) return;
  const root = document.createElement("div");
  root.className = "fpage";
  if (!row) {
    mount(root, html`${pageHead({ crumbs: [["Factories", hashFor("factories")]], name: at.name })}${emptyLine("no factory named “" + at.name + "” in this world", "it may have been renamed or forgotten; pick one from the list")}`);
    body.appendChild(root);
    return;
  }
  const c = buildCtx(row, at.aspect, at.floor);
  const wide = c.aspect === "flows";
  mount(root, html`${header(c)}<div class="${cls("fsplit", wide && "fwide")}">${wide ? "" : planPanel(c)}<section class="fside" aria-label="${c.aspect || "overview"}">${aspectBody(c)}</section></div>`);
  if (wide) mountGraph(c, root);
  if (!c.aspect) adviceCard(root.querySelector<HTMLElement>(".fside")!, { factory: row.name });
  wire(root, c);
  body.appendChild(root);
  if (scrollTo) {
    const key = scrollTo;
    scrollTo = "";
    requestAnimationFrame(() => root.querySelector('.fside [data-hl~="' + key + '"]')?.scrollIntoView({ block: "nearest" }));
  }
}

onRenamed(function () {
  if (state.dash.indexOf("factories/") === 0) requestRender();
});
