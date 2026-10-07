/* The header's one search box: items, recipes and named factories, each routed to its view.
 * See docs/frontend_vision.md §10. */

import { get, latest } from "../api/client";
import { el, make } from "../kit/dom";
import { count } from "../kit/format";
import { go } from "./nav";
import { spoilerQuery } from "./settings";
import { friendlyError } from "../kit/toast";
import { counted, RECIPE_KIND } from "../kit/words";

import type { SearchResponse } from "../api/shapes";

interface Hit {
  group: string;
  text: string;
  sub: string;
  dash: string;
}

const DEBOUNCE_MS = 150;

let timer = 0;
let hits: Hit[] = [];
let activeIndex = -1;
let answeredQuery = "";
let openFirstHitFor = "";

function input(): HTMLInputElement {
  return el<HTMLInputElement>("search-q");
}

function recipeSub(r: SearchResponse["recipes"][number]): string {
  const what = (r.kind === "part" && r.machine) || RECIPE_KIND[r.kind] || r.kind;
  return what + (r.unlocked === false ? " · locked" : "");
}

function flatten(data: SearchResponse): Hit[] {
  const rows: Hit[] = [];
  data.factories.forEach(function (f) {
    rows.push({ group: "factories", text: f.name, sub: counted(f.machines, "machine"), dash: "factories/" + f.name });
  });
  data.items.forEach(function (i) {
    rows.push({ group: "items", text: i.name, sub: "", dash: "recipes/item/" + i.cls });
  });
  data.recipes.forEach(function (r) {
    rows.push({ group: "recipes", text: r.name, sub: recipeSub(r), dash: "recipes/recipe/" + r.cls });
  });
  return rows;
}

function truncationNote(data: SearchResponse): string {
  const extra: string[] = [];
  if (data.factories_total > data.factories.length) extra.push(count(data.factories_total) + " factories");
  if (data.items_total > data.items.length) extra.push(count(data.items_total) + " items");
  if (data.recipes_total > data.recipes.length) extra.push(count(data.recipes_total) + " recipes");
  return extra.length ? "showing the first few of " + extra.join(", ") + "; type more to narrow" : "";
}

function open(hit: Hit): void {
  close();
  input().blur();
  go(hit.dash);
}

function close(): void {
  el("search-hits").hidden = true;
  input().setAttribute("aria-expanded", "false");
  input().removeAttribute("aria-activedescendant");
  activeIndex = -1;
}

function highlightActiveHit(): void {
  const rows = el("search-hits").querySelectorAll<HTMLElement>(".search-hit");
  Array.prototype.forEach.call(rows, function (row: HTMLElement, i: number) {
    row.classList.toggle("on", i === activeIndex);
    row.setAttribute("aria-selected", String(i === activeIndex));
    if (i === activeIndex) {
      row.scrollIntoView({ block: "nearest" });
      input().setAttribute("aria-activedescendant", row.id);
    }
  });
}

function show(box: HTMLElement): void {
  box.hidden = false;
  input().setAttribute("aria-expanded", "true");
}

function draw(data: SearchResponse): void {
  const box = el("search-hits");
  box.textContent = "";
  hits = flatten(data);
  activeIndex = hits.length ? 0 : -1;
  let group = "";
  hits.forEach(function (hit, i) {
    if (hit.group !== group) {
      group = hit.group;
      const label = make("div", "search-group", group);
      label.setAttribute("role", "presentation");
      box.appendChild(label);
    }
    const row = make("div", "search-hit");
    row.id = "search-hit-" + i;
    row.setAttribute("role", "option");
    row.appendChild(make("span", "search-text", hit.text));
    if (hit.sub) row.appendChild(make("span", "search-sub", hit.sub));
    row.onmousedown = function (event) {
      event.preventDefault();
      open(hits[i]!);
    };
    box.appendChild(row);
  });
  const notes = [hits.length ? "" : "nothing matches", truncationNote(data), data.save_note ? friendlyError(data.save_note) : ""];
  notes.forEach(function (text) {
    if (!text) return;
    const line = make("div", "search-note", text);
    line.setAttribute("role", "presentation");
    box.appendChild(line);
  });
  show(box);
  highlightActiveHit();
}

function runSearch(openFirst: boolean): void {
  const text = input().value.trim();
  const ticket = latest("search");
  openFirstHitFor = openFirst ? text : "";
  if (!text) {
    answeredQuery = "";
    hits = [];
    close();
    return;
  }
  get<SearchResponse>(`/api/search?q=${encodeURIComponent(text)}&${spoilerQuery()}`)
    .then(function (data) {
      if (!ticket.fresh()) return;
      answeredQuery = text;
      draw(data);
      if (openFirstHitFor === text && hits.length) open(hits[0]!);
      openFirstHitFor = "";
    })
    .catch(function (error: unknown) {
      if (!ticket.fresh()) return;
      answeredQuery = "";
      hits = [];
      const box = el("search-hits");
      box.textContent = "";
      box.appendChild(make("div", "search-note", "search failed: " + friendlyError(error)));
      show(box);
    });
}

function onSearchKeydown(event: KeyboardEvent): void {
  const box = el("search-hits");
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    if (!hits.length || box.hidden) return;
    event.preventDefault();
    activeIndex = (activeIndex + (event.key === "ArrowDown" ? 1 : hits.length - 1)) % hits.length;
    highlightActiveHit();
  } else if (event.key === "Enter") {
    event.preventDefault();
    window.clearTimeout(timer);
    const text = input().value.trim();
    if (text && text === answeredQuery && !box.hidden && hits[activeIndex]) open(hits[activeIndex]!);
    else if (text) runSearch(true);
  } else if (event.key === "Escape") {
    if (!box.hidden) {
      event.preventDefault();
      close();
    } else if (!input().value) input().blur();
  }
}

export function wireSearch(): void {
  const box = input();
  box.setAttribute("role", "combobox");
  box.setAttribute("aria-autocomplete", "list");
  box.setAttribute("aria-controls", "search-hits");
  box.setAttribute("aria-expanded", "false");
  box.oninput = function () {
    window.clearTimeout(timer);
    timer = window.setTimeout(function () {
      runSearch(false);
    }, DEBOUNCE_MS);
  };
  box.onkeydown = onSearchKeydown;
  box.onblur = close;
  box.onfocus = function () {
    if (box.value.trim()) runSearch(false);
  };
  document.addEventListener("keydown", function (event) {
    const target = event.target as HTMLElement | null;
    const typing = target && (target.tagName === "INPUT" || target.tagName === "SELECT" || target.tagName === "TEXTAREA");
    if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      box.focus();
      box.select();
    }
  });
}
