/* The side rail: the map and every dashboard section, one list on every view.
 * See docs/frontend_vision.md §2.1 and §16. */

import { link } from "./dashkit";
import { el, make } from "./dom";

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
  }
  var links = rail.querySelectorAll<HTMLAnchorElement>("[data-rail]");
  Array.prototype.forEach.call(links, function (a: HTMLAnchorElement) {
    if (a.getAttribute("data-rail") === current) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}
