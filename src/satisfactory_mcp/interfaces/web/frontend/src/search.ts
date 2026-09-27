/* The header's one search box: items, recipes and named factories, each routed to its view.
 * See docs/frontend_vision.md §10. */

import { get } from "./api";
import { el, make } from "./dom";
import { hashFor } from "./map";
import { setting } from "./settings";

import type { SearchResponse } from "./api-shapes";

interface Hit {
  group: string;
  text: string;
  sub: string;
  dash: string;
}

var DEBOUNCE_MS = 150;

var timer = 0;
var asked = 0;
var hits: Hit[] = [];
var active = -1;

function flatten(data: SearchResponse): { rows: Hit[]; hidden: number } {
  var rows: Hit[] = [];
  data.factories.forEach(function (f) {
    rows.push({ group: "factories", text: f.name, sub: f.machines + " machines", dash: "factories/" + f.name });
  });
  data.items.forEach(function (i) {
    rows.push({ group: "items", text: i.name, sub: "", dash: "recipes/item/" + i.cls });
  });
  var hidden = 0;
  data.recipes.forEach(function (r) {
    if (r.unlocked === false && !setting("spoilers")) {
      hidden += 1;
      return;
    }
    var sub = r.unlocked === null ? "" : r.unlocked ? "have" : "locked";
    rows.push({ group: "recipes", text: r.name, sub: sub, dash: "recipes/recipe/" + r.cls });
  });
  return { rows: rows, hidden: hidden };
}

function more(data: SearchResponse): string {
  var extra: string[] = [];
  if (data.factories_total > data.factories.length) extra.push(data.factories_total + " factories");
  if (data.items_total > data.items.length) extra.push(data.items_total + " items");
  if (data.recipes_total > data.recipes.length) extra.push(data.recipes_total + " recipes");
  return extra.length ? "showing the first few of " + extra.join(", ") + "; type more to narrow" : "";
}

function open(hit: Hit): void {
  close();
  el<HTMLInputElement>("search-q").blur();
  location.hash = hashFor(hit.dash);
}

function close(): void {
  el("search-hits").hidden = true;
  active = -1;
}

function mark(): void {
  var rows = el("search-hits").querySelectorAll<HTMLElement>(".search-hit");
  Array.prototype.forEach.call(rows, function (row: HTMLElement, i: number) {
    row.classList.toggle("on", i === active);
    row.setAttribute("aria-selected", String(i === active));
    if (i === active) row.scrollIntoView({ block: "nearest" });
  });
}

function draw(data: SearchResponse): void {
  var box = el("search-hits");
  box.textContent = "";
  var flat = flatten(data);
  hits = flat.rows;
  active = hits.length ? 0 : -1;
  var group = "";
  hits.forEach(function (hit, i) {
    if (hit.group !== group) {
      group = hit.group;
      box.appendChild(make("div", "search-group", group));
    }
    var row = make("div", "search-hit");
    row.setAttribute("role", "option");
    row.appendChild(make("span", "search-text", hit.text));
    if (hit.sub) row.appendChild(make("span", "search-sub" + (hit.sub === "locked" ? " locked" : ""), hit.sub));
    row.onmousedown = function (event) {
      event.preventDefault();
      open(hits[i]!);
    };
    box.appendChild(row);
  });
  if (!hits.length) box.appendChild(make("div", "search-note", "nothing matches"));
  if (flat.hidden) box.appendChild(make("div", "search-note", flat.hidden + " locked recipes hidden by the spoiler setting"));
  var tail = more(data);
  if (tail) box.appendChild(make("div", "search-note", tail));
  if (data.save_note) box.appendChild(make("div", "search-note", data.save_note));
  box.hidden = false;
  mark();
}

function run(): void {
  var text = el<HTMLInputElement>("search-q").value.trim();
  var mine = ++asked;
  if (!text) {
    close();
    return;
  }
  get<SearchResponse>(`/api/search?q=${encodeURIComponent(text)}`)
    .then(function (data) {
      if (mine === asked) draw(data);
    })
    .catch(function (error: unknown) {
      if (mine !== asked) return;
      var box = el("search-hits");
      box.textContent = "";
      box.appendChild(make("div", "search-note", "search failed: " + (error instanceof Error ? error.message : String(error))));
      box.hidden = false;
    });
}

function key(event: KeyboardEvent): void {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    if (!hits.length) return;
    event.preventDefault();
    active = (active + (event.key === "ArrowDown" ? 1 : hits.length - 1)) % hits.length;
    mark();
  } else if (event.key === "Enter") {
    event.preventDefault();
    window.clearTimeout(timer);
    if (active >= 0 && hits[active] && !el("search-hits").hidden) open(hits[active]!);
    else run();
  } else if (event.key === "Escape") {
    close();
    el<HTMLInputElement>("search-q").blur();
  }
}

export function wireSearch(): void {
  var input = el<HTMLInputElement>("search-q");
  input.oninput = function () {
    window.clearTimeout(timer);
    timer = window.setTimeout(run, DEBOUNCE_MS);
  };
  input.onkeydown = key;
  input.onblur = close;
  input.onfocus = function () {
    if (input.value.trim()) run();
  };
  document.addEventListener("keydown", function (event) {
    var target = event.target as HTMLElement | null;
    var typing = target && (target.tagName === "INPUT" || target.tagName === "SELECT" || target.tagName === "TEXTAREA");
    if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      input.focus();
      input.select();
    }
  });
}
