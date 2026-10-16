/* The production graph of a named factory (its flows tab) or of an unnamed cluster, drawn on
 * the shared canvas (dash/prodgraph/) as cards. One graph at a time; dragged places last for
 * the page session. docs/frontend_kit.md, "Factories". */

import { make } from "../../kit/dom";
import { count } from "../../kit/format";
import { needsAction } from "../machine-states";
import { canvasFrame, newCanvas, settleFrame, useView } from "../prodgraph/canvas";
import { readGraph } from "../prodgraph/model";
import { GRAPH_HINT } from "../graph-frame";
import { requestRender } from "../actions";
import { q, read, type Slot } from "./data";

import type { FactoryGraphResponse } from "../../api/shapes";
import type { CanvasHost, Positions } from "../prodgraph/canvas";
import type { NodeMarks } from "../prodgraph/draw";
import type { Loose, ProdGraph, ProdNode } from "../prodgraph/model";

const canvas = newCanvas("card");
const placed: Record<string, Positions> = {};
const views = new WeakMap<FactoryGraphResponse, ProdGraph>();

export function candidateGraphPath(index: number, token: string): string {
  return q("/api/factories/graph", { candidate: "proposal:" + index, token });
}

export function graphRead(path: string): Slot<FactoryGraphResponse> {
  return read<FactoryGraphResponse>(path);
}

export function viewOf(data: FactoryGraphResponse): ProdGraph {
  let view = views.get(data);
  if (!view) {
    view = readGraph(data as unknown as Loose);
    views.set(data, view);
  }
  return view;
}

/** Each group's machines that need action, as badges, and the worst as the outline tone. */
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

/** The graph's element: the canvas and its key line. `pick` hears a node click. */
export function graphElement(key: string, view: ProdGraph, pick: (node: ProdNode) => void): HTMLElement {
  useView(canvas, view);
  const host: CanvasHost = {
    key,
    positions: () => placed[key] || {},
    marks: () => stateMarks(view),
    pick,
    clear: function () {},
    moved: function (id, at) {
      (placed[key] = placed[key] || {})[id] = [Math.round(at[0]), Math.round(at[1])];
    },
    changed: requestRender,
  };
  const box = make("div", "fgraph");
  const wrap = canvasFrame(canvas, host);
  box.appendChild(wrap);
  const keyLine = make("div", "fg-key");
  keyLine.textContent = "dashed: fed in · card outline: needs action · ring: measured productivity · line width: rate · " + GRAPH_HINT;
  box.appendChild(keyLine);
  settleFrame(canvas, wrap, "");
  return box;
}
