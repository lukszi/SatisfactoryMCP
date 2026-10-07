/* Progress > Hard drives: the analysed drives waiting for a pick, and what each option grants. */

import { appendNote, heading, table, tile } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { flow, formatNumber } from "../../kit/format";
import { drives, waiting } from "./feeds";

import type { Column } from "../../kit/dashkit";
import type { DriveRow } from "../../api/shapes";

type Option = DriveRow["options"][number];

interface OptionRow {
  drive: DriveRow;
  option: Option;
  first: boolean;
  last: boolean;
}

function grants(option: Option): string {
  if (option.slots) return "+" + option.slots + " inventory slots";
  if (!option.recipes.length) return "nothing new: its recipes are already unlocked";
  return option.recipes
    .map(function (recipe) {
      const made = recipe.products
        .map(function (product) {
          return flow(product.name, product.amount, 2);
        })
        .join(" + ");
      return (made || recipe.name) + (recipe.machine ? " · " + recipe.machine : "");
    })
    .join("; ");
}

const DRIVE_COLUMNS: Column<OptionRow>[] = [
  {
    key: "drive",
    label: "drive",
    align: "right",
    render: function (row) {
      if (!row.first) return "";
      return row.drive.hard_drive_id ?? "?";
    },
  },
  {
    key: "rerolls",
    label: "rerolls",
    align: "right",
    render: function (row) {
      return row.first ? row.drive.rerolls_left : "";
    },
  },
  {
    key: "option",
    label: "option",
    render: function (row) {
      return row.option.name;
    },
  },
  {
    key: "grants",
    label: "grants",
    title: "rates are per machine at 100%",
    render: function (row) {
      return grants(row.option);
    },
  },
];

function optionRows(pending: DriveRow[]): OptionRow[] {
  const rows: OptionRow[] = [];
  pending.forEach(function (drive) {
    drive.options.forEach(function (option, i) {
      rows.push({ drive: drive, option: option, first: i === 0, last: i === drive.options.length - 1 });
    });
  });
  return rows;
}

export function renderDrives(body: HTMLElement): void {
  const data = drives.data;
  if (!data) {
    waiting(body, drives);
    return;
  }
  let rerolls = 0;
  data.drives.forEach(function (drive) {
    rerolls += drive.rerolls_left;
  });
  const tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("pending choices", formatNumber(data.drives.length), "analysed drives waiting for a pick"));
  tiles.appendChild(tile("rerolls left", formatNumber(rerolls), "across the pending drives"));
  tiles.appendChild(tile("unanalysed", formatNumber(data.spare), "hard drives on hand"));
  if (data.last_used !== null) tiles.appendChild(tile("last drive analysed", "drive " + data.last_used, "the newest drive the MAM has analysed"));
  body.appendChild(tiles);

  const card = make("section", "dash-card");
  heading(card, "pending hard drives");
  if (!data.drives.length) appendNote(card, "no hard drive is waiting for a pick");
  else {
    card.appendChild(
      table(DRIVE_COLUMNS, optionRows(data.drives), {
        caption: "pending hard drives",
        rowClass: function (row) {
          return row.last ? "" : "dash-lead";
        },
      })
    );
    appendNote(card, "the option not picked returns to the pool; only the drive is spent");
  }
  body.appendChild(card);
}
