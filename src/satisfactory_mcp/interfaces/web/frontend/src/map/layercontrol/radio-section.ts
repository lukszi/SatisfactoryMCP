/* A folded section of radios at the top of the layer list: one question with one answer, drawn
 * here and decided by whoever configures it. The base-map modes and the floor picker are two.
 *
 * Built ONCE and updated in place, unlike the family heads Leaflet wipes on every render:
 * rebuilding real radios would drop the keyboard mid-arrow-walk. So the box lives where
 * `_update` does not reach -- a child of the list element, ahead of Leaflet's own base and
 * overlay divs -- and inside the list, so the page's one fold puts it away with the rest. */

import { L } from "../leaflet";
import { state } from "../../app/state";
import { control, fold, foldHead, onActivate } from "./control";

/** What every radio row carries: its value, its name, and its tooltip. */
export interface RadioChoice {
  key: string;
  label: string;
  note: string;
}

export interface RadioSectionConfig<C extends RadioChoice> {
  /** This section's key in `state.panel.sections`, where its fold is remembered. */
  sectionKey: string;
  /** The radio group's name, which is the whole of what makes the rows exclusive. */
  groupName: string;
  ariaLabel: string;
  /** The box's class. */
  className: string;
  /** The head's title when `show` is not given one. */
  title: string;
  /** The head's tooltip when no row is picked. */
  emptyNote: string;
  /** The part of one row that, when it changes, means the box is rebuilt. */
  rowKey(choice: C): string;
  /** The row's markup after the radio; every value in it must already be escaped. */
  rowHtml(choice: C): string;
  rowClass(choice: C): string;
  /** Build-time extras inside a row, after its text. */
  decorateRow?(choice: C, holder: HTMLElement): void;
  /** Render-time extras for a row, after its radio is set. */
  refreshRow?(choice: C, row: HTMLElement, input: HTMLInputElement): void;
  /** Build-time extras on the head, after its fold. */
  decorateHead?(head: HTMLElement): void;
  /** A sentence shown above the rows, or instead of them; its element's class and text. */
  message?: { className: string; text(): string };
  /** An element of the caller's kept under the rows and folded with them. */
  tail?(): HTMLElement;
  onPick(key: string): void;
}

export interface RadioSection<C extends RadioChoice> {
  /** The rows as they now stand, which one is picked, and optionally a new head title. */
  show(choices: C[], active: string, title?: string): void;
  /** Take the section out of the list altogether. */
  hide(): void;
  render(): void;
}

/* Both pickers start open: the modes answer "why is the map dark?", and arriving in floor mode
 * is a gesture that should show its picker. */
export function radioSection<C extends RadioChoice>(config: RadioSectionConfig<C>): RadioSection<C> {
  let choices: C[] = [];
  let active = "";
  let title = config.title;
  let box: HTMLElement | null = null;
  let head: HTMLElement | null = null;
  let rows: Record<string, HTMLElement> = {};
  let inputs: Record<string, HTMLInputElement> = {};
  let built = "";
  state.panel.sections[config.sectionKey] = true;

  function messageText(): string {
    return config.message ? config.message.text() : "";
  }

  function build(list: HTMLElement): HTMLElement {
    const made = L.DomUtil.create("div", config.className);
    made.setAttribute("role", "group");
    made.setAttribute("aria-label", config.ariaLabel);
    const headRow = L.DomUtil.create("div", "layer-section", made);
    head = L.DomUtil.create("span", "layer-fold", headRow);
    onActivate(head, function () {
      state.panel.sections[config.sectionKey] = !state.panel.sections[config.sectionKey];
      render();
    });
    if (config.decorateHead) config.decorateHead(headRow);
    rows = {};
    inputs = {};
    const said = messageText();
    if (config.message && said) {
      const note = L.DomUtil.create("div", config.message.className, made);
      note.textContent = said;
    }
    choices.forEach(function (choice) {
      // Leaflet's own row shape -- label > span > (input, span) -- so the radios line up with
      // the checkboxes below them.
      const row = L.DomUtil.create("label", config.rowClass(choice), made);
      const holder = L.DomUtil.create("span", "", row);
      const input = L.DomUtil.create("input", "", holder) as HTMLInputElement;
      input.type = "radio";
      input.name = config.groupName;
      input.value = choice.key;
      const text = L.DomUtil.create("span", "", holder);
      text.innerHTML = config.rowHtml(choice);
      if (config.decorateRow) config.decorateRow(choice, holder);
      // `change`, not `click`: an arrow key inside a radio group is a pick like any other, and a
      // disabled radio fires neither.
      L.DomEvent.on(input, "change", function () {
        if (input.checked) config.onPick(choice.key);
      });
      rows[choice.key] = row;
      inputs[choice.key] = input;
    });
    list.insertBefore(made, list.firstChild);
    return made;
  }

  function container(): HTMLElement | null {
    const outer = control.getContainer();
    if (!outer) return null;
    const list = outer.querySelector<HTMLElement>(".leaflet-control-layers-list");
    if (!list) return null;
    const key = title + "|" + messageText() + "|" + choices.map(config.rowKey).join(",");
    if (box?.parentNode === list && built === key) return box;
    if (box?.parentNode) box.parentNode.removeChild(box);
    built = key;
    box = build(list);
    return box;
  }

  function render(): void {
    if (!choices.length && !messageText()) return; // nothing to ask: no section at all
    const current = container();
    if (!current || !head) return;
    const open = state.panel.sections[config.sectionKey];
    let picked = "";
    choices.forEach(function (choice) {
      const row = rows[choice.key];
      const input = inputs[choice.key];
      if (!row || !input) return;
      input.checked = choice.key === active;
      if (config.refreshRow) config.refreshRow(choice, row, input);
      row.title = choice.note;
      fold(row, !open);
      if (choice.key === active) picked = choice.label;
    });
    if (config.message) {
      const note = current.querySelector<HTMLElement>("." + config.message.className);
      if (note) fold(note, !open);
    }
    if (config.tail) {
      const tail = config.tail();
      if (tail.parentNode !== current) current.appendChild(tail);
      fold(tail, !open);
    }
    foldHead(head, open, title, picked, picked ? "showing " + picked : config.emptyNote);
  }

  return {
    show: function (next, picked, nextTitle) {
      choices = next;
      active = picked;
      if (nextTitle !== undefined) title = nextTitle;
      render();
    },
    hide: function () {
      choices = [];
      built = "";
      if (box?.parentNode) box.parentNode.removeChild(box);
      box = null;
      head = null;
      rows = {};
      inputs = {};
    },
    render: render,
  };
}
