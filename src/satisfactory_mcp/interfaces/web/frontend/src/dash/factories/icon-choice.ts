/* The icon a factory or plan shows: the one chosen with "change icon", kept on the server (in
 * the factory's label, or as plan state), else the default the page works out. A chosen ""
 * means "no icon". docs/frontend_kit.md, "Factories". */

import { send } from "../../api/client";
import { loadOne } from "../../app/load";
import { state } from "../../app/state";
import { onVitals, vitals } from "../../app/vitals";
import { cls, html, type Safe } from "../../kit/html";
import { fail, friendlyError } from "../../kit/toast";
import { requestRender } from "../actions";
import { browserStore, takeLocalChoices } from "./local-icons";

import type { FactoryHealthRow } from "../../api/shapes";

export interface IconStore {
  /** The chosen icon class, "" for a chosen blank, undefined for no choice. */
  chosen(id: string): string | undefined;
  /** Saves the choice; null forgets it. Shows at once, rereads after. */
  choose(id: string, icon: string | null): void;
}

/** A row's choice as the store reads it: null (no choice) becomes undefined. */
export function choiceOf(icon: string | null | undefined): string | undefined {
  return icon === null ? undefined : icon;
}

function healthRow(name: string): FactoryHealthRow | undefined {
  return vitals().health?.factories.filter((r) => r.name === name)[0];
}

export const factoryIcons: IconStore = {
  chosen: (name) => choiceOf(healthRow(name)?.icon),
  choose(name, icon) {
    const row = healthRow(name);
    if (row) row.icon = icon;
    send("POST", "/api/labels/icon", { name, icon })
      .catch((failure) => fail("the icon for “" + name + "” was not saved: " + friendlyError(failure)))
      .then(() => loadOne("/api/factories/health"));
  },
};

export function factoryIcon(row: Pick<FactoryHealthRow, "icon" | "product_icon">): string | null {
  return row.icon !== null ? row.icon || null : row.product_icon;
}

/** The lead slot: the icon, or an empty slot of the same size, never letters. */
export function iconLead(icon: string | null, title: string, extra?: string): Safe {
  if (!icon) return html`<i class="${cls("noic", extra)}" aria-hidden="true"></i>`;
  return html`<img class="${cls("ic", extra)}" src="/api/icons/${encodeURIComponent(icon)}" alt="" title="${title}" loading="lazy">`;
}

export function factoryLead(row: Pick<FactoryHealthRow, "icon" | "product_icon" | "product">, extra?: string): Safe {
  const title = row.icon !== null ? "chosen icon" : row.product ? "main product: " + row.product : "";
  return iconLead(factoryIcon(row), title, extra);
}

const adopted = new Set<string>();

/* A factory choice kept in this browser moves to its label, unless the label has one. */
function adoptFactoryIcons(): void {
  const health = vitals().health;
  if (!health || !state.world || adopted.has(state.world)) return;
  adopted.add(state.world);
  const local = takeLocalChoices(browserStore(), "satisfactory.factoryIcons", state.world);
  Object.keys(local).forEach(function (name) {
    const row = healthRow(name);
    if (row && row.icon === null) factoryIcons.choose(name, local[name]!);
  });
  if (Object.keys(local).length) requestRender();
}

onVitals(adoptFactoryIcons);
adoptFactoryIcons();
