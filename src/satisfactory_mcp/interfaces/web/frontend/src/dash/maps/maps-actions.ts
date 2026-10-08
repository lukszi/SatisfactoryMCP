/* What the maps section sends: registry writes, job submissions, and the one inline confirm
 * open at a time. */

import { send } from "../../api/client";
import { button } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { adoptMapRegistry, fetchMapRegistry } from "../../app/map-types";
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
    if (status === 409 && ((reason as StatusError).body as { stale?: boolean } | undefined)?.stale) fetchMapRegistry();
    fail(what + ": " + friendlyError(reason));
  };
}

export function write(method: "PUT" | "PATCH" | "DELETE" | "POST", path: ApiPath, body?: object, subject?: string, query?: string): Promise<MapsResponse> {
  return send<MapsResponse>(method, path, body, subject, query).then(function (reply) {
    adoptMapRegistry(reply);
    return reply;
  });
}

/* Whether a render of these layers needs the paint input built first: the painted layer is
 * drawn from it, and a render without it is refused. */
export function paintFirst(body: MapsResponse, layers: string[]): boolean {
  return (
    layers.indexOf("painted") >= 0 &&
    !body.inputs.some(function (row) {
      return row.name === "paint" && row.present;
    })
  );
}

/* Queues the paint input first when `paintFirst` says so, then the render; false when either
 * was refused. A queued render waits behind the paint job. */
export function submitRender(body: MapsResponse, options: Record<string, unknown>, label: string, replaces: string | null): Promise<boolean> {
  const first = paintFirst(body, options.layers as string[]) ? submit("paint", {}, "", null) : Promise.resolve(true);
  return first.then(function (ok) {
    return ok ? submit("render", options, label, replaces) : false;
  });
}

/* Resolves false when the job was refused, so a caller can keep what was typed. */
export function submit(preset: string, options: Record<string, unknown>, label: string, replaces: string | null): Promise<boolean> {
  return send<MapJobResponse>("POST", "/api/maps/jobs", { preset: preset, options: options, label: label || null, replaces: replaces })
    .then(function () {
      fetchMapRegistry();
      return true;
    })
    .catch(function (reason) {
      fail("the job was not queued: " + friendlyError(reason));
      return false;
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
