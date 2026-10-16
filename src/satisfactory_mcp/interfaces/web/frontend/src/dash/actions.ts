/* What a dashboard tab asks of the dashboard around it: a redraw, or leaving for the map.
 * The shell hands over its hooks when it loads, so no tab imports the shell. */

import { button } from "../kit/dashkit";
import { el } from "../kit/dom";
import { writeHash } from "../map/map";
import { pushDash } from "../app/nav";
import { vitals } from "../app/vitals";
import { editName } from "./factories/rename";

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
