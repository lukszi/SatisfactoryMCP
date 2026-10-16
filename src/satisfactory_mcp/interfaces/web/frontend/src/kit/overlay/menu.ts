/* The one floating ⋯ menu: opened under its button, arrows move, Esc or an outside click
 * closes it and returns focus to the button. */

import { html, mount } from "../html";

export interface MenuItem {
  label: string;
  href?: string;
  act?: () => void;
}

let owner: HTMLElement | null = null;
let actions: MenuItem[] = [];

function box(): HTMLElement {
  let menu = document.getElementById("menu");
  if (menu) return menu;
  menu = document.createElement("div");
  menu.id = "menu";
  menu.className = "menu";
  menu.setAttribute("role", "menu");
  menu.hidden = true;
  document.body.appendChild(menu);
  wire(menu);
  return menu;
}

export function closeMenu(refocus?: boolean): void {
  const menu = document.getElementById("menu");
  if (menu) menu.hidden = true;
  if (owner) {
    owner.setAttribute("aria-expanded", "false");
    if (refocus) owner.focus();
  }
  owner = null;
}

export function openMenu(button: HTMLElement, items: MenuItem[]): void {
  if (owner === button) {
    closeMenu(true);
    return;
  }
  closeMenu();
  const menu = box();
  actions = items;
  mount(
    menu,
    html`${items.map(function (item, i) {
      return item.href ? html`<a role="menuitem" href="${item.href}" data-i="${i}">${item.label}</a>` : html`<button type="button" role="menuitem" data-i="${i}">${item.label}</button>`;
    })}`
  );
  menu.hidden = false;
  const r = button.getBoundingClientRect();
  const w = menu.offsetWidth;
  menu.style.top = r.bottom + window.scrollY + 2 + "px";
  menu.style.left = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)) + window.scrollX + "px";
  button.setAttribute("aria-expanded", "true");
  owner = button;
  menu.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
}

function step(menu: HTMLElement, from: Element, by: number): void {
  const items = Array.prototype.slice.call(menu.querySelectorAll('[role="menuitem"]')) as HTMLElement[];
  const at = items.indexOf(from as HTMLElement);
  items[(at + by + items.length) % items.length]?.focus();
}

function wire(menu: HTMLElement): void {
  menu.addEventListener("click", function (event) {
    const hit = (event.target as Element).closest<HTMLElement>("[data-i]");
    if (!hit) return;
    const item = actions[Number(hit.dataset.i)];
    closeMenu(!item?.href);
    if (item?.act) item.act();
  });
  menu.addEventListener("keydown", function (event) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      step(menu, event.target as Element, event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Escape") {
      event.stopPropagation();
      closeMenu(true);
    }
  });
  document.addEventListener("click", function (event) {
    if (!owner) return;
    const target = event.target as Node;
    if (!menu.contains(target) && !owner.contains(target)) closeMenu();
  });
  window.addEventListener("resize", function () {
    closeMenu();
  });
}

/** Every `.more[data-menu]` button inside `root` opens its labels; `pick` hears the choice. */
export function wireMoreMenus(root: HTMLElement, pick: (label: string, button: HTMLElement) => void): void {
  root.addEventListener("click", function (event) {
    const button = (event.target as Element).closest<HTMLElement>(".more[data-menu]");
    if (!button) return;
    const labels = (button.dataset.menu || "").split("|").filter(Boolean);
    openMenu(
      button,
      labels.map(function (label) {
        return {
          label: label,
          act: function () {
            pick(label, button);
          },
        };
      })
    );
  });
}
