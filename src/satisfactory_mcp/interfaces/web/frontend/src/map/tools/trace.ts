/* Trace on the map: what feeds a machine or a factory, or what it feeds, drawn as a path.
 * See docs/frontend_vision.md §10. */

import { get, latest } from "../../api/client";
import { button, chip, table, toggleButton } from "../../kit/dashkit";
import { code, esc, make, onAttributeClick, popup, TRACE_ATTR, TRACE_DIR_ATTR, traceButtons } from "../../kit/dom";
import { count, perMin } from "../../kit/format";
import { L } from "../leaflet";
import { boundsOfBbox, flyPadded, latLngOf, map } from "../map";
import { cardLine, cardSectionHeading, cardSubject, cardTitleBar, closeOtherCards, mapCard } from "../mapcard";
import { makeRoom } from "../panel";
import { HIGHLIGHT } from "../map-highlight";
import { onVitals } from "../../app/vitals";
import { BLOCKED_COLOUR, STOPPED_COLOUR } from "../drawn/placements";
import { state } from "../../app/state";
import { tone } from "../../dash/machine-states";
import { fail, friendlyError } from "../../kit/toast";
import { counted, WORDS } from "../../kit/words";

import type { Row } from "../../kit/dom";
import type { TraceMachine, TraceResponse } from "../../api/shapes";

type Direction = "up" | "down";

type TraceItem = TraceResponse["items"][number];

type TraceEdge = TraceResponse["edges"][number];

/** How many rows each table in the card lists; the rest are counted. */
var MAX_TABLE_ROWS = 10;

var TRACE_MAX_ZOOM = 2;

var RATE_TITLE = "nameplate rate at each machine's clock";

var pane = map.createPane("trace");
pane.style.zIndex = "450";
pane.style.pointerEvents = "none";
var renderer = L.svg({ pane: "trace", padding: 0.5 });
var group = L.layerGroup();

var view = {
  seed: "",
  direction: "up" as Direction,
  world: "",
  epoch: 0,
  busy: false,
  data: null as TraceResponse | null,
  error: "",
};

function traceCard(): HTMLElement {
  return mapCard("trace", "trace", clearTrace);
}

function machineRingColour(m: TraceMachine): string {
  const t = tone(m.state, m.actionable);
  return t === "blocked" ? BLOCKED_COLOUR : t === "bad" ? STOPPED_COLOUR : HIGHLIGHT;
}

function rateText(rows: { item: string; per_min: number }[]): string {
  return rows
    .map(function (r) {
      return r.item + " " + perMin(r.per_min);
    })
    .join(", ");
}

function machinePopup(m: TraceMachine): string {
  const rows: Row[] = [
    ["building", m.name],
    ["recipe", m.recipe],
    ["makes", m.makes.length ? rateText(m.makes) : null],
    ["uses", m.uses.length ? rateText(m.uses) : null],
    ["state", m.state + (m.actionable ? " · " + WORDS.needAction : "")],
    ["on path", m.seed ? "traced from here" : counted(m.hops, "hop") + " away"],
    ["id", code(m.instance)],
    ["trace", traceButtons(m.instance)],
  ];
  return popup(rows);
}

function draw(data: TraceResponse): void {
  group.clearLayers();
  data.runs.forEach(function (run) {
    const line = L.polyline(
      run.lines_m.map(function (points) {
        return points.map(function (p) {
          return latLngOf(p);
        });
      }),
      { color: HIGHLIGHT, weight: 4, opacity: 0.8, renderer: renderer, pane: "trace" }
    );
    line.bindTooltip(esc(run.medium + " · " + counted(run.pieces, "piece")), { sticky: true });
    line.addTo(group);
  });
  data.machines.forEach(function (m) {
    if (m.x_m === null || m.y_m === null) return;
    const ring = L.circleMarker(latLngOf([m.x_m, m.y_m]), {
      radius: m.seed ? 11 : 7,
      color: machineRingColour(m),
      weight: m.seed ? 4 : 3,
      fillColor: machineRingColour(m),
      fillOpacity: m.actionable ? 0.45 : 0.2,
      renderer: renderer,
      pane: "trace",
    });
    ring.bindPopup(machinePopup(m));
    ring.addTo(group);
  });
  if (!map.hasLayer(group)) group.addTo(map);
}

function fly(data: TraceResponse): void {
  const b = data.bbox_m;
  if (!b) return;
  flyPadded(boundsOfBbox(b).pad(0.15), TRACE_MAX_ZOOM);
}

function stateCounts(data: TraceResponse): { stopped: number; blocked: number } {
  const out = { stopped: 0, blocked: 0 };
  data.machines.forEach(function (m) {
    if (m.seed) return;
    const t = tone(m.state, m.actionable);
    if (t === "blocked") out.blocked += 1;
    else if (t === "bad") out.stopped += 1;
  });
  return out;
}

function groupName(data: TraceResponse, id: string): string {
  if (id.indexOf("in:") === 0) return "outside the traced set";
  const found = data.groups.filter(function (g) {
    return g.id === id;
  })[0];
  return found ? found.label + " (" + found.detail + ")" : id;
}

function itemTable(rows: TraceItem[]): HTMLElement {
  return table<TraceItem>(
    [
      {
        key: "item",
        label: "item",
        render: function (r) {
          return r.item;
        },
      },
      {
        key: "rate",
        label: "per min",
        align: "right",
        title: RATE_TITLE,
        render: function (r) {
          return perMin(r.per_min, false);
        },
      },
    ],
    rows.slice(0, MAX_TABLE_ROWS)
  );
}

function flowTable(data: TraceResponse, rows: TraceEdge[]): HTMLElement {
  return table<TraceEdge>(
    [
      {
        key: "item",
        label: "item",
        render: function (e) {
          return e.item;
        },
      },
      {
        key: "rate",
        label: "per min",
        align: "right",
        title: RATE_TITLE,
        render: function (e) {
          return perMin(e.per_min || 0, false);
        },
      },
      {
        key: "path",
        label: "from → to",
        render: function (e) {
          return groupName(data, e.source) + " → " + groupName(data, e.target);
        },
      },
    ],
    rows.slice(0, MAX_TABLE_ROWS)
  );
}

function render(): void {
  const box = traceCard();
  box.textContent = "";
  if (!view.seed) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const head = cardTitleBar(view.direction === "up" ? "Supply" : "Output");
  head.appendChild(
    toggleButton("↑ supply", view.direction === "up", function () {
      startTrace(view.seed, "up");
    }, { title: "what feeds it" })
  );
  head.appendChild(
    toggleButton("↓ output", view.direction === "down", function () {
      startTrace(view.seed, "down");
    }, { title: "what it feeds" })
  );
  head.appendChild(button("×", clearTrace, { title: "clear the trace", label: "clear the trace" }));
  box.appendChild(head);
  const data = view.data;
  if (view.error) {
    cardLine(box, view.error, "bad");
    return;
  }
  if (!data) {
    cardLine(box, "tracing " + view.seed.replace(/^label:/, "") + "…");
    return;
  }
  const only = data.seeds === 1 ? data.machines.filter(function (m) { return m.seed; })[0] : undefined;
  cardSubject(box, only ? only.name + (only.recipe ? " · " + only.recipe : "") : data.subject);
  if (data.truncated) {
    cardLine(box, "the walk stopped at its hop limit: this is a floor, more lies beyond it", "blocked");
  }
  if (data.ambiguous) {
    cardLine(box, "may over-report a feeder").title =
      "the save has " +
      count(data.ambiguous) +
      " belt or pipe joins that state no direction; the walk takes them both ways, so it can over-report a feeder but never miss one";
  }
  const others = data.machines.length - data.seeds;
  const states = stateCounts(data);
  const chips = make("div", "panel-chips");
  chips.appendChild(chip(counted(others, "machine") + " " + (view.direction === "up" ? "upstream" : "downstream"), "muted"));
  if (states.stopped) chips.appendChild(chip(states.stopped + " " + WORDS.needAction, "bad"));
  if (states.blocked) chips.appendChild(chip(states.blocked + " " + WORDS.blocked, "blocked"));
  chips.appendChild(chip(counted(data.runs.length, "run") + " · depth " + data.deepest, "muted"));
  box.appendChild(chips);
  if (!others) {
    cardLine(
      box,
      "nothing outside this selection " +
        (view.direction === "up" ? "feeds it" : "is fed by it") +
        (data.seeds > 1
          ? "; trace one machine to see the chain inside it"
          : "; its runs reach no other machine")
    );
  }
  if (data.items.length) {
    cardSectionHeading(box, view.direction === "up" ? "made along the path" : "used along the path");
    box.appendChild(itemTable(data.items));
    if (data.items.length > MAX_TABLE_ROWS) cardLine(box, data.items.length - MAX_TABLE_ROWS + " more");
  }
  const groupIds = new Set(
    data.groups.map(function (g) {
      return g.id;
    })
  );
  const flows = data.edges.filter(function (e) {
    return e.per_min !== null && groupIds.has(e.target);
  });
  flows.sort(function (a, b) {
    return (b.per_min || 0) - (a.per_min || 0);
  });
  if (flows.length) {
    cardSectionHeading(box, "flows between groups");
    box.appendChild(flowTable(data, flows));
    if (flows.length > MAX_TABLE_ROWS) cardLine(box, flows.length - MAX_TABLE_ROWS + " more");
  }
}

export function clearTrace(): void {
  view.seed = "";
  view.data = null;
  view.error = "";
  view.busy = false;
  latest("trace");
  group.clearLayers();
  render();
}

function fetchTrace(flyAfter: boolean): void {
  const ticket = latest("trace");
  view.busy = true;
  const path = ("/api/trace?seed=" + encodeURIComponent(view.seed) + "&direction=" + view.direction) as `/api/trace?${string}`;
  get<TraceResponse>(path)
    .then(function (data) {
      if (!ticket.fresh()) return;
      view.busy = false;
      view.data = data;
      view.error = "";
      draw(data);
      render();
      if (flyAfter) fly(data);
    })
    .catch(function (err) {
      if (!ticket.fresh()) return;
      view.busy = false;
      view.data = null;
      view.error = friendlyError(err);
      group.clearLayers();
      render();
      fail("trace: " + view.error);
    });
}

export function startTrace(seed: string, direction: Direction): void {
  view.seed = seed;
  view.direction = direction;
  view.world = state.world;
  view.epoch = state.epoch;
  view.data = null;
  view.error = "";
  closeOtherCards("trace");
  makeRoom("trace");
  render();
  fetchTrace(true);
}

/** The debounce on a vitals-driven refetch. */
var refreshTimer = 0;

function refresh(): void {
  if (!view.seed) return;
  if (view.world !== state.world || view.epoch !== state.epoch) {
    clearTrace();
    return;
  }
  if (view.busy) return;
  clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(function () {
    fetchTrace(false);
  }, 50);
}

export function listenForTraces(): void {
  onAttributeClick(TRACE_ATTR, function (hit) {
    const direction: Direction = hit.getAttribute(TRACE_DIR_ATTR) === "down" ? "down" : "up";
    map.closePopup();
    startTrace(hit.getAttribute(TRACE_ATTR) || "", direction);
  });
  onVitals(refresh);
}
