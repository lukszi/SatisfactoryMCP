/* An icon chosen with "change icon", kept in this browser only (per world), else the default the
 * page works out. A stored choice "" means "no icon". Factories and plans keep separate stores. */

import { state } from "../../app/state";
import { cls, html, type Safe } from "../../kit/html";

import type { FactoryHealthRow } from "../../api/shapes";

type Choices = Record<string, Record<string, string>>;

export interface IconStore {
  /** The chosen icon class, "" for a chosen blank, undefined for no choice. */
  chosen(name: string): string | undefined;
  /** null forgets the choice. */
  choose(name: string, icon: string | null): void;
  /** Follows a rename so the choice stays with its owner. */
  move(was: string, to: string): void;
}

export function iconStore(key: string): IconStore {
  let choices: Choices = {};
  try {
    const raw = localStorage.getItem(key);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    if (parsed && typeof parsed === "object") choices = parsed as Choices;
  } catch (_e) {
    /* nothing kept */
  }
  const store: IconStore = {
    chosen(name) {
      const world = choices[state.world];
      return world ? world[name] : undefined;
    },
    choose(name, icon) {
      const world = (choices[state.world] = choices[state.world] || {});
      if (icon === null) delete world[name];
      else world[name] = icon;
      try {
        localStorage.setItem(key, JSON.stringify(choices));
      } catch (_e) {
        /* kept for this page session only */
      }
    },
    move(was, to) {
      const icon = store.chosen(was);
      if (icon === undefined) return;
      store.choose(to, icon);
      store.choose(was, null);
    },
  };
  return store;
}

export const factoryIcons = iconStore("satisfactory.factoryIcons");

export function factoryIcon(row: Pick<FactoryHealthRow, "name" | "product_icon">): string | null {
  const chosen = factoryIcons.chosen(row.name);
  if (chosen !== undefined) return chosen || null;
  return row.product_icon;
}

/** The lead slot: the icon, or an empty slot of the same size, never letters. */
export function iconLead(icon: string | null, title: string, extra?: string): Safe {
  if (!icon) return html`<i class="${cls("noic", extra)}" aria-hidden="true"></i>`;
  return html`<img class="${cls("ic", extra)}" src="/api/icons/${encodeURIComponent(icon)}" alt="" title="${title}" loading="lazy">`;
}

export function factoryLead(row: Pick<FactoryHealthRow, "name" | "product_icon" | "product">, extra?: string): Safe {
  const title = factoryIcons.chosen(row.name) !== undefined ? "chosen icon" : row.product ? "main product: " + row.product : "";
  return iconLead(factoryIcon(row), title, extra);
}
