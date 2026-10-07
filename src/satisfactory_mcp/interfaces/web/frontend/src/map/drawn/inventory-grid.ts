/* A container's contents as tiles of the game's item artwork with the quantity in the corner,
 * for a crate and a storage box alike. See frontend/README.md, "The inventory grid". */

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

/** The popup width, in pixels, of a card carrying an inventory grid: seven tiles to a row. */
export const CONTENTS_POPUP_PX = 380;

/* One tile: the picture, the quantity over its corner, and the name under both, shown when
 * the picture fails. */
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

/** What is in one container, as two popup rows: the grid, and the names under it. `more` is
 *  how many kinds the server left out. */
export function contentsRows(items: Stack[], more: number): Row[] {
  const stacks = items || [];
  if (!stacks.length) return [["contents", more ? "not shown" : "empty"]];
  let tiles = stacks.map(tile).join("");
  if (more) {
    tiles +=
      '<span class="item-tile item-tile-more" title="' +
      esc(more + " more kinds; the server sends the biggest few") +
      '">+' +
      esc(String(more)) +
      "</span>";
  }
  let names = stacks
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
