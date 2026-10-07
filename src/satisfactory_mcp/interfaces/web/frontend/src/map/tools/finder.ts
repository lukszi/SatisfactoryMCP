/* The finder on the map: what is near a point, or a dashboard result, ringed in its own pane
 * and listed in the one map card. See docs/world-finders_contract.md §2.5 and §8. */

import { get, latest } from "../../api/client";
import { button, link, selectBox, statusChip, subTabs, table, toggleButton } from "../../kit/dashkit";
import { FIND_AT_ATTR, FIND_ATTR, onAttributeClick } from "../../kit/dom";
import { keepFocus } from "../../kit/focus";
import { coords, count, formatNumber, metres, perMin } from "../../kit/format";
import { reveal } from "../labels";
import { L } from "../leaflet";
import { boundsOfBbox, FIT_SNAP, flyPadded, flyToBox, flyToPoint, map, latLngOf } from "../map";
import { cardTitleBar, cardLine, cardToolbar, cardSubject, closeOtherCards, mapCard } from "../mapcard";
import { knownNodes } from "../drawn/markers";
import { pickupName } from "../drawn/pickups";
import { withQuery } from "../../app/nav";
import {
  carriesText,
  fieldLabel,
  fieldSelection,
  nodeLabel,
  nodeRate,
  nodeSelection,
  pickupSelection,
  resourceOptions,
  runLabel,
  runSelection,
  siteSelection,
  worldUrl,
} from "../../dash/world/world-finds";
import { makeRoom } from "../panel";
import { HIGHLIGHT, showPoint } from "../map-highlight";
import { onVitals } from "../../app/vitals";
import { select } from "../../app/selection";
import { onSetting, settingOn, spoilerFlag } from "../../app/settings";
import { state } from "../../app/state";
import { friendlyError } from "../../kit/toast";
import { WORDS } from "../../kit/words";

import type { ApiUrl } from "../../api/client";
import type { Column } from "../../kit/dashkit";
import type { BboxM } from "../geometry";
import type {
  CollectibleRow,
  CollectiblesResponse,
  ConduitsResponse,
  FoundField,
  FoundNode,
  NodeFindResponse,
  RankedSite,
  RunRow,
} from "../../api/shapes";
import type { Selection } from "../../app/selection";

export type FindKind = "nodes" | "conduits" | "pickups";

/** What the finder card lists and the map rings: one kind of row at a time. */
export type FinderResults =
  | { kind: "nodes"; rows: FoundNode[] }
  | { kind: "fields"; rows: FoundField[] }
  | { kind: "sites"; rows: RankedSite[] }
  | { kind: "runs"; rows: RunRow[] }
  | { kind: "pickups"; rows: CollectibleRow[] };

/** How many rows the card lists; the rest are counted. */
const MAX_TABLE_ROWS = 25;

const NEAR_M = 500;

const POINT_ZOOM = 1;

const ALL_ZOOM = 1;

const ALL_PAD = 0.05;

export const CONDUIT_RADIUS_M = "250";

const RADII = ["100", CONDUIT_RADIUS_M, "500", "1000"];

const pane = map.createPane("finder");
pane.style.zIndex = "445";
pane.style.pointerEvents = "none";
const renderer = L.svg({ pane: "finder", padding: 0.5 });
const group = L.layerGroup();

const view = {
  open: false,
  title: "",
  /** The point a "near here" search is about, or null for a list handed in from elsewhere. */
  at: null as { x: number; y: number } | null,
  kind: "nodes" as FindKind,
  ref: "",
  dash: "",
  results: null as FinderResults | null,
  /** The row picked in the card and ringed boldest on the map, or -1. */
  selectedIndex: -1,
  note: "",
  error: "",
  busy: false,
  world: "",
  epoch: 0,
  filter: { resource: "", free: false, conduitKind: "all", radius: CONDUIT_RADIUS_M, group: "" },
  groups: [] as string[],
  /** Rows the server sent past the cap, counted in the card's "n more". */
  hiddenByCap: 0,
  focusOnRender: false,
  returnFocusTo: null as HTMLElement | null,
};

function finderCard(): HTMLElement {
  return mapCard("finder", "finder", closeFinder);
}

function ringPoint(at: { x_m: number; y_m: number }, selected: boolean): void {
  L.circleMarker(latLngOf(at), {
    radius: selected ? 11 : 7,
    color: HIGHLIGHT,
    weight: selected ? 4 : 2,
    fill: false,
    renderer: renderer,
    pane: "finder",
    interactive: false,
  }).addTo(group);
}

function outlineBox(bbox: BboxM): void {
  L.rectangle(boundsOfBbox(bbox), {
    color: HIGHLIGHT,
    weight: 2,
    dashArray: "6 4",
    fill: false,
    renderer: renderer,
    pane: "finder",
    interactive: false,
  }).addTo(group);
}

function latlngs(run: RunRow): L.LatLngTuple[][] {
  return run.lines_m.map(function (points) {
    return points.map(function (point) {
      return latLngOf(point);
    });
  });
}

function drawRunLine(run: RunRow, selected: boolean): L.Polyline {
  return L.polyline(latlngs(run), {
    color: HIGHLIGHT,
    weight: selected ? 5 : 3,
    opacity: 0.8,
    renderer: renderer,
    pane: "finder",
    interactive: false,
  }).addTo(group);
}

function runBounds(run: RunRow): L.LatLngBounds | null {
  const points = ([] as L.LatLngTuple[]).concat.apply([], latlngs(run));
  return points.length ? L.latLngBounds(points) : null;
}

/** Ring every node a field is made of, which only the drawn node dots know the places of. */
function ringFieldMembers(field: FoundField): void {
  const wanted: Record<string, boolean> = {};
  field.members.forEach(function (leaf) {
    wanted[leaf] = true;
  });
  knownNodes().forEach(function (node) {
    if (wanted[node.name]) ringPoint(node, false);
  });
}

function draw(results: FinderResults, selectedIndex: number): L.LatLngBounds | null {
  group.clearLayers();
  let bounds: L.LatLngBounds | null = null;
  function grow(more: L.LatLngBounds | L.LatLngTuple): void {
    if (bounds) bounds.extend(more);
    else bounds = more instanceof L.LatLngBounds ? L.latLngBounds(more.getSouthWest(), more.getNorthEast()) : L.latLngBounds([more, more]);
  }
  if (results.kind === "nodes" || results.kind === "pickups" || results.kind === "sites") {
    (results.rows as { x_m: number; y_m: number }[]).forEach(function (row, i) {
      ringPoint(row, i === selectedIndex);
      grow(latLngOf(row));
    });
  } else if (results.kind === "fields") {
    results.rows.forEach(function (field, i) {
      outlineBox(field.bbox_m);
      if (i === selectedIndex) ringFieldMembers(field);
      grow(boundsOfBbox(field.bbox_m));
    });
  } else {
    results.rows.forEach(function (run, i) {
      drawRunLine(run, i === selectedIndex);
      const runBox = runBounds(run);
      if (runBox) grow(runBox);
    });
  }
  if (!map.hasLayer(group)) group.addTo(map);
  return bounds;
}

function flyTo(results: FinderResults, selectedIndex: number, bounds: L.LatLngBounds | null): void {
  const row = selectedIndex >= 0 ? results.rows[selectedIndex] : undefined;
  if (row && results.kind === "fields") {
    flyToBox((row as FoundField).bbox_m, { maxZoom: POINT_ZOOM });
    return;
  }
  if (row && results.kind === "runs") {
    const runBox = runBounds(row as RunRow);
    if (runBox) flyPadded(runBox.pad(0.2), POINT_ZOOM);
    return;
  }
  if (row) {
    const at = row as { x_m: number; y_m: number };
    flyToPoint(latLngOf(at), Math.max(map.getZoom(), POINT_ZOOM));
    return;
  }
  if (bounds) flyPadded(bounds.pad(ALL_PAD), ALL_ZOOM, FIT_SNAP);
}

function selectionOf(results: FinderResults, i: number): Selection {
  if (results.kind === "nodes") return nodeSelection(results.rows[i]!);
  if (results.kind === "fields") return fieldSelection(results.rows[i]!);
  if (results.kind === "sites") return siteSelection(results.rows[i]!);
  if (results.kind === "runs") return runSelection(results.rows[i]!);
  return pickupSelection(results.rows[i]!);
}

/** Pick one row: ring it boldest, fly to it, and make it the page's selection. */
function selectResult(i: number): void {
  if (!view.results) return;
  view.selectedIndex = i;
  const bounds = draw(view.results, i);
  renderFinder();
  flyTo(view.results, i, bounds);
  if (view.results.kind === "pickups") reveal(["pickup: " + view.results.rows[i]!.category]);
  select(selectionOf(view.results, i));
}

/** One table row of the card: the index of a result. */
interface Listed {
  i: number;
}

function column(key: string, label: string, render: (i: number) => string | HTMLElement, right?: boolean): Column<Listed> {
  return {
    key: key,
    label: label,
    align: right ? "right" : undefined,
    className: right ? "dash-nowrap" : undefined,
    render: function (listed) {
      return render(listed.i);
    },
  };
}

function columns(results: FinderResults): Column<Listed>[] {
  if (results.kind === "nodes") {
    const nodes = results.rows;
    return [
      column("node", WORDS.node, function (i) {
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
  if (results.kind === "fields") {
    const fields = results.rows;
    return [
      column("field", WORDS.field, function (i) {
        return fieldLabel(fields[i]!);
      }),
      column("free", WORDS.free, function (i) {
        return perMin(fields[i]!.free, false);
      }, true),
      column("distance", "away", function (i) {
        return metres(fields[i]!.distance_m);
      }, true),
    ];
  }
  if (results.kind === "sites") {
    const sites = results.rows;
    return [
      column("rank", "rank", function (i) {
        return String(sites[i]!.rank);
      }, true),
      column("region", "region", function (i) {
        return sites[i]!.region || "–";
      }),
      column("score", "score", function (i) {
        return formatNumber(sites[i]!.score, 2);
      }, true),
    ];
  }
  if (results.kind === "runs") {
    const runs = results.rows;
    return [
      column("run", WORDS.run, function (i) {
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
  const pickups = results.rows;
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

function renderResultTable(box: HTMLElement, results: FinderResults): void {
  const total = results.rows.length;
  if (!total) {
    cardLine(box, view.at && results.kind === "nodes" ? "no " + WORDS.node + " within " + count(NEAR_M) + " m" : "nothing found here");
    return;
  }
  const rows: Listed[] = [];
  for (let i = 0; i < Math.min(total, MAX_TABLE_ROWS); i++) rows.push({ i: i });
  const measured = (results.rows as { distance_m?: number | null }[]).some(function (row) {
    return row.distance_m !== null && row.distance_m !== undefined;
  });
  const shownColumns = columns(results).filter(function (shown) {
    return measured || shown.key !== "distance";
  });
  box.appendChild(
    table<Listed>(shownColumns, rows, {
      caption: view.title,
      onRow: function (listed) {
        selectResult(listed.i);
      },
      rowClass: function (listed) {
        return listed.i === view.selectedIndex ? "on" : "";
      },
    })
  );
  const more = Math.max(0, total - MAX_TABLE_ROWS) + view.hiddenByCap;
  if (more) cardLine(box, count(more) + " more");
}

function filterRow(box: HTMLElement): void {
  const row = cardToolbar();
  row.classList.add("finder-filter");
  const filter = view.filter;
  if (view.kind === "nodes") {
    row.appendChild(
      choose("resource", "finder-resource", filter.resource, resourceOptions("any resource", filter.resource), function (value) {
        filter.resource = value;
      })
    );
    row.appendChild(
      toggleButton(WORDS.free, filter.free, function () {
        filter.free = !filter.free;
        fetchPoint(true);
      }, { title: "only nodes with no extractor on them" })
    );
  } else if (view.kind === "conduits") {
    row.appendChild(
      choose("kind", "finder-kind", filter.conduitKind, [["all", "belts and pipes"], ["belt", "belts"], ["pipe", "pipes"]], function (value) {
        filter.conduitKind = value;
      })
    );
    row.appendChild(
      choose("radius", "finder-radius", filter.radius, RADII.map(function (radius): [string, string] { return [radius, "within " + radius + " m"]; }), function (value) {
        filter.radius = value;
      })
    );
  } else {
    row.appendChild(
      choose(
        "pickup kind",
        "finder-group",
        filter.group,
        [["", "every kind"] as [string, string]].concat(
          view.groups.map(function (category): [string, string] {
            return [category, pickupName(category)];
          })
        ),
        function (value) {
          filter.group = value;
        }
      )
    );
  }
  box.appendChild(row);
}

function choose(label: string, candidate: string, value: string, options: [string, string][], change: (value: string) => void): HTMLSelectElement {
  return selectBox(options, value, function (picked) {
    change(picked);
    fetchPoint(true);
  }, { label: label, candidate: candidate });
}

function renderFinder(): void {
  const card = finderCard();
  keepFocus(card, function () {
    fillFinderCard(card);
  });
  if (view.open && view.focusOnRender) {
    view.focusOnRender = false;
    const first = card.querySelector<HTMLElement>(".subtabs-item.on") || card.querySelector<HTMLElement>("tbody tr.on[tabindex]") || card.querySelector<HTMLElement>("tbody tr[tabindex]") || card.querySelector<HTMLElement>(".mapcard-head button");
    if (first) first.focus({ preventScroll: true });
  }
}

function fillFinderCard(card: HTMLElement): void {
  card.textContent = "";
  if (!view.open) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const head = cardTitleBar(view.at ? "Near this point" : "On the map");
  if (view.note) head.title = view.note;
  head.appendChild(button("×", closeFinder, { title: "close the finder", label: "close the finder and clear its rings" }));
  card.appendChild(head);
  cardSubject(card, view.title);
  if (view.at) {
    card.appendChild(
      subTabs(
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
    filterRow(card);
  }
  if (view.error) cardLine(card, view.error, "bad");
  else if (view.results) renderResultTable(card, view.results);
  else if (view.busy) cardLine(card, "finding…");
  if (view.dash) {
    const foot = cardToolbar();
    foot.appendChild(link(view.dash, "open in World"));
    card.appendChild(foot);
  }
}

export function closeFinder(): void {
  const inside = finderCard().contains(document.activeElement);
  const back = view.returnFocusTo;
  view.returnFocusTo = null;
  latest("finder");
  view.open = false;
  view.results = null;
  view.at = null;
  view.ref = "";
  view.selectedIndex = -1;
  view.error = "";
  view.busy = false;
  group.clearLayers();
  renderFinder();
  if (!inside) return;
  if (back && back.isConnected && back.getClientRects().length) back.focus({ preventScroll: true });
  else map.getContainer().focus({ preventScroll: true });
}

/** Open the card for a new search, remembering where the keyboard was so closing can go back. */
function openFinder(title: string, dash: string): void {
  const from = document.activeElement as HTMLElement | null;
  if (!view.open) view.returnFocusTo = from && from !== document.body && !finderCard().contains(from) ? from : null;
  view.focusOnRender = true;
  closeOtherCards("finder");
  makeRoom("trace");
  view.open = true;
  view.title = title;
  view.dash = dash;
  view.note = "";
  view.error = "";
  view.selectedIndex = -1;
  view.hiddenByCap = 0;
  view.world = state.world;
  view.epoch = state.epoch;
}

/** The searched point as the `near` parameter spells it, or "" when there is none. */
function nearParam(): string {
  return view.at ? view.at.x + "," + view.at.y : "";
}

function pointQuery(): { url: ApiUrl; dash: string } {
  const filter = view.filter;
  const here = nearParam();
  if (view.kind === "nodes") {
    return {
      url: worldUrl("/api/world/nodes", {
        view: "nodes",
        source: "near:" + here + "@" + NEAR_M,
        near: here,
        resource: filter.resource,
        status: filter.free ? "free" : "",
      }),
      dash: withQuery("world/nodes", { near: here, resource: filter.resource, status: filter.free ? "free" : "" }),
    };
  }
  if (view.kind === "conduits") {
    const params = { near: here, radius_m: filter.radius, conduit_kind: filter.conduitKind === "all" ? "" : filter.conduitKind };
    return { url: worldUrl("/api/world/conduits", params), dash: withQuery("world/conduits", params) };
  }
  return {
    url: worldUrl("/api/collectibles", { mode: "nearest", near: here, group: filter.group, spoilers: spoilerFlag() }),
    dash: withQuery("world/pickups", { view: "nearest", near: here, group: filter.group }),
  };
}

/** One point search's reply as card rows, plus what it says beside them. */
interface PointReply {
  results: FinderResults;
  /** The conduit search's age note; absent for the other kinds, which leave the note as it is. */
  note?: string;
  /** The pickup categories the group picker offers; absent for the other kinds. */
  groups?: string[];
  hiddenByCap: number;
}

function resultsFromReply(kind: FindKind, data: NodeFindResponse | ConduitsResponse | CollectiblesResponse): PointReply {
  if (kind === "nodes") {
    const nodes = (data as NodeFindResponse).nodes.slice().sort(function (a, b) {
      return (a.distance_m || 0) - (b.distance_m || 0);
    });
    return { results: { kind: "nodes", rows: nodes }, hiddenByCap: 0 };
  }
  if (kind === "conduits") {
    const conduits = data as ConduitsResponse;
    return { results: { kind: "runs", rows: conduits.runs }, note: conduits.age_note, hiddenByCap: 0 };
  }
  const pickups = data as CollectiblesResponse;
  return {
    results: { kind: "pickups", rows: pickups.rows.slice(0, MAX_TABLE_ROWS) },
    groups: pickups.census
      .filter(function (entry) {
        return settingOn("spoilers") || !entry.spoiler;
      })
      .map(function (entry) {
        return entry.category;
      }),
    hiddenByCap: Math.max(0, pickups.rows.length - MAX_TABLE_ROWS),
  };
}

function fetchPoint(fit: boolean): void {
  const kind = view.kind;
  const query = pointQuery();
  const ticket = latest("finder");
  view.dash = query.dash;
  view.busy = true;
  view.error = "";
  renderFinder();
  get<NodeFindResponse | ConduitsResponse | CollectiblesResponse>(query.url)
    .then(function (data) {
      if (!ticket.fresh()) return;
      view.busy = false;
      const reply = resultsFromReply(kind, data);
      view.hiddenByCap = reply.hiddenByCap;
      if (reply.note !== undefined) view.note = reply.note;
      if (reply.groups) view.groups = reply.groups;
      view.results = reply.results;
      view.selectedIndex = -1;
      const bounds = draw(view.results, -1);
      renderFinder();
      if (fit && bounds) flyPadded(bounds.pad(0.15), map.getZoom());
    })
    .catch(function (err) {
      if (!ticket.fresh()) return;
      view.busy = false;
      view.results = null;
      view.error = friendlyError(err);
      group.clearLayers();
      renderFinder();
    });
}

export function startAt(kind: FindKind, x: number, y: number): void {
  openFinder("near " + coords(x, y), "");
  view.at = { x: x, y: y };
  view.kind = kind;
  view.ref = "";
  view.results = null;
  fetchPoint(true);
}

/** List rows another page found, ringed on the map; `selectedIndex` picks one of them. */
export function showRows(results: FinderResults, title: string, dash: string, selectedIndex?: number): void {
  openFinder(title, dash);
  view.at = null;
  view.ref = "";
  view.results = results;
  if (selectedIndex !== undefined && selectedIndex >= 0) {
    selectResult(selectedIndex);
    return;
  }
  view.selectedIndex = -1;
  const bounds = draw(results, -1);
  renderFinder();
  flyTo(results, -1, bounds);
}

function parsePoint(text: string): { x: number; y: number; r: number } | null {
  const match = /^(?:near:)?(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)(?:@(\d+(?:\.\d+)?))?$/.exec(text.trim());
  return match ? { x: +match[1]!, y: +match[2]!, r: match[3] ? +match[3] : 0 } : null;
}

function fetchRef(ref: string): void {
  const ticket = latest("finder");
  const conduit = /^(chain|pipe):/.test(ref);
  const url = conduit
    ? worldUrl("/api/world/conduits", { near: ref, run: ref })
    : worldUrl("/api/world/nodes", { source: ref });
  view.busy = true;
  renderFinder();
  get<NodeFindResponse | ConduitsResponse>(url)
    .then(function (data) {
      if (!ticket.fresh()) return;
      view.busy = false;
      const results: FinderResults = conduit
        ? { kind: "runs", rows: (data as ConduitsResponse).runs.filter(function (run) { return run.id === ref; }) }
        : { kind: "nodes", rows: (data as NodeFindResponse).nodes };
      if (!results.rows.length) {
        view.error = ref + " is not in this save";
        renderFinder();
        return;
      }
      view.results = results;
      view.title = selectionOf(results, 0).label;
      selectResult(0);
    })
    .catch(function (err) {
      if (!ticket.fresh()) return;
      view.busy = false;
      view.error = friendlyError(err);
      renderFinder();
    });
}

export function showRef(ref: string, spot?: { x_m?: number; y_m?: number; label: string }): void {
  const point = parsePoint(ref);
  if (/^(node|chain|pipe):/.test(ref)) {
    openFinder(ref.indexOf("node:") === 0 ? WORDS.node : WORDS.run, "");
    view.at = null;
    view.results = null;
    view.ref = ref;
    fetchRef(ref);
    return;
  }
  const x = point ? point.x : spot ? spot.x_m : undefined;
  const y = point ? point.y : spot ? spot.y_m : undefined;
  if (x === undefined || y === undefined) return;
  openFinder(spot ? spot.label : coords(x, y), "");
  view.at = null;
  view.ref = "";
  view.results = null;
  group.clearLayers();
  ringPoint({ x_m: x, y_m: y }, true);
  if (!map.hasLayer(group)) group.addTo(map);
  renderFinder();
  flyToPoint(latLngOf({ x_m: x, y_m: y }), Math.max(map.getZoom(), POINT_ZOOM));
}

/** The debounce on a vitals-driven refetch. */
let refreshTimer = 0;

function refresh(): void {
  if (!view.open) return;
  if (view.world !== state.world || view.epoch !== state.epoch) {
    closeFinder();
    return;
  }
  if (view.busy || (!view.at && !view.ref)) return;
  clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(function () {
    if (view.at) fetchPoint(false);
    else if (view.ref) fetchRef(view.ref);
  }, 50);
}

function hideSpoilers(): void {
  if (!view.open || !view.results || view.results.kind !== "pickups" || settingOn("spoilers")) return;
  const rows = view.results.rows as { spoiler?: boolean }[];
  if (!rows.some(function (row) { return row.spoiler; })) return;
  if (view.at || view.ref) {
    refresh();
    return;
  }
  view.results = { kind: view.results.kind, rows: rows.filter(function (row) { return !row.spoiler; }) } as FinderResults;
  view.selectedIndex = -1;
  draw(view.results, -1);
  renderFinder();
}

/** A "find" button in a popup: select that point, or search near it. */
function handleFindClick(hit: Element): void {
  const spot = parsePoint(hit.getAttribute(FIND_AT_ATTR) || "");
  const kind = hit.getAttribute(FIND_ATTR);
  map.closePopup();
  if (!spot) return;
  if (kind === "point") {
    closeFinder();
    showPoint(spot.x, spot.y, { label: coords(spot.x, spot.y) });
  } else startAt(kind === "conduits" || kind === "pickups" ? kind : "nodes", spot.x, spot.y);
}

export function listenForFinds(): void {
  onAttributeClick(FIND_ATTR, handleFindClick);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && view.open && !(event.target as Element).closest("input[type=text], input[type=search], textarea")) closeFinder();
  });
  onVitals(refresh);
  onSetting(hideSpoilers);
}
