/* Trace on the map: what feeds a machine or a factory, or what it feeds, drawn as a path.
 * See docs/frontend_vision.md §10. */

import { get, latest } from "./api";
import { button, chip, table } from "./dashkit";
import { code, count, esc, make, popup, TRACE_ATTR, TRACE_DIR_ATTR, traceButtons } from "./dom";
import { perMin } from "./format";
import { L } from "./leaflet";
import { flyPadded, map } from "./map";
import { HIGHLIGHT, makeRoom, onVitals } from "./panel";
import { BLOCKED_COLOUR, STOPPED_COLOUR } from "./placements";
import { state } from "./state";
import { tone } from "./states";
import { fail, friendly } from "./toast";
import { counted, W } from "./words";

import type { Row } from "./dom";
import type { TraceMachine, TraceResponse } from "./api-shapes";

type Direction = "up" | "down";

type TraceItem = TraceResponse["items"][number];

type TraceEdge = TraceResponse["edges"][number];

var SHOWN = 10;

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

function card(): HTMLElement {
  var box = document.getElementById("trace");
  if (box) return box;
  box = make("aside");
  box.id = "trace";
  box.hidden = true;
  box.setAttribute("aria-label", "trace");
  document.body.appendChild(box);
  return box;
}

function colour(m: TraceMachine): string {
  var t = tone(m.state, m.actionable);
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
  var rows: Row[] = [
    ["building", m.name],
    ["recipe", m.recipe],
    ["makes", m.makes.length ? rateText(m.makes) : null],
    ["uses", m.uses.length ? rateText(m.uses) : null],
    ["state", m.state + (m.actionable ? " · " + W.needAction : "")],
    ["on path", m.seed ? "traced from here" : counted(m.hops, "hop") + " away"],
    ["id", code(m.instance)],
    ["trace", traceButtons(m.instance)],
  ];
  return popup(rows);
}

function draw(data: TraceResponse): void {
  group.clearLayers();
  data.runs.forEach(function (run) {
    var line = L.polyline(
      run.lines_m.map(function (points) {
        return points.map(function (p) {
          return [-p[1], p[0]] as L.LatLngTuple;
        });
      }),
      { color: HIGHLIGHT, weight: 4, opacity: 0.8, renderer: renderer, pane: "trace" }
    );
    line.bindTooltip(esc(run.medium + " · " + counted(run.pieces, "piece")), { sticky: true });
    line.addTo(group);
  });
  data.machines.forEach(function (m) {
    if (m.x_m === null || m.y_m === null) return;
    var ring = L.circleMarker([-m.y_m, m.x_m], {
      radius: m.seed ? 11 : 7,
      color: colour(m),
      weight: m.seed ? 4 : 3,
      fillColor: colour(m),
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
  var b = data.bbox_m;
  if (!b) return;
  flyPadded(L.latLngBounds([-b[1], b[0]], [-b[3], b[2]]).pad(0.15), TRACE_MAX_ZOOM);
}

function toggle(text: string, title: string, on: boolean, action: () => void): HTMLButtonElement {
  var b = button(text, action, { title: title });
  b.setAttribute("aria-pressed", String(on));
  return b;
}

function line(parent: HTMLElement, text: string, className?: string): HTMLElement {
  var p = make("p", "trace-note" + (className ? " " + className : ""), text);
  parent.appendChild(p);
  return p;
}

function stateCounts(data: TraceResponse): { stopped: number; blocked: number } {
  var out = { stopped: 0, blocked: 0 };
  data.machines.forEach(function (m) {
    if (m.seed) return;
    var t = tone(m.state, m.actionable);
    if (t === "blocked") out.blocked += 1;
    else if (t === "bad") out.stopped += 1;
  });
  return out;
}

function groupName(data: TraceResponse, id: string): string {
  if (id.indexOf("in:") === 0) return "outside the traced set";
  var found = data.groups.filter(function (g) {
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
    rows.slice(0, SHOWN)
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
    rows.slice(0, SHOWN)
  );
}

function render(): void {
  var box = card();
  box.textContent = "";
  if (!view.seed) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  var head = make("div", "trace-head");
  head.appendChild(make("strong", "", view.direction === "up" ? "Supply" : "Output"));
  head.appendChild(
    toggle("↑ supply", "what feeds it", view.direction === "up", function () {
      startTrace(view.seed, "up");
    })
  );
  head.appendChild(
    toggle("↓ output", "what it feeds", view.direction === "down", function () {
      startTrace(view.seed, "down");
    })
  );
  head.appendChild(button("×", clearTrace, { title: "clear the trace", label: "clear the trace" }));
  box.appendChild(head);
  var data = view.data;
  if (view.error) {
    line(box, view.error, "bad");
    return;
  }
  if (!data) {
    line(box, "tracing " + view.seed.replace(/^label:/, "") + "…");
    return;
  }
  var only = data.seeds === 1 ? data.machines.filter(function (m) { return m.seed; })[0] : undefined;
  box.appendChild(make("div", "trace-subject", only ? only.name + (only.recipe ? " · " + only.recipe : "") : data.subject));
  if (data.truncated) {
    line(box, "the walk stopped at its hop limit: this is a floor, more lies beyond it", "bad");
  }
  if (data.ambiguous) {
    line(box, "may over-report a feeder").title =
      "the save has " +
      count(data.ambiguous) +
      " belt or pipe joins that state no direction; the walk takes them both ways, so it can over-report a feeder but never miss one";
  }
  var others = data.machines.length - data.seeds;
  var states = stateCounts(data);
  var chips = make("div", "panel-chips");
  chips.appendChild(chip(counted(others, "machine") + " " + (view.direction === "up" ? "upstream" : "downstream"), "muted"));
  if (states.stopped) chips.appendChild(chip(states.stopped + " " + W.needAction, "bad"));
  if (states.blocked) chips.appendChild(chip(states.blocked + " " + W.blocked, "blocked"));
  chips.appendChild(chip(counted(data.runs.length, "run") + " · depth " + data.deepest, "muted"));
  box.appendChild(chips);
  if (!others) {
    line(
      box,
      "nothing outside this selection " +
        (view.direction === "up" ? "feeds it" : "is fed by it") +
        (data.seeds > 1
          ? "; trace one machine to see the chain inside it"
          : "; its runs reach no other machine")
    );
  }
  if (data.items.length) {
    box.appendChild(make("h4", "trace-h", view.direction === "up" ? "made along the path" : "used along the path"));
    box.appendChild(itemTable(data.items));
    if (data.items.length > SHOWN) line(box, data.items.length - SHOWN + " more");
  }
  var groupIds = new Set(
    data.groups.map(function (g) {
      return g.id;
    })
  );
  var flows = data.edges.filter(function (e) {
    return e.per_min !== null && groupIds.has(e.target);
  });
  flows.sort(function (a, b) {
    return (b.per_min || 0) - (a.per_min || 0);
  });
  if (flows.length) {
    box.appendChild(make("h4", "trace-h", "flows between groups"));
    box.appendChild(flowTable(data, flows));
    if (flows.length > SHOWN) line(box, flows.length - SHOWN + " more");
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
  var ticket = latest("trace");
  view.busy = true;
  var path = ("/api/trace?seed=" + encodeURIComponent(view.seed) + "&direction=" + view.direction) as `/api/trace?${string}`;
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
      view.error = friendly(err);
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
  makeRoom("trace");
  render();
  fetchTrace(true);
}

var pending = 0;

function refresh(): void {
  if (!view.seed) return;
  if (view.world !== state.world || view.epoch !== state.epoch) {
    clearTrace();
    return;
  }
  if (view.busy) return;
  clearTimeout(pending);
  pending = window.setTimeout(function () {
    fetchTrace(false);
  }, 50);
}

export function listenForTraces(): void {
  document.addEventListener(
    "click",
    function (event) {
      var target = event.target as Element | null;
      var hit = target && target.closest ? target.closest("[" + TRACE_ATTR + "]") : null;
      if (!hit) return;
      event.stopPropagation();
      event.preventDefault();
      var dir: Direction = hit.getAttribute(TRACE_DIR_ATTR) === "down" ? "down" : "up";
      map.closePopup();
      startTrace(hit.getAttribute(TRACE_ATTR) || "", dir);
    },
    true
  );
  onVitals(refresh);
}
