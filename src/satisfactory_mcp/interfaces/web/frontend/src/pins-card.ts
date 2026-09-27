/* The pins card on the plans list: every live pin, with map, rename, copy and delete.
 * See docs/planner-p3_contract.md §2 F4. */

import { button, chip, empty, error, fieldError, link, loading, table } from "./dashkit";
import { COPY_ATTR, COPY_CLASS, make } from "./dom";
import { dropPin, LABEL_MAX, pinStore, refetchPins, renamePin, showPin } from "./pins";
import { counted, PIN_KIND } from "./words";

import type { Column, SortState } from "./dashkit";
import type { PinRow } from "./api-shapes";

var editing = { n: 0, fresh: false };
var order: SortState = { key: "pin", desc: false };

function labelCell(p: PinRow, redraw: () => void): HTMLElement | string {
  if (editing.n !== p.n) return p.label || "–";
  var box = make("input", "dash-name pin-rename");
  box.value = box.defaultValue = p.label;
  box.setAttribute("data-ctl", "pin-label:" + p.n);
  box.setAttribute("aria-label", "label for " + p.id);
  var settled = false;
  var stop = function () {
    settled = true;
    editing.n = 0;
    redraw();
  };
  var commit = function () {
    if (settled) return;
    var wanted = box.value.trim();
    if (wanted.length > LABEL_MAX) {
      fieldError(box, "a label is at most " + LABEL_MAX + " characters");
      return;
    }
    if (wanted === p.label) {
      stop();
      return;
    }
    settled = true;
    box.defaultValue = box.value;
    renamePin(p, wanted).then(function () {
      editing.n = 0;
      redraw();
    });
  };
  box.onkeydown = function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      commit();
    } else if (event.key === "Escape") {
      event.stopPropagation();
      box.value = box.defaultValue;
      stop();
    }
  };
  box.onblur = commit;
  if (editing.fresh) {
    editing.fresh = false;
    setTimeout(function () {
      box.focus();
      box.select();
    }, 0);
  }
  return box;
}

function place(p: PinRow): HTMLElement | string {
  if (p.gone) return "";
  if (p.x_m !== null && p.y_m !== null) {
    return button(
      "map",
      function () {
        showPin(p);
      },
      { map: true, title: "fly the map to " + p.id + " and open it", label: "show " + p.id + " on the map" }
    );
  }
  if (p.ref.plan) return link("planner/" + p.ref.plan, "open plan");
  return "";
}

function actions(p: PinRow, redraw: () => void): HTMLElement {
  var box = make("span", "dash-acts");
  var where = place(p);
  if (where) box.appendChild(typeof where === "string" ? make("span", "", where) : where);
  box.appendChild(
    button(
      "rename",
      function () {
        editing.n = p.n;
        editing.fresh = true;
        redraw();
      },
      { label: "rename " + p.id, disabled: editing.n === p.n }
    )
  );
  var copy = make("button", "btn " + COPY_CLASS, "copy");
  copy.type = "button";
  copy.title = "copy " + p.id + " for chat";
  copy.setAttribute("aria-label", "copy " + p.id);
  copy.setAttribute(COPY_ATTR, p.id);
  box.appendChild(copy);
  box.appendChild(
    button(
      "delete",
      function () {
        dropPin(p);
      },
      { title: "delete " + p.id + "; its number is not reused", label: "delete " + p.id }
    )
  );
  return box;
}

export function renderPins(parent: HTMLElement, redraw: () => void): void {
  var card = make("section", "dash-card");
  var got = pinStore();
  var rows = got.data ? got.data.pins : [];
  card.appendChild(make("h2", "dash-h", got.data && rows.length ? "pins · " + counted(rows.length, "pin") : "pins"));
  if (got.error && !got.data) error(card, "the pins", got.error, refetchPins);
  else if (!got.data) loading(card, "pins");
  else if (!rows.length) empty(card, "no pins yet: pin a plan, a process, or a place on the map");
  else {
    var columns: Column<PinRow>[] = [
      {
        key: "pin",
        label: "pin",
        sort: function (p) {
          return p.n;
        },
        render: function (p) {
          return p.id;
        },
      },
      {
        key: "what",
        label: "what",
        sort: function (p) {
          return PIN_KIND[p.kind] || p.kind;
        },
        render: function (p) {
          return p.text;
        },
      },
      {
        key: "label",
        label: "label",
        sort: function (p) {
          return p.label;
        },
        render: function (p) {
          return labelCell(p, redraw);
        },
      },
      {
        key: "state",
        label: "state",
        render: function (p) {
          if (!p.gone) return "live";
          var cell = make("span", "");
          cell.appendChild(chip("gone", "muted", p.gone_why));
          cell.appendChild(make("span", "dash-sub", p.gone_why));
          return cell;
        },
      },
      {
        key: "acts",
        label: "",
        render: function (p) {
          return actions(p, redraw);
        },
      },
    ];
    card.appendChild(table(columns, rows, { sort: order, onSort: redraw, caption: "pins" }));
  }
  parent.appendChild(card);
}
