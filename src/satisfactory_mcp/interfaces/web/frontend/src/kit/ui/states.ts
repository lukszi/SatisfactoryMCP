/* A read in place of a page or block: still loading, or failed with a retry. */

import { html, toElement, type Safe } from "../html";
import { friendlyError } from "../toast";
import { btn } from "./controls";

export function loadingLine(what: string): Safe {
  return html`<p class="note" aria-busy="true">loading ${what}…</p>`;
}

export function loadingInto(parent: HTMLElement, what: string): void {
  parent.appendChild(toElement(loadingLine(what)));
}

/** "<thing> could not be read: why", and "try again" when `retry` is given. */
export function failedInto(parent: HTMLElement, thing: string, reason: unknown, retry?: () => void): void {
  const why = reason === undefined || reason === null || reason === "" ? "" : friendlyError(reason);
  const line = toElement(html`<p class="empty"><b>${thing} could not be read</b>${why ? html` · ${why}` : ""} ${retry ? btn("try again") : ""}</p>`);
  const again = line.querySelector("button");
  if (again && retry) {
    again.addEventListener("click", function () {
      again.disabled = true;
      again.textContent = "retrying…";
      retry();
    });
  }
  parent.appendChild(line);
}
