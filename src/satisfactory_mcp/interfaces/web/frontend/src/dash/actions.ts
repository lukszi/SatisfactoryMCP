/* What a dashboard tab asks of the dashboard around it: a redraw, or leaving for the map.
 * The shell hands over its hooks when it loads, so no tab imports the shell. */

import { button } from "../kit/dashkit";
import { el, make } from "../kit/dom";
import { writeHash } from "../map/map";
import { showFactory } from "../map/panel";
import { located } from "../map/panel-rows";
import { showMachine, showPoint } from "../map/map-highlight";
import { pushDash } from "../app/nav";
import { vitals } from "../app/vitals";
import { editName } from "./factories/rename";

import type { FactoryHealthRow } from "../api/shapes";

export interface DashHooks {
  render: () => void;
  show: () => void;
  renderIfDeferred: () => void;
}

const NOTHING = function () {};

let hooks: DashHooks = { render: NOTHING, show: NOTHING, renderIfDeferred: NOTHING };

export function setDashHooks(next: DashHooks): void {
  hooks = next;
}

export function requestRender(): void {
  hooks.render();
}

export function leaveDashThen(action: () => void): void {
  const from = document.activeElement;
  const keyed = !!from && el("dash").contains(from);
  pushDash("");
  hooks.show();
  action();
  writeHash();
  const now = document.activeElement;
  if (keyed && (!now || now === document.body || el("dash").contains(now))) el("map").focus({ preventScroll: true });
}

export function mapButton(title: string, action: () => void, label?: string): HTMLButtonElement {
  return button(
    "map",
    function () {
      leaveDashThen(action);
    },
    { title: title, map: true, label: label }
  );
}

export function pointButton(
  row: {
    x_m: number | null;
    y_m: number | null;
    instance?: string;
    name?: string | null;
    what?: string;
  },
  label?: string
): HTMLElement {
  const instance = row.instance;
  const shown = { label: row.name || row.what, layers: instance ? ["machines"] : undefined };
  if (!located(row)) return make("span", "dash-muted", "–");
  const at = row;
  return mapButton(
    "fly the map to it",
    function () {
      if (instance) showMachine(instance, shown.label || "a machine", at.x_m, at.y_m, shown);
      else showPoint(at.x_m, at.y_m, shown);
    },
    label || (shown.label ? "show " + shown.label + " on the map" : undefined)
  );
}

export function factoryMapButton(row: FactoryHealthRow): HTMLElement {
  if (!row.bbox_m) return make("span", "dash-muted", "–");
  return mapButton(
    "fly the map to this factory",
    function () {
      showFactory(row.name);
    },
    "show " + row.name + " on the map"
  );
}

/* A redraw that arrived while the name was being edited was held back; it runs on cancel. */
export function renameButton(name: string, host: HTMLElement, onRenamed: (to: string) => void): HTMLButtonElement {
  return button(
    "rename",
    function () {
      const health = vitals().health;
      if (!health) return;
      editName(host, name, health.labels_version, function (reply) {
        if (reply) onRenamed(reply.name);
        else hooks.renderIfDeferred();
      });
    },
    { title: "rename this factory", label: "rename " + name }
  );
}
