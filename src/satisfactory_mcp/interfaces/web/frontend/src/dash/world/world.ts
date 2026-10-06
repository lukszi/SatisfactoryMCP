/* The dashboard's World section: where the player is, and the finders for nodes, fields,
 * conduits, pickups and regions. See docs/world-finders_contract.md §2 and §7. */

import { get, latest } from "../../api/client";
import { appendNote, capRows, empty, error, heading, link, loading, selectBox, subTabs, table } from "../../kit/dashkit";
import { mapButton, render } from "../shell";
import { code, make } from "../../kit/dom";
import { isRebuilding } from "../../kit/focus";
import { resourceOptions, worldUrl } from "./world-finds";
import { coords, count, formatNumber, metres, regionLine, roundHalfEven } from "../../kit/format";
import { loadOne } from "../../app/load";
import { hashFor, writeHash } from "../../map/map";
import { dashParts, go, subjectQuery, withQuery } from "../../app/nav";
import { showPoint } from "../../map/map-highlight";
import { registerFetch } from "../../app/registry";
import { actorWord } from "../planner/planner-core";
import { onSetting, settingChoice } from "../../app/settings";
import { state } from "../../app/state";
import { notify, offer } from "../../kit/toast";
import { renderConduits } from "./world-conduits";
import { nodeTable, renderNodes } from "./world-nodes";
import { renderPickups } from "./world-pickups";
import { fromFieldsRank, renderRank } from "./world-rank";
import { counted, gapText, WORDS } from "../../kit/words";

import type { ApiError, ApiPath, ApiUrl } from "../../api/client";
import type { Column, SortState } from "../../kit/dashkit";
import type { HereResponse, Region, RegionRow, RegionTableResponse, TableAge } from "../../api/shapes";
import type { ActivityEvent } from "../planner/planner-core";

var VIEWS: [string, string][] = [
  ["", "here"],
  ["nodes", "nodes"],
  ["fields", "fields"],
  ["rank", "rank"],
  ["conduits", "conduits"],
  ["pickups", "pickups"],
  ["regions", "regions"],
];

var HERE_PATH: ApiPath = "/api/world/here";

var HERE_RADIUS_M = 500;

var DEBOUNCE_MS = 200;

var SHOWN: 50 = 50;

var herePart = {
  data: null as HereResponse | null,
  failed: false,
  token: "",
  wave: 0,
};

var hereListeners: Array<() => void> = [];

var editTimer = 0;

var uncapped: Record<string, boolean> = {};

var drafts: Record<string, { base: string; text: string }> = {};

interface Address {
  view: string;
  params: Record<string, string>;
}

function address(): Address {
  var q = subjectQuery(dashParts().subject);
  var view = "";
  VIEWS.forEach(function (v) {
    if (v[0] === q.head) view = v[0];
  });
  return { view: view, params: q.params };
}

export function viewDash(view: string, params: Record<string, string>): string {
  return withQuery(view ? "world/" + view : "world", params);
}

export function changed(params: Record<string, string>, patch: Record<string, string>): Record<string, string> {
  var next: Record<string, string> = {};
  Object.keys(params).forEach(function (k) {
    next[k] = params[k]!;
  });
  Object.keys(patch).forEach(function (k) {
    next[k] = patch[k]!;
  });
  return next;
}

function shown(): boolean {
  return dashParts().tab === "world";
}

function redraw(): void {
  if (shown()) render();
}

export function edit(params: Record<string, string>, soon?: boolean): void {
  state.dash = viewDash(address().view, params);
  writeHash();
  window.clearTimeout(editTimer);
  if (soon) editTimer = window.setTimeout(redraw, DEBOUNCE_MS);
  else redraw();
}

export function here(): HereResponse | null {
  return herePart.data;
}

export function onHere(listener: () => void): void {
  hereListeners.push(listener);
}

export interface Loaded<T> {
  key: string;
  scope: string;
  data: T | null;
  failed: boolean;
  reason: unknown;
  busy: boolean;
}

export function loaded<T>(): Loaded<T> {
  return { key: "", scope: "", data: null, failed: false, reason: null, busy: false };
}

export function want<T extends ApiError>(slot: string, box: Loaded<T>, url: ApiUrl): void {
  var scope = JSON.stringify([state.epoch, state.world, state.save]);
  var key = JSON.stringify([scope, state.saveToken || "wave " + herePart.wave, url]);
  if (box.key === key) return;
  if (box.scope !== scope) box.data = null;
  box.key = key;
  box.scope = scope;
  box.busy = true;
  box.failed = false;
  var ticket = latest(slot);
  get<T>(url)
    .then(function (data) {
      if (!ticket.fresh()) return;
      box.data = data;
      box.busy = false;
      redraw();
    })
    .catch(function (reason) {
      if (!ticket.fresh()) return;
      box.data = null;
      box.failed = true;
      box.reason = reason;
      box.busy = false;
      redraw();
    });
}

export function waiting<T>(parent: HTMLElement, box: Loaded<T>, what: string): boolean {
  if (box.failed) {
    error(parent, what, box.reason, function () {
      box.key = "";
      redraw();
    });
    return true;
  }
  if (!box.data) {
    loading(parent, what);
    return true;
  }
  if (box.busy) loading(parent, "the new " + what);
  return false;
}

export function copyCell(text: string, shown?: string): HTMLElement {
  var holder = make("span");
  holder.innerHTML = code(text, shown).html;
  return holder.firstChild as HTMLElement;
}

export function filterBar(parent: HTMLElement): HTMLElement {
  var bar = make("div", "world-filters");
  parent.appendChild(bar);
  return bar;
}

function labelled(label: string, control: HTMLElement): HTMLElement {
  var box = make("label", "world-field");
  box.appendChild(make("span", "world-field-k", label));
  box.appendChild(control);
  return box;
}

export interface FieldOptions {
  disabled?: boolean;
  title?: string;
}

export function selectField(
  label: string,
  candidate: string,
  value: string,
  options: [string, string][],
  change: (value: string) => void,
  o?: FieldOptions
): HTMLElement {
  var opts = o || {};
  return labelled(label, selectBox(options, value, change, { candidate: candidate, disabled: opts.disabled, title: opts.title }));
}

export function textField(
  label: string,
  candidate: string,
  value: string,
  placeholder: string,
  change: (value: string) => void,
  o?: FieldOptions
): HTMLElement {
  var opts = o || {};
  var input = make("input", "dash-name world-text");
  input.type = "search";
  input.placeholder = placeholder;
  input.title = opts.title || "press Enter to search";
  input.disabled = !!opts.disabled;
  var draft = drafts[candidate];
  input.value = draft && draft.base === value ? draft.text : value;
  input.setAttribute("data-candidate", candidate);
  function commit(text: string): void {
    if (isRebuilding()) return;
    delete drafts[candidate];
    change(text);
  }
  input.onchange = function () {
    commit(input.value.trim());
  };
  input.oninput = function () {
    drafts[candidate] = { base: value, text: input.value };
    if (!input.value.trim() && value) commit("");
  };
  input.onfocus = function () {
    var end = input.value.length;
    input.setSelectionRange(end, end);
  };
  return labelled(label, input);
}

export interface Span {
  min: number;
  max: number;
  step: number;
}

export function rangeField(
  label: string,
  candidate: string,
  value: number,
  span: Span,
  show: (value: number) => string,
  change: (value: number) => void,
  o?: FieldOptions
): HTMLElement {
  var opts = o || {};
  var box = make("span", "world-range");
  var input = make("input", "world-slider");
  input.type = "range";
  input.min = String(span.min);
  input.max = String(span.max);
  input.step = String(span.step);
  input.value = String(value);
  input.disabled = !!opts.disabled;
  input.setAttribute("data-candidate", candidate);
  input.setAttribute("aria-label", label);
  if (opts.title) input.title = opts.title;
  var out = make("output", "world-range-v", show(value));
  input.oninput = function () {
    out.textContent = show(Number(input.value));
  };
  input.onchange = function () {
    if (!isRebuilding()) change(Number(input.value));
  };
  box.appendChild(input);
  box.appendChild(out);
  return labelled(label, box);
}

export function capped(card: HTMLElement, grid: HTMLElement, rows: number, key: string, noun: string): void {
  capRows(card, grid, rows, SHOWN, "show all " + counted(rows, noun), !!uncapped[key], function () {
    uncapped[key] = true;
  });
}

export function staleText(t: TableAge): string {
  if (t.notes.length) return t.notes.join(" ");
  return WORDS.mapDataBehind + (t.gap ? " (" + gapText(t.gap) + ")" : "");
}

export function staleLine(parent: HTMLElement, t: TableAge | null): void {
  if (!t || (!t.behind && !t.moved && !t.unjoinable)) return;
  appendNote(parent, staleText(t));
}

export function hiddenLine(parent: HTMLElement, n: number, one: string, many?: string): void {
  if (!n) return;
  var line = make("p", "dash-note");
  line.appendChild(document.createTextNode(counted(n, one, many) + " " + WORDS.hiddenBySpoilers + " · "));
  line.appendChild(link("settings", "Settings"));
  parent.appendChild(line);
}

export function regionCell(region: Region | null, full?: boolean): HTMLElement {
  var cell = make("span", "", region ? (full ? regionLine(region) : region.name) : "off the map");
  if (region) cell.title = regionLine(region) + ", good to about " + formatNumber(region.accuracy_m, 0) + " m";
  return cell;
}

export function distanceColumn<R extends { distance_m: number | null }>(label?: string): Column<R> {
  return {
    key: "distance",
    label: label || "away",
    align: "right",
    title: "straight-line distance from the place the search is near",
    sort: function (r) {
      return r.distance_m === null ? Infinity : r.distance_m;
    },
    render: function (r) {
      return metres(r.distance_m);
    },
  };
}

function hereQuery(): string {
  return withQuery("", { radius_m: String(HERE_RADIUS_M) }).slice(1);
}

function renderHere(body: HTMLElement): void {
  var card = make("section", "dash-card");
  heading(card, "where the player is");
  body.appendChild(card);
  var data = herePart.data;
  if (!data) {
    if (herePart.failed) error(card, "the player's position", null, function () { loadOne(HERE_PATH); });
    else loading(card, "the player's position");
    return;
  }
  appendNote(card, data.written_ago ? "as of the save written " + data.written_ago : "as of the save shown in the header").title = data.age_note;
  var player = data.player;
  if (!player) {
    empty(card, "no player position in this save", "a dedicated-server save holds no pawn; nodes, fields, conduits, pickups and regions still work");
    return;
  }
  var at = player;
  var facts: [string, string | HTMLElement][] = [
    ["position", coords(at.x_m, at.y_m) + ", " + formatNumber(at.z_m, 0) + " m up"],
    ["region", regionCell(data.region, true)],
    ["grid", data.grid ? data.grid + (data.direction ? " · " + data.direction : "") : "–"],
    ["nearest building", data.nearest_building ? data.nearest_building.name + ", " + metres(data.nearest_building.distance_m) : "none"],
    ["id", copyCell(roundHalfEven(at.x_m) + "," + roundHalfEven(at.y_m), "copy")],
  ];
  if (data.pawns > 1) facts.push(["players", count(data.pawns) + " in this save; the position is the host's"]);
  var list = make("dl", "world-facts");
  facts.forEach(function (f) {
    list.appendChild(make("dt", "", f[0]));
    var value = make("dd");
    if (typeof f[1] === "string") value.textContent = f[1];
    else value.appendChild(f[1]);
    list.appendChild(value);
  });
  card.appendChild(list);
  var acts = make("div", "world-acts");
  acts.appendChild(
    mapButton(
      "fly the map to the player",
      function () {
        showPoint(at.x_m, at.y_m, { label: "the player" });
      },
      "show the player on the map"
    )
  );
  acts.appendChild(link(viewDash("nodes", { near: "me" }), "nodes near me"));
  acts.appendChild(link(viewDash("conduits", { near: "me" }), "conduits near me"));
  acts.appendChild(link(viewDash("pickups", { view: "nearest" }), "pickups near me"));
  card.appendChild(acts);
  data.stale.forEach(function (t) {
    if (t.table === "nodes") staleLine(card, t);
  });
  var near = make("section", "dash-card");
  heading(near, counted(data.nodes_total, "node") + " within " + formatNumber(data.radius_m, 0), "m");
  body.appendChild(near);
  if (!data.nodes.length) {
    empty(near, "no resource node within " + formatNumber(data.radius_m, 0) + " m");
    return;
  }
  var grid = nodeTable(data.nodes, true, null);
  near.appendChild(grid);
  capped(near, grid, data.nodes.length, "here", "node");
}

var regionsBox = loaded<RegionTableResponse>();

var regionSort: SortState = { key: "nodes", desc: true };

function renderRegions(body: HTMLElement, params: Record<string, string>): void {
  var card = make("section", "dash-card");
  body.appendChild(card);
  var bar = filterBar(card);
  bar.appendChild(
    selectField("resource", "world-regions-resource", params.resource || "", resourceOptions("every resource", params.resource || ""), function (v) {
      edit({ resource: v });
    })
  );
  want("world-regions", regionsBox, worldUrl("/api/world/regions", { resource: params.resource || "" }));
  if (waiting(card, regionsBox, "regions")) return;
  var data = regionsBox.data!;
  appendNote(card, "region names are good to about " + formatNumber(data.accuracy_m, 0) + " m" + (data.resource_name ? " · nodes counted: " + data.resource_name : ""));
  if (!data.rows.length) {
    empty(card, "no region holds a matching node");
    return;
  }
  var columns: Column<RegionRow>[] = [
    { key: "name", label: "region", sort: function (r) { return r.name; }, render: function (r) { return r.name; } },
    { key: "direction", label: "direction", render: function (r) { return r.direction; } },
    { key: "grid", label: "grid", sort: function (r) { return r.grid; }, render: function (r) { return r.grid; } },
    {
      key: "area",
      label: "area",
      align: "right",
      sort: function (r) { return r.area_km2; },
      render: function (r) { return formatNumber(r.area_km2, 2) + " km²"; },
    },
    { key: "nodes", label: "nodes", align: "right", sort: function (r) { return r.nodes; }, render: function (r) { return count(r.nodes); } },
    { key: "selector", label: "selector", render: function (r) { return copyCell("region:" + r.name); } },
    {
      key: "map",
      label: "",
      align: "right",
      render: function (r) {
        return mapButton("fly the map to the region's name", function () {
          showPoint(r.anchor_m[0], r.anchor_m[1], { label: r.name });
        }, "show " + r.name + " on the map");
      },
    },
  ];
  card.appendChild(table(columns, data.rows, { sort: regionSort, caption: "regions" }));
}

export function worldTitle(subject: string): string {
  var head = subjectQuery(subject).head;
  var label = "";
  VIEWS.forEach(function (v) {
    if (v[0] && v[0] === head) label = v[1].charAt(0).toUpperCase() + v[1].slice(1);
  });
  return label;
}

export function renderWorld(body: HTMLElement): void {
  var at = address();
  if (at.view === "fields" && at.params.rank === "1") {
    at = { view: "rank", params: fromFieldsRank(at.params) };
    state.dash = viewDash(at.view, at.params);
    writeHash();
  }
  body.appendChild(
    subTabs(
      VIEWS.map(function (v) {
        return { id: v[0], label: v[1], href: hashFor(viewDash(v[0], carried(v[0], at.params))) };
      }),
      at.view,
      undefined,
      "world view"
    )
  );
  if (at.view === "") renderHere(body);
  else if (at.view === "nodes" || at.view === "fields") renderNodes(body, at.view, at.params);
  else if (at.view === "rank") renderRank(body, at.params);
  else if (at.view === "conduits") renderConduits(body, at.params);
  else if (at.view === "pickups") renderPickups(body, at.params);
  else renderRegions(body, at.params);
}

function carried(view: string, params: Record<string, string>): Record<string, string> {
  var kept: Record<string, string> = {};
  if (view === "nodes" || view === "fields" || view === "rank" || view === "regions") kept.resource = params.resource || "";
  if (view === "nodes" || view === "fields") kept.near = params.near || "";
  return kept;
}

registerFetch<HereResponse>({
  wave: "live",
  rank: 80,
  path: HERE_PATH,
  query: hereQuery,
  label: "where the player is",
  clears: [],
  refilters: false,
  draw: function (data) {
    if (herePart.token && herePart.token !== data.save_token) herePart.wave += 1;
    herePart.token = data.save_token;
    herePart.data = data;
    herePart.failed = false;
    hereListeners.forEach(function (listener) {
      listener();
    });
    redraw();
  },
  failed: function () {
    herePart.data = null;
    herePart.failed = true;
    redraw();
  },
});

onSetting(redraw);

function busy(): boolean {
  var active = document.activeElement;
  return !!active && /^(INPUT|TEXTAREA)$/.test(active.tagName);
}

export function onFindActivity(entry: ActivityEvent): void {
  if (entry.kind !== "world.find" || entry.world !== state.world || entry.actor.kind === "page") return;
  var mode = settingChoice("follow");
  var args = (entry.args || {}) as { view?: string; params?: Record<string, string> };
  if (mode === "off" || typeof args.view !== "string") return;
  var there = viewDash(args.view, args.params || {});
  if (state.dash === there) return;
  var who = actorWord(entry.actor);
  if (mode === "toasts" || busy()) {
    offer(who + " " + entry.text, "open", function () {
      go(there);
    });
    return;
  }
  notify(who + " " + entry.text + " (Settings, follow chat)");
  go(there);
}
