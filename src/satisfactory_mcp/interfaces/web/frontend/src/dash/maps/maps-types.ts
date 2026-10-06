/* The map types card: one row per registered map, with its freshness and what can be done
 * to it. */

import { button, checkbox, chip, empty, idChip } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { bytes, isoDate } from "../../kit/format";
import { mapDetails, mapState, staleWhy, staleWord } from "../../app/map-types";
import { askMode } from "../../map/tiles";
import { leaveDashThen } from "../actions";
import { confirmingOn, inlineConfirm, openConfirm, redraw, refused, submit, write } from "./maps-actions";

import type { MapsResponse, MapTypeBody } from "../../api/shapes";

/* The id whose label is being edited; "" when none is. */
let renaming = "";

/* A re-render that needs the heightfield queues its rebuild first; an otherwise current map
 * restyles from a kept raster cache. */
function rerender(row: MapTypeBody): void {
  const pending = row.freshness.rerender;
  let chain: Promise<void> = Promise.resolve();
  if (pending && pending.needs.indexOf("heightfield") >= 0) chain = submit("heightmap", {}, "", null);
  const body = mapState.body;
  const size = row.size_px || 32768;
  const options =
    row.kind === "artwork"
      ? { enhance: !!body && body.can_generate.vulkan }
      : {
          layers: [row.layer],
          size: size,
          recipe: "current",
          top: true,
          light: !!row.axes.light,
          restyle: !pending && !row.freshness.stale.length && row.freshness.restyle && !!body && body.cached_sizes.indexOf(size) >= 0,
        };
  chain.then(function () {
    return submit(row.kind === "artwork" ? "artwork" : "render", options, row.label || "", row.id);
  });
}

function labelEditor(row: MapTypeBody, body: MapsResponse, title: HTMLElement): void {
  const input = make("input", "dash-input");
  input.type = "text";
  input.maxLength = 80;
  input.value = row.label || "";
  input.placeholder = "empty: named by its style";
  input.setAttribute("aria-label", "label for " + row.id);
  const save = function () {
    renaming = "";
    write("PATCH", "/api/maps/{ident}", { label: input.value, version: body.version }, row.id).catch(refused("the label was not saved"));
  };
  input.onkeydown = function (event) {
    if (event.key === "Enter") save();
    if (event.key === "Escape") {
      renaming = "";
      redraw();
    }
  };
  title.appendChild(input);
  title.appendChild(button("save", save));
  setTimeout(function () {
    input.focus();
  }, 0);
}

function nameCell(row: MapTypeBody, body: MapsResponse): HTMLElement {
  const cell = make("div", "maps-name");
  const title = make("div", "maps-title");
  if (renaming === row.id) labelEditor(row, body, title);
  else title.appendChild(make("strong", "", row.title));
  title.appendChild(idChip(row.id, row.dir));
  if (row.default) title.appendChild(chip("default", "ok"));
  if (row.status !== "ready") title.appendChild(chip(row.status, row.status === "failed" ? "bad" : "muted"));
  cell.appendChild(title);
  cell.appendChild(make("div", "dash-sub", mapDetails(row) + " · data/local/" + row.dir.replace(/^\.$/, "")));
  return cell;
}

function freshnessCell(row: MapTypeBody): HTMLElement {
  const cell = make("div", "maps-fresh");
  const stale = staleWord(row);
  if (stale) {
    const amber = chip(stale, "muted", staleWhy(row));
    amber.classList.add("maps-stale");
    cell.appendChild(amber);
  }
  if (row.freshness.rerender) cell.appendChild(chip("re-render available", "muted", row.freshness.rerender.text));
  if (row.freshness.restyle) cell.appendChild(chip("newer palette", "muted"));
  if (stale) cell.appendChild(make("div", "dash-sub", staleWhy(row)));
  if (row.freshness.incomplete) cell.title = "provenance incomplete: this map was drawn before its sidecar recorded every input";
  return cell;
}

function actionsCell(row: MapTypeBody, body: MapsResponse): HTMLElement {
  const cell = make("div", "maps-actions");
  const key = "delete:" + row.id;
  if (confirmingOn(key)) {
    inlineConfirm(cell, "delete " + row.id + "? " + bytes(row.bytes), "delete", "keep", function () {
      write("DELETE", "/api/maps/{ident}", undefined, row.id, "version=" + body.version).catch(refused(row.id + " was not deleted"));
    });
    return cell;
  }
  cell.appendChild(
    button("set as default", function () {
      write("PUT", "/api/maps/default", { id: row.id, version: body.version }).catch(refused("the default map was not changed"));
    }, { disabled: row.default || row.status !== "ready", title: row.default ? "this is the default" : "open fresh pages on this map" })
  );
  if (row.freshness.rerender || row.freshness.restyle || row.freshness.stale.length) {
    const pending = row.freshness.rerender;
    cell.appendChild(
      button(row.freshness.stale.length ? "regenerate" : "re-render", function () {
        rerender(row);
      }, {
        disabled: !body.can_generate.ok,
        title: body.can_generate.ok ? (pending ? pending.text : "queue a new map; this one stays until you delete it") : body.can_generate.reason || "",
      })
    );
  }
  cell.appendChild(
    button("rename", function () {
      renaming = row.id;
      redraw();
    }, { label: "rename " + row.id })
  );
  cell.appendChild(
    checkbox("in switcher", row.in_switcher, function (on) {
      write("PATCH", "/api/maps/{ident}", { in_switcher: on, version: body.version }, row.id).catch(refused("the switcher was not changed"));
    })
  );
  cell.appendChild(
    button("delete", function () {
      openConfirm(key);
    }, { disabled: row.default, title: row.default ? "pick another default first" : "moves its files to the trash, then deletes them", label: "delete " + row.id })
  );
  return cell;
}

function thumb(row: MapTypeBody): HTMLElement {
  const open = make("button", "maps-thumb");
  open.type = "button";
  open.title = "open the map on " + row.title;
  open.setAttribute("aria-label", "open the map on " + row.title);
  if (row.status === "ready") {
    const img = make("img");
    img.loading = "lazy";
    img.alt = "";
    img.src = "/api/maptiles/" + encodeURIComponent(row.id) + "/0/0/0";
    open.appendChild(img);
    open.onclick = function () {
      leaveDashThen(function () {
        askMode(row.id, true);
      });
    };
  } else open.disabled = true;
  return open;
}

export function renderTypes(parent: HTMLElement, body: MapsResponse): void {
  const card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "map types"));
  if (!body.types.length) {
    empty(card, "no map types yet", "generate one above, or put a pyramid under data/local and press add");
    parent.appendChild(card);
    return;
  }
  const list = make("div", "maps-list");
  list.setAttribute("role", "list");
  body.types.forEach(function (row) {
    const item = make("div", "maps-row" + (row.freshness.stale.length ? " stale" : ""));
    item.setAttribute("role", "listitem");
    item.appendChild(thumb(row));
    item.appendChild(nameCell(row, body));
    const meta = make("div", "maps-meta");
    meta.appendChild(make("span", "", "built " + isoDate(row.created)));
    meta.appendChild(make("span", "", bytes(row.bytes)));
    item.appendChild(meta);
    item.appendChild(freshnessCell(row));
    item.appendChild(actionsCell(row, body));
    list.appendChild(item);
  });
  card.appendChild(list);
  parent.appendChild(card);
}
