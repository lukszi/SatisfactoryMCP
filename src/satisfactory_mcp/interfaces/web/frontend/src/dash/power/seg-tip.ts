/* The circuit page's popover: hover or keyboard focus on a `[data-tip]` element opens a small
 * tip under it (flipped above near the bottom edge); Esc, blur, scroll or leaving closes it.
 * The look is the graph tip's (.pg-tip) plus the overlay shadow. */

import { html, mount, type Safe } from "../../kit/html";

const BOX_ID = "segtip";
const GAP = 6;

/* What each tip says, by key; refilled on every render of the page. */
const tips = new Map<string, Safe>();
let wired = false;

export function clearTips(): void {
  tips.clear();
  show(null);
}

/** The attributes that give an element a tip: its key and the box it is described by. */
export function tipAttrs(key: string, content: Safe): Safe {
  tips.set(key, content);
  return html` data-tip="${key}" aria-describedby="${BOX_ID}"`;
}

function box(): HTMLElement {
  let el = document.getElementById(BOX_ID);
  if (!el) {
    el = document.createElement("div");
    el.id = BOX_ID;
    el.className = "segtip";
    el.setAttribute("role", "tooltip");
    el.hidden = true;
    document.body.appendChild(el);
  }
  return el;
}

function show(target: HTMLElement | null): void {
  const content = target ? tips.get(target.dataset.tip || "") : undefined;
  const existing = document.getElementById(BOX_ID);
  if (!target || !content) {
    if (existing) existing.hidden = true;
    return;
  }
  const el = box();
  mount(el, content);
  el.hidden = false;
  const r = target.getBoundingClientRect();
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  el.style.left = Math.max(GAP, Math.min(window.innerWidth - w - GAP, r.left)) + "px";
  el.style.top = (r.bottom + GAP + h > window.innerHeight ? r.top - GAP - h : r.bottom + GAP) + "px";
}

function tipOf(node: EventTarget | null): HTMLElement | null {
  return node instanceof Element ? node.closest<HTMLElement>("[data-tip]") : null;
}

/** Listens once, on the document: tips outlive any one render of the page. */
export function wireTips(): void {
  if (wired) return;
  wired = true;
  document.addEventListener("mouseover", function (event) {
    const target = tipOf(event.target);
    const focused = tipOf(document.activeElement);
    if (target || !focused) show(target);
  });
  document.addEventListener("focusin", (event) => show(tipOf(event.target)));
  document.addEventListener("focusout", () => show(null));
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") show(null);
  });
  window.addEventListener("hashchange", () => show(null));
  window.addEventListener("scroll", () => show(null), true);
}
