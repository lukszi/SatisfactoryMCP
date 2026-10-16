/* The top bar's "jump to…" search, `/` or Ctrl+K: factories, items and recipes from
 * /api/search, plans and circuits from what the page already holds, grouped by kind. */

import { get, latest } from "../../api/client";
import { el } from "../../kit/dom";
import { html, mount, type Safe } from "../../kit/html";
import { icon } from "../../kit/ui/text";
import { friendlyError } from "../../kit/toast";
import { counted, RECIPE_KIND } from "../../kit/words";
import { circuitName } from "../../dash/power/readings";
import { loadList, planIndex } from "../../dash/planner/plan-index";
import { itemsDash } from "../../dash/items/address";
import { itemDash } from "../../dash/items/stock";
import { go } from "../nav";
import { spoilerQuery } from "../settings";
import { vitals } from "../vitals";

import type { SearchResponse } from "../../api/shapes";

const KINDS = ["factories", "plans", "circuits", "items", "recipes"] as const;
const PER_KIND = 5;
const DEBOUNCE_MS = 150;

interface Hit {
  kind: (typeof KINDS)[number];
  name: string;
  ctx: string;
  dash: string;
  lead: Safe;
}

let hits: Hit[] = [];
let active = 0;
let timer = 0;
let failure = "";

function input(): HTMLInputElement {
  return el<HTMLInputElement>("jump");
}

/** Prefix matches first, then by name. */
export function rank<T extends { name: string }>(rows: T[], q: string): T[] {
  return rows
    .filter((r) => r.name.toLowerCase().indexOf(q) >= 0)
    .sort((a, b) => Number(a.name.toLowerCase().indexOf(q) !== 0) - Number(b.name.toLowerCase().indexOf(q) !== 0) || a.name.localeCompare(b.name))
    .slice(0, PER_KIND);
}

function localHits(q: string): Hit[] {
  const plans = planIndex.data ? planIndex.data.index : [];
  const planHits = rank(plans, q).map(function (p): Hit {
    return { kind: "plans", name: p.name, ctx: p.objective + " · v" + p.rev, dash: "planner/" + p.key, lead: html`<i class="ic"></i>` };
  });
  const circuits = (vitals().circuits?.circuits || []).map((row) => ({ name: circuitName(row), n: row.index + 1 }));
  const circuitHits = rank(circuits, q).map(function (c): Hit {
    return { kind: "circuits", name: c.name, ctx: "", dash: "power/" + c.n, lead: html`<i class="mono">C${c.n}</i>` };
  });
  return planHits.concat(circuitHits);
}

function serverHits(data: SearchResponse): Hit[] {
  const out: Hit[] = [];
  data.factories.slice(0, PER_KIND).forEach(function (f) {
    out.push({ kind: "factories", name: f.name, ctx: counted(f.machines, "machine"), dash: "factories/" + f.name, lead: html`<i class="ic"></i>` });
  });
  data.items.slice(0, PER_KIND).forEach(function (i) {
    out.push({ kind: "items", name: i.name, ctx: "", dash: itemDash(i.cls, i.name), lead: icon(i.cls, i.name) });
  });
  data.recipes.slice(0, PER_KIND).forEach(function (r) {
    const what = (r.kind === "part" && r.machine) || RECIPE_KIND[r.kind] || r.kind;
    out.push({ kind: "recipes", name: r.name, ctx: what + (r.alternate ? " · alt" : ""), dash: itemsDash({ view: "all", rec: r.cls }), lead: icon(r.icon, r.name) });
  });
  return out;
}

function ordered(rows: Hit[]): Hit[] {
  return KINDS.flatMap((kind) => rows.filter((h) => h.kind === kind));
}

function draw(): void {
  const box = el("jump-list");
  const text = input().value.trim();
  const groups = KINDS.map(function (kind) {
    const rows = hits.filter((h) => h.kind === kind);
    if (!rows.length) return "";
    return html`<div role="group" aria-label="${kind}"><div class="jgrp" aria-hidden="true">${kind}</div>${rows.map(function (h) {
      const i = hits.indexOf(h);
      return html`<a role="option" id="${"jo-" + i}" data-i="${i}" tabindex="-1" class="${i === active ? "jopt on" : "jopt"}" aria-selected="${String(i === active)}">${h.lead}<span class="jn">${h.name}</span><span class="jc">${h.ctx}</span></a>`;
    })}</div>`;
  });
  const none = failure ? html`<p class="jnone">search failed: ${failure}</p>` : html`<p class="jnone">no match for “${text}”</p>`;
  mount(box, hits.length ? html`${groups}` : none);
  box.hidden = false;
  input().setAttribute("aria-expanded", "true");
  if (hits.length) input().setAttribute("aria-activedescendant", "jo-" + active);
  else input().removeAttribute("aria-activedescendant");
  document.getElementById("jo-" + active)?.scrollIntoView({ block: "nearest" });
}

function closeList(): void {
  el("jump-list").hidden = true;
  input().setAttribute("aria-expanded", "false");
  input().removeAttribute("aria-activedescendant");
  hits = [];
}

function run(): void {
  const text = input().value.trim();
  const q = text.toLowerCase();
  if (!q) return closeList();
  const ticket = latest("jump");
  if (!planIndex.data && !planIndex.error) loadList();
  get<SearchResponse>(`/api/search?q=${encodeURIComponent(text)}&${spoilerQuery()}`)
    .then(function (data) {
      if (!ticket.fresh()) return;
      failure = "";
      hits = ordered(localHits(q).concat(serverHits(data)));
    })
    .catch(function (reason: unknown) {
      if (!ticket.fresh()) return;
      failure = friendlyError(reason);
      hits = ordered(localHits(q));
    })
    .then(function () {
      if (!ticket.fresh() || !input().value.trim()) return;
      active = 0;
      draw();
    });
}

function choose(hit: Hit | undefined): void {
  if (!hit) return;
  closeSearch(true);
  go(hit.dash);
}

export function openSearch(): void {
  el("bar").classList.add("searching");
  input().focus();
  input().select();
  if (input().value.trim()) run();
}

export function closeSearch(blur: boolean): void {
  input().value = "";
  closeList();
  el("bar").classList.remove("searching");
  if (blur) input().blur();
}

function onKey(event: KeyboardEvent): void {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    if (!hits.length) return;
    active = (active + (event.key === "ArrowDown" ? 1 : hits.length - 1)) % hits.length;
    draw();
  } else if (event.key === "Enter") {
    event.preventDefault();
    choose(hits[active]);
  } else if (event.key === "Escape") {
    event.preventDefault();
    closeSearch(true);
  }
}

function isField(target: EventTarget | null): boolean {
  const node = target as HTMLElement | null;
  return !!node && (node.tagName === "INPUT" || node.tagName === "TEXTAREA" || node.tagName === "SELECT" || node.isContentEditable);
}

export function wireJump(): void {
  input().addEventListener("input", function () {
    window.clearTimeout(timer);
    timer = window.setTimeout(run, DEBOUNCE_MS);
  });
  input().addEventListener("keydown", onKey);
  el("jump-list").addEventListener("mousedown", function (event) {
    const row = (event.target as Element).closest<HTMLElement>("[data-i]");
    if (!row) return;
    event.preventDefault();
    choose(hits[Number(row.dataset.i)]);
  });
  el("search").addEventListener("focusout", function (event) {
    const next = event.relatedTarget as Node | null;
    if (!next || !el("search").contains(next)) closeSearch(false);
  });
  el("search-btn").addEventListener("click", openSearch);
  document.addEventListener("keydown", function (event) {
    if ((document.querySelector("dialog[open]") as HTMLElement | null) !== null) return;
    const ctrlK = (event.ctrlKey || event.metaKey) && (event.key === "k" || event.key === "K");
    const slash = event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !isField(event.target);
    if (!ctrlK && !slash) return;
    event.preventDefault();
    openSearch();
  });
}
