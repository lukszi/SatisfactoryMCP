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

var current: Selection | null = null;

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
  listeners.forEach(function (listener) {
    listener();
  });
}

export function onSelect(listener: () => void): void {
  listeners.push(listener);
}
