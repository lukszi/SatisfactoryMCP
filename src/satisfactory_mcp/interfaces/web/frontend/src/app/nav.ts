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
  var raw = dash === undefined ? state.dash : dash;
  var cut = raw.indexOf("/");
  var subject = cut < 0 ? "" : raw.slice(cut + 1);
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

export function onMap(action: () => void): void {
  var run = function () {
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

export interface SubjectQuery {
  head: string;
  params: Record<string, string>;
}

export function decoded(text: string): string {
  try {
    return decodeURIComponent(text);
  } catch (_e) {
    return text;
  }
}

export function subjectQuery(subject: string): SubjectQuery {
  var cut = subject.indexOf("?");
  var params: Record<string, string> = {};
  (cut < 0 ? "" : subject.slice(cut + 1)).split("&").forEach(function (pair) {
    if (!pair) return;
    var eq = pair.indexOf("=");
    params[eq < 0 ? pair : pair.slice(0, eq)] = eq < 0 ? "" : decoded(pair.slice(eq + 1));
  });
  return { head: cut < 0 ? subject : subject.slice(0, cut), params: params };
}

export function withQuery(head: string, params: Record<string, string>): string {
  var pairs: string[] = [];
  Object.keys(params).forEach(function (key) {
    var value = params[key];
    if (value) pairs.push(key + "=" + encodeURIComponent(value));
  });
  return head + (pairs.length ? "?" + pairs.join("&") : "");
}
