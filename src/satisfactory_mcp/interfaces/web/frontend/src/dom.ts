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
export function code(text: unknown): Markup {
  var value = esc(text);
  return html(
    '<code class="' +
      COPY_CLASS +
      '" ' +
      COPY_ATTR +
      '="' +
      value +
      '" title="click to copy">' +
      value +
      "</code>"
  );
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

/* ------------------------------------------------- what is in a container, as the game draws it */

/* THE INVENTORY GRID: a container's contents as tiles of the reader's own game artwork with
 * the quantity in the corner, rather than as a table of names.
 *
 * Here rather than in either feature because there are two callers -- a crate and a storage
 * box -- and "what is in it" has to be one idea on this map rather than two that resemble
 * each other.
 *
 * THE NAMES DO NOT GO AWAY, which is the one thing a grid must not get wrong. Every tile
 * carries its name three times over: `title` for a pointer, `alt` for a screen reader and for
 * the tile whose picture never arrives, and once more as text in the caption line under the
 * grid, which is the copy that needs no pointer, no hover and no working icon directory. The
 * caption is why this returns ROWS rather than one lump of markup: a caller assembling the
 * two itself could leave one out.
 *
 * THE URL IS UNTAGGED. `/api/icons/{desc}` serves a `?v=<build>` request `immutable` for a
 * year and an untagged one `no-cache` with an ETag, and this page cannot carry the tag: it
 * would have to learn it from a probe, which means naming a descriptor class up front and
 * hoping the reader's own generated directory holds it. The directory is optional and some
 * item classes ship no picture at all, so a probe is a guess whose failure mode is silently
 * unversioned URLs. Untagged plus an ETag makes a revalidation a 304 rather than 50 KB.
 *
 * NOTHING IS FETCHED UNTIL A POPUP OPENS: Leaflet holds a bound popup as a STRING and builds
 * its DOM on the click, so these <img> tags are markup rather than requests for as long as
 * the card is shut.
 */

/** One kind of thing in a container: the class its picture is named by, the name a reader
 *  reads, and how many. The record `/api/crates` and `/api/storage` both send. */
export interface Stack {
  cls: string;
  name: string;
  count: number;
}

/* How wide a card carrying an inventory grid may get, in pixels, handed to `bindPopup` by the
 * two layers that draw one. Leaflet's default of 300 is right for every other popup here --
 * short keys against short values, and a card wider than it needs to be covers more of the
 * map than it has to. This number is arithmetic: at 380 the value cell fits SEVEN 38 px tiles
 * to a row, and eight would need 424 px and start covering the thing that was clicked.
 * Measured with the widest card either layer can produce -- the fullest crate on this
 * machine, 38 kinds at 381x568 px, without overflow.
 */
export var CONTENTS_POPUP_PX = 380;

/** Thousands separators, because these are counts of things and they get large: a full
 *  Industrial Storage Container holds 24,000 Wire, and a badge whose digits have to be counted
 *  is not a reading. Shared, so a tile's badge and the total under the grid cannot disagree. */
export function count(n: number): string {
  return n.toLocaleString("en-GB");
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

/* One tile: the picture, the quantity over its bottom-right corner, and the name underneath
 * all of it -- literally. `.item-abbr` sits in the tile the whole time and is revealed when
 * the <img> stacked over it gives up.
 *
 * That is the missing-icon answer, and it is a tile rather than a hole: a reader who never
 * ran the generator has no pictures at all, so a failed icon is the ordinary state here and a
 * grid with gaps would be a grid lying about how many kinds are in the box.
 *
 * `onerror` is inline because the failure has to be handled by the element that failed, inside
 * markup that is a string until Leaflet inserts it: there is no node to attach a listener to
 * at the moment this is built. It ADDS a class rather than assigning one, so a tile that grows
 * a second class later cannot be silently undressed by this line.
 */
function tile(item: Stack): string {
  return (
    '<span class="item-tile" title="' +
    esc(item.name + " — " + count(item.count)) +
    '"><img class="item-icon" src="/api/icons/' +
    encodeURIComponent(item.cls) +
    '" alt="' +
    esc(item.name) +
    "\" onerror=\"this.parentNode.classList.add('item-tile-bare');this.remove()\">" +
    '<span class="item-abbr">' +
    esc(item.name) +
    '</span><b class="item-count">' +
    esc(count(item.count)) +
    "</b></span>"
  );
}

/* What is in one container, as the two popup rows that say it: the grid, and the names under
 * it.
 *
 * `more` is the SERVER's truncation and not this file's; both routers send whole inventories,
 * so it arrives as 0. The "+N more" tile is the net under any server that reports a remainder
 * anyway -- a grid that simply stops is a container that looks emptier than it is.
 */
export function contentsRows(items: Stack[], more: number): Row[] {
  var stacks = items || [];
  // Said in words, because an empty grid and a container this page failed to read are the
  // same picture, and one of the two is an answer.
  if (!stacks.length) return [["contents", more ? "not shown" : "empty"]];
  var tiles = stacks.map(tile).join("");
  if (more) {
    tiles +=
      '<span class="item-tile item-tile-more" title="' +
      esc(more + " more kinds — the server sends the biggest few") +
      '">+' +
      esc(String(more)) +
      "</span>";
  }
  /* The caption, which keeps a grid honest: a tile says what a thing is to anyone who
   * recognises the picture, and the names say it to everyone else, including anyone with no
   * pointer to hover with. A middle dot rather than a comma, because several item names have
   * a comma in them and none has this. */
  var names = stacks
    .map(function (s) {
      return s.name;
    })
    .join(" · ");
  if (more) names += " · and " + more + " more";
  return [
    ["contents", html('<span class="item-grid">' + tiles + "</span>")],
    ["", html('<span class="item-names">' + esc(names) + "</span>")],
  ];
}
