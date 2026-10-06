/* The side rail: the map and every dashboard section, one list on every view.
 * Below 900 px it is a drawer behind the header's sections button.
 * See docs/frontend_vision.md §2.1 and §16. */

import { link } from "../kit/dashkit";
import { el, make } from "../kit/dom";

var OPEN_CLASS = "rail-drawer-open";

function isOpen(): boolean {
  return el("rail").classList.contains(OPEN_CLASS);
}

function setDrawer(open: boolean, moveFocus: boolean): void {
  var rail = el("rail");
  var toggle = el("rail-open");
  rail.classList.toggle(OPEN_CLASS, open);
  toggle.setAttribute("aria-expanded", String(open));
  if (!moveFocus) return;
  if (open) {
    var target = rail.querySelector<HTMLElement>("[aria-current=page]") || rail.querySelector<HTMLElement>("a");
    if (target) target.focus();
  } else toggle.focus();
}

function wireDrawer(rail: HTMLElement): void {
  var toggle = el("rail-open");
  toggle.addEventListener("click", function () {
    setDrawer(!isOpen(), true);
  });
  rail.addEventListener(
    "click",
    function (event) {
      if (isOpen() && (event.target as Element).closest("a")) setDrawer(false, true);
    },
    true
  );
  rail.addEventListener("focusout", function (event) {
    var next = event.relatedTarget as Node | null;
    if (isOpen() && next && !rail.contains(next) && next !== toggle) setDrawer(false, false);
  });
  document.addEventListener(
    "keydown",
    function (event) {
      if (event.key !== "Escape" || !isOpen()) return;
      event.stopPropagation();
      setDrawer(false, true);
    },
    true
  );
  document.addEventListener("click", function (event) {
    var target = event.target as Node;
    if (isOpen() && !rail.contains(target) && !toggle.contains(target)) setDrawer(false, false);
  });
  window.matchMedia("(min-width: 900px)").addEventListener("change", function () {
    setDrawer(false, false);
  });
}

export function drawRail(tabs: [string, string][], current: string): void {
  var rail = el("rail");
  if (!rail.firstChild) {
    var list = make("ul", "rail-list");
    [["", "Map"] as [string, string]].concat(tabs).forEach(function (t) {
      var item = make("li");
      var a = link(t[0], t[1], "rail-link");
      a.setAttribute("data-rail", t[0]);
      item.appendChild(a);
      list.appendChild(item);
    });
    rail.appendChild(list);
    wireDrawer(rail);
  }
  var links = rail.querySelectorAll<HTMLAnchorElement>("[data-rail]");
  Array.prototype.forEach.call(links, function (a: HTMLAnchorElement) {
    if (a.getAttribute("data-rail") === current) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}
