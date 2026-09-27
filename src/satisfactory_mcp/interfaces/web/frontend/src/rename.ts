/* Renaming a factory in place: an input over its name, Enter saves, Esc cancels. The dashboard
 * and the side panel share it. See docs/frontend_vision.md §9.7. */

import { send } from "./api";
import { make } from "./dom";
import { loadOne } from "./load";
import { fail, friendly, note } from "./toast";

import type { RenamedResponse } from "./api-shapes";

var formerly: Record<string, string> = {};

export function renamedTo(name: string): string | undefined {
  var seen: Record<string, boolean> = {};
  var now = formerly[name];
  while (now !== undefined && formerly[now] !== undefined && !seen[now]) {
    seen[now] = true;
    now = formerly[now];
  }
  return now;
}

export function refreshLabels(): void {
  loadOne("/api/factories");
  loadOne("/api/factories/health");
}

export function editName(
  host: HTMLElement,
  name: string,
  version: number,
  done: (reply: RenamedResponse | null) => void
): void {
  var input = make("input", "dash-name");
  input.type = "text";
  input.value = name;
  input.spellcheck = false;
  input.setAttribute("aria-label", "new name for " + name);
  input.setAttribute("data-renaming", name);
  var saving = false;
  var finish = function (reply: RenamedResponse | null) {
    saving = true;
    done(reply);
  };
  input.onclick = function (event) {
    event.stopPropagation();
  };
  input.onkeydown = function (event) {
    event.stopPropagation();
    if (event.key === "Escape") finish(null);
    if (event.key !== "Enter" || saving) return;
    var to = input.value.trim();
    if (!to) {
      fail("a factory name cannot be blank");
      return;
    }
    if (to === name) {
      finish(null);
      return;
    }
    saving = true;
    input.disabled = true;
    send<RenamedResponse>("PATCH", "/api/labels/{name}", { to: to, version: version }, name)
      .then(function (reply) {
        formerly[reply.was] = reply.name;
        note(
          "renamed “" + reply.was + "” to “" + reply.name + "”" +
            (reply.plans.length ? "; " + reply.plans.length + " stored plan(s) followed it" : "")
        );
        refreshLabels();
        finish(reply);
      })
      .catch(function (error) {
        fail("renaming “" + name + "”: " + friendly(error));
        saving = false;
        input.disabled = false;
        if (/changed elsewhere/.test(String(error))) {
          refreshLabels();
          finish(null);
        } else input.focus();
      });
  };
  host.textContent = "";
  host.appendChild(input);
  input.focus();
  input.select();
}
