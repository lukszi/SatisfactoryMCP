/* Renaming a factory in place: an input over its name, Enter saves, Esc cancels. The dashboard
 * and the side panel share it. See docs/frontend_vision.md §9.7. */

import { send } from "./api";
import { fieldError } from "./dashkit";
import { make } from "./dom";
import { loadOne } from "./load";
import { state } from "./state";
import { fail, friendly, note } from "./toast";

import type { StatusError } from "./api";
import type { FactoryRow, LabelRefused, RenamedResponse } from "./api-shapes";

export var NAME_MAX = 60;

var formerly: Record<string, string> = {};

var written: Record<string, number> = {};

var open: { input: HTMLInputElement; cancel: () => void } | null = null;

export type Refusal = "stale" | "name_taken" | "pin" | "bad" | "";

export function refusal(error: unknown): Refusal {
  var e = error as StatusError | null;
  if (!e || !e.status) return "";
  if (e.status === 400) return "bad";
  if (e.status !== 409) return "";
  var body = (e.body || {}) as Partial<LabelRefused>;
  if (body.name_taken) return "name_taken";
  if (body.stale) return "stale";
  if (body.pin) return "pin";
  return "";
}

export function wrote(version: number): void {
  written[state.world] = Math.max(written[state.world] || 0, version);
}

export function newest(version: number): number {
  return Math.max(version, written[state.world] || 0);
}

export function blankOrLong(name: string): string {
  if (!name) return "a factory name cannot be blank";
  if (name.length > NAME_MAX) return "at most " + NAME_MAX + " characters";
  return "";
}

export function renamedTo(name: string): string | undefined {
  var seen: Record<string, boolean> = {};
  var now = formerly[name];
  while (now !== undefined && formerly[now] !== undefined && !seen[now]) {
    seen[now] = true;
    now = formerly[now];
  }
  return now;
}

var spots: Record<string, Record<string, string>> = {};

var renamedListeners: Array<() => void> = [];

export function onRenamed(listener: () => void): void {
  renamedListeners.push(listener);
}

function spot(row: FactoryRow): string {
  return row.centroid_m.join(",") + "|" + row.machines;
}

export function noticeRenames(rows: FactoryRow[]): void {
  var before = spots[state.world];
  var now: Record<string, string> = {};
  rows.forEach(function (row) {
    now[row.name] = spot(row);
  });
  spots[state.world] = now;
  if (!before) return;
  var appeared: Record<string, string[]> = {};
  Object.keys(now).forEach(function (name) {
    if (before![name] === undefined) (appeared[now[name]!] = appeared[now[name]!] || []).push(name);
  });
  var learned = false;
  Object.keys(before).forEach(function (was) {
    if (now[was] !== undefined) return;
    var to = appeared[before![was]!];
    if (to && to.length === 1 && formerly[was] !== to[0]) {
      formerly[was] = to[0]!;
      learned = true;
    }
  });
  if (learned)
    renamedListeners.forEach(function (listener) {
      listener();
    });
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
  var trigger = document.activeElement as HTMLElement | null;
  var kept = Array.prototype.slice.call(host.childNodes) as Node[];
  var input = make("input", "dash-name");
  input.type = "text";
  input.value = name;
  input.maxLength = NAME_MAX;
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
    var lost = !document.activeElement || document.activeElement === document.body;
    done(reply);
    if (lost && trigger && trigger.isConnected && (!document.activeElement || document.activeElement === document.body)) {
      trigger.focus({ preventScroll: true });
    }
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
  input.oninput = function () {
    fieldError(input, "");
  };
  input.onkeydown = function (event) {
    event.stopPropagation();
    if (event.key === "Escape") cancel();
    if (event.key !== "Enter" || saving) return;
    var to = input.value.trim();
    var invalid = blankOrLong(to);
    if (invalid) {
      fieldError(input, invalid);
      return;
    }
    if (to === name) {
      finish(null);
      return;
    }
    saving = true;
    input.disabled = true;
    send<RenamedResponse>("PATCH", "/api/labels/{name}", { to: to, version: newest(version) }, name)
      .then(function (reply) {
        wrote(reply.version);
        formerly[reply.was] = reply.name;
        note(
          "renamed “" + reply.was + "” to “" + reply.name + "”" +
            (reply.plans.length ? "; " + reply.plans.length + " stored plan(s) followed it" : "")
        );
        if (reply.plans_stuck.length) {
          fail(reply.plans_stuck.length + " stored plan(s) still name “" + reply.was + "”: " + reply.stuck_reason);
        }
        refreshLabels();
        finish(reply);
      })
      .catch(function (error) {
        saving = false;
        input.disabled = false;
        var why = refusal(error);
        if (why === "name_taken") {
          fieldError(input, "“" + to + "” is already a factory name");
          input.focus();
        } else if (why === "bad") {
          fieldError(input, friendly(error));
          input.focus();
        } else if (why === "stale") {
          fail("factory names changed elsewhere, so “" + name + "” was not renamed; they are reloaded now, try again");
          refreshLabels();
          finish(null);
        } else {
          fail("renaming “" + name + "”: " + friendly(error));
          input.focus();
        }
      });
  };
  open = { input: input, cancel: cancel };
  host.textContent = "";
  host.appendChild(input);
  input.focus();
  input.select();
}
