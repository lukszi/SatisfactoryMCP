/* The finder on the map: what is near a point, or a dashboard result, ringed in its own pane
 * and listed in the one map card. See docs/world-finders_contract.md §2.5 and §8. */

import { get, latest } from "./api";
import { button, choice, link, pressed, statusChip, table, tabs2 } from "./dashkit";
import { FIND_AT_ATTR, FIND_ATTR, keepFocus } from "./dom";
import { coords, count, metres, num, perMin, rounded } from "./format";
import { reveal } from "./labels";
import { L } from "./leaflet";
import { FIT_SNAP, flyPadded, flyToBox, flyToPoint, map, xy } from "./map";
import { cardHead, cardLine, cardRow, cardSubject, claim, mapCard } from "./mapcard";
import { knownNodes, pickupName } from "./markers";
import { withQuery } from "./nav";
import { HIGHLIGHT, makeRoom, onVitals, showPoint } from "./panel";
import { select } from "./selection";
import { onSetting, setting, spoilerFlag } from "./settings";
import { state } from "./state";
import { friendly } from "./toast";
import { W } from "./words";

import type { ApiPath, ApiUrl } from "./api";
import type { Column } from "./dashkit";
import type {
  CollectibleRow,
  CollectiblesResponse,
  ConduitsResponse,
  FoundField,
  FoundNode,
  NodeFindResponse,
  RankedSite,
  RunRow,
} from "./api-shapes";
import type { Selection } from "./selection";

export type FindKind = "nodes" | "conduits" | "pickups";

export type Shown =
  | { kind: "nodes"; rows: FoundNode[] }
  | { kind: "fields"; rows: FoundField[] }
  | { kind: "sites"; rows: RankedSite[] }
  | { kind: "runs"; rows: RunRow[] }
  | { kind: "pickups"; rows: CollectibleRow[] };

var SHOWN = 25;

var NEAR_M = 500;

var POINT_ZOOM = 1;

var ALL_ZOOM = 1;

var ALL_PAD = 0.05;

export var CONDUIT_RADIUS_M = "250";

var RADII = ["100", CONDUIT_RADIUS_M, "500", "1000"];

var pane = map.createPane("finder");
pane.style.zIndex = "445";
pane.style.pointerEvents = "none";
var renderer = L.svg({ pane: "finder", padding: 0.5 });
var group = L.layerGroup();

var view = {
  open: false,
  title: "",
  at: null as { x: number; y: number } | null,
  kind: "nodes" as FindKind,
  ref: "",
  dash: "",
  set: null as Shown | null,
  seed: -1,
  note: "",
  error: "",
  busy: false,
  world: "",
  epoch: 0,
  filter: { resource: "", free: false, conduitKind: "all", radius: CONDUIT_RADIUS_M, group: "" },
  groups: [] as string[],
  beyond: 0,
  focus: false,
  back: null as HTMLElement | null,
};

function card(): HTMLElement {
  return mapCard("finder", "finder", closeFinder);
}

export function worldUrl(path: ApiPath, params: Record<string, string>): ApiUrl {
  var query = withQuery("", params).slice(1);
  return query ? `${path}?${query}` : path;
}

export function resourceOptions(any: string, current: string): [string, string][] {
  var names: Record<string, string> = {};
  var every: Record<string, string> = {};
  var all = setting("spoilers");
  knownNodes().forEach(function (n) {
    if (n.kind === "geyser") return;
    every[n.resource] = n.resource_name;
    if (all || !n.spoiler) names[n.resource] = n.resource_name;
  });
  var options = Object.keys(names)
    .map(function (id): [string, string] {
      return [id, names[id]!];
    })
    .sort(function (a, b) {
      return a[1].localeCompare(b[1]);
    });
  if (current && !names[current]) options.unshift([current, (every[current] || current) + " (" + W.hiddenBySpoilers + ")"]);
  return [["", any] as [string, string]].concat(options);
}

export function nodeLabel(n: { resource_name: string; purity: string }): string {
  return n.resource_name + ", " + n.purity;
}

export function fieldLabel(f: FoundField): string {
  return f.resources.join(" + ") + " " + W.field + " · " + (f.region || f.grid);
}

export function nodeRate(n: FoundNode): string {
  return n.kind === "geyser" ? "–" : perMin(n.rate, false);
}

export function carriesText(r: RunRow): string {
  var parts: string[] = [];
  if (r.carries) parts.push(r.carries);
  else if (r.kind === "pipe") parts.push("nothing known");
  if (r.rate !== null) parts.push((r.kind === "pipe" ? num(r.rate, 0) + " m³/min" : perMin(r.rate)) + " max");
  return parts.length ? parts.join(" · ") : "–";
}

export function runLabel(r: RunRow): string {
  return r.label && r.label !== r.id ? r.id + " · " + r.label : r.id;
}

export function nodeSelection(n: FoundNode): Selection {
  return { kind: "node", key: n.id, label: nodeLabel(n), x_m: n.x_m, y_m: n.y_m, ref: "node:" + n.name };
}

export function fieldSelection(f: FoundField): Selection {
  return { kind: "field", key: f.key, label: fieldLabel(f), x_m: f.x_m, y_m: f.y_m, ref: f.selector };
}

export function siteSelection(s: RankedSite): Selection {
  return { kind: "field", key: s.selector, label: "site " + s.rank + (s.region ? ", " + s.region : ""), x_m: s.x_m, y_m: s.y_m, ref: s.selector };
}

export function runSelection(r: RunRow): Selection {
  return { kind: "conduit", key: r.id, label: runLabel(r), x_m: r.a.x_m, y_m: r.a.y_m, ref: r.id };
}

export function pickupPlace(p: { x_m: number; y_m: number }): string {
  return rounded(p.x_m) + "," + rounded(p.y_m);
}

export function pickupSelection(p: CollectibleRow): Selection {
  return { kind: "pickup", key: p.name, label: pickupName(p.category), x_m: p.x_m, y_m: p.y_m, ref: pickupPlace(p) };
}

function ring(at: { x_m: number; y_m: number }, seed: boolean): void {
  L.circleMarker(xy(at), {
    radius: seed ? 11 : 7,
    color: HIGHLIGHT,
    weight: seed ? 4 : 2,
    fill: false,
    renderer: renderer,
    pane: "finder",
    interactive: false,
  }).addTo(group);
}

function box(b: [number, number, number, number]): void {
  L.rectangle(L.latLngBounds([xy({ x_m: b[0], y_m: b[1] }), xy({ x_m: b[2], y_m: b[3] })]), {
    color: HIGHLIGHT,
    weight: 2,
    dashArray: "6 4",
    fill: false,
    renderer: renderer,
    pane: "finder",
    interactive: false,
  }).addTo(group);
}

function latlngs(r: RunRow): L.LatLngTuple[][] {
  return r.lines_m.map(function (points) {
    return points.map(function (p) {
      return [-p[1], p[0]] as L.LatLngTuple;
    });
  });
}

function line(r: RunRow, seed: boolean): L.Polyline {
  return L.polyline(latlngs(r), {
    color: HIGHLIGHT,
    weight: seed ? 5 : 3,
    opacity: 0.8,
    renderer: renderer,
    pane: "finder",
    interactive: false,
  }).addTo(group);
}

function runBounds(r: RunRow): L.LatLngBounds | null {
  var points = ([] as L.LatLngTuple[]).concat.apply([], latlngs(r));
  return points.length ? L.latLngBounds(points) : null;
}

function members(f: FoundField): void {
  var wanted: Record<string, boolean> = {};
  f.members.forEach(function (leaf) {
    wanted[leaf] = true;
  });
  knownNodes().forEach(function (n) {
    if (wanted[n.name]) ring(n, false);
  });
}

function draw(set: Shown, seed: number): L.LatLngBounds | null {
  group.clearLayers();
  var bounds: L.LatLngBounds | null = null;
  function grow(b: L.LatLngBounds | L.LatLngTuple): void {
    if (bounds) bounds.extend(b);
    else bounds = b instanceof L.LatLngBounds ? L.latLngBounds(b.getSouthWest(), b.getNorthEast()) : L.latLngBounds([b, b]);
  }
  if (set.kind === "nodes" || set.kind === "pickups" || set.kind === "sites") {
    (set.rows as { x_m: number; y_m: number }[]).forEach(function (r, i) {
      ring(r, i === seed);
      grow(xy(r));
    });
  } else if (set.kind === "fields") {
    set.rows.forEach(function (f, i) {
      box(f.bbox_m);
      if (i === seed) members(f);
      grow(L.latLngBounds([xy({ x_m: f.bbox_m[0], y_m: f.bbox_m[1] }), xy({ x_m: f.bbox_m[2], y_m: f.bbox_m[3] })]));
    });
  } else {
    set.rows.forEach(function (r, i) {
      line(r, i === seed);
      var b = runBounds(r);
      if (b) grow(b);
    });
  }
  if (!map.hasLayer(group)) group.addTo(map);
  return bounds;
}

function flyTo(set: Shown, seed: number, bounds: L.LatLngBounds | null): void {
  var row = seed >= 0 ? set.rows[seed] : undefined;
  if (row && set.kind === "fields") {
    flyToBox((row as FoundField).bbox_m, { maxZoom: POINT_ZOOM });
    return;
  }
  if (row && set.kind === "runs") {
    var b = runBounds(row as RunRow);
    if (b) flyPadded(b.pad(0.2), POINT_ZOOM);
    return;
  }
  if (row) {
    var at = row as { x_m: number; y_m: number };
    flyToPoint(xy(at), Math.max(map.getZoom(), POINT_ZOOM));
    return;
  }
  if (bounds) flyPadded(bounds.pad(ALL_PAD), ALL_ZOOM, FIT_SNAP);
}

function selectionOf(set: Shown, i: number): Selection {
  if (set.kind === "nodes") return nodeSelection(set.rows[i]!);
  if (set.kind === "fields") return fieldSelection(set.rows[i]!);
  if (set.kind === "sites") return siteSelection(set.rows[i]!);
  if (set.kind === "runs") return runSelection(set.rows[i]!);
  return pickupSelection(set.rows[i]!);
}

function pick(i: number): void {
  if (!view.set) return;
  view.seed = i;
  var bounds = draw(view.set, i);
  render();
  flyTo(view.set, i, bounds);
  if (view.set.kind === "pickups") reveal(["pickup: " + view.set.rows[i]!.category]);
  select(selectionOf(view.set, i));
}

interface Listed {
  i: number;
}

function column(key: string, label: string, render: (i: number) => string | HTMLElement, right?: boolean): Column<Listed> {
  return {
    key: key,
    label: label,
    align: right ? "right" : undefined,
    className: right ? "dash-nowrap" : undefined,
    render: function (r) {
      return render(r.i);
    },
  };
}

function columns(set: Shown): Column<Listed>[] {
  if (set.kind === "nodes") {
    var nodes = set.rows;
    return [
      column("node", W.node, function (i) {
        return nodeLabel(nodes[i]!);
      }),
      column("status", "status", function (i) {
        return statusChip(nodes[i]!.status);
      }),
      column("rate", "per min", function (i) {
        return nodeRate(nodes[i]!);
      }, true),
      column("distance", "away", function (i) {
        return metres(nodes[i]!.distance_m);
      }, true),
    ];
  }
  if (set.kind === "fields") {
    var fields = set.rows;
    return [
      column("field", W.field, function (i) {
        return fieldLabel(fields[i]!);
      }),
      column("free", W.free, function (i) {
        return perMin(fields[i]!.free, false);
      }, true),
      column("distance", "away", function (i) {
        return metres(fields[i]!.distance_m);
      }, true),
    ];
  }
  if (set.kind === "sites") {
    var sites = set.rows;
    return [
      column("rank", "rank", function (i) {
        return String(sites[i]!.rank);
      }, true),
      column("region", "region", function (i) {
        return sites[i]!.region || "–";
      }),
      column("score", "score", function (i) {
        return num(sites[i]!.score, 2);
      }, true),
    ];
  }
  if (set.kind === "runs") {
    var runs = set.rows;
    return [
      column("run", W.run, function (i) {
        return runLabel(runs[i]!);
      }),
      column("carries", "carries", function (i) {
        return carriesText(runs[i]!);
      }),
      column("length", "length", function (i) {
        return metres(runs[i]!.length_m);
      }, true),
      column("distance", "away", function (i) {
        return metres(runs[i]!.distance_m);
      }, true),
    ];
  }
  var pickups = set.rows;
  return [
    column("pickup", "pickup", function (i) {
      return pickupName(pickups[i]!.category);
    }),
    column("place", "at", function (i) {
      return coords(pickups[i]!.x_m, pickups[i]!.y_m);
    }, true),
    column("distance", "away", function (i) {
      return metres(pickups[i]!.distance_m);
    }, true),
  ];
}

function listed(box: HTMLElement, set: Shown): void {
  var total = set.rows.length;
  if (!total) {
    cardLine(box, view.at && set.kind === "nodes" ? "no " + W.node + " within " + count(NEAR_M) + " m" : "nothing found here");
    return;
  }
  var rows: Listed[] = [];
  for (var i = 0; i < Math.min(total, SHOWN); i++) rows.push({ i: i });
  var measured = (set.rows as { distance_m?: number | null }[]).some(function (r) {
    return r.distance_m !== null && r.distance_m !== undefined;
  });
  var shownColumns = columns(set).filter(function (c) {
    return measured || c.key !== "distance";
  });
  box.appendChild(
    table<Listed>(shownColumns, rows, {
      caption: view.title,
      onRow: function (r) {
        pick(r.i);
      },
      rowClass: function (r) {
        return r.i === view.seed ? "on" : "";
      },
    })
  );
  var more = Math.max(0, total - SHOWN) + view.beyond;
  if (more) cardLine(box, count(more) + " more");
}

function filterRow(box: HTMLElement): void {
  var row = cardRow();
  row.classList.add("finder-filter");
  var f = view.filter;
  if (view.kind === "nodes") {
    row.appendChild(
      choose("resource", "finder-resource", f.resource, resourceOptions("any resource", f.resource), function (v) {
        f.resource = v;
      })
    );
    row.appendChild(
      pressed(W.free, f.free, function () {
        f.free = !f.free;
        fetchPoint(true);
      }, { title: "only nodes with no extractor on them" })
    );
  } else if (view.kind === "conduits") {
    row.appendChild(
      choose("kind", "finder-kind", f.conduitKind, [["all", "belts and pipes"], ["belt", "belts"], ["pipe", "pipes"]], function (v) {
        f.conduitKind = v;
      })
    );
    row.appendChild(
      choose("radius", "finder-radius", f.radius, RADII.map(function (r): [string, string] { return [r, "within " + r + " m"]; }), function (v) {
        f.radius = v;
      })
    );
  } else {
    row.appendChild(
      choose(
        "pickup kind",
        "finder-group",
        f.group,
        [["", "every kind"] as [string, string]].concat(
          view.groups.map(function (g): [string, string] {
            return [g, pickupName(g)];
          })
        ),
        function (v) {
          f.group = v;
        }
      )
    );
  }
  box.appendChild(row);
}

function choose(label: string, candidate: string, value: string, options: [string, string][], change: (v: string) => void): HTMLSelectElement {
  return choice(options, value, function (v) {
    change(v);
    fetchPoint(true);
  }, { label: label, candidate: candidate });
}

function render(): void {
  var el = card();
  keepFocus(el, function () {
    fill(el);
  });
  if (view.open && view.focus) {
    view.focus = false;
    var first = el.querySelector<HTMLElement>(".tabs2-item.on") || el.querySelector<HTMLElement>("tbody tr.on[tabindex]") || el.querySelector<HTMLElement>("tbody tr[tabindex]") || el.querySelector<HTMLElement>(".mapcard-head button");
    if (first) first.focus({ preventScroll: true });
  }
}

function fill(el: HTMLElement): void {
  el.textContent = "";
  if (!view.open) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  var head = cardHead(view.at ? "Near this point" : "On the map");
  if (view.note) head.title = view.note;
  head.appendChild(button("×", closeFinder, { title: "close the finder", label: "close the finder and clear its rings" }));
  el.appendChild(head);
  cardSubject(el, view.title);
  if (view.at) {
    el.appendChild(
      tabs2(
        [
          { id: "nodes", label: "nodes" },
          { id: "conduits", label: "conduits" },
          { id: "pickups", label: "pickups" },
        ],
        view.kind,
        function (id) {
          view.kind = id as FindKind;
          fetchPoint(true);
        },
        "what to find near this point"
      )
    );
    filterRow(el);
  }
  if (view.error) cardLine(el, view.error, "bad");
  else if (view.set) listed(el, view.set);
  else if (view.busy) cardLine(el, "finding…");
  if (view.dash) {
    var foot = cardRow();
    foot.appendChild(link(view.dash, "open in World"));
    el.appendChild(foot);
  }
}

export function closeFinder(): void {
  var inside = card().contains(document.activeElement);
  var back = view.back;
  view.back = null;
  latest("finder");
  view.open = false;
  view.set = null;
  view.at = null;
  view.ref = "";
  view.seed = -1;
  view.error = "";
  view.busy = false;
  group.clearLayers();
  render();
  if (!inside) return;
  if (back && back.isConnected && back.getClientRects().length) back.focus({ preventScroll: true });
  else map.getContainer().focus({ preventScroll: true });
}

function begin(title: string, dash: string): void {
  var from = document.activeElement as HTMLElement | null;
  if (!view.open) view.back = from && from !== document.body && !card().contains(from) ? from : null;
  view.focus = true;
  claim("finder");
  makeRoom("trace");
  view.open = true;
  view.title = title;
  view.dash = dash;
  view.note = "";
  view.error = "";
  view.seed = -1;
  view.beyond = 0;
  view.world = state.world;
  view.epoch = state.epoch;
}

function at(): string {
  return view.at ? view.at.x + "," + view.at.y : "";
}

function pointQuery(): { url: ApiUrl; dash: string } {
  var f = view.filter;
  var here = at();
  if (view.kind === "nodes") {
    return {
      url: worldUrl("/api/world/nodes", {
        view: "nearest",
        source: "near:" + here + "@" + NEAR_M,
        near: here,
        resource: f.resource,
        status: f.free ? "free" : "",
        spoilers: spoilerFlag(),
      }),
      dash: withQuery("world/nodes", { near: here, resource: f.resource, status: f.free ? "free" : "" }),
    };
  }
  if (view.kind === "conduits") {
    return {
      url: worldUrl("/api/world/conduits", { near: here, radius_m: f.radius, conduit_kind: f.conduitKind === "all" ? "" : f.conduitKind }),
      dash: withQuery("world/conduits", { near: here, radius_m: f.radius, conduit_kind: f.conduitKind === "all" ? "" : f.conduitKind }),
    };
  }
  return {
    url: worldUrl("/api/collectibles", { mode: "nearest", near: here, group: f.group, spoilers: spoilerFlag() }),
    dash: withQuery("world/pickups", { view: "nearest", near: here, group: f.group }),
  };
}

function landed(kind: FindKind, data: NodeFindResponse | ConduitsResponse | CollectiblesResponse): Shown {
  view.beyond = 0;
  if (kind === "nodes") return { kind: "nodes", rows: (data as NodeFindResponse).nodes };
  if (kind === "conduits") {
    view.note = (data as ConduitsResponse).age_note;
    return { kind: "runs", rows: (data as ConduitsResponse).runs };
  }
  var pickups = data as CollectiblesResponse;
  view.groups = pickups.census
    .filter(function (c) {
      return setting("spoilers") || !c.spoiler;
    })
    .map(function (c) {
      return c.category;
    });
  view.beyond = Math.max(0, pickups.rows.length - SHOWN);
  return { kind: "pickups", rows: pickups.rows.slice(0, SHOWN) };
}

function fetchPoint(fit: boolean): void {
  var kind = view.kind;
  var q = pointQuery();
  var ticket = latest("finder");
  view.dash = q.dash;
  view.busy = true;
  view.error = "";
  render();
  get<NodeFindResponse | ConduitsResponse | CollectiblesResponse>(q.url)
    .then(function (data) {
      if (!ticket.fresh()) return;
      view.busy = false;
      view.set = landed(kind, data);
      view.seed = -1;
      var bounds = draw(view.set, -1);
      render();
      if (fit && bounds) flyPadded(bounds.pad(0.15), map.getZoom());
    })
    .catch(function (err) {
      if (!ticket.fresh()) return;
      view.busy = false;
      view.set = null;
      view.error = friendly(err);
      group.clearLayers();
      render();
    });
}

export function startAt(kind: FindKind, x: number, y: number): void {
  begin("near " + coords(x, y), "");
  view.at = { x: x, y: y };
  view.kind = kind;
  view.ref = "";
  view.set = null;
  fetchPoint(true);
}

export function showRows(set: Shown, title: string, dash: string, seed?: number): void {
  begin(title, dash);
  view.at = null;
  view.ref = "";
  view.set = set;
  view.seed = seed === undefined ? -1 : seed;
  var bounds = draw(set, view.seed);
  render();
  flyTo(set, view.seed, bounds);
  if (set.kind === "pickups" && view.seed >= 0) reveal(["pickup: " + set.rows[view.seed]!.category]);
  if (view.seed >= 0) select(selectionOf(set, view.seed));
}

function parsePoint(text: string): { x: number; y: number; r: number } | null {
  var m = /^(?:near:)?(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)(?:@(\d+(?:\.\d+)?))?$/.exec(text.trim());
  return m ? { x: +m[1]!, y: +m[2]!, r: m[3] ? +m[3] : 0 } : null;
}

function fetchRef(ref: string): void {
  var ticket = latest("finder");
  var conduit = /^(chain|pipe):/.test(ref);
  var url = conduit
    ? worldUrl("/api/world/conduits", { near: ref, run: ref })
    : worldUrl("/api/world/nodes", { source: ref });
  view.busy = true;
  render();
  get<NodeFindResponse | ConduitsResponse>(url)
    .then(function (data) {
      if (!ticket.fresh()) return;
      view.busy = false;
      var set: Shown = conduit
        ? { kind: "runs", rows: (data as ConduitsResponse).runs.filter(function (r) { return r.id === ref; }) }
        : { kind: "nodes", rows: (data as NodeFindResponse).nodes };
      if (!set.rows.length) {
        view.error = ref + " is not in this save";
        render();
        return;
      }
      view.set = set;
      view.title = selectionOf(set, 0).label;
      pick(0);
    })
    .catch(function (err) {
      if (!ticket.fresh()) return;
      view.busy = false;
      view.error = friendly(err);
      render();
    });
}

export function showRef(ref: string, spot?: { x_m?: number; y_m?: number; label: string }): void {
  var point = parsePoint(ref);
  if (/^(node|chain|pipe):/.test(ref)) {
    begin(ref.indexOf("node:") === 0 ? W.node : W.run, "");
    view.at = null;
    view.set = null;
    view.ref = ref;
    fetchRef(ref);
    return;
  }
  var x = point ? point.x : spot ? spot.x_m : undefined;
  var y = point ? point.y : spot ? spot.y_m : undefined;
  if (x === undefined || y === undefined) return;
  begin(spot ? spot.label : coords(x, y), "");
  view.at = null;
  view.ref = "";
  view.set = null;
  group.clearLayers();
  ring({ x_m: x, y_m: y }, true);
  if (!map.hasLayer(group)) group.addTo(map);
  render();
  flyToPoint(xy({ x_m: x, y_m: y }), Math.max(map.getZoom(), POINT_ZOOM));
}

var pending = 0;

function refresh(): void {
  if (!view.open) return;
  if (view.world !== state.world || view.epoch !== state.epoch) {
    closeFinder();
    return;
  }
  if (view.busy || (!view.at && !view.ref)) return;
  clearTimeout(pending);
  pending = window.setTimeout(function () {
    if (view.at) fetchPoint(false);
    else if (view.ref) fetchRef(view.ref);
  }, 50);
}

function hideSpoilers(): void {
  if (!view.open || !view.set || setting("spoilers")) return;
  var rows = view.set.rows as { spoiler?: boolean }[];
  if (!rows.some(function (r) { return r.spoiler; })) return;
  if (view.at || view.ref) {
    refresh();
    return;
  }
  view.set = { kind: view.set.kind, rows: rows.filter(function (r) { return !r.spoiler; }) } as Shown;
  view.seed = -1;
  draw(view.set, -1);
  render();
}

function finding(event: Event): void {
  var target = event.target as Element | null;
  var hit = target && target.closest ? target.closest("[" + FIND_ATTR + "]") : null;
  if (!hit) return;
  event.stopPropagation();
  event.preventDefault();
  var spot = parsePoint(hit.getAttribute(FIND_AT_ATTR) || "");
  var kind = hit.getAttribute(FIND_ATTR);
  map.closePopup();
  if (!spot) return;
  if (kind === "point") {
    closeFinder();
    showPoint(spot.x, spot.y, { label: coords(spot.x, spot.y) });
  } else startAt(kind === "conduits" || kind === "pickups" ? kind : "nodes", spot.x, spot.y);
}

export function listenForFinds(): void {
  document.addEventListener("click", finding, true);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && view.open && !(event.target as Element).closest("input[type=text], input[type=search], textarea")) closeFinder();
  });
  onVitals(refresh);
  onSetting(hideSpoilers);
}
