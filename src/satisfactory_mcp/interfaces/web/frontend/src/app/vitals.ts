/* Factory health and the power circuits as the last reads left them. The side panel fetches
 * both and is the only writer; every other view reads here, so none of them imports the panel. */

import type { CircuitsResponse, FactoryHealthResponse } from "../api/shapes";

export interface Vitals {
  health: FactoryHealthResponse | null;
  healthError: string;
  circuits: CircuitsResponse | null;
  circuitsError: string;
}

var store: Vitals = { health: null, healthError: "", circuits: null, circuitsError: "" };

var listeners: Array<() => void> = [];

export function vitals(): Vitals {
  return store;
}

export function onVitals(listener: () => void): void {
  listeners.push(listener);
}

export function notifyVitals(): void {
  listeners.forEach(function (listener) {
    listener();
  });
}
