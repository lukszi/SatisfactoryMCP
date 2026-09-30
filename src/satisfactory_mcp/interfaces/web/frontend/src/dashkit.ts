/* The dashboard's building blocks, shared by dashboard.ts and the sections split out of it. */

import "./dashkit.css";
import { COPY_ATTR, COPY_CLASS, count, make } from "./dom";
import { hashFor } from "./map";
import { tone } from "./states";
import { friendly } from "./toast";
import { W } from "./words";

import type { MachineIssue } from "./api-shapes";

export function link(dash: string, text: string, className?: string): HTMLAnchorElement {
  var a = make("a", className, text);
  a.setAttribute("href", hashFor(dash));
  a.onclick = function (event) {
    event.stopPropagation();
    a.setAttribute("href", hashFor(dash));
  };
  return a;
}

export function note(parent: HTMLElement, text: string): HTMLElement {
  var line = make("p", "dash-note", text);
  parent.appendChild(line);
  return line;
}

export function heading(parent: HTMLElement, text: string, unit?: string): void {
  var h = make("h2", "dash-h", text);
  if (unit) h.appendChild(make("span", "dash-unit", " " + unit));
  parent.appendChild(h);
}

export function tile(label: string, value: string, sub: string, bad?: boolean, href?: string): HTMLElement {
  var box = make(href ? "a" : "div", "dash-tile" + (bad ? " bad" : ""));
  if (href) box.setAttribute("href", href);
  box.appendChild(make("span", "dash-tile-k", label));
  box.appendChild(make("span", "dash-tile-v", value));
  if (sub) box.appendChild(make("span", "dash-tile-sub", sub));
  return box;
}

export function cell(tr: HTMLElement, content: string | number | HTMLElement, className?: string): void {
  var td = make("td", className);
  if (content instanceof HTMLElement) td.appendChild(content);
  else td.textContent = String(content);
  tr.appendChild(td);
}

export function grid(headers: [string, boolean][]): HTMLTableElement {
  var t = make("table", "dash-table");
  var head = make("thead");
  var tr = make("tr");
  headers.forEach(function (h) {
    tr.appendChild(make("th", h[1] ? "num" : "", h[0]));
  });
  head.appendChild(tr);
  t.appendChild(head);
  t.appendChild(make("tbody"));
  return t;
}

export function scroll(parent: HTMLElement, table: HTMLTableElement): void {
  var wrap = make("div", "dash-scroll");
  wrap.appendChild(table);
  parent.appendChild(wrap);
}

export function checkbox(label: string, checked: boolean, change: (on: boolean) => void): HTMLLabelElement {
  var toggle = make("label", "dash-toggle");
  var box = make("input");
  box.type = "checkbox";
  box.checked = checked;
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
  var wrap = make("div", "dash-slider");
  var input = make("input");
  input.type = "range";
  input.min = "0";
  input.max = String(stops.length - 1);
  input.step = "1";
  input.value = String(value);
  input.disabled = !!o.disabled;
  input.setAttribute("aria-label", o.label);
  if (o.title) input.title = o.title;
  var say = function () {
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
  var ticks = make("div", "dash-slider-ticks");
  stops.forEach(function (text) {
    ticks.appendChild(make("span", "", text));
  });
  wrap.appendChild(ticks);
  if (o.ends) {
    var ends = make("div", "dash-slider-ends");
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

export function choice(options: [string, string][], value: string, change: (value: string) => void, o?: ChoiceOptions): HTMLSelectElement {
  var opts = o || {};
  var pick = make("select", "dash-select");
  if (opts.candidate) pick.setAttribute("data-candidate", opts.candidate);
  if (opts.label) pick.setAttribute("aria-label", opts.label);
  if (opts.title) pick.title = opts.title;
  pick.disabled = !!opts.disabled;
  options.forEach(function (item) {
    var option = make("option", "", item[1]);
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

var refocus: { sort: SortState; key: string } | null = null;
var landed: HTMLElement | null = null;

function aligned(align: Align | undefined, className?: string): string {
  var names: string[] = [];
  if (align && align !== "left") names.push(align);
  if (className) names.push(className);
  return names.join(" ");
}

function sorted<R>(columns: Column<R>[], rows: R[], sort?: SortState): R[] {
  if (!sort) return rows;
  var by = columns.filter(function (c) {
    return c.key === sort.key;
  })[0];
  if (!by || !by.sort) return rows;
  var key = by.sort;
  var sign = sort.desc ? -1 : 1;
  return rows.slice().sort(function (a, b) {
    var x = key(a);
    var y = key(b);
    if (typeof x === "number" && typeof y === "number") return (x - y) * sign;
    return String(x).localeCompare(String(y), undefined, { sensitivity: "base", numeric: true }) * sign;
  });
}

function body<R>(tbody: HTMLElement, columns: Column<R>[], rows: R[], options: TableOptions<R>): void {
  tbody.textContent = "";
  sorted(columns, rows, options.sort).forEach(function (row) {
    var tr = make("tr");
    var extra = options.rowClass ? options.rowClass(row) : "";
    if (extra) tr.className = extra;
    if (options.rowTitle) tr.title = options.rowTitle(row);
    if (options.onRow) {
      var pick = options.onRow;
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
      var extras = [i === 0 ? "dk-lead" : "", c.className || "", c.tone ? c.tone(row) : ""].filter(Boolean).join(" ");
      cell(tr, c.render(row), aligned(c.align, extras));
    });
    if (options.onRow && !tr.querySelector("a[href]")) tr.tabIndex = 0;
    tbody.appendChild(tr);
  });
}

function marks<R>(ths: HTMLElement[], columns: Column<R>[], sort: SortState): void {
  columns.forEach(function (c, i) {
    var th = ths[i];
    if (!th || !c.sort) return;
    var on = sort.key === c.key;
    th.setAttribute("aria-sort", on ? (sort.desc ? "descending" : "ascending") : "none");
    var arrow = th.querySelector(".sort-arrow");
    if (arrow) arrow.textContent = on ? (sort.desc ? "▼" : "▲") : "";
  });
}

export function table<R>(columns: Column<R>[], rows: R[], options?: TableOptions<R>): HTMLElement {
  var o = options || {};
  var wrap = make("div", "dash-scroll");
  var t = make("table", "dash-table dk-table");
  if (o.caption) t.appendChild(make("caption", "dk-hidden", o.caption));
  var head = make("thead");
  var tr = make("tr");
  var tbody = make("tbody");
  var ths: HTMLElement[] = [];
  columns.forEach(function (c) {
    var th = make("th", aligned(c.align));
    th.scope = "col";
    if (c.title) th.title = c.title;
    ths.push(th);
    tr.appendChild(th);
    var sort = o.sort;
    if (!c.sort || !sort) {
      th.textContent = c.label;
      return;
    }
    var state = sort;
    th.classList.add("dk-sort");
    th.tabIndex = 0;
    var arrow = make("span", "sort-arrow");
    arrow.setAttribute("aria-hidden", "true");
    if (c.align === "right") {
      th.appendChild(arrow);
      th.appendChild(document.createTextNode(c.label));
    } else {
      th.appendChild(document.createTextNode(c.label));
      th.appendChild(arrow);
    }
    if (refocus && refocus.sort === state && refocus.key === c.key) landed = th;
    var pick = function () {
      if (state.key === c.key) state.desc = !state.desc;
      else {
        state.key = c.key;
        state.desc = c.align === "right";
      }
      marks(ths, columns, state);
      body(tbody, columns, rows, o);
      if (!o.onSort) return;
      refocus = { sort: state, key: c.key };
      landed = null;
      o.onSort();
      var again = landed as HTMLElement | null;
      refocus = null;
      landed = null;
      if (again && again.isConnected) again.focus();
      else if (th.isConnected) th.focus();
    };
    th.onclick = pick;
    th.onkeydown = function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        pick();
      }
    };
  });
  if (o.sort) marks(ths, columns, o.sort);
  head.appendChild(tr);
  t.appendChild(head);
  body(tbody, columns, rows, o);
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
  var o = options || {};
  var b = make("button", "btn" + (o.map ? " btn-map" : ""), text);
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
  var b = make("button", "btn " + COPY_CLASS, text);
  b.type = "button";
  b.title = options.title || "copy " + value;
  if (options.label) b.setAttribute("aria-label", options.label);
  b.setAttribute(COPY_ATTR, value);
  return b;
}

export function pressed(text: string, on: boolean, action: () => void, options?: ButtonOptions): HTMLButtonElement {
  var b = button(text, action, options);
  b.setAttribute("aria-pressed", String(on));
  return b;
}

export interface Tab2 {
  id: string;
  label: string;
  href?: string;
}

export function tabs2(items: Tab2[], current: string, onPick?: (id: string) => void, label?: string): HTMLElement {
  var nav = make("nav", "tabs2");
  if (label) nav.setAttribute("aria-label", label);
  items.forEach(function (item) {
    var on = item.id === current;
    var node: HTMLElement;
    if (item.href) {
      node = make("a", "tabs2-item", item.label);
      node.setAttribute("href", item.href);
      if (on) node.setAttribute("aria-current", "page");
    } else {
      var b = make("button", "tabs2-item", item.label);
      b.type = "button";
      b.setAttribute("aria-pressed", String(on));
      node = b;
    }
    if (on) node.classList.add("on");
    if (onPick) {
      var pick = onPick;
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
  var box = make("div", "dk-state dk-" + kind);
  box.setAttribute("role", kind === "error" ? "alert" : "status");
  box.appendChild(make("span", "dk-what", text));
  return box;
}

export function loading(parent: HTMLElement, what?: string): void {
  parent.appendChild(stateBox("loading", what ? "loading " + what + "…" : "loading…"));
}

export function empty(parent: HTMLElement, what: string, how?: string | HTMLElement): void {
  var box = stateBox("empty", what);
  if (how instanceof HTMLElement) {
    how.classList.add("dk-how");
    box.appendChild(how);
  } else if (how) box.appendChild(make("span", "dk-how", how));
  parent.appendChild(box);
}

export function error(parent: HTMLElement, thing: string, reason: unknown, retry?: () => void): void {
  var why = reason === undefined || reason === null || reason === "" ? "" : friendly(reason);
  var box = stateBox("error", thing + " could not be read" + (why ? ": " + why : ""));
  if (retry) {
    var again = retry;
    var b: HTMLButtonElement = button("retry", function () {
      b.disabled = true;
      b.textContent = "retrying…";
      again();
    });
    box.appendChild(b);
  }
  parent.appendChild(box);
}

export function statusChip(status: "free" | "tapped" | "locked"): HTMLElement {
  return chip(W[status], status === "free" ? "ok" : "muted");
}

export function showAll(card: HTMLElement, grid: HTMLElement, rows: number, shown: 25 | 50, label: string, open: boolean, onOpen: () => void): void {
  if (rows <= shown || open) return;
  var cls = "dk-capped-" + shown;
  grid.classList.add(cls);
  var more = make("div", "dk-more");
  more.appendChild(
    button(label, function () {
      onOpen();
      grid.classList.remove(cls);
      more.remove();
    })
  );
  card.appendChild(more);
}

export function chip(text: string, tone?: "ok" | "bad" | "blocked" | "mid" | "muted", title?: string): HTMLElement {
  var c = make("span", "chip chip-" + (tone || "muted"), text);
  if (title) c.title = title;
  return c;
}

export function idChip(id: string, title?: string): HTMLElement {
  var c = chip(id, "muted", title);
  c.classList.add("chip-id");
  return c;
}

var fieldSerial = 0;

export function fieldError(field: HTMLElement, message: string): void {
  var id = field.getAttribute("aria-describedby");
  var old = id ? document.getElementById(id) : null;
  if (old) old.remove();
  field.removeAttribute("aria-describedby");
  field.removeAttribute("aria-invalid");
  if (!message || !field.parentNode) return;
  var hint = make("span", "dk-field-error", message);
  hint.id = "dk-field-error-" + ++fieldSerial;
  hint.setAttribute("role", "alert");
  field.setAttribute("aria-invalid", "true");
  field.setAttribute("aria-describedby", hint.id);
  field.parentNode.insertBefore(hint, field.nextSibling);
}

export interface IssueGroup {
  factory: string;
  state: string;
  what: string;
  cause: string;
  issues: MachineIssue[];
}

function because(issue: MachineIssue): string {
  if (!issue.cause.length) return "";
  var items = issue.cause.join(", ");
  if (issue.state === W.blocked) return "can't output " + items;
  if (issue.state === "starved") return "short of " + items;
  return items;
}

export function issueGroups(rows: { name: string; worst_actionable: MachineIssue[] }[]): IssueGroup[] {
  var found: IssueGroup[] = [];
  rows.forEach(function (r) {
    r.worst_actionable.forEach(function (issue) {
      var cause = because(issue);
      var same = found.filter(function (g) {
        return g.factory === r.name && g.state === issue.state && g.what === issue.what && g.cause === cause;
      })[0];
      if (same) same.issues.push(issue);
      else found.push({ factory: r.name, state: issue.state, what: issue.what, cause: cause, issues: [issue] });
    });
  });
  return found;
}

export function issueCount(groups: IssueGroup[]): number {
  var n = 0;
  groups.forEach(function (g) {
    n += g.issues.length;
  });
  return n;
}

export function issueTable(
  groups: IssueGroup[],
  place: (g: IssueGroup) => HTMLElement,
  withFactory: boolean
): HTMLElement {
  var columns: Column<IssueGroup>[] = [
    {
      key: "state",
      label: "state",
      className: "dash-nowrap",
      tone: function (g) {
        return tone(g.state, true);
      },
      render: function (g) {
        return g.state;
      },
    },
    {
      key: "n",
      label: "machines",
      align: "right",
      render: function (g) {
        return count(g.issues.length);
      },
    },
    {
      key: "what",
      label: "machine",
      className: "dash-wide",
      render: function (g) {
        if (!g.cause) return g.what;
        var cell = make("span", "", g.what);
        cell.appendChild(make("span", "dash-sub", g.cause));
        return cell;
      },
    },
  ];
  if (withFactory) {
    columns.push({
      key: "factory",
      label: W.factory,
      render: function (g) {
        var a = link("factories/" + g.factory, g.factory, "dash-trunc");
        a.title = g.factory;
        return a;
      },
    });
  }
  columns.push({ key: "map", label: "", align: "right", render: place });
  return table<IssueGroup>(columns, groups, {
    rowTitle: function (g) {
      return g.issues
        .map(function (i) {
          return i.instance;
        })
        .join(", ");
    },
    caption: "machines that " + W.needAction,
  });
}
