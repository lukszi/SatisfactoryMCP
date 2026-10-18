/* Buttons and page controls: segmented control, sort select, filter field, switch, ⋯ button. */

import { cls, html, type Hole, type Safe } from "../html";

export function btn(label: Hole, attrs?: { id?: string; title?: string; extra?: string; disabled?: boolean; data?: Record<string, string> }): Safe {
  const o = attrs || {};
  const data = Object.keys(o.data || {}).map(function (key) {
    return html` data-${key}="${o.data![key]!}"`;
  });
  return html`<button type="button" class="${cls("btn", o.extra)}"${o.id ? html` id="${o.id}"` : ""}${o.title ? html` title="${o.title}"` : ""}${o.disabled ? html` disabled` : ""}${data}>${label}</button>`;
}

/** An async action's button: busyButton() gives it the spinner. */
export function busyBtn(label: Hole, attrs?: { id?: string; title?: string; extra?: string; disabled?: boolean; data?: Record<string, string> }): Safe {
  return btn(html`<span class="spinner" aria-hidden="true"></span>${label}`, attrs);
}

/** A glyph button (◎ ⋯ ×). */
export function glyphBtn(glyph: string, label: string, attrs?: { id?: string; extra?: string; data?: Record<string, string> }): Safe {
  const o = attrs || {};
  const data = Object.keys(o.data || {}).map((key) => html` data-${key}="${o.data![key]!}"`);
  return html`<button type="button" class="${cls("btn icon", o.extra)}"${o.id ? html` id="${o.id}"` : ""} aria-label="${label}" title="${label}"${data}>${glyph}</button>`;
}

/** The ⋯ button; its items open through kit/overlay/menu.ts. */
export function moreBtn(label: string, items: string[]): Safe {
  return html`<button class="btn icon more" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="${"more for " + label}" data-menu="${items.join("|")}">⋯</button>`;
}

export interface SegItem {
  value: string;
  label: string;
  href?: string;
}

/** The segmented control: links when items carry href (a click replaces the entry), else
 *  buttons with data-seg. */
export function seg(name: string, items: SegItem[], on: string, label: string): Safe {
  return html`<span class="seg" role="group" aria-label="${label}">${items.map(function (item) {
    const pressed = item.value === on;
    if (item.href) return html`<a href="${item.href}" data-nav="replace"${pressed ? html` class="on" aria-current="page"` : ""}>${item.label}</a>`;
    return html`<button type="button" data-seg="${name}" data-val="${item.value}" aria-pressed="${String(pressed)}"${pressed ? html` class="on"` : ""}>${item.label}</button>`;
  })}</span>`;
}

export function selectBox(id: string, options: [string, string][], value: string, label?: string): Safe {
  return html`<select id="${id}"${label ? html` aria-label="${label}"` : ""}>${options.map(function (o) {
    return html`<option value="${o[0]}"${o[0] === value ? html` selected` : ""}>${o[1]}</option>`;
  })}</select>`;
}

export function sortSelect(id: string, options: [string, string][], value: string): Safe {
  return html`<label class="sort">sort ${selectBox(id, options, value)}</label>`;
}

export function filterField(id: string, placeholder: string, value: string, label?: string): Safe {
  return html`<input type="search" id="${id}" value="${value}" placeholder="${placeholder}" aria-label="${label || placeholder}">`;
}

/** A checkbox drawn as a switch. */
export function switchBox(id: string, on: boolean, label?: string): Safe {
  return html`<input type="checkbox" role="switch" class="switch" id="${id}"${on ? html` checked` : ""}${label ? html` aria-label="${label}"` : ""}>`;
}

export function check(id: string, on: boolean, label: Hole, title?: string): Safe {
  return html`<label${title ? html` title="${title}"` : ""}><input type="checkbox" id="${id}"${on ? html` checked` : ""}> ${label}</label>`;
}

/** A filter chip whose state is its pressed look. */
export function filterChip(id: string, label: Hole, on: boolean): Safe {
  return html`<button type="button" class="fchip" id="${id}" aria-pressed="${String(on)}">${label}</button>`;
}
