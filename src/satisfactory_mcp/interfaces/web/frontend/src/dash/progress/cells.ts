/* The pieces the Progress sections share: amounts as words, the tier bars, the spoiler note
 * and the card whose done rows hide behind a checkbox. */

import { appendNote, checkbox, settingsLinkNote, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { formatNumber } from "../../kit/format";

import type { Column, SortState } from "../../kit/dashkit";

export const PLACES: Record<string, string> = {
  carried: "carried",
  storage: "in storage containers",
  depot: "in the Dimensional Depot",
};

export const STOCK = "checked against spendable stock: carried, storage containers and the Dimensional Depot";

export function amountList(rows: { name: string; amount: number }[]): string {
  return (
    rows
      .map(function (row) {
        return formatNumber(row.amount) + " " + row.name;
      })
      .join(", ") || "–"
  );
}

export function amountsByPlace(rows: { name: string; amount: number }[]): string {
  return rows
    .map(function (row) {
      return formatNumber(row.amount) + " " + (PLACES[row.name] || row.name);
    })
    .join(", ");
}

export function spoilerNote(body: HTMLElement, text: string): void {
  settingsLinkNote(body, text + "; ", " can show them");
}

export function tierProgressBar(label: string, done: number, total: number): HTMLElement {
  const box = make("div", "dash-tier");
  box.appendChild(make("span", "dash-tier-k", label));
  const bar = make("div", "dash-hbar");
  const fill = make("span", "dash-mix-ok");
  fill.style.width = (total ? (done / total) * 100 : 0) + "%";
  bar.appendChild(fill);
  box.appendChild(bar);
  box.appendChild(make("span", "dash-tier-v", done + "/" + total));
  return box;
}

export function doneFilteredCard<R>(
  title: string,
  showDone: boolean,
  setShowDone: (on: boolean) => void,
  rows: R[],
  isDone: (row: R) => boolean,
  columns: Column<R>[],
  sort: SortState,
  allDoneText: string
): HTMLElement {
  const card = make("section", "dash-card");
  const bar = make("div", "dash-title");
  bar.appendChild(make("h2", "dash-h", title));
  bar.appendChild(checkbox("show done", showDone, setShowDone));
  card.appendChild(bar);
  const listed = rows.filter(function (row) {
    return showDone || !isDone(row);
  });
  if (!listed.length) appendNote(card, allDoneText);
  else card.appendChild(table(columns, listed, { sort: sort, caption: title }));
  return card;
}
