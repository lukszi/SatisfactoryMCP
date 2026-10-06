/* What the maps section sends: registry writes, job submissions, and the one inline confirm
 * open at a time. */

import { send } from "../../api/client";
import { button } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { adoptMaps, fetchMaps } from "../../app/map-types";
import { fail, friendlyError } from "../../kit/toast";
import { requestRender } from "../actions";

import type { ApiPath, StatusError } from "../../api/client";
import type { MapJobResponse, MapsResponse } from "../../api/shapes";

/* `delete:<id>`, `cancel:<job>` or `replace:<job>`; "" when none is open. */
let confirming = "";

export function redraw(): void {
  requestRender();
}

export function confirmingOn(key: string): boolean {
  return confirming === key;
}

export function openConfirm(key: string): void {
  confirming = key;
  redraw();
}

/* A stale refusal means the list moved under the page, so it is read again. */
export function refused(what: string): (reason: unknown) => void {
  return function (reason: unknown) {
    const status = (reason as StatusError).status;
    if (status === 409 && ((reason as StatusError).body as { stale?: boolean } | undefined)?.stale) fetchMaps();
    fail(what + ": " + friendlyError(reason));
  };
}

export function write(method: "PUT" | "PATCH" | "DELETE" | "POST", path: ApiPath, body?: object, subject?: string, query?: string): Promise<MapsResponse> {
  return send<MapsResponse>(method, path, body, subject, query).then(function (reply) {
    adoptMaps(reply);
    return reply;
  });
}

export function submit(preset: string, options: Record<string, unknown>, label: string, replaces: string | null): Promise<void> {
  return send<MapJobResponse>("POST", "/api/maps/jobs", { preset: preset, options: options, label: label || null, replaces: replaces })
    .then(function () {
      fetchMaps();
    })
    .catch(function (reason) {
      fail("the job was not queued: " + friendlyError(reason));
    });
}

export function inlineConfirm(parent: HTMLElement, question: string, yes: string, no: string, act: () => void): void {
  const line = make("div", "maps-confirm");
  line.setAttribute("role", "group");
  line.appendChild(make("span", "", question));
  line.appendChild(
    button(yes, function () {
      confirming = "";
      act();
    })
  );
  line.appendChild(
    button(no, function () {
      confirming = "";
      redraw();
    })
  );
  parent.appendChild(line);
}
