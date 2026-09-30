/* The one selected thing the map, the side panel, the dashboard and the status strip share.
 * See docs/frontend_vision.md §2.3 and §16. */

export type SelectionKind = "factory" | "circuit" | "point" | "node" | "field" | "conduit" | "pickup";

export interface Selection {
  kind: SelectionKind;
  key: string;
  label: string;
  x_m?: number;
  y_m?: number;
  ref?: string;
}

var KINDS: SelectionKind[] = ["factory", "circuit", "point", "node", "field", "conduit", "pickup"];

var STORE_KEY = "selection";

function recall(): Selection | null {
  try {
    var s = JSON.parse(sessionStorage.getItem(STORE_KEY) || "null");
    if (!s || KINDS.indexOf(s.kind) < 0 || typeof s.key !== "string" || typeof s.label !== "string") return null;
    var out: Selection = { kind: s.kind, key: s.key, label: s.label };
    if (typeof s.x_m === "number" && typeof s.y_m === "number") {
      out.x_m = s.x_m;
      out.y_m = s.y_m;
    }
    if (typeof s.ref === "string") out.ref = s.ref;
    return out;
  } catch (ignored) {
    return null;
  }
}

function remember(s: Selection | null): void {
  try {
    if (s) sessionStorage.setItem(STORE_KEY, JSON.stringify(s));
    else sessionStorage.removeItem(STORE_KEY);
  } catch (ignored) {
    return;
  }
}

var current: Selection | null = recall();

var listeners: Array<() => void> = [];

function same(a: Selection | null, b: Selection | null): boolean {
  if (!a || !b) return a === b;
  return a.kind === b.kind && a.key === b.key && a.label === b.label;
}

export function selected(): Selection | null {
  return current;
}

export function isSelected(kind: SelectionKind, key: string): boolean {
  return !!current && current.kind === kind && current.key === key;
}

export function selectionRef(s: Selection): string {
  if (s.ref) return s.ref;
  if (s.kind === "factory") return "label:" + s.key;
  if (s.kind === "point") return s.key;
  return "";
}

export function select(next: Selection | null): void {
  if (same(current, next)) return;
  current = next;
  remember(next);
  listeners.forEach(function (listener) {
    listener();
  });
}

export function onSelect(listener: () => void): void {
  listeners.push(listener);
}
