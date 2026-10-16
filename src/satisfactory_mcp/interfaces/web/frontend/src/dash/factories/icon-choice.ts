/* A factory's icon: the one chosen with "change icon", kept in this browser only (per world),
 * else the main product the health read names. A stored choice "" means "no icon". */

import { state } from "../../app/state";
import { cls, html, type Safe } from "../../kit/html";

import type { FactoryHealthRow } from "../../api/shapes";

const KEY = "satisfactory.factoryIcons";

type Choices = Record<string, Record<string, string>>;

function load(): Choices {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? (parsed as Choices) : {};
  } catch (_e) {
    return {};
  }
}

const choices: Choices = load();

/** The chosen icon class, "" for a chosen blank, undefined for no choice. */
export function chosenIcon(name: string): string | undefined {
  const world = choices[state.world];
  return world ? world[name] : undefined;
}

export function chooseIcon(name: string, icon: string | null): void {
  const world = (choices[state.world] = choices[state.world] || {});
  if (icon === null) delete world[name];
  else world[name] = icon;
  try {
    localStorage.setItem(KEY, JSON.stringify(choices));
  } catch (_e) {
    /* kept for this page session only */
  }
}

/** Follows a rename so the choice stays with the factory. */
export function moveIcon(was: string, to: string): void {
  const icon = chosenIcon(was);
  if (icon === undefined) return;
  chooseIcon(to, icon);
  chooseIcon(was, null);
}

export function factoryIcon(row: Pick<FactoryHealthRow, "name" | "product_icon">): string | null {
  const chosen = chosenIcon(row.name);
  if (chosen !== undefined) return chosen || null;
  return row.product_icon;
}

/** The lead slot: the icon, or an empty slot of the same size, never letters. */
export function factoryLead(row: Pick<FactoryHealthRow, "name" | "product_icon" | "product">, extra?: string): Safe {
  const icon = factoryIcon(row);
  const title = chosenIcon(row.name) !== undefined ? "chosen icon" : row.product ? "main product: " + row.product : "";
  if (!icon) return html`<i class="${cls("noic", extra)}" aria-hidden="true"></i>`;
  return html`<img class="${cls("ic", extra)}" src="/api/icons/${encodeURIComponent(icon)}" alt="" title="${title}" loading="lazy">`;
}
