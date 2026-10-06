/* The page's DOM primitives: finding an element, and turning data into safe markup.
 *
 * `popup()` is the whole reason this file exists. Every string that reaches a popup or a
 * label is DATA -- factory names and notes from the player's label file, class ids from the
 * save, region names from a JSON file -- so escaping is the DEFAULT here and markup is the
 * exception a caller asks for by name with html(). Every popup builder on the page goes
 * through one function rather than through seven places where someone could forget.
 */

/* Non-null, and asserted rather than checked: every id this is called with is written in
 * index.html, so a miss is a broken page rather than a case to handle. The type parameter is
 * what lets the callers that need a <select> get one without a cast at each use. */
export function el<T extends HTMLElement = HTMLElement>(id: string): T {
  return document.getElementById(id) as T;
}

export function esc(value: unknown): string {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* A value that has already been made safe, and is therefore allowed through popup() without
 * escaping. The wrapper object IS the type: there is no way to reach the unescaped branch
 * except by calling html(), which is what makes "escaped by default" checkable. */
export interface Markup {
  html: string;
}

/* One row of a popup table: a key, and a value that is either data (escaped) or finished
 * markup (not). `undefined` is in the union because half the rows are conditional expressions
 * that evaluate to null when there is nothing to say, and those rows are dropped rather than
 * printed empty. */
export type Row = [string, string | number | Markup | null | undefined];

/* Everything interpolated into a fragment still goes through esc() at the call site: html()
 * marks a result as finished, it does not bless its inputs. */
export function html(markup: string): Markup {
  return { html: markup };
}

/** The class a copyable span carries, and the attribute holding what a click puts on the
 *  clipboard. Declared with the writer rather than with the listener in copy.ts, because
 *  copy.ts reaches toast.ts, which reaches this file -- the other way round is a ring. */
export var COPY_CLASS = "copyable";
export var COPY_ATTR = "data-copy";

/* A selector, and a click that copies it -- every one of these exists to be pasted into an
 * MCP tool call. The exact text is repeated into `data-copy` so that what gets copied is
 * this string and not whatever the cell ends up rendering. The listener is in copy.ts. */
export function code(text: unknown, shown?: string): Markup {
  var value = esc(text);
  return html(
    '<code class="' +
      COPY_CLASS +
      '" ' +
      COPY_ATTR +
      '="' +
      value +
      '" title="' +
      (shown ? value + ": click to copy" : "click to copy") +
      '" tabindex="0" role="button">' +
      (shown ? esc(shown) : value) +
      "</code>"
  );
}

export var TRACE_ATTR = "data-trace";
export var TRACE_DIR_ATTR = "data-trace-dir";
export var LASSO_ATTR = "data-lasso";
export var FIND_ATTR = "data-find";
export var FIND_AT_ATTR = "data-find-at";

/* A popup button for a delegated listener, as markup: every attribute value and the text are
 * escaped, because a popup is a string until Leaflet opens it. */
export function dataButton(attrs: Record<string, string>, text: string, title: string): string {
  var pairs = Object.keys(attrs).map(function (name) {
    return name + '="' + esc(attrs[name]) + '"';
  });
  return '<button type="button" class="btn" ' + pairs.join(" ") + ' title="' + esc(title) + '">' + esc(text) + "</button>";
}

/* One capture-phase click listener for every element carrying `attr`, so a button inside a
 * popup works without a handler of its own. */
export function onAttributeClick(attr: string, handler: (hit: Element, event: Event) => void): void {
  document.addEventListener(
    "click",
    function (event) {
      var target = event.target as Element | null;
      var hit = target && target.closest ? target.closest("[" + attr + "]") : null;
      if (!hit) return;
      event.stopPropagation();
      event.preventDefault();
      handler(hit, event);
    },
    true
  );
}

export function traceButtons(seed: string): Markup {
  function button(dir: string, text: string, title: string): string {
    var attrs: Record<string, string> = {};
    attrs[TRACE_ATTR] = seed;
    attrs[TRACE_DIR_ATTR] = dir;
    return dataButton(attrs, text, title);
  }
  return html(
    button("up", "supply ↑", "draw what feeds this on the map") +
      " " +
      button("down", "output ↓", "draw what this feeds on the map")
  );
}

/* A popup's title row is always emitted: a null name would drop the row and leave the card
 * without its title. */
export function popupTitleRow(key: string, name: string | null, cls: string | null): Row {
  return [key, name || cls || "class not recorded in this projection"];
}

export function popup(pairs: Row[]): string {
  return (
    "<table>" +
    pairs
      .filter(function (p) {
        return p[1] !== null && p[1] !== undefined && p[1] !== "";
      })
      .map(function (p) {
        var cell = p[1];
        var value = cell && (cell as Markup).html !== undefined ? (cell as Markup).html : esc(cell);
        return '<tr><td class="popup-key">' + esc(p[0]) + "</td><td>" + value + "</td></tr>";
      })
      .join("") +
    "</table>"
  );
}

export function make<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string | number
): HTMLElementTagNameMap[K] {
  var node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
