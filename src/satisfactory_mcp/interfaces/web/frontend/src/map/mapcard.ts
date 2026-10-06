/* The floating card beside the map that trace and lasso draw into; one is open at a time. */

import { make } from "../kit/dom";

var closers: Record<string, () => void> = {};

export function mapCard(id: string, label: string, close: () => void): HTMLElement {
  closers[id] = close;
  var box = document.getElementById(id);
  if (box) return box;
  box = make("aside", "mapcard");
  box.id = id;
  box.hidden = true;
  box.setAttribute("aria-label", label);
  document.body.appendChild(box);
  return box;
}

export function claim(id: string): void {
  Object.keys(closers).forEach(function (other) {
    var box = document.getElementById(other);
    if (other !== id && box && !box.hidden) closers[other]!();
  });
}

export function cardHead(title: string): HTMLElement {
  var head = make("div", "mapcard-head");
  head.appendChild(make("strong", "", title));
  return head;
}

export function cardRow(): HTMLElement {
  return make("div", "mapcard-head");
}

export function cardSubject(parent: HTMLElement, text: string): void {
  parent.appendChild(make("div", "mapcard-subject", text));
}

export function cardHeading(parent: HTMLElement, text: string): void {
  parent.appendChild(make("h4", "mapcard-h", text));
}

export function cardLine(parent: HTMLElement, text: string, className?: string): HTMLElement {
  var p = make("p", "mapcard-note" + (className ? " " + className : ""), text);
  parent.appendChild(p);
  return p;
}
