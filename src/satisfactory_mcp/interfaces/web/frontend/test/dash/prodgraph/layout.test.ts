import ELK from "elkjs/lib/elk.bundled.js";
import { describe, expect, it } from "vitest";

import { columnLayout, completed, placeNodes } from "../../../src/dash/prodgraph/layout";
import { elkGraph, ordered, readElk } from "../../../src/dash/prodgraph/layout-input";
import { syntheticGraph } from "./synthetic";

import type { Layout } from "../../../src/dash/prodgraph/layout-input";

function overlapping(layout: Layout): string[] {
  const ids = Object.keys(layout.boxes);
  const out: string[] = [];
  ids.forEach(function (a, i) {
    ids.slice(i + 1).forEach(function (b) {
      const p = layout.boxes[a]!;
      const q = layout.boxes[b]!;
      if (p.x < q.x + q.w && q.x < p.x + p.w && p.y < q.y + q.h && q.y < p.y + p.h) out.push(a + "/" + b);
    });
  });
  return out;
}

describe("the ELK graph", () => {
  it("runs left to right with fixed west and east ports and partitions by rank", () => {
    const graph = elkGraph(syntheticGraph(4));
    expect(graph.layoutOptions!["elk.direction"]).toBe("RIGHT");
    expect(graph.layoutOptions!["elk.edgeRouting"]).toBe("ORTHOGONAL");
    const p = graph.children!.find((c) => c.id === "p:0")!;
    expect(p.layoutOptions!["elk.portConstraints"]).toBe("FIXED_POS");
    expect(p.layoutOptions!["elk.partitioning.partition"]).toBe("1");
    expect(p.ports!.map((q) => q.layoutOptions!["elk.port.side"])).toEqual(["WEST", "EAST"]);
    expect(graph.edges![0]!.sources[0]!.endsWith("|out")).toBe(true);
  });

  it("orders each column by the last layout before the model order", () => {
    const input = syntheticGraph(8);
    input.order = { "p:6": 10, "p:0": 500 };
    const ids = ordered(input).map((n) => n.id);
    expect(ids.indexOf("p:6")).toBeLessThan(ids.indexOf("p:0"));
  });
});

describe("layout of a 100-process plan", () => {
  it("places every node without overlap and routes every edge", async () => {
    const input = syntheticGraph(100);
    const elk = new ELK();
    const t0 = performance.now();
    const layout = completed(input, readElk(await elk.layout(elkGraph(input))));
    const ms = performance.now() - t0;
    console.log("ELK layout, " + input.nodes.length + " nodes, " + input.edges.length + " edges: " + ms.toFixed(0) + " ms");
    expect(overlapping(layout)).toEqual([]);
    input.edges.forEach((e) => expect(layout.routes[e.id]?.points.length).toBeGreaterThan(1));
    const a = layout.boxes["in:0"]!;
    const b = layout.boxes["ex:0"]!;
    expect(a.x).toBeLessThan(b.x);
  }, 60000);

  it("gives the same layout for the same input", async () => {
    const elk = new ELK();
    const one = readElk(await elk.layout(elkGraph(syntheticGraph(30))));
    const two = readElk(await elk.layout(elkGraph(syntheticGraph(30))));
    expect(two).toEqual(one);
  }, 60000);

  it("has a column fallback that routes everything", () => {
    const input = syntheticGraph(100);
    const t0 = performance.now();
    const layout = columnLayout(input);
    console.log("column fallback, routed by ours: " + (performance.now() - t0).toFixed(0) + " ms");
    expect(overlapping(layout)).toEqual([]);
    expect(Object.keys(layout.routes).length).toBe(input.edges.length);
  });

  it("re-routes only the edges of a moved node", () => {
    const input = syntheticGraph(20);
    const base = columnLayout(input);
    const moved = placeNodes(input, base, { "p:3": [900, 900] });
    expect(moved.boxes["p:3"]).toMatchObject({ x: 900, y: 900 });
    input.edges.forEach((e) => {
      const touches = e.source === "p:3" || e.target === "p:3";
      if (!touches) expect(moved.routes[e.id]).toBe(base.routes[e.id]);
      else expect(moved.routes[e.id]).not.toBe(base.routes[e.id]);
    });
    expect(moved.width).toBeGreaterThan(900);
  });
});
