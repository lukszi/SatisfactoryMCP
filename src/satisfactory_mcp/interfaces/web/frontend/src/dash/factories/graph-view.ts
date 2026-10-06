/* The Factories tab's production graph card: one graph open at a time, of a named factory or
 * of an unnamed cluster. */

import { get, latest } from "../../api/client";
import { appendNote, error, loading } from "../../kit/dashkit";
import { count, flow, pct } from "../../kit/format";
import { showBox } from "../../map/map-highlight";
import { state } from "../../app/state";
import { drawGraph, graphCardFrame, GRAPH_HINT, stateLine } from "../graph";
import { leaveDashThen, requestRender } from "../actions";

import type { FactoryGraphResponse, GraphNode } from "../../api/shapes";

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
  drawn: null as HTMLElement | null,
};

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
  graphView.drawn = null;
}

export function resetGraphOnNewEpoch(): void {
  if (graphView.source && graphView.epoch !== state.epoch) resetGraph();
}

function nodeTip(node: GraphNode): string {
  const lines = [node.label + (node.detail ? " · " + node.detail : "")];
  if (node.kind === "group") {
    lines.push(stateLine(node) + (node.clock !== null ? " · clock " + pct(node.clock) : ""));
    node.makes.forEach(function (made) {
      lines.push("makes " + flow(made.name, made.per_min) + (made.to.length ? " → " + made.to.join(", ") : ""));
    });
    lines.push("click: show these machines on the map");
  }
  if (node.kind === "sink") lines.push("what reaches the AWESOME Sink is sunk, not a product");
  return lines.join("\n");
}

function showGroupOnMap(node: GraphNode): void {
  const box = node.bbox_m;
  if (box) {
    leaveDashThen(function () {
      showBox(box);
    });
  }
}

export function openGraph(source: GraphSource, subject: string, title: string, token?: string): void {
  const ticket = latest("graph");
  graphView.source = source;
  graphView.subject = subject;
  graphView.title = title;
  graphView.token = token || "";
  graphView.epoch = state.epoch;
  graphView.busy = true;
  graphView.failure = null;
  graphView.data = null;
  graphView.drawn = null;
  requestRender();
  get<FactoryGraphResponse>(graphPath(source, subject, token))
    .then(function (data) {
      if (!ticket.fresh()) return;
      graphView.data = data;
      graphView.drawn = data.nodes.length ? drawGraph(data, nodeTip, showGroupOnMap) : null;
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

export function renderGraphCard(parent: HTMLElement): void {
  const data = graphView.data;
  const card = graphCardFrame("production graph · " + graphView.title, true, graphView.source === "candidate" ? closeGraph : undefined);
  if (graphView.busy) loading(card, "the graph");
  else if (graphView.failure) {
    const again = { source: graphView.source, subject: graphView.subject, title: graphView.title, token: graphView.token };
    error(card, "the graph", graphView.failure, function () {
      if (again.source) openGraph(again.source, again.subject, again.title, again.token);
    });
  } else if (data && !data.nodes.length) {
    appendNote(card, "nothing to draw");
  } else if (graphView.drawn && data) {
    card.appendChild(graphView.drawn);
    const lead = "nameplate rates split over each item's producers by share" + (data.buffers ? " · " + count(data.buffers) + " boxes walked through" : "");
    appendNote(card, lead + " · " + GRAPH_HINT + ", click a group for the map");
  }
  parent.appendChild(card);
}
