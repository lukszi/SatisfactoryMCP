/* The message strip: what the page says when something failed, and the one thing it says
 * when something went right without being asked. Two kinds of message, one mechanism, two
 * lifetimes and two colours.
 */

import { el } from "./dom";

/* Long enough to read when six endpoints fail at once, because failures stack into their own
 * rows rather than overwriting each other. A click dismisses one, so the strip is never an
 * undismissable patch of dead map. */
var FAIL_MS = 12000;

/* Shorter, because a note describes something the reader can already see on the map. Its
 * colour differs from a failure's for the same reason: a note the eye reads as an error is
 * worse than no note. */
var NOTE_MS = 6000;

var MAX_ROWS = 4;

export function toast(message: string, kind: "fail" | "note", ms: number): void {
  var box = el("err");
  var rows: Element[] = Array.prototype.slice.call(box.children);
  rows.forEach(function (row) {
    // The same message twice is one problem, not two rows.
    if (row.textContent === message) row.remove();
  });
  var row = document.createElement("div");
  row.className = "err-row " + kind;
  row.textContent = message;
  row.title = "click to dismiss";
  row.onclick = function () {
    row.remove();
  };
  box.appendChild(row);
  while (box.children.length > MAX_ROWS && box.firstElementChild) box.firstElementChild.remove();
  setTimeout(function () {
    row.remove();
  }, ms);
}

export function offer(message: string, label: string, action: () => void): void {
  toast(message, "note", NOTE_MS * 2);
  var box = el("err");
  var row = box.lastElementChild;
  if (!row) return;
  var button = document.createElement("button");
  button.type = "button";
  button.className = "btn";
  button.textContent = label;
  button.onclick = function (event) {
    event.stopPropagation();
    row!.remove();
    action();
  };
  row.appendChild(document.createTextNode(" "));
  row.appendChild(button);
}

export function fail(message: string): void {
  toast(scrubbed(message) || message, "fail", FAIL_MS);
}

export function note(message: string): void {
  toast(message, "note", NOTE_MS);
}

/* Browser-internal error phrases, translated to what they mean HERE. "Failed to fetch"
 * is Chrome for "the server you started is gone", and that is the actionable sentence. */
export function friendly(error: unknown): string {
  // Read structurally rather than with `instanceof Error`: a rejected fetch that arrives as a
  // DOMException still carries a `message`, and asking about the constructor would start
  // printing "[object DOMException]" instead.
  var message = (error as { message?: unknown } | null | undefined)?.message;
  var text = error && message ? String(message) : String(error);
  if (/Failed to fetch|NetworkError|Load failed/i.test(text)) {
    return "the server is not answering; is it still running?";
  }
  if (/Unexpected token|not valid JSON/i.test(text)) {
    return "the server answered with something that is not JSON";
  }
  if (/^\d{3} \/api\//.test(text)) return "the server hit an error";
  return scrubbed(text) || "the server hit an error";
}

function leaf(path: string): string {
  var parts = path.split(/[\\/]+/).filter(function (p) {
    return p !== "";
  });
  return parts.length ? parts[parts.length - 1]! : "";
}

function scrubbed(text: string): string {
  return text
    .replace(/(['"])((?:[A-Za-z]:[\\/]|\\\\|\/(?!api\/))[^'"]*)\1/g, function (_all, quote: string, path: string) {
      return quote + leaf(path) + quote;
    })
    .replace(/\/api\/[^\s'",;)]*/g, "")
    .replace(/(?:[A-Za-z]:[\\/]|\\\\)[^\s'",;)]*/g, leaf)
    .replace(/(^|[\s(=:])(\/(?:[^\s'",;:)\/]+\/)+[^\s'",;:)\/]*)/g, function (_all, lead: string, path: string) {
      return lead + leaf(path);
    })
    .replace(/\s{2,}/g, " ")
    .replace(/\s+([:,.;)])/g, "$1")
    .trim();
}
