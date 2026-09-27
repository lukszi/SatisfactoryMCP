/* The dashboard's building blocks, shared by dashboard.ts and the sections split out of it. */

import { make } from "./dom";
import { hashFor } from "./map";

export function link(dash: string, text: string, className?: string): HTMLAnchorElement {
  var a = make("a", className, text);
  a.setAttribute("href", hashFor(dash));
  a.onclick = function (event) {
    event.stopPropagation();
    a.setAttribute("href", hashFor(dash));
  };
  return a;
}

export function note(parent: HTMLElement, text: string): void {
  parent.appendChild(make("p", "dash-note", text));
}

export function heading(parent: HTMLElement, text: string): void {
  parent.appendChild(make("h2", "dash-h", text));
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
