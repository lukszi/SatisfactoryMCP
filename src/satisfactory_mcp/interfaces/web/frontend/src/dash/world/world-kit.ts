/* What every World view shares: its address and filters, one read per view keyed to the save,
 * the filter fields, and the columns and lines several tables repeat. See
 * docs/world-finders_contract.md §7. */

import { get, latest } from "../../api/client";
import { appendNote, capRows, loading, pendingNotice, selectBox, settingsLinkNote } from "../../kit/dashkit";
import { code, make } from "../../kit/dom";
import { isRebuilding } from "../../kit/focus";
import { count, formatNumber, metres, regionLine } from "../../kit/format";
import { writeHash } from "../../map/map";
import { showRows } from "../../map/tools/finder";
import { dashParts, subjectQuery, withQuery } from "../../app/nav";
import { select } from "../../app/selection";
import { state } from "../../app/state";
import { counted, staleText, WORDS } from "../../kit/words";
import { leaveDashThen, requestRender } from "../actions";

import type { ApiError, ApiUrl } from "../../api/client";
import type { Column } from "../../kit/dashkit";
import type { Selection } from "../../app/selection";
import type { FinderResults } from "../../map/tools/finder";
import type { Region, TableAge } from "../../api/shapes";

export const VIEWS: [string, string][] = [
  ["", "here"],
  ["nodes", "nodes"],
  ["fields", "fields"],
  ["rank", "rank"],
  ["conduits", "conduits"],
  ["pickups", "pickups"],
  ["regions", "regions"],
];

const DEBOUNCE_MS = 200;

const SHOWN: 50 = 50;

let editTimer = 0;

/* Moves when the here-read sees a new save, for the reads a save token cannot key. */
let saveWave = 0;

const uncapped: Record<string, boolean> = {};

const drafts: Record<string, { base: string; text: string }> = {};

export interface Address {
  view: string;
  params: Record<string, string>;
}

export function address(): Address {
  const query = subjectQuery(dashParts().subject);
  let view = "";
  VIEWS.forEach(function (entry) {
    if (entry[0] === query.head) view = entry[0];
  });
  return { view: view, params: query.params };
}

export function viewDash(view: string, params: Record<string, string>): string {
  return withQuery(view ? "world/" + view : "world", params);
}

export function withParams(params: Record<string, string>, patch: Record<string, string>): Record<string, string> {
  return { ...params, ...patch };
}

export function worldTabShown(): boolean {
  return dashParts().tab === "world";
}

export function redraw(): void {
  if (worldTabShown()) requestRender();
}

/* `debounce` holds the redraw while a place is still being typed. */
export function goToWorldParams(params: Record<string, string>, debounce?: boolean): void {
  state.dash = viewDash(address().view, params);
  writeHash();
  window.clearTimeout(editTimer);
  if (debounce) editTimer = window.setTimeout(redraw, DEBOUNCE_MS);
  else redraw();
}

/* A setter per filter key; `reset` is patched in too, so a new filter starts on page one. */
export function paramSetter(
  params: Record<string, string>,
  reset?: Record<string, string>
): (key: string, debounce?: boolean) => (value: string) => void {
  return function (key, debounce) {
    return function (value) {
      const patch: Record<string, string> = { ...reset };
      patch[key] = value;
      goToWorldParams(withParams(params, patch), debounce);
    };
  };
}

export function bumpSaveWave(): void {
  saveWave += 1;
}

export interface Loaded<T> {
  key: string;
  scope: string;
  data: T | null;
  failed: boolean;
  reason: unknown;
  busy: boolean;
}

export function loaded<T>(): Loaded<T> {
  return { key: "", scope: "", data: null, failed: false, reason: null, busy: false };
}

/* Reads `url` once per world, save and save token; another world drops what the box held. */
export function want<T extends ApiError>(slot: string, box: Loaded<T>, url: ApiUrl): void {
  const scope = JSON.stringify([state.epoch, state.world, state.save]);
  const key = JSON.stringify([scope, state.saveToken || "wave " + saveWave, url]);
  if (box.key === key) return;
  if (box.scope !== scope) box.data = null;
  box.key = key;
  box.scope = scope;
  box.busy = true;
  box.failed = false;
  const ticket = latest(slot);
  get<T>(url)
    .then(function (data) {
      if (!ticket.fresh()) return;
      box.data = data;
      box.busy = false;
      redraw();
    })
    .catch(function (reason) {
      if (!ticket.fresh()) return;
      box.data = null;
      box.failed = true;
      box.reason = reason;
      box.busy = false;
      redraw();
    });
}

/* True while there is nothing to draw; a read in flight over old data says so and draws on. */
export function waiting<T>(parent: HTMLElement, box: Loaded<T>, what: string): boolean {
  if (box.failed || !box.data) {
    pendingNotice(parent, what, box.reason, box.failed, function () {
      box.key = "";
      redraw();
    });
    return true;
  }
  if (box.busy) loading(parent, "the new " + what);
  return false;
}

export function copyCell(text: string, shown?: string): HTMLElement {
  const holder = make("span");
  holder.innerHTML = code(text, shown).html;
  return holder.firstChild as HTMLElement;
}

export function filterBar(parent: HTMLElement): HTMLElement {
  const bar = make("div", "world-filters");
  parent.appendChild(bar);
  return bar;
}

function labelled(label: string, control: HTMLElement): HTMLElement {
  const box = make("label", "world-field");
  box.appendChild(make("span", "world-field-k", label));
  box.appendChild(control);
  return box;
}

export interface FieldOptions {
  disabled?: boolean;
  title?: string;
}

export function selectField(
  label: string,
  candidate: string,
  value: string,
  options: [string, string][],
  change: (value: string) => void,
  fieldOptions?: FieldOptions
): HTMLElement {
  const opts = fieldOptions || {};
  return labelled(label, selectBox(options, value, change, { candidate: candidate, disabled: opts.disabled, title: opts.title }));
}

/* Commits on Enter or blur; what was typed survives a redraw until then. */
export function textField(
  label: string,
  candidate: string,
  value: string,
  placeholder: string,
  change: (value: string) => void,
  fieldOptions?: FieldOptions
): HTMLElement {
  const opts = fieldOptions || {};
  const input = make("input", "dash-name world-text");
  input.type = "search";
  input.placeholder = placeholder;
  input.title = opts.title || "press Enter to search";
  input.disabled = !!opts.disabled;
  const draft = drafts[candidate];
  input.value = draft?.base === value ? draft.text : value;
  input.setAttribute("data-candidate", candidate);
  function commit(text: string): void {
    if (isRebuilding()) return;
    delete drafts[candidate];
    change(text);
  }
  input.onchange = function () {
    commit(input.value.trim());
  };
  input.oninput = function () {
    drafts[candidate] = { base: value, text: input.value };
    if (!input.value.trim() && value) commit("");
  };
  input.onfocus = function () {
    const end = input.value.length;
    input.setSelectionRange(end, end);
  };
  return labelled(label, input);
}

export interface RangeSpec {
  min: number;
  max: number;
  step: number;
}

export function rangeField(
  label: string,
  candidate: string,
  value: number,
  span: RangeSpec,
  show: (value: number) => string,
  change: (value: number) => void,
  fieldOptions?: FieldOptions
): HTMLElement {
  const opts = fieldOptions || {};
  const box = make("span", "world-range");
  const input = make("input", "world-slider");
  input.type = "range";
  input.min = String(span.min);
  input.max = String(span.max);
  input.step = String(span.step);
  input.value = String(value);
  input.disabled = !!opts.disabled;
  input.setAttribute("data-candidate", candidate);
  input.setAttribute("aria-label", label);
  if (opts.title) input.title = opts.title;
  const out = make("output", "world-range-v", show(value));
  input.oninput = function () {
    out.textContent = show(Number(input.value));
  };
  input.onchange = function () {
    if (!isRebuilding()) change(Number(input.value));
  };
  box.appendChild(input);
  box.appendChild(out);
  return labelled(label, box);
}

export function showAllToggle(card: HTMLElement, grid: HTMLElement, rows: number, key: string, noun: string): void {
  capRows(card, grid, rows, SHOWN, "show all " + counted(rows, noun), !!uncapped[key], function () {
    uncapped[key] = true;
  });
}

export function staleLine(parent: HTMLElement, age: TableAge | null): void {
  if (!age || (!age.behind && !age.moved && !age.unjoinable)) return;
  appendNote(parent, staleText(age));
}

export function hiddenLine(parent: HTMLElement, hidden: number, one: string, many?: string): void {
  if (!hidden) return;
  settingsLinkNote(parent, counted(hidden, one, many) + " " + WORDS.hiddenBySpoilers + " · ", "");
}

function regionText(region: Region | null, full?: boolean): string {
  if (!region) return "off the map";
  return full ? regionLine(region) : region.name;
}

export function regionCell(region: Region | null, full?: boolean): HTMLElement {
  const cell = make("span", "", regionText(region, full));
  if (region) cell.title = regionLine(region) + ", good to about " + formatNumber(region.accuracy_m, 0) + " m";
  return cell;
}

export function distanceColumn<R extends { distance_m: number | null }>(label?: string): Column<R> {
  return {
    key: "distance",
    label: label || "away",
    align: "right",
    title: "straight-line distance from the place the search is near",
    sort: function (row) {
      return row.distance_m === null ? Infinity : row.distance_m;
    },
    render: function (row) {
      return metres(row.distance_m);
    },
  };
}

/* A right-aligned number column; a missing value sorts last unless `nullsFirst`. */
export function numericColumn<R>(
  key: string,
  label: string,
  pick: (row: R) => number | null,
  options?: { render?: (row: R) => string; title?: string; nullsFirst?: boolean }
): Column<R> {
  const opts = options || {};
  return {
    key: key,
    label: label,
    align: "right",
    title: opts.title,
    sort: function (row) {
      const value = pick(row);
      if (value !== null) return value;
      return opts.nullsFirst ? -1 : Infinity;
    },
    render:
      opts.render ||
      function (row) {
        const value = pick(row);
        return value === null ? "–" : count(value);
      },
  };
}

/* Leaves for the map with `shown` ringed and listed beside it, coming back to this view. */
export function openRowsOnMap(shown: FinderResults, title: string, seed?: number): void {
  const dash = state.dash;
  leaveDashThen(function () {
    showRows(shown, title, dash, seed);
  });
}

export function selectAndRender<R>(toSelection: (row: R) => Selection): (row: R) => void {
  return function (row) {
    select(toSelection(row));
    requestRender();
  };
}
