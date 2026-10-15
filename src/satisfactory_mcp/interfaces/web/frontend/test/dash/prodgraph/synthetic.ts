/* A plan-shaped graph of any size for layout tests: inputs, ranked process columns, exports. */

import type { LayoutIn } from "../../../src/dash/prodgraph/layout-input";

export function syntheticGraph(processes: number, seed = 7): LayoutIn {
  let s = seed;
  const rand = function (): number {
    s = (s * 1103515245 + 12345) % 2147483648;
    return s / 2147483648;
  };
  const ranks = 6;
  const nodes: LayoutIn["nodes"] = [];
  const edges: LayoutIn["edges"] = [];
  for (let i = 0; i < 8; i++) nodes.push({ id: "in:" + i, rank: 0, w: 120, h: 36, portY: 18, below: null });
  for (let i = 0; i < processes; i++) {
    const rank = 1 + (i % ranks);
    nodes.push({ id: "p:" + i, rank: rank, w: 52, h: 52, portY: 26, below: { w: 90 + Math.round(rand() * 60), h: 28 } });
  }
  for (let i = 0; i < 6; i++) nodes.push({ id: "ex:" + i, rank: ranks + 1, w: 120, h: 36, portY: 18, below: null });
  const atRank = function (r: number): string[] {
    return nodes
      .filter(function (n) {
        return n.rank === r;
      })
      .map(function (n) {
        return n.id;
      });
  };
  for (let r = 1; r <= ranks + 1; r++) {
    const here = atRank(r);
    const before = atRank(r - 1);
    here.forEach(function (target) {
      const fan = target.startsWith("ex:") ? 1 : 1 + Math.floor(rand() * 2);
      for (let k = 0; k < fan; k++) {
        const source = before[Math.floor(rand() * before.length)]!;
        const id = source + ">" + target + ":" + k;
        if (edges.some((e) => e.id === id)) continue;
        edges.push({ id: id, source: source, target: target, labelW: 80 + Math.round(rand() * 40), labelH: 16 });
      }
    });
  }
  return { nodes: nodes, edges: edges, order: {} };
}
