/* The Factories tab's production graph card: one graph open at a time, of a named factory or of
 * an unnamed cluster, drawn on the shared canvas (dash/prodgraph/) like a plan's. Dragged places
 * last for the page session only; the layout keeps its order per factory. See
 * docs/frontend_vision.md §9.8. */

import { get, latest } from "../../api/client";
import { appendNote, button, error, loading } from "../../kit/dashkit";
import { count } from "../../kit/format";
import { make } from "../../kit/dom";
import { showBox } from "../../map/map-highlight";
import { state } from "../../app/state";
import { graphCardFrame, GRAPH_HINT } from "../graph-frame";
import { canvasFrame, newCanvas, settleFrame, useView } from "../prodgraph/canvas";
import { tipLines } from "../prodgraph/interact";
import { renderLists } from "../prodgraph/lists";
import { nodeForPick, readGraph } from "../prodgraph/model";
import { leaveDashThen, requestRender } from "../actions";
import { needsAction } from "../machine-states";

import type { FactoryGraphResponse } from "../../api/shapes";
import type { CanvasHost, Positions } from "../prodgraph/canvas";
import type { NodeMarks } from "../prodgraph/draw";
import type { Loose, ProdGraph, ProdNode } from "../prodgraph/model";

export type GraphSource = "factory" | "candidate";

export const graphView = {
  source: "" as GraphSource | "",
  subject: "",
  title: "",
  token: "",
  epoch: -1,
  busy: false,
  failure: null as unknown,
  data: null as FactoryGraphResponse | null,
  view: null as ProdGraph | null,
  picked: "",
};

const canvas = newCanvas();
/** Dragged places per graph key, for this page session. */
const placed: Record<string, Positions> = {};

function graphPath(source: GraphSource, subject: string, token?: string): `/api/factories/graph?${string}` {
  const query =
    source === "factory"
      ? "factory=" + encodeURIComponent(subject)
      : "candidate=" + encodeURIComponent("proposal:" + subject) + "&token=" + encodeURIComponent(token || "");
  return ("/api/factories/graph?" + query) as `/api/factories/graph?${string}`;
}

function resetGraph(): void {
  latest("graph");
  graphView.source = "";
  graphView.subject = "";
  graphView.title = "";
  graphView.busy = false;
  graphView.failure = null;
  graphView.data = null;
  graphView.view = null;
  graphView.picked = "";
}

export function resetGraphOnNewEpoch(): void {
  if (graphView.source && graphView.epoch !== state.epoch) resetGraph();
}

function graphKey(): string {
  return graphView.source + ":" + graphView.subject;
}

/** Each group's machines that need action, as badges, and the worst of them as the outline. */
export function stateMarks(view: ProdGraph): NodeMarks {
  const marks: NodeMarks = { badges: {}, flash: [], tones: {} };
  view.nodes.forEach(function (node) {
    const words: string[] = [];
    let tone = "";
    Object.keys(node.states).forEach(function (s) {
      if (!needsAction(s)) return;
      words.push(count(node.states[s]!) + " " + s);
      if (s !== "blocked") tone = "bad";
      else if (!tone) tone = "blocked";
    });
    if (words.length) marks.badges[node.id] = words;
    if (tone) marks.tones![node.id] = tone;
  });
  return marks;
}

function host(view: ProdGraph): CanvasHost {
  const key = graphKey();
  return {
    key: key,
    positions: function () {
      return placed[key] || {};
    },
    marks: function () {
      return stateMarks(view);
    },
    pick: function (node) {
      graphView.picked = node.id;
      requestRender();
    },
    clear: function () {
      if (!graphView.picked) return;
      graphView.picked = "";
      requestRender();
    },
    moved: function (id, at) {
      (placed[key] = placed[key] || {})[id] = [Math.round(at[0]), Math.round(at[1])];
    },
    changed: requestRender,
  };
}

export function openGraph(source: GraphSource, subject: string, title: string, token?: string): void {
  resetGraph();
  const ticket = latest("graph");
  graphView.source = source;
  graphView.subject = subject;
  graphView.title = title;
  graphView.token = token || "";
  graphView.epoch = state.epoch;
  graphView.busy = true;
  requestRender();
  get<FactoryGraphResponse>(graphPath(source, subject, token))
    .then(function (data) {
      if (!ticket.fresh()) return;
      graphView.data = data;
      graphView.view = readGraph(data as unknown as Loose);
    })
    .catch(function (failure) {
      if (!ticket.fresh()) return;
      graphView.failure = failure;
    })
    .then(function () {
      if (!ticket.fresh()) return;
      graphView.busy = false;
      requestRender();
    });
}

export function closeGraph(): void {
  resetGraph();
  requestRender();
}

function tools(): HTMLElement {
  const bar = make("div", "dash-acts pg-tools");
  const key = graphKey();
  if (placed[key] && Object.keys(placed[key]!).length) {
    bar.appendChild(
      button(
        "reset layout",
        function () {
          delete placed[key];
          canvas.drawn = "";
          requestRender();
        },
        { title: "put every moved node back where the layout puts it" }
      )
    );
  }
  return bar;
}

function nodeCard(parent: HTMLElement, view: ProdGraph, node: ProdNode): void {
  const card = make("section", "dash-card plan-node");
  card.setAttribute("aria-label", "picked: " + node.label);
  const lines = tipLines(view, node);
  const title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", lines[0]!));
  card.appendChild(title);
  lines.slice(1).forEach(function (line) {
    card.appendChild(make("p", "dash-sub", line));
  });
  const box = node.bbox;
  if (box) {
    const acts = make("div", "dash-acts");
    acts.appendChild(
      button(
        "show on map",
        function () {
          leaveDashThen(function () {
            showBox(box);
          });
        },
        { title: "fly the map to these machines and outline them" }
      )
    );
    card.appendChild(acts);
  }
  parent.appendChild(card);
}

function drawGraph(card: HTMLElement, data: FactoryGraphResponse, view: ProdGraph): void {
  useView(canvas, view);
  card.appendChild(tools());
  const wrap = canvasFrame(canvas, host(view));
  card.appendChild(wrap);
  const lead = "nameplate rates at the saved clocks, split over each item's producers by share; rings are measured productivity" + (data.buffers ? " · " + count(data.buffers) + " boxes walked through" : "");
  appendNote(card, lead + " · click a node for its chain · drag a node to place it (this session) · " + GRAPH_HINT);
  const picked = nodeForPick(view, graphView.picked);
  settleFrame(canvas, wrap, picked ? picked.id : "");
  if (picked) nodeCard(card, view, picked);
  const power = { draw: data.mw_draw, made: data.mw_generated, net: data.mw_generated - data.mw_draw };
  renderLists(card, view, power, requestRender);
}

export function renderGraphCard(parent: HTMLElement): void {
  const data = graphView.data;
  const view = graphView.view;
  const card = graphCardFrame("production graph · " + graphView.title, true, graphView.source === "candidate" ? closeGraph : undefined);
  if (graphView.busy) loading(card, "the graph");
  else if (graphView.failure) {
    const again = { source: graphView.source, subject: graphView.subject, title: graphView.title, token: graphView.token };
    error(card, "the graph", graphView.failure, function () {
      if (again.source) openGraph(again.source, again.subject, again.title, again.token);
    });
  } else if (data && view && !view.nodes.length) {
    appendNote(card, "nothing to draw");
  } else if (data && view) {
    drawGraph(card, data, view);
  }
  parent.appendChild(card);
}
