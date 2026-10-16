/* The side panel (a pick's sheet, sticky right; a bottom sheet on phones) and the toast. */

import { html, type Hole, type Safe } from "../html";

/** The sheet beside a list: header row, then the body; `id` lets the page swap its body. */
export function sidePanel(head: Hole, body: Hole, id?: string): Safe {
  return html`<aside class="items-side"${id ? html` id="${id}"` : ""}><div class="side-head">${head}</div>${body}</aside>`;
}

const TOAST_MS = 2200;
let timer = 0;

/** One short line at the bottom, gone after two seconds; for confirmations the page owes. */
export function toast(text: string): void {
  let line = document.getElementById("toast");
  if (!line) {
    line = document.createElement("div");
    line.id = "toast";
    line.className = "toast";
    line.setAttribute("role", "status");
    line.setAttribute("aria-live", "polite");
    document.body.appendChild(line);
  }
  const shown = line;
  shown.textContent = text;
  shown.hidden = false;
  window.clearTimeout(timer);
  timer = window.setTimeout(function () {
    shown.hidden = true;
  }, TOAST_MS);
}
