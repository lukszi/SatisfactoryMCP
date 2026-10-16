/* Form rows (docs/frontend_kit.md, forms): a fixed label column and one control column. */

import { cls, html, type Hole, type Safe } from "../html";

/** One row; `forId` ties the label to its control, `sub` continues a group without a rule. */
export function frow(label: Hole, control: Hole, o?: { forId?: string; sub?: boolean }): Safe {
  const opts = o || {};
  const lab = opts.forId ? html`<label class="flab" for="${opts.forId}">${label}</label>` : html`<span class="flab">${label}</span>`;
  return html`<div class="${cls("frow", opts.sub && "fsub")}">${lab}<div class="fctl">${control}</div></div>`;
}

/** Rows split into groups by a soft rule. */
export function form(rows: Hole, id?: string): Safe {
  return html`<div class="form ruled"${id ? html` id="${id}"` : ""}>${rows}</div>`;
}

/** A number field with its unit in a fixed column. */
export function fnum(id: string, value: number | string, unit: string, attrs?: { min?: number; max?: number; step?: number }): Safe {
  const a = attrs || {};
  return html`<span class="fnum"><input type="number" id="${id}" value="${value}"${a.min === undefined ? "" : html` min="${a.min}"`}${a.max === undefined ? "" : html` max="${a.max}"`}${a.step === undefined ? "" : html` step="${a.step}"`}><span class="u">${unit}</span></span>`;
}

export function ftext(id: string, value: string, placeholder?: string, maxLength?: number): Safe {
  return html`<input type="text" id="${id}" value="${value}"${placeholder ? html` placeholder="${placeholder}"` : ""}${maxLength ? html` maxlength="${maxLength}"` : ""}>`;
}

/** An "add" input with its removable chips above it. */
export function fadd(id: string, placeholder: string, label: string, chips: string[]): Safe {
  const shown = chips.length
    ? html`<span class="fchips">${chips.map(function (text) {
        return html`<span class="pchip">${text}<button type="button" data-remove="${text}" aria-label="${"remove " + text}">×</button></span>`;
      })}</span>`
    : "";
  return html`${shown}<span class="fadd"><input type="text" id="${id}" placeholder="${placeholder}" aria-label="${label}"></span>`;
}

/** A range with its value read out beside it. */
export function frange(id: string, max: number, at: number, shown: string): Safe {
  return html`<span class="frange"><input type="range" id="${id}" min="0" max="${max}" value="${at}" aria-valuetext="${shown}"><output for="${id}">${shown}</output></span>`;
}

export function checks(inner: Hole): Safe {
  return html`<span class="checks">${inner}</span>`;
}
