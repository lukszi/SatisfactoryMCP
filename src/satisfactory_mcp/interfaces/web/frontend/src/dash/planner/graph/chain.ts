/* A node's whole chain: everything that feeds it, everything it feeds, and the edges walked to
 * get there. Edges between two chain nodes that neither walk used stay out, so a sibling's
 * input is not lit just because both ends are. */

export interface ChainEdge {
  id: string;
  source: string;
  target: string;
}

export interface Chain {
  nodes: Set<string>;
  edges: Set<string>;
}

function walk(start: string, edges: ChainEdge[], forward: boolean, chain: Chain): void {
  const next: Record<string, ChainEdge[]> = {};
  edges.forEach(function (edge) {
    const from = forward ? edge.source : edge.target;
    (next[from] = next[from] || []).push(edge);
  });
  const seen = new Set<string>([start]);
  const queue = [start];
  while (queue.length) {
    const at = queue.shift()!;
    (next[at] || []).forEach(function (edge) {
      const to = forward ? edge.target : edge.source;
      chain.edges.add(edge.id);
      if (seen.has(to)) return;
      seen.add(to);
      chain.nodes.add(to);
      queue.push(to);
    });
  }
}

export function chainOf(id: string, edges: ChainEdge[]): Chain {
  const chain: Chain = { nodes: new Set([id]), edges: new Set() };
  walk(id, edges, true, chain);
  walk(id, edges, false, chain);
  return chain;
}
