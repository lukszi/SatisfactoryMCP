/* The one top bar: section tabs (those that do not fit go into "more"), and on phones the
 * bottom bar with its More sheet. Links are rebuilt on click so they carry the current view. */

import { el } from "../../kit/dom";
import { html, mount, type Safe } from "../../kit/html";
import { openMenu } from "../../kit/overlay/menu";
import { hashFor, map } from "../../map/map";
import { leaveDashThen } from "../../dash/actions";
import { openSettings } from "../settings-dialog/dialog";
import { state } from "../state";
import { PHONE_TABS, TOP_TABS, type TabLink, type TopTab } from "./router";

let current: TopTab | "" = "";

function tabLink(tab: TabLink): Safe {
  const on = tab.id === current;
  return html`<a href="${hashFor(tab.dash)}" data-dash="${tab.dash}"${on ? html` class="on" aria-current="page"` : ""}>${tab.label}</a>`;
}

function label(id: TopTab | ""): string {
  return TOP_TABS.filter((t) => t.id === id)[0]?.label || "";
}

export function drawTopBar(top: TopTab | ""): void {
  current = top;
  mount(el("tabs"), html`${TOP_TABS.map(tabLink)}`);
  const phone = TOP_TABS.filter((t) => PHONE_TABS.indexOf(t.id) >= 0);
  const rest = TOP_TABS.filter((t) => PHONE_TABS.indexOf(t.id) < 0);
  const inMore = rest.some((t) => t.id === top);
  mount(
    el("bnav-row"),
    html`${phone.map(tabLink)}<button type="button" id="bn-more" aria-controls="sheet" aria-expanded="false"${inMore ? html` class="on"` : ""}>More${inMore ? html`<span class="sr"> (current: ${label(top)})</span>` : ""}</button>`
  );
  mount(el("sheet"), html`${rest.map(tabLink)}`);
  fitTabs();
}

/** Hides tabs from the right until the row fits; the current tab always stays. */
function fitTabs(): void {
  const nav = el("tabs");
  const more = el("tabs-more");
  const links = Array.prototype.slice.call(nav.querySelectorAll("a")) as HTMLAnchorElement[];
  links.forEach((a) => (a.hidden = false));
  more.hidden = true;
  if (!nav.offsetWidth || nav.scrollWidth <= nav.clientWidth) return;
  more.hidden = false;
  for (let i = links.length - 1; i >= 0 && nav.scrollWidth > nav.clientWidth; i--) {
    if (!links[i]!.classList.contains("on")) links[i]!.hidden = true;
  }
}

function openSheet(open: boolean): void {
  el("sheet").hidden = !open;
  document.getElementById("bn-more")?.setAttribute("aria-expanded", String(open));
}

/* A tab's href is rewritten as it is clicked, so it carries the map view of that moment; the
 * Map tab leaves the dashboard in place instead of reloading the map. */
function followTab(event: MouseEvent): void {
  const a = (event.target as Element).closest<HTMLAnchorElement>("a[data-dash]");
  if (!a) return;
  openSheet(false);
  const dash = a.dataset.dash || "";
  if (!dash && state.dash) {
    event.preventDefault();
    leaveDashThen(function () {
      map.invalidateSize();
    });
    return;
  }
  a.setAttribute("href", hashFor(dash));
}

function openMoreTabs(): void {
  const hidden = Array.prototype.slice.call(el("tabs").querySelectorAll("a[hidden]")) as HTMLAnchorElement[];
  openMenu(
    el("tabs-more"),
    hidden.map(function (a) {
      return { label: a.textContent || "", href: hashFor(a.dataset.dash || "") };
    })
  );
}

export function wireTopBar(): void {
  el("tabs").addEventListener("click", followTab);
  el("bnav").addEventListener("click", function (event) {
    if ((event.target as Element).closest("#bn-more")) return openSheet(el("sheet").hidden);
    followTab(event);
  });
  el("tabs-more").addEventListener("click", openMoreTabs);
  el("gear").addEventListener("click", function () {
    openSettings(state.settings || "");
  });
  document.addEventListener("click", function (event) {
    if (!el("sheet").hidden && !el("bnav").contains(event.target as Node)) openSheet(false);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape" || el("sheet").hidden) return;
    openSheet(false);
    document.getElementById("bn-more")?.focus();
  });
  window.addEventListener("resize", fitTabs);
}
