/* The dashboard address: reading `tab/subject` apart, and going to one.
 * One parser, so every tab splits its address the same way. */

import { hashFor, map } from "../map/map";
import { state } from "./state";

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

export function go(dash: string, replace?: boolean): void {
  if (replace) location.replace(hashFor(dash));
  else location.hash = hashFor(dash);
}

/* The two below move the address without a hashchange: the caller redraws the view itself. */
export function replaceDash(dash: string): void {
  history.replaceState(null, "", hashFor(dash));
  state.dash = dash;
}

export function pushDash(dash: string): void {
  history.pushState(null, "", hashFor(dash));
  state.dash = dash;
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
