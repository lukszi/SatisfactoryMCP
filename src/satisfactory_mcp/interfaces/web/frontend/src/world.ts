/* The dashboard's World section: where the player is, and the finders for nodes, fields,
 * conduits, pickups and regions. See docs/world-finders_contract.md §2 and §7. */

import { get, latest } from "./api";
import { choice, empty, error, heading, link, loading, note, showAll, table, tabs2 } from "./dashkit";
import { mapButton, render } from "./dashboard";
import { code, make, rebuilding } from "./dom";
import { resourceOptions, worldUrl } from "./finder";
import { coords, count, metres, num, regionLine, rounded } from "./format";
import { loadOne } from "./load";
import { hashFor, writeHash } from "./map";
import { dashParts, subjectQuery, withQuery } from "./nav";
import { showPoint } from "./panel";
import { registerFetch } from "./registry";
import { onSetting, spoilerFlag } from "./settings";
import { state } from "./state";
import { renderConduits } from "./world-conduits";
import { nodeTable, renderNodes } from "./world-nodes";
import { renderPickups } from "./world-pickups";
import { counted, gapText, W } from "./words";

import type { ApiError, ApiPath, ApiUrl } from "./api";
import type { Column, SortState } from "./dashkit";
import type { HereResponse, Region, RegionRow, RegionTableResponse, TableAge } from "./api-shapes";

var VIEWS: [string, string][] = [
  ["", "here"],
  ["nodes", "nodes"],
  ["fields", "fields"],
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
  var key = JSON.stringify([scope, state.token || "wave " + herePart.wave, url]);
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
  return labelled(label, choice(options, value, change, { candidate: candidate, disabled: opts.disabled, title: opts.title }));
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
    if (rebuilding()) return;
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

export function capped(card: HTMLElement, grid: HTMLElement, rows: number, key: string, noun: string): void {
  showAll(card, grid, rows, SHOWN, "show all " + counted(rows, noun), !!uncapped[key], function () {
    uncapped[key] = true;
  });
}

export function staleText(t: TableAge): string {
  if (t.notes.length) return t.notes.join(" ");
  return W.mapDataBehind + (t.gap ? " (" + gapText(t.gap) + ")" : "");
}

export function staleLine(parent: HTMLElement, t: TableAge | null): void {
  if (!t || (!t.behind && !t.moved && !t.unjoinable)) return;
  note(parent, staleText(t));
}

export function hiddenLine(parent: HTMLElement, n: number, one: string, many?: string): void {
  if (!n) return;
  var line = make("p", "dash-note");
  line.appendChild(document.createTextNode(counted(n, one, many) + " " + W.hiddenBySpoilers + " · "));
  line.appendChild(link("settings", "Settings"));
  parent.appendChild(line);
}

export function regionCell(region: Region | null, full?: boolean): HTMLElement {
  var cell = make("span", "", region ? (full ? regionLine(region) : region.name) : "off the map");
  if (region) cell.title = regionLine(region) + ", good to about " + num(region.accuracy_m, 0) + " m";
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
  return withQuery("", { radius_m: String(HERE_RADIUS_M), spoilers: spoilerFlag() }).slice(1);
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
  note(card, data.written_ago ? "as of the save written " + data.written_ago : "as of the save shown in the header").title = data.age_note;
  var player = data.player;
  if (!player) {
    empty(card, "no player position in this save", "a dedicated-server save holds no pawn; nodes, fields, conduits, pickups and regions still work");
    return;
  }
  var at = player;
  var facts: [string, string | HTMLElement][] = [
    ["position", coords(at.x_m, at.y_m) + ", " + num(at.z_m, 0) + " m up"],
    ["region", regionCell(data.region, true)],
    ["grid", data.grid ? data.grid + (data.direction ? " · " + data.direction : "") : "–"],
    ["nearest building", data.nearest_building ? data.nearest_building.name + ", " + metres(data.nearest_building.distance_m) : "none"],
    ["id", copyCell(rounded(at.x_m) + "," + rounded(at.y_m), "copy")],
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
  heading(near, counted(data.nodes_total, "node") + " within " + num(data.radius_m, 0), "m");
  body.appendChild(near);
  if (!data.nodes.length) {
    empty(near, "no resource node within " + num(data.radius_m, 0) + " m");
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
  want("world-regions", regionsBox, worldUrl("/api/world/regions", { resource: params.resource || "", spoilers: spoilerFlag() }));
  if (waiting(card, regionsBox, "regions")) return;
  var data = regionsBox.data!;
  note(card, "region names are good to about " + num(data.accuracy_m, 0) + " m" + (data.resource_name ? " · nodes counted: " + data.resource_name : ""));
  hiddenLine(card, data.hidden_spoilers, "locked node");
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
      render: function (r) { return num(r.area_km2, 2) + " km²"; },
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
  body.appendChild(
    tabs2(
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
  else if (at.view === "conduits") renderConduits(body, at.params);
  else if (at.view === "pickups") renderPickups(body, at.params);
  else renderRegions(body, at.params);
}

function carried(view: string, params: Record<string, string>): Record<string, string> {
  var kept: Record<string, string> = {};
  if (view === "nodes" || view === "fields" || view === "regions") kept.resource = params.resource || "";
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

onSetting(function () {
  loadOne(HERE_PATH);
  redraw();
});
