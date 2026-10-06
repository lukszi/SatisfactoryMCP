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

import { esc, html } from "../../kit/dom";
import { count } from "../../kit/format";

import type { Row } from "../../kit/dom";

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
      esc(more + " more kinds; the server sends the biggest few") +
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
