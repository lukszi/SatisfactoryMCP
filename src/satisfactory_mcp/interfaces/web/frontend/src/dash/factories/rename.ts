/* Renaming a factory in place: an input over its name, Enter saves, Esc cancels. The dashboard
 * and the side panel share it. See docs/frontend_vision.md §9.7. */

import { send } from "../../api/client";
import { fieldError } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { createListeners } from "../../app/listeners";
import { loadOne } from "../../app/load";
import { state } from "../../app/state";
import { fail, friendlyError, notify } from "../../kit/toast";

import type { StatusError } from "../../api/client";
import type { LabelRefused, RenamedResponse } from "../../api/shapes";

export const NAME_MAX = 60;

const newNameByOldName: Record<string, string> = {};

const labelsVersionByWorld: Record<string, number> = {};

let open: { input: HTMLInputElement; cancel: () => void } | null = null;

const renamed = createListeners();

export type Refusal = "stale" | "name_taken" | "pin" | "bad" | "";

export function labelRefusal(error: unknown): Refusal {
  const e = error as StatusError | null;
  if (!e || !e.status) return "";
  if (e.status === 400) return "bad";
  if (e.status !== 409) return "";
  const body = (e.body || {}) as Partial<LabelRefused>;
  if (body.name_taken) return "name_taken";
  if (body.stale) return "stale";
  if (body.pin) return "pin";
  return "";
}

/* What changed under a refused write, as the first half of the toast that says so. */
export function staleWriteReason(why: "stale" | "pin"): string {
  return why === "pin" ? "a newer save was written" : "factory names changed elsewhere";
}

export function recordLabelsVersion(version: number): void {
  labelsVersionByWorld[state.world] = Math.max(labelsVersionByWorld[state.world] || 0, version);
}

/* A reply this page wrote may be newer than the read it is editing from. */
export function labelsVersionToSend(version: number): number {
  return Math.max(version, labelsVersionByWorld[state.world] || 0);
}

export function factoryNameProblem(name: string): string {
  if (!name) return "a factory name cannot be blank";
  if (name.length > NAME_MAX) return "at most " + NAME_MAX + " characters";
  return "";
}

export function renamedTo(name: string): string | undefined {
  const seen: Record<string, boolean> = {};
  let now = newNameByOldName[name];
  while (now !== undefined && newNameByOldName[now] !== undefined && !seen[now]) {
    seen[now] = true;
    now = newNameByOldName[now];
  }
  return now;
}

export function onRenamed(listener: () => void): void {
  renamed.on(listener);
}

export function onRenameActivity(entry: { kind: string; args?: unknown }): void {
  if (entry.kind !== "label.rename") return;
  const args = (entry.args || {}) as { was?: unknown; to?: unknown };
  if (typeof args.was !== "string" || typeof args.to !== "string" || newNameByOldName[args.was] === args.to) return;
  newNameByOldName[args.was] = args.to;
  renamed.emit();
}

export function refetchLabelledViews(): void {
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
  const trigger = document.activeElement as HTMLElement | null;
  const kept = Array.prototype.slice.call(host.childNodes) as Node[];
  const input = make("input", "dash-name");
  input.type = "text";
  input.value = name;
  input.maxLength = NAME_MAX;
  input.spellcheck = false;
  input.setAttribute("aria-label", "new name for " + name);
  input.setAttribute("data-renaming", name);
  let saving = false;
  let closed = false;
  const finish = function (reply: RenamedResponse | null) {
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
    const lost = !document.activeElement || document.activeElement === document.body;
    done(reply);
    if (lost && trigger && trigger.isConnected && (!document.activeElement || document.activeElement === document.body)) {
      trigger.focus({ preventScroll: true });
    }
  };
  const cancel = function () {
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
    const to = input.value.trim();
    const problem = factoryNameProblem(to);
    if (problem) {
      fieldError(input, problem);
      return;
    }
    if (to === name) {
      finish(null);
      return;
    }
    saving = true;
    input.disabled = true;
    send<RenamedResponse>("PATCH", "/api/labels/{name}", { to: to, version: labelsVersionToSend(version) }, name)
      .then(function (reply) {
        recordLabelsVersion(reply.version);
        newNameByOldName[reply.was] = reply.name;
        notify(
          "renamed “" + reply.was + "” to “" + reply.name + "”" +
            (reply.plans.length ? "; " + reply.plans.length + " stored plan(s) followed it" : "")
        );
        if (reply.plans_stuck.length) {
          fail(reply.plans_stuck.length + " stored plan(s) still name “" + reply.was + "”: " + reply.stuck_reason);
        }
        refetchLabelledViews();
        finish(reply);
      })
      .catch(function (error) {
        saving = false;
        input.disabled = false;
        const why = labelRefusal(error);
        if (why === "name_taken") {
          fieldError(input, "“" + to + "” is already a factory name");
          input.focus();
        } else if (why === "bad") {
          fieldError(input, friendlyError(error));
          input.focus();
        } else if (why === "stale") {
          fail(staleWriteReason(why) + ", so “" + name + "” was not renamed; they are reloaded now, try again");
          refetchLabelledViews();
          finish(null);
        } else {
          fail("renaming “" + name + "”: " + friendlyError(error));
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
