/* The pins card on the plans list: every live pin, with map, rename, copy and delete.
 * See docs/planner-p3_contract.md §2 F4. */

import { askButton } from "./asks";
import { button, capRows, chip, copyButton, empty, error, inlineTextEdit, link, loading, table } from "../kit/dashkit";
import { make } from "../kit/dom";
import { dropPin, LABEL_MAX, pinStore, refetchPins, renamePin, showPin } from "./pins";
import { counted, PIN_KIND } from "../kit/words";

import type { Column, SortState } from "../kit/dashkit";
import type { AskAbout, PinRow } from "../api/shapes";

var editing = { pinNumber: 0, fresh: false };
var order: SortState = { key: "pin", desc: false };
var showAllPins = false;

function labelCell(pin: PinRow, redraw: () => void): HTMLElement | string {
  if (editing.pinNumber !== pin.n) return pin.label || "–";
  var focusNow = editing.fresh;
  editing.fresh = false;
  return inlineTextEdit({
    value: pin.label,
    ctl: "pin-label:" + pin.n,
    label: "label for " + pin.id,
    className: "dash-name pin-rename",
    focusNow: focusNow,
    stopEscape: true,
    validate: function (text) {
      return text.length > LABEL_MAX ? "a label is at most " + LABEL_MAX + " characters" : "";
    },
    onCommit: function (text) {
      if (text === pin.label) {
        editing.pinNumber = 0;
        redraw();
        return;
      }
      renamePin(pin, text).then(function () {
        editing.pinNumber = 0;
        redraw();
      });
    },
    onCancel: function () {
      editing.pinNumber = 0;
      redraw();
    },
  });
}

function locationCell(pin: PinRow): HTMLElement | string {
  if (pin.x_m !== null && pin.y_m !== null) {
    return button(
      "map",
      function () {
        showPin(pin);
      },
      { map: true, title: "fly the map to " + pin.id + " and open it", label: "show " + pin.id + " on the map" }
    );
  }
  if (pin.ref.plan && !pin.gone) return link("planner/" + pin.ref.plan, "open plan", "btn btn-map");
  return "";
}

function actionsCell(pin: PinRow, redraw: () => void): HTMLElement {
  var box = make("span", "dash-acts");
  var slot = make("span", "pin-place");
  var where = locationCell(pin);
  if (where) slot.appendChild(typeof where === "string" ? make("span", "", where) : where);
  box.appendChild(slot);
  box.appendChild(
    button(
      "rename",
      function () {
        editing.pinNumber = pin.n;
        editing.fresh = true;
        redraw();
      },
      { label: "rename " + pin.id, disabled: editing.pinNumber === pin.n }
    )
  );
  box.appendChild(copyButton(pin.id, "copy", { title: "copy " + pin.id + " for chat", label: "copy " + pin.id }));
  box.appendChild(
    button(
      "delete",
      function () {
        box.querySelectorAll("button").forEach(function (b) {
          b.disabled = true;
        });
        dropPin(pin);
      },
      { title: "delete " + pin.id + "; its number is not reused", label: "delete " + pin.id }
    )
  );
  var about: AskAbout = { kind: "pin", label: pin.id + " " + (pin.label || pin.text.replace(/ in “[^”]*”$/, "")), ref: pin.id };
  if (pin.ref.plan && !pin.gone) about.plan = pin.ref.plan;
  box.appendChild(askButton(about, "pin:" + pin.n));
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
        sort: function (pin) {
          return pin.n;
        },
        render: function (pin) {
          return pin.id;
        },
      },
      {
        key: "what",
        label: "what",
        sort: function (pin) {
          return PIN_KIND[pin.kind] || pin.kind;
        },
        render: function (pin) {
          return pin.text;
        },
      },
      {
        key: "label",
        label: "label",
        sort: function (pin) {
          return pin.label;
        },
        render: function (pin) {
          return labelCell(pin, redraw);
        },
      },
      {
        key: "state",
        label: "state",
        render: function (pin) {
          if (!pin.gone) return "live";
          var cell = make("span", "");
          cell.appendChild(chip("gone", "muted", pin.gone_why));
          cell.appendChild(make("span", "dash-sub", pin.gone_why));
          return cell;
        },
      },
      {
        key: "acts",
        label: "",
        render: function (pin) {
          return actionsCell(pin, redraw);
        },
      },
    ];
    var grid = table(columns, rows, { sort: order, onSort: redraw, caption: "pins" });
    card.appendChild(grid);
    capRows(card, grid, rows.length, 50, "show all " + counted(rows.length, "pin"), showAllPins, function () {
      showAllPins = true;
    });
  }
  parent.appendChild(card);
}
