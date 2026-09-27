/* Renaming a factory in place: an input over its name, Enter saves, Esc cancels. The dashboard
 * and the side panel share it. See docs/frontend_vision.md §9.7. */

import { send } from "./api";
import { make } from "./dom";
import { loadOne } from "./load";
import { fail, friendly, note } from "./toast";

import type { RenamedResponse } from "./api-shapes";

var formerly: Record<string, string> = {};

var open: { input: HTMLInputElement; cancel: () => void } | null = null;

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
  loadOne("/api/power/circuits");
}

export function renamingIn(container: HTMLElement): boolean {
  return !!open && open.input.isConnected && container.contains(open.input);
}

export function cancelRename(): void {
  if (open) open.cancel();
}

export function editName(
  host: HTMLElement,
  name: string,
  version: number,
  done: (reply: RenamedResponse | null) => void
): void {
  cancelRename();
  var kept = Array.prototype.slice.call(host.childNodes) as Node[];
  var input = make("input", "dash-name");
  input.type = "text";
  input.value = name;
  input.spellcheck = false;
  input.setAttribute("aria-label", "new name for " + name);
  input.setAttribute("data-renaming", name);
  var saving = false;
  var closed = false;
  var finish = function (reply: RenamedResponse | null) {
    if (closed) return;
    closed = true;
    saving = true;
    if (open && open.input === input) open = null;
    if (!reply && input.parentNode === host) {
      host.textContent = "";
      kept.forEach(function (node) {
        host.appendChild(node);
      });
    }
    done(reply);
  };
  var cancel = function () {
    if (!saving) finish(null);
  };
  input.onclick = function (event) {
    event.stopPropagation();
  };
  input.onblur = function () {
    if (document.hasFocus()) cancel();
  };
  input.onkeydown = function (event) {
    event.stopPropagation();
    if (event.key === "Escape") cancel();
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
  open = { input: input, cancel: cancel };
  host.textContent = "";
  host.appendChild(input);
  input.focus();
  input.select();
}
