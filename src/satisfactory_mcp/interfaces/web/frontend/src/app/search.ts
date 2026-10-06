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

var DEBOUNCE_MS = 150;

var timer = 0;
var hits: Hit[] = [];
var active = -1;
var answered = "";
var opening = "";

function input(): HTMLInputElement {
  return el<HTMLInputElement>("search-q");
}

function recipeSub(r: SearchResponse["recipes"][number]): string {
  var what = (r.kind === "part" && r.machine) || RECIPE_KIND[r.kind] || r.kind;
  return what + (r.unlocked === false ? " · locked" : "");
}

function flatten(data: SearchResponse): Hit[] {
  var rows: Hit[] = [];
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

function more(data: SearchResponse): string {
  var extra: string[] = [];
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
  active = -1;
}

function mark(): void {
  var rows = el("search-hits").querySelectorAll<HTMLElement>(".search-hit");
  Array.prototype.forEach.call(rows, function (row: HTMLElement, i: number) {
    row.classList.toggle("on", i === active);
    row.setAttribute("aria-selected", String(i === active));
    if (i === active) {
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
  var box = el("search-hits");
  box.textContent = "";
  hits = flatten(data);
  active = hits.length ? 0 : -1;
  var group = "";
  hits.forEach(function (hit, i) {
    if (hit.group !== group) {
      group = hit.group;
      var label = make("div", "search-group", group);
      label.setAttribute("role", "presentation");
      box.appendChild(label);
    }
    var row = make("div", "search-hit");
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
  var notes = [hits.length ? "" : "nothing matches", more(data), data.save_note ? friendlyError(data.save_note) : ""];
  notes.forEach(function (text) {
    if (!text) return;
    var line = make("div", "search-note", text);
    line.setAttribute("role", "presentation");
    box.appendChild(line);
  });
  show(box);
  mark();
}

function run(openFirst: boolean): void {
  var text = input().value.trim();
  var ticket = latest("search");
  opening = openFirst ? text : "";
  if (!text) {
    answered = "";
    hits = [];
    close();
    return;
  }
  get<SearchResponse>(`/api/search?q=${encodeURIComponent(text)}&${spoilerQuery()}`)
    .then(function (data) {
      if (!ticket.fresh()) return;
      answered = text;
      draw(data);
      if (opening === text && hits.length) open(hits[0]!);
      opening = "";
    })
    .catch(function (error: unknown) {
      if (!ticket.fresh()) return;
      answered = "";
      hits = [];
      var box = el("search-hits");
      box.textContent = "";
      box.appendChild(make("div", "search-note", "search failed: " + friendlyError(error)));
      show(box);
    });
}

function key(event: KeyboardEvent): void {
  var box = el("search-hits");
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    if (!hits.length || box.hidden) return;
    event.preventDefault();
    active = (active + (event.key === "ArrowDown" ? 1 : hits.length - 1)) % hits.length;
    mark();
  } else if (event.key === "Enter") {
    event.preventDefault();
    window.clearTimeout(timer);
    var text = input().value.trim();
    if (text && text === answered && !box.hidden && hits[active]) open(hits[active]!);
    else if (text) run(true);
  } else if (event.key === "Escape") {
    if (!box.hidden) {
      event.preventDefault();
      close();
    } else if (!input().value) input().blur();
  }
}

export function wireSearch(): void {
  var box = input();
  box.setAttribute("role", "combobox");
  box.setAttribute("aria-autocomplete", "list");
  box.setAttribute("aria-controls", "search-hits");
  box.setAttribute("aria-expanded", "false");
  box.oninput = function () {
    window.clearTimeout(timer);
    timer = window.setTimeout(function () {
      run(false);
    }, DEBOUNCE_MS);
  };
  box.onkeydown = key;
  box.onblur = close;
  box.onfocus = function () {
    if (box.value.trim()) run(false);
  };
  document.addEventListener("keydown", function (event) {
    var target = event.target as HTMLElement | null;
    var typing = target && (target.tagName === "INPUT" || target.tagName === "SELECT" || target.tagName === "TEXTAREA");
    if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      box.focus();
      box.select();
    }
  });
}
