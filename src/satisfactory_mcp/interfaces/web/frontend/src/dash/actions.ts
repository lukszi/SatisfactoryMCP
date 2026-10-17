/* What a dashboard tab asks of the dashboard around it: a redraw, or leaving for the map.
 * The shell hands over its hooks when it loads, so no tab imports the shell. */

import { el } from "../kit/dom";
import { writeHash } from "../map/map";
import { pushDash } from "../app/nav";

export interface DashHooks {
  render: () => void;
  show: () => void;
}

const NOTHING = function () {};

let hooks: DashHooks = { render: NOTHING, show: NOTHING };

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
