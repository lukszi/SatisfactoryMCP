/* The dashboard address: reading `tab/subject` apart, and going to one. Every move in the
 * page's history goes through here; the rules are docs/frontend_kit.md, Navigation. */

import { hashFor, map } from "../map/map";
import { arrivalOf, closeMove, crumbMove, stampOf, type Stamp } from "./shell/history";
import { dashFromFragment, parseHash, state } from "./state";

export interface DashParts {
  tab: string;
  subject: string;
  rest: string[];
}

export function dashParts(dash?: string): DashParts {
  const raw = dash === undefined ? state.dash : dash;
  const cut = raw.indexOf("/");
  const subject = cut < 0 ? "" : raw.slice(cut + 1);
  return {
    tab: cut < 0 ? raw : raw.slice(0, cut),
    subject: subject,
    rest: subject ? subject.split("/") : [],
  };
}

const VISIT = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
let made = 0;
let current = "";

function newId(): string {
  made += 1;
  return VISIT + "." + made;
}

function stamp(): Stamp | null {
  return stampOf(history.state);
}

function scrollNow(): number {
  const dash = document.getElementById("dash");
  return dash ? dash.scrollTop : 0;
}

/* The first entry of a visit gets a stamp in place; a reload keeps the one it had. */
const booted = stamp();
if (booted) current = booted.nav;
else {
  current = newId();
  history.replaceState({ nav: current, from: null, scroll: 0 } satisfies Stamp, "", location.href);
}

/** The #dash scroll the entry on screen had when the page last left it (a reload). */
export const bootScroll = booted ? booted.scroll : 0;

/** Writes the #dash scroll into the entry on screen, so Back can bring it back. */
export function saveScroll(): void {
  const s = stamp();
  const top = scrollNow();
  if (!s || s.scroll === top) return;
  history.replaceState({ ...s, scroll: top } satisfies Stamp, "", location.href);
}

function pushEntry(url: string, scroll: number): void {
  saveScroll();
  current = newId();
  history.pushState({ nav: current, from: state.dash, scroll: scroll } satisfies Stamp, "", url);
}

/** On every hashchange, before the page follows it: stamps an entry a plain link made, and
 *  returns the scroll to restore when Back or Forward brought the page here, else null. */
export function arrive(): number | null {
  const s = stamp();
  const kind = arrivalOf(s, current);
  if (kind === "traverse") {
    current = s!.nav;
    return s!.scroll;
  }
  if (kind === "fresh") {
    current = newId();
    history.replaceState({ nav: current, from: state.dash, scroll: 0 } satisfies Stamp, "", location.href);
  }
  return null;
}

/** Push opens a place; replace changes how it is shown. The page follows on a queued
 *  hashchange, as it would after a link. */
export function go(dash: string, how: "push" | "replace" = "push"): void {
  const url = hashFor(dash);
  if (url === location.hash) return;
  if (how === "push") pushEntry(url, 0);
  else history.replaceState(history.state, "", url);
  window.setTimeout(function () {
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  }, 0);
}

/** Closes back to `parent`: Back when this visit opened from there, else a replace. */
export function closeTo(parent: string): void {
  if (closeMove(stamp(), parent) === "back") history.back();
  else go(parent, "replace");
}

/** A breadcrumb: Back when the entry before is `parent` (its scroll returns), else a push. */
export function upTo(parent: string): void {
  if (crumbMove(stamp(), parent) === "back") history.back();
  else go(parent);
}

/** Whether closing what was opened over this page (the settings dialog) is a step Back. */
export function closesBack(): boolean {
  return closeMove(stamp(), state.dash) === "back";
}

/** A new entry for the page on screen whose fragment changes: the dialog, a world switch. */
export function pushHere(): void {
  pushEntry(hashFor(state.dash), scrollNow());
}

/* The two below move the address without a hashchange: the caller redraws the view itself. */
export function replaceDash(dash: string): void {
  history.replaceState(history.state, "", hashFor(dash));
  state.dash = dash;
}

export function pushDash(dash: string): void {
  pushEntry(hashFor(dash), 0);
  state.dash = dash;
}

function dashOfHref(href: string): string {
  return dashFromFragment(parseHash(href.slice(href.indexOf("#") + 1)));
}

/* A link marked data-nav keeps its real href for middle-click; a plain click follows the
 * rule instead: up (breadcrumb), close (×, "back to …") or replace (a filter drawn as links). */
function followMarked(event: MouseEvent): void {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const a = (event.target as Element).closest?.<HTMLAnchorElement>("a[data-nav]");
  if (!a) return;
  event.preventDefault();
  const dash = dashOfHref(a.getAttribute("href") || "");
  if (a.dataset.nav === "up") upTo(dash);
  else if (a.dataset.nav === "close") closeTo(dash);
  else go(dash, "replace");
}

/** Keeps the entry's scroll current and follows data-nav links; the shell calls it once. */
export function wireHistory(scroller: HTMLElement): void {
  let timer = 0;
  scroller.addEventListener("scroll", function () {
    window.clearTimeout(timer);
    timer = window.setTimeout(saveScroll, 200);
  });
  document.addEventListener("click", saveScroll, true);
  document.addEventListener("click", followMarked);
}

export function goToMapThen(action: () => void): void {
  const run = function () {
    map.invalidateSize();
    action();
  };
  if (!state.dash) {
    run();
    return;
  }
  window.addEventListener(
    "hashchange",
    function () {
      requestAnimationFrame(run);
    },
    { once: true }
  );
  go("");
}

export function withQuery(head: string, params: Record<string, string>): string {
  const pairs: string[] = [];
  Object.keys(params).forEach(function (key) {
    const value = params[key];
    if (value) pairs.push(key + "=" + encodeURIComponent(value));
  });
  return head + (pairs.length ? "?" + pairs.join("&") : "");
}
