/* The dashboard's building blocks, shared by dash/shell.ts and the sections split out of it. */

import "./dashkit.css";
import { COPY_ATTR, COPY_CLASS, make } from "./dom";
import { hashFor } from "../map/map";
import { friendlyError } from "./toast";
import { WORDS } from "./words";

export function link(dash: string, text: string, className?: string): HTMLAnchorElement {
  const a = make("a", className, text);
  a.setAttribute("href", hashFor(dash));
  a.onclick = function (event) {
    event.stopPropagation();
    a.setAttribute("href", hashFor(dash));
  };
  return a;
}

export function appendNote(parent: HTMLElement, text: string): HTMLElement {
  const line = make("p", "dash-note", text);
  parent.appendChild(line);
  return line;
}

/* "<before>Settings<after>" as a note, the link being the way to the setting that hid something. */
export function settingsLinkNote(parent: HTMLElement, before: string, after: string, tag?: "p" | "span"): HTMLElement {
  const line = make(tag || "p", "dash-note", before);
  line.appendChild(link("settings", "Settings"));
  if (after) line.appendChild(document.createTextNode(after));
  parent.appendChild(line);
  return line;
}

export function heading(parent: HTMLElement, text: string, unit?: string): void {
  const h = make("h2", "dash-h", text);
  if (unit) h.appendChild(make("span", "dash-unit", " " + unit));
  parent.appendChild(h);
}

export function tile(label: string, value: string, sub: string, bad?: boolean, href?: string): HTMLElement {
  const box = make(href ? "a" : "div", "dash-tile" + (bad ? " bad" : ""));
  if (href) box.setAttribute("href", href);
  box.appendChild(make("span", "dash-tile-k", label));
  box.appendChild(make("span", "dash-tile-v", value));
  if (sub) box.appendChild(make("span", "dash-tile-sub", sub));
  return box;
}

export function cell(tr: HTMLElement, content: string | number | HTMLElement, className?: string): void {
  const td = make("td", className);
  if (content instanceof HTMLElement) td.appendChild(content);
  else td.textContent = String(content);
  tr.appendChild(td);
}

export function checkbox(
  label: string,
  checked: boolean,
  change: (on: boolean) => void,
  options?: { candidate?: string; className?: string }
): HTMLLabelElement {
  const o = options || {};
  const toggle = make("label", "dash-toggle" + (o.className ? " " + o.className : ""));
  const box = make("input");
  box.type = "checkbox";
  box.checked = checked;
  if (o.candidate) box.setAttribute("data-candidate", o.candidate);
  box.onchange = function () {
    change(box.checked);
  };
  toggle.appendChild(box);
  toggle.appendChild(document.createTextNode(" " + label));
  return toggle;
}

export interface SliderOptions {
  label: string;
  ends?: [string, string];
  disabled?: boolean;
  title?: string;
}

export function slider(stops: string[], value: number, move: (i: number) => void, commit: (i: number) => void, o: SliderOptions): HTMLElement {
  const wrap = make("div", "dash-slider");
  const input = make("input");
  input.type = "range";
  input.min = "0";
  input.max = String(stops.length - 1);
  input.step = "1";
  input.value = String(value);
  input.disabled = !!o.disabled;
  input.setAttribute("aria-label", o.label);
  if (o.title) input.title = o.title;
  const say = function () {
    input.setAttribute("aria-valuetext", stops[Number(input.value)] || input.value);
  };
  say();
  input.oninput = function () {
    say();
    move(Number(input.value));
  };
  input.onchange = function () {
    commit(Number(input.value));
  };
  wrap.appendChild(input);
  const ticks = make("div", "dash-slider-ticks");
  stops.forEach(function (text) {
    ticks.appendChild(make("span", "", text));
  });
  wrap.appendChild(ticks);
  if (o.ends) {
    const ends = make("div", "dash-slider-ends");
    ends.appendChild(make("span", "", o.ends[0]));
    ends.appendChild(make("span", "", o.ends[1]));
    wrap.appendChild(ends);
  }
  return wrap;
}

export interface ChoiceOptions {
  candidate?: string;
  label?: string;
  title?: string;
  disabled?: boolean;
}

export function selectBox(options: [string, string][], value: string, change: (value: string) => void, o?: ChoiceOptions): HTMLSelectElement {
  const opts = o || {};
  const pick = make("select", "dash-select");
  if (opts.candidate) pick.setAttribute("data-candidate", opts.candidate);
  if (opts.label) pick.setAttribute("aria-label", opts.label);
  if (opts.title) pick.title = opts.title;
  pick.disabled = !!opts.disabled;
  options.forEach(function (item) {
    const option = make("option", "", item[1]);
    option.value = item[0];
    pick.appendChild(option);
  });
  pick.value = value;
  pick.onchange = function () {
    change(pick.value);
  };
  return pick;
}

export type Align = "left" | "right" | "center";

export interface Column<R> {
  key: string;
  label: string;
  align?: Align;
  sort?: (row: R) => number | string;
  render: (row: R) => string | number | HTMLElement;
  title?: string;
  className?: string;
  tone?: (row: R) => string;
}

export interface SortState {
  key: string;
  desc: boolean;
}

export interface TableOptions<R> {
  sort?: SortState;
  onSort?: () => void;
  onRow?: (row: R) => void;
  rowClass?: (row: R) => string;
  rowTitle?: (row: R) => string;
  caption?: string;
}

/* onSort usually redraws the whole view, so the clicked header is gone: the rebuilt header for
 * the same column registers itself here and takes the focus back. */
let pendingSortFocus: { sort: SortState; key: string } | null = null;
let refocusTarget: HTMLElement | null = null;

function alignClass(align: Align | undefined, className?: string): string {
  const names: string[] = [];
  if (align && align !== "left") names.push(align);
  if (className) names.push(className);
  return names.join(" ");
}

function sorted<R>(columns: Column<R>[], rows: R[], sort?: SortState): R[] {
  if (!sort) return rows;
  const by = columns.filter(function (c) {
    return c.key === sort.key;
  })[0];
  if (!by?.sort) return rows;
  const key = by.sort;
  const sign = sort.desc ? -1 : 1;
  return rows.slice().sort(function (a, b) {
    const x = key(a);
    const y = key(b);
    if (typeof x === "number" && typeof y === "number") return (x - y) * sign;
    return String(x).localeCompare(String(y), undefined, { sensitivity: "base", numeric: true }) * sign;
  });
}

function fillTableBody<R>(tbody: HTMLElement, columns: Column<R>[], rows: R[], options: TableOptions<R>): void {
  tbody.textContent = "";
  sorted(columns, rows, options.sort).forEach(function (row) {
    const tr = make("tr");
    const extra = options.rowClass ? options.rowClass(row) : "";
    if (extra) tr.className = extra;
    if (options.rowTitle) tr.title = options.rowTitle(row);
    if (options.onRow) {
      const pick = options.onRow;
      tr.classList.add("go");
      tr.onclick = function () {
        pick(row);
      };
      tr.onkeydown = function (event) {
        if (event.key === "Enter" && event.target === tr) {
          event.preventDefault();
          pick(row);
        }
      };
    }
    columns.forEach(function (c, i) {
      const extras = [i === 0 ? "dk-lead" : "", c.className || "", c.tone ? c.tone(row) : ""].filter(Boolean).join(" ");
      cell(tr, c.render(row), alignClass(c.align, extras));
    });
    if (options.onRow && !tr.querySelector("a[href]")) tr.tabIndex = 0;
    tbody.appendChild(tr);
  });
}

function markSortHeaders<R>(ths: HTMLElement[], columns: Column<R>[], sort: SortState): void {
  columns.forEach(function (c, i) {
    const th = ths[i];
    if (!th || !c.sort) return;
    const on = sort.key === c.key;
    th.setAttribute("aria-sort", on ? (sort.desc ? "descending" : "ascending") : "none");
    const arrow = th.querySelector(".sort-arrow");
    if (arrow) arrow.textContent = on ? (sort.desc ? "▼" : "▲") : "";
  });
}

function headerCell<R>(column: Column<R>): HTMLTableCellElement {
  const th = make("th", alignClass(column.align));
  th.scope = "col";
  if (column.title) th.title = column.title;
  return th;
}

/* The arrow sits on the side the column aligns to, so it never pushes the label off its edge. */
function sortableHeader<R>(column: Column<R>, sortState: SortState, onPick: () => void): HTMLTableCellElement {
  const th = headerCell(column);
  th.classList.add("dk-sort");
  th.tabIndex = 0;
  const arrow = make("span", "sort-arrow");
  arrow.setAttribute("aria-hidden", "true");
  if (column.align === "right") {
    th.appendChild(arrow);
    th.appendChild(document.createTextNode(column.label));
  } else {
    th.appendChild(document.createTextNode(column.label));
    th.appendChild(arrow);
  }
  if (pendingSortFocus?.sort === sortState && pendingSortFocus.key === column.key) refocusTarget = th;
  th.onclick = onPick;
  th.onkeydown = function (event) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onPick();
    }
  };
  return th;
}

function resortAndRefocus<R>(
  th: HTMLElement,
  column: Column<R>,
  sortState: SortState,
  redrawBody: () => void,
  onSort?: () => void
): void {
  if (sortState.key === column.key) sortState.desc = !sortState.desc;
  else {
    sortState.key = column.key;
    sortState.desc = column.align === "right";
  }
  redrawBody();
  if (!onSort) return;
  pendingSortFocus = { sort: sortState, key: column.key };
  refocusTarget = null;
  onSort();
  const again = refocusTarget as HTMLElement | null;
  pendingSortFocus = null;
  refocusTarget = null;
  if (again?.isConnected) again.focus();
  else if (th.isConnected) th.focus();
}

export function table<R>(columns: Column<R>[], rows: R[], options?: TableOptions<R>): HTMLElement {
  const o = options || {};
  const wrap = make("div", "dash-scroll");
  const t = make("table", "dash-table dk-table");
  if (o.caption) t.appendChild(make("caption", "dk-hidden", o.caption));
  const head = make("thead");
  const tr = make("tr");
  const tbody = make("tbody");
  const ths: HTMLElement[] = [];
  columns.forEach(function (c) {
    const sort = o.sort;
    let th: HTMLTableCellElement;
    if (!c.sort || !sort) {
      th = headerCell(c);
      th.textContent = c.label;
    } else {
      const sortState = sort;
      th = sortableHeader(c, sortState, function () {
        resortAndRefocus(
          th,
          c,
          sortState,
          function () {
            markSortHeaders(ths, columns, sortState);
            fillTableBody(tbody, columns, rows, o);
          },
          o.onSort
        );
      });
    }
    ths.push(th);
    tr.appendChild(th);
  });
  if (o.sort) markSortHeaders(ths, columns, o.sort);
  head.appendChild(tr);
  t.appendChild(head);
  fillTableBody(tbody, columns, rows, o);
  t.appendChild(tbody);
  wrap.appendChild(t);
  return wrap;
}

export interface ButtonOptions {
  title?: string;
  disabled?: boolean;
  map?: boolean;
  label?: string;
}

export function button(text: string, action: () => void, options?: ButtonOptions): HTMLButtonElement {
  const o = options || {};
  const b = make("button", "btn" + (o.map ? " btn-map" : ""), text);
  b.type = "button";
  if (o.title) b.title = o.title;
  if (o.label) b.setAttribute("aria-label", o.label);
  b.disabled = !!o.disabled;
  b.onclick = function (event) {
    event.stopPropagation();
    action();
  };
  return b;
}

export function copyButton(value: string, text: string, options: ButtonOptions): HTMLButtonElement {
  const b = make("button", "btn " + COPY_CLASS, text);
  b.type = "button";
  b.title = options.title || "copy " + value;
  if (options.label) b.setAttribute("aria-label", options.label);
  b.setAttribute(COPY_ATTR, value);
  return b;
}

/* A button a document-level listener handles (trace, lasso, find): it carries the data
 * attributes and, like copyButton, no onclick of its own. */
export function delegatedButton(text: string, attrs: Record<string, string>, options?: ButtonOptions): HTMLButtonElement {
  const o = options || {};
  const b = make("button", "btn" + (o.map ? " btn-map" : ""), text);
  b.type = "button";
  if (o.title) b.title = o.title;
  if (o.label) b.setAttribute("aria-label", o.label);
  b.disabled = !!o.disabled;
  Object.keys(attrs).forEach(function (name) {
    b.setAttribute(name, attrs[name]!);
  });
  return b;
}

export function toggleButton(text: string, on: boolean, action: () => void, options?: ButtonOptions): HTMLButtonElement {
  const b = button(text, action, options);
  b.setAttribute("aria-pressed", String(on));
  return b;
}

export interface SubTab {
  id: string;
  label: string;
  href?: string;
}

export function subTabs(items: SubTab[], current: string, onPick?: (id: string) => void, label?: string): HTMLElement {
  const nav = make("nav", "subtabs");
  if (label) nav.setAttribute("aria-label", label);
  items.forEach(function (item) {
    const on = item.id === current;
    let node: HTMLElement;
    if (item.href) {
      node = make("a", "subtabs-item", item.label);
      node.setAttribute("href", item.href);
      if (on) node.setAttribute("aria-current", "page");
    } else {
      const b = make("button", "subtabs-item", item.label);
      b.type = "button";
      b.setAttribute("aria-pressed", String(on));
      node = b;
    }
    if (on) node.classList.add("on");
    if (onPick) {
      const pick = onPick;
      node.addEventListener("click", function (event) {
        if (item.href) event.preventDefault();
        event.stopPropagation();
        pick(item.id);
      });
    }
    nav.appendChild(node);
  });
  return nav;
}

function stateBox(kind: "loading" | "empty" | "error", text: string): HTMLElement {
  const box = make("div", "dk-state dk-" + kind);
  box.setAttribute("role", kind === "error" ? "alert" : "status");
  box.appendChild(make("span", "dk-what", text));
  return box;
}

export function loading(parent: HTMLElement, what?: string): void {
  parent.appendChild(stateBox("loading", what ? "loading " + what + "…" : "loading…"));
}

export function empty(parent: HTMLElement, what: string, how?: string | HTMLElement): void {
  const box = stateBox("empty", what);
  if (how instanceof HTMLElement) {
    how.classList.add("dk-how");
    box.appendChild(how);
  } else if (how) box.appendChild(make("span", "dk-how", how));
  parent.appendChild(box);
}

export function error(parent: HTMLElement, thing: string, reason: unknown, retry?: () => void): void {
  const why = reason === undefined || reason === null || reason === "" ? "" : friendlyError(reason);
  const box = stateBox("error", thing + " could not be read" + (why ? ": " + why : ""));
  if (retry) {
    const again = retry;
    const b: HTMLButtonElement = button("retry", function () {
      b.disabled = true;
      b.textContent = "retrying…";
      again();
    });
    box.appendChild(b);
  }
  parent.appendChild(box);
}

/* A read that has not landed: still loading, or failed and worth a retry. */
export function pendingNotice(parent: HTMLElement, label: string, failure: unknown, failed: boolean, retry: () => void): void {
  if (failed) error(parent, label, failure, retry);
  else loading(parent, label);
}

export function statusChip(status: "free" | "tapped" | "locked"): HTMLElement {
  return chip(WORDS[status], status === "free" ? "ok" : "muted");
}

export function capRows(card: HTMLElement, grid: HTMLElement, rows: number, shown: 25 | 50, label: string, open: boolean, onOpen: () => void): void {
  if (rows <= shown || open) return;
  const cls = "dk-capped-" + shown;
  grid.classList.add(cls);
  const more = make("div", "dk-more");
  more.appendChild(
    button(label, function () {
      onOpen();
      grid.classList.remove(cls);
      more.remove();
    })
  );
  card.appendChild(more);
}

export function chip(text: string, tone?: "ok" | "bad" | "blocked" | "remove" | "mid" | "muted", title?: string): HTMLElement {
  const c = make("span", "chip chip-" + (tone || "muted"), text);
  if (title) c.title = title;
  return c;
}

export function idChip(id: string, title?: string): HTMLElement {
  const c = chip(id, "muted", title);
  c.classList.add("chip-id");
  return c;
}

let fieldSerial = 0;

export function fieldError(field: HTMLElement, message: string): void {
  const id = field.getAttribute("aria-describedby");
  const old = id ? document.getElementById(id) : null;
  if (old) old.remove();
  field.removeAttribute("aria-describedby");
  field.removeAttribute("aria-invalid");
  if (!message || !field.parentNode) return;
  const hint = make("span", "dk-field-error", message);
  hint.id = "dk-field-error-" + ++fieldSerial;
  hint.setAttribute("role", "alert");
  field.setAttribute("aria-invalid", "true");
  field.setAttribute("aria-describedby", hint.id);
  field.parentNode.insertBefore(hint, field.nextSibling);
}

export interface InlineTextEdit {
  value: string;
  /** The `data-ctl` keepFocus finds the box by after a redraw. */
  ctl: string;
  label: string;
  className?: string;
  maxLength?: number;
  /** Focus and select on the next tick: the box was just opened by a click. */
  focusNow?: boolean;
  /** A problem with the trimmed text, shown under the box; "" accepts it. */
  validate?: (text: string) => string;
  onCommit: (text: string) => void;
  onCancel: () => void;
  stopEscape?: boolean;
}

/* One name edited in place: Enter or leaving the box commits, Escape reverts. `settled` keeps
 * the blur that follows either one from committing a second time. */
export function inlineTextEdit(o: InlineTextEdit): HTMLInputElement {
  const box = make("input", o.className || "dash-name");
  box.value = box.defaultValue = o.value;
  if (o.maxLength !== undefined) box.maxLength = o.maxLength;
  box.setAttribute("data-ctl", o.ctl);
  box.setAttribute("aria-label", o.label);
  let settled = false;
  const commit = function () {
    if (settled) return;
    const text = box.value.trim();
    const problem = o.validate ? o.validate(text) : "";
    if (problem) {
      fieldError(box, problem);
      return;
    }
    settled = true;
    box.defaultValue = box.value;
    o.onCommit(text);
  };
  box.onkeydown = function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      commit();
    } else if (event.key === "Escape") {
      if (o.stopEscape) event.stopPropagation();
      if (settled) return;
      settled = true;
      box.value = box.defaultValue;
      o.onCancel();
    }
  };
  box.onblur = function () {
    setTimeout(function () {
      if (box.isConnected) commit();
    }, 0);
  };
  if (o.focusNow) {
    setTimeout(function () {
      box.focus();
      box.select();
    }, 0);
  }
  return box;
}
