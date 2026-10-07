/* The floating card beside the map that the finder, trace and lasso draw into; one is open at
 * a time. */

import { make } from "../kit/dom";

const closers: Record<string, () => void> = {};

export function mapCard(id: string, label: string, close: () => void): HTMLElement {
  closers[id] = close;
  let box = document.getElementById(id);
  if (box) return box;
  box = make("aside", "mapcard");
  box.id = id;
  box.hidden = true;
  box.setAttribute("aria-label", label);
  document.body.appendChild(box);
  return box;
}

/** Close every open card but this one. */
export function closeOtherCards(id: string): void {
  Object.keys(closers).forEach(function (other) {
    const box = document.getElementById(other);
    if (other !== id && box && !box.hidden) closers[other]!();
  });
}

/** The card's top row: its title, with room for the buttons a tool adds after it. */
export function cardTitleBar(title: string): HTMLElement {
  const head = make("div", "mapcard-head");
  head.appendChild(make("strong", "", title));
  return head;
}

/** A row of controls, laid out like the title bar. */
export function cardToolbar(): HTMLElement {
  return make("div", "mapcard-head");
}

export function cardSubject(parent: HTMLElement, text: string): void {
  parent.appendChild(make("div", "mapcard-subject", text));
}

export function cardSectionHeading(parent: HTMLElement, text: string): void {
  parent.appendChild(make("h4", "mapcard-h", text));
}

export function cardLine(parent: HTMLElement, text: string, className?: string): HTMLElement {
  const line = make("p", "mapcard-note" + (className ? " " + className : ""), text);
  parent.appendChild(line);
  return line;
}
