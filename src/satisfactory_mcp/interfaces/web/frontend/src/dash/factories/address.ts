/* A factory page's address, `factories/<name>[/<aspect>][?floor=<n|all>]`. Pure. */

export const ASPECTS: [string, string][] = [
  ["", "overview"],
  ["flows", "flows"],
  ["machines", "machines"],
  ["power", "power"],
  ["nodes", "nodes"],
  ["links", "links"],
  ["floors", "floors"],
  ["sites", "sites"],
];

export interface FactoryAddress {
  name: string;
  aspect: string;
  /** The floor asked for: an ordinal, "all", or "" for the default. */
  floor: string;
}

function isAspect(id: string): boolean {
  return !!id && ASPECTS.some((a) => a[0] === id);
}

const FLOOR = /\?floor=(all|-?\d+)$/;

/* A name that itself ends in "/flows" stays whole when `known` says it is a factory. */
export function factoryAddress(subject: string, known?: (name: string) => boolean): FactoryAddress {
  const q = FLOOR.exec(subject);
  const floor = q ? q[1]! : "";
  const rest = q ? subject.slice(0, q.index) : subject;
  const cut = rest.lastIndexOf("/");
  if (cut > 0) {
    const tail = rest.slice(cut + 1);
    if (isAspect(tail) && !known?.(rest)) return { name: rest.slice(0, cut), aspect: tail, floor };
  }
  return { name: rest, aspect: "", floor };
}

export function factoryDash(name: string, aspect: string, floor?: string): string {
  return "factories/" + name + (aspect ? "/" + aspect : "") + (floor ? "?floor=" + floor : "");
}
