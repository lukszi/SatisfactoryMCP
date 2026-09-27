/* Trace on the map: what feeds a machine or a factory, or what it feeds, drawn as a path.
 * See docs/frontend_vision.md §10. */

import { get } from "./api";
import { code, count, make, popup, TRACE_ATTR, TRACE_DIR_ATTR, traceButtons } from "./dom";
import { L } from "./leaflet";
import { map } from "./map";
import { HIGHLIGHT, onVitals } from "./panel";
import { BLOCKED, BLOCKED_COLOUR, STOPPED_COLOUR } from "./placements";
import { state } from "./state";
import { fail, friendly } from "./toast";

import type { Row } from "./dom";
import type { TraceMachine, TraceResponse } from "./api-shapes";

type Direction = "up" | "down";

var SHOWN = 10;

var pane = map.createPane("trace");
pane.style.zIndex = "450";
var renderer = L.canvas({ pane: "trace", padding: 0.5 });
var group = L.layerGroup();

var view = {
  seed: "",
  direction: "up" as Direction,
  world: "",
  busy: false,
  data: null as TraceResponse | null,
  error: "",
  ask: 0,
};

function card(): HTMLElement {
  var box = document.getElementById("trace");
  if (box) return box;
  box = make("aside");
  box.id = "trace";
  box.hidden = true;
  document.body.appendChild(box);
  return box;
}

function colour(m: TraceMachine): string {
  if (m.state === BLOCKED) return BLOCKED_COLOUR;
  return m.actionable ? STOPPED_COLOUR || HIGHLIGHT : HIGHLIGHT;
}

function rateText(rows: { item: string; per_min: number }[]): string {
  return rows
    .map(function (r) {
      return r.item + " " + count(r.per_min) + "/min";
    })
    .join(", ");
}

function machinePopup(m: TraceMachine): string {
  var rows: Row[] = [
    ["building", m.name],
    ["recipe", m.recipe],
    ["makes", m.makes.length ? rateText(m.makes) : null],
    ["uses", m.uses.length ? rateText(m.uses) : null],
    ["state", m.state === BLOCKED ? "blocked — output full, runs again once emptied" : m.state],
    ["on path", m.seed ? "traced from here" : m.hops + " hops away"],
    ["instance", code(m.instance)],
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
    line.bindTooltip((run.ident || run.medium) + " · " + run.pieces + " pieces", { sticky: true });
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
  var bounds = L.latLngBounds([-b[1], b[0]], [-b[3], b[2]]).pad(0.15);
  map.flyToBounds(bounds, { maxZoom: 3 });
}

function button(text: string, title: string, on: boolean, action: () => void): HTMLButtonElement {
  var b = make("button", "trace-btn" + (on ? " on" : ""), text);
  b.type = "button";
  b.title = title;
  b.onclick = function (event) {
    event.stopPropagation();
    action();
  };
  return b;
}

function line(parent: HTMLElement, text: string, className?: string): HTMLElement {
  var p = make("p", "trace-note" + (className ? " " + className : ""), text);
  parent.appendChild(p);
  return p;
}

function stateCounts(data: TraceResponse): { stopped: number; blocked: number; running: number } {
  var out = { stopped: 0, blocked: 0, running: 0 };
  data.machines.forEach(function (m) {
    if (m.seed) return;
    if (m.state === BLOCKED) out.blocked += 1;
    else if (m.actionable) out.stopped += 1;
    else out.running += 1;
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
    button("↑ supply", "what feeds it", view.direction === "up", function () {
      startTrace(view.seed, "up");
    })
  );
  head.appendChild(
    button("↓ output", "what it feeds", view.direction === "down", function () {
      startTrace(view.seed, "down");
    })
  );
  head.appendChild(button("×", "clear the trace", false, clearTrace));
  box.appendChild(head);
  var data = view.data;
  if (view.error) {
    line(box, view.error, "bad");
    return;
  }
  if (!data) {
    line(box, "tracing " + view.seed + "…");
    return;
  }
  var only = data.seeds === 1 ? data.machines.filter(function (m) { return m.seed; })[0] : undefined;
  box.appendChild(make("div", "trace-subject", only ? only.name + (only.recipe ? " · " + only.recipe : "") : data.subject));
  if (data.truncated) {
    line(box, "the walk stopped at its hop limit: this is a floor, more lies beyond it", "bad");
  }
  if (data.ambiguous) {
    line(
      box,
      "the save has " +
        count(data.ambiguous) +
        " belt or pipe joins that state no direction; the walk takes them both ways, so this can over-report a feeder but never miss one",
      "warn"
    );
  }
  var others = data.machines.length - data.seeds;
  var states = stateCounts(data);
  var chips = make("div", "panel-chips");
  chips.appendChild(make("span", "panel-chip", count(others) + " machines " + (view.direction === "up" ? "upstream" : "downstream")));
  if (states.stopped) chips.appendChild(make("span", "panel-chip bad", states.stopped + " stopped"));
  if (states.blocked) chips.appendChild(make("span", "panel-chip blocked", states.blocked + " blocked"));
  chips.appendChild(make("span", "panel-chip", count(data.runs.length) + " runs · depth " + data.deepest));
  box.appendChild(chips);
  if (!others) {
    line(
      box,
      "nothing outside this selection " +
        (view.direction === "up" ? "feeds it" : "is fed by it") +
        (data.seeds > 1
          ? " — trace one machine to see the chain inside it"
          : " — its runs reach no other machine")
    );
  }
  if (data.items.length) {
    box.appendChild(make("h4", "trace-h", view.direction === "up" ? "made along the path" : "used along the path"));
    var items = make("ul", "trace-list");
    data.items.slice(0, SHOWN).forEach(function (r) {
      var li = make("li");
      li.appendChild(make("span", "trace-item", r.item));
      li.appendChild(make("span", "trace-rate", count(r.per_min) + "/min"));
      items.appendChild(li);
    });
    if (data.items.length > SHOWN) items.appendChild(make("li", "trace-more", data.items.length - SHOWN + " more"));
    box.appendChild(items);
  }
  var flows = data.edges.filter(function (e) {
    return e.per_min !== null && e.target.indexOf(":") < 0 && e.target.indexOf("|") > 0;
  });
  flows.sort(function (a, b) {
    return (b.per_min || 0) - (a.per_min || 0);
  });
  if (flows.length) {
    box.appendChild(make("h4", "trace-h", "flows, nameplate"));
    var list = make("ul", "trace-list");
    flows.slice(0, SHOWN).forEach(function (e) {
      var li = make("li");
      li.appendChild(make("span", "trace-item", e.item + " " + count(e.per_min || 0) + "/min"));
      li.appendChild(make("span", "trace-flow", groupName(data!, e.source) + " → " + groupName(data!, e.target)));
      list.appendChild(li);
    });
    if (flows.length > SHOWN) list.appendChild(make("li", "trace-more", flows.length - SHOWN + " more"));
    box.appendChild(list);
  }
  line(box, "rates are nameplate at each machine's clock; ring colour is the machine's state");
}

export function clearTrace(): void {
  view.seed = "";
  view.data = null;
  view.error = "";
  view.ask += 1;
  group.clearLayers();
  render();
}

function fetchTrace(flyAfter: boolean): void {
  var ask = ++view.ask;
  view.busy = true;
  var path = ("/api/trace?seed=" + encodeURIComponent(view.seed) + "&direction=" + view.direction) as `/api/trace?${string}`;
  get<TraceResponse>(path)
    .then(function (data) {
      if (ask !== view.ask) return;
      view.busy = false;
      view.data = data;
      view.error = "";
      draw(data);
      render();
      if (flyAfter) fly(data);
    })
    .catch(function (err) {
      if (ask !== view.ask) return;
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
  view.data = null;
  view.error = "";
  render();
  fetchTrace(true);
}

var pending = 0;

function refresh(): void {
  if (!view.seed || view.busy) return;
  if (view.world !== state.world) {
    clearTrace();
    return;
  }
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
