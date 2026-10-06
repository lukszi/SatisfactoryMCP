/* The one selected thing the map, the side panel, the dashboard and the status strip share,
 * shared across tabs too. See docs/frontend_vision.md §2.3 and §16. */

import { createListeners } from "./listeners";

export type SelectionKind = "factory" | "circuit" | "machine" | "point" | "node" | "field" | "conduit" | "pickup";

export interface Selection {
  kind: SelectionKind;
  key: string;
  label: string;
  x_m?: number;
  y_m?: number;
  ref?: string;
}

var KINDS: SelectionKind[] = ["factory", "circuit", "machine", "point", "node", "field", "conduit", "pickup"];

var STORE_KEY = "selection";

function parse(text: string | null): Selection | null {
  try {
    const s = JSON.parse(text || "null");
    if (!s || KINDS.indexOf(s.kind) < 0 || typeof s.key !== "string" || typeof s.label !== "string") return null;
    const out: Selection = { kind: s.kind, key: s.key, label: s.label };
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

function recall(): Selection | null {
  try {
    return parse(localStorage.getItem(STORE_KEY));
  } catch (ignored) {
    return null;
  }
}

function remember(s: Selection | null): void {
  try {
    if (s) localStorage.setItem(STORE_KEY, JSON.stringify(s));
    else localStorage.removeItem(STORE_KEY);
  } catch (ignored) {
    return;
  }
}

var current: Selection | null = recall();

var selectListeners = createListeners();

function same(a: Selection | null, b: Selection | null): boolean {
  if (!a || !b) return a === b;
  return a.kind === b.kind && a.key === b.key && a.label === b.label;
}

function settle(next: Selection | null): void {
  current = next;
  selectListeners.emit();
}

export function selected(): Selection | null {
  return current;
}

export function isSelected(kind: SelectionKind, key: string): boolean {
  return !!current && current.kind === kind && current.key === key;
}

/** A machine's leaf, from a leaf or a full instance path. */
export function machineLeaf(instance: string): string {
  return instance.slice(instance.lastIndexOf(".") + 1);
}

export function machineSelection(instance: string, name: string, x_m: number, y_m: number): Selection {
  const leaf = machineLeaf(instance);
  return { kind: "machine", key: leaf, label: name, x_m: x_m, y_m: y_m, ref: "machine:" + leaf };
}

export function selectionRef(s: Selection): string {
  if (s.ref) return s.ref;
  if (s.kind === "factory") return "label:" + s.key;
  if (s.kind === "machine") return "machine:" + s.key;
  if (s.kind === "point") return s.key;
  return "";
}

export function select(next: Selection | null): void {
  if (same(current, next)) return;
  remember(next);
  settle(next);
}

export function onSelect(listener: () => void): void {
  selectListeners.on(listener);
}

window.addEventListener("storage", function (event) {
  if (event.key !== STORE_KEY && event.key !== null) return;
  const next = event.key === null ? null : parse(event.newValue);
  if (!same(current, next)) settle(next);
});
