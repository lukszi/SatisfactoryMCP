/* The layer control: the map's legend, its only filter, and the two folds that make
 * thirty-five rows fit on a laptop.
 *
 * One file because it is one widget: the folds, the tri-state family boxes, the focus that has
 * to survive Leaflet emptying the list, and the batching that stops fourteen layer events
 * becoming twenty-eight renders are four halves of one problem. The radio sections above the
 * overlays are mode-picker.ts and floor-picker.ts, which hook in through `onDecorate`.
 *
 * This file knows about layers and nothing about what is drawn in them; `onSettled` is how.
 */

import { esc } from "../../kit/dom";
import { L } from "../leaflet";
import { fitWorld, map, NARROW } from "../map";
import { createListeners } from "../../app/listeners";
import { state } from "../../app/state";

import type { LayerInput, SectionPart } from "../leaflet-private";

export var control = L.control.layers(
  undefined,
  {},
  {
    collapsed: false,
    sortLayers: true,
    /* The ROW RANK, and the only thing on the page that reads one: [band, slot, name], stamped
     * onto every group by `clearedLayer()`. It is not draw order; layers.ts says why.
     *
     * `[9, 0, ""]` sorts a group that reached the control without a rank below every band;
     * `clearedLayer()` requires one, so it is only a defence. */
    sortFunction: function (a: L.Layer, b: L.Layer) {
      const ra = a._rank || [9, 0, ""];
      const rb = b._rank || [9, 0, ""];
      if (ra[0] !== rb[0]) return ra[0] - rb[0];
      if (ra[1] !== rb[1]) return ra[1] - rb[1];
      return ra[2] < rb[2] ? -1 : ra[2] > rb[2] ? 1 : 0;
    },
  }
).addTo(map);
state.control = control;
L.control.scale({ imperial: false }).addTo(map);

/* Thirty-five overlay rows plus four modes is 306x700 px fully open, which is why there are
 * folds at all.
 *
 * `collapsed: true` IS NOT THE FIX. This control is the map's legend and its only filter, so
 * Leaflet's own hover toggle would hide the page's one index behind a 36 px square drawn from
 * `vendor/images/layers.png` -- which this project does not vendor, and which would be the
 * page's only 404. All three folds below are the page's own.
 *
 *   * The head row folds the whole list to one strip that still says how many layers exist and
 *     how many are drawn, so "there ARE layers here" survives folding.
 *   * A section head folds one data-driven family. Which families exist and which start shut is
 *     declared by the module that draws them; `registerSection` below is the seam.
 *   * A radio section's head folds its radios (radio-section.ts). Its tail is the picked row's
 *     name rather than a count, since one row is always the answer.
 *
 * A section head also OWNS its family: the checkbox on it ticks or unticks every row at once,
 * in the three states such a box can honestly be in -- see sectionBox. That is a second gesture
 * on one row, so the two live on separate elements rather than being told apart by guesswork
 * about where inside the row the click landed.
 *
 * All of these survive a world switch, because they live in objects built once at module scope
 * and a switch replaces layer CONTENTS without rebuilding the control.
 */
/** One data-driven family of control rows, folded together and toggled together. */
export interface Section {
  /** This section's own name in `state.panel.sections`, where its fold is remembered. */
  key: string;
  /** The layer-name prefix whose rows are this family. The trailing space is load-bearing
   *  -- matching is `indexOf === 0`, and "node:" would also take "node:-something-else". */
  prefix: string;
  /** What the head calls the family, and what its two tooltips are phrased around. */
  title: string;
  /** Whether it starts unfolded. A family that grows with the world says no, because its head
   *  carries a count that answers "are the ore dots on?" without being opened. */
  startOpen: boolean;
}

/* The families, registered by the module that names them. The MECHANISM is this file's -- a
 * fold, a tri-state box, a count and a place in the render -- and WHICH families exist is the
 * business of whichever module creates their rows.
 *
 * Order here is registration order and is not load-bearing: a head is inserted immediately
 * before its own family's first row, and where that row sits was decided by the row rank.
 */
var SECTIONS: Section[] = [];

export function registerSection(section: Section): void {
  if (import.meta.env.DEV) {
    const clash = SECTIONS.filter(function (other) {
      return other.key === section.key || other.prefix === section.prefix;
    });
    if (clash.length) {
      console.error("two sections registered for " + section.key + "/" + section.prefix);
    }
  }
  SECTIONS.push(section);
  // Safe to write into `state.panel` from here because every caller is another module, and this
  // file has finished evaluating -- panel and all -- before any of them can be evaluated.
  state.panel.sections[section.key] = section.startOpen;
}

/* Every section sets its own starting fold -- a family as it registers, a radio section as it
 * is made -- so a section is one declaration in one file. */
state.panel = {
  open: !NARROW.matches,
  sections: {},
};

function sectionFor(name: string): Section | null {
  let found: Section | null = null;
  SECTIONS.forEach(function (section) {
    if (name.indexOf(section.prefix) === 0) found = section;
  });
  return found;
}

/* A control row back to the layer it toggles. Leaflet stamps the layer's id onto the
 * checkbox it builds, and clearedLayer() files the name under that same stamp, so the mapping
 * survives every re-render of the list without parsing the row's text back. */
function rowName(row: HTMLElement): string {
  const input = row.querySelector<LayerInput>("input");
  return (input && state.layerName[input.layerId]) || "";
}

function rowOn(row: HTMLElement): boolean {
  const input = row.querySelector("input");
  return !!(input && input.checked);
}

/* A control row back to the LayerGroup itself, for the one caller that has to toggle a
 * layer without a human clicking its box -- see setSection. */
function rowLayer(row: HTMLElement): L.LayerGroup | null {
  const input = row.querySelector<LayerInput>("input");
  return (input && state.layers[state.layerName[input.layerId]!]) || null;
}

export function fold(element: HTMLElement | null, folded: boolean): void {
  if (!element) return;
  if (folded) L.DomUtil.addClass(element, "layer-folded");
  else L.DomUtil.removeClass(element, "layer-folded");
}

/* A fold head, in the grammar every head shares: a caret, a title, and a tail at the right edge
 * that says what is inside without opening it. The tail is a STRING and not an "n of m",
 * because a radio head's answer is a name. `note` is the same sentence for the tooltip, phrased
 * by each caller: "drawn right now" is true of a family of layers and false of a radio. */
export function foldHead(
  element: HTMLElement,
  // `boolean | undefined`, because a section key not yet in `state.panel.sections` is a section
  // nobody has folded, which reads as closed here exactly as `false` does.
  open: boolean | undefined,
  title: string,
  tail: string,
  note: string
): void {
  element.setAttribute("role", "button");
  element.setAttribute("tabindex", "0");
  element.setAttribute("aria-expanded", open ? "true" : "false");
  element.innerHTML =
    '<span class="layer-caret">' +
    (open ? "&#9662;" : "&#9656;") +
    "</span>" +
    esc(title) +
    '<span class="layer-count">' +
    esc(tail) +
    "</span>";
  element.title = (open ? "hide " : "show ") + title + ": " + note;
}

/** What both counting heads put in their tail and their tooltip. */
function drawnOf(count: number, total: number): string {
  return count + " of " + total;
}

/* Both heads say `role="button"`, so both have to answer a keyboard the way a button
 * does. Every checkbox in this control is already reachable by Tab; a fold that could only
 * be opened with a pointer would put those checkboxes behind a mouse. */
export function onActivate(element: HTMLElement, action: () => void): void {
  L.DomEvent.on(element, "click", function (event) {
    L.DomEvent.stop(event);
    action();
  });
  L.DomEvent.on(element, "keydown", function (event) {
    const key = (event as KeyboardEvent).key;
    if (key !== "Enter" && key !== " ") return;
    L.DomEvent.stop(event);
    action();
  });
}

/* Ticking a family of fourteen is fourteen layer events, and Leaflet re-renders the whole list
 * on each one -- 28 full control renders for one click on "resource nodes". The cost is not the
 * point: every intermediate render DESTROYS the checkbox the pointer is on and re-runs the
 * focus restore against a half-toggled family, so the tri-state flickers through thirteen wrong
 * values.
 *
 * `_handlingClick` is Leaflet's own flag for exactly this -- its `_onLayerChange` skips the
 * re-render while it is set. The two decorators this file adds take the same hint, and one
 * render happens at the end. */
var batching = false;

/* Read through a function rather than exported as a variable, so the caller outside this file
 * gets the value at the moment it asks rather than at the moment it imported. */
export function isBatching() {
  return batching;
}

/* What runs once a batched change has settled, REGISTERED rather than imported: calling
 * `declutter()` by name here would make the layer control import the module that draws factory
 * labels, which imports the module that creates layers, which imports this one. The control's
 * claim is only that the list has stopped changing; who cares is main.ts's business. */
var settled = createListeners();

export var onSettled = settled.on;

/* The radio sections above the overlays, refreshed after every render of the list in the order
 * they registered; REGISTERED for the same reason as `onSettled`, since they import this file. */
var decorators = createListeners();

export var onDecorate = decorators.on;

/* Exported for the callers outside this file that also change several layers in one gesture.
 *
 * A function rather than a "please batch" flag, because the end of a batch is not just "stop
 * suppressing": it is one `_update` and then the settled passes, and a caller that had to
 * remember both would eventually remember one. */
export function batch(action: () => void): void {
  batching = true;
  control._handlingClick = true;
  try {
    action();
  } finally {
    control._handlingClick = false;
    batching = false;
  }
  control._update(); // one render, which re-runs decorateControl with the settled state
  settled.emit();
}

/* Every layer of one family at once. The layers are toggled directly rather than by
 * clicking their boxes: Leaflet's own `_onInputClick` would do the adding, but it ends by
 * calling `_refocusOnMap`, and a keyboard user who just pressed Space on the family box
 * would find focus on the map. */
function setSection(rows: HTMLElement[], on: boolean): void {
  batch(function () {
    rows.forEach(function (row) {
      const group = rowLayer(row);
      if (!group) return;
      if (on) map.addLayer(group);
      else map.removeLayer(group);
    });
  });
}

/* The family's own checkbox, and its third state.
 *
 * `indeterminate` is not decoration: a family with one member ticked would otherwise draw an
 * empty box, the same picture as a family with none, contradicting the "3 of 14" beside it.
 *
 * What a click MEANS is decided from the members, never from the box's own post-click state: a
 * click on an indeterminate box lands on a different `checked` value in different engines, and
 * "some are on, so turn them all on" is the rule regardless.
 */
function sectionBox(section: Section, rows: HTMLElement[]): HTMLInputElement {
  const on = rows.filter(rowOn).length;
  const box = L.DomUtil.create("input", "layer-section-box") as HTMLInputElement & SectionPart;
  box.type = "checkbox";
  box._section = section.key;
  box._part = "box";
  box.checked = on === rows.length;
  box.indeterminate = on > 0 && on < rows.length;
  box.title =
    (on === rows.length ? "hide" : "show") + " all " + rows.length + " " + section.title;
  box.setAttribute("aria-label", section.title + ", all " + rows.length);
  L.DomEvent.on(box, "click", function (event) {
    // stopPropagation, not stop(): preventDefault would cancel the native tick, and the
    // native result already agrees with what setSection is about to do in all three cases.
    L.DomEvent.stopPropagation(event);
    setSection(rows, on !== rows.length);
  });
  return box;
}

/* A section head is two controls on one row: the BOX toggles the family, the caret and title
 * fold it. The fold listener sits on the TEXT SPAN and not on the row, because a handler on the
 * row would also fire for a click on the box -- ticking "pickups" would fold the section shut
 * under the pointer in the same gesture. */
function sectionHead(section: Section, rows: HTMLElement[]): HTMLElement {
  const head = L.DomUtil.create("div", "layer-section");
  head.appendChild(sectionBox(section, rows));
  const text = L.DomUtil.create("span", "layer-fold", head) as HTMLSpanElement & SectionPart;
  text._section = section.key;
  text._part = "fold";
  const open = state.panel.sections[section.key];
  const tail = drawnOf(rows.filter(rowOn).length, rows.length);
  foldHead(text, open, section.title, tail, tail + " drawn right now");
  onActivate(text, function () {
    state.panel.sections[section.key] = !state.panel.sections[section.key];
    decorateControl();
  });
  return head;
}

/* The top head is FOLD-ONLY: no master checkbox. A family box is undoable, because its rows are
 * all on or all off either way; a master box is not, because this control's rows are not
 * uniform, so one click that unticked all 35 would throw the selection away and re-ticking
 * would turn all 35 on rather than restore it.
 *
 * The MODE radios are not counted here: they are not overlays, they live outside the list this
 * head measures, and "how many of four modes are drawn" has one answer forever. */
function panelHead(rows: HTMLElement[]): HTMLElement {
  const container = control.getContainer()!;
  let head = container.querySelector<HTMLElement>(".layers-head");
  if (!head) {
    head = L.DomUtil.create("div", "layers-head");
    onActivate(head, function () {
      setLayersOpen(!state.panel.open);
      layersToggled.emit(state.panel.open);
    });
    // First child, ahead of Leaflet's own (permanently hidden) toggle anchor: the head is
    // what stays on screen when the list folds, so it has to be the top of the box.
    container.insertBefore(head, container.firstChild);
  }
  const tail = drawnOf(rows.filter(rowOn).length, rows.length);
  foldHead(head, state.panel.open, "layers", tail, tail + " drawn right now");
  fold(head, false);
  if (state.panel.open) L.DomUtil.removeClass(head, "shut");
  else L.DomUtil.addClass(head, "shut");
  return head;
}

/** Who to tell when the reader folds or unfolds the whole list from its head. */
var layersToggled = createListeners<[boolean]>();

export var onLayersToggle = layersToggled.on;

export function setLayersOpen(open: boolean): void {
  state.panel.open = open;
  decorateControl();
}

function dockLegend(list: HTMLElement): void {
  const legend = document.getElementById("legend") as HTMLDetailsElement | null;
  if (!legend || legend.parentNode === list) return;
  list.appendChild(legend);
  const key = legend;
  L.DomEvent.on(key, "keydown", function (event) {
    if ((event as KeyboardEvent).key !== "Escape" || !key.open) return;
    L.DomEvent.stop(event);
    key.open = false;
    key.querySelector("summary")!.focus();
  });
}

/* Which half of which section head holds the keyboard, as a value that can outlive the element
 * holding it. Reading `document.activeElement` inside the decorator is enough on a fold click,
 * where the decorator does the removing; it is NOT enough for a family box, because Leaflet's
 * `_update` empties the overlays list first and activeElement is <body> by the time the
 * decorator runs. THE MARK IS TAKEN BEFORE THE WIPE. */
/** Which half of which section head held the keyboard, as a value, not an element. */
interface FocusMark {
  key: string;
  part: "box" | "fold" | undefined;
}

function focusMark(): FocusMark | null {
  const active = document.activeElement as SectionPart | null;
  return active && active._section ? { key: active._section, part: active._part } : null;
}

var pendingFocus: FocusMark | null = null;

/* Re-applied after every render of the list, and idempotent: Leaflet empties the overlay
 * list on each `_update`, so the section heads are rebuilt rather than moved. */
function decorateControl(): void {
  if (batching) return; // one render at the end of the batch, not one per member layer
  const container = control.getContainer();
  if (!container) return;
  const list = container.querySelector(".leaflet-control-layers-overlays");
  if (!list) return;
  // A section head is replaced, not updated, so keyboard focus would land on a removed node and
  // the next Enter would go to the document. Restored below.
  const focused = focusMark() || pendingFocus;
  pendingFocus = null;
  const heads: Element[] = Array.prototype.slice.call(list.querySelectorAll(".layer-section"));
  heads.forEach(function (head) {
    head.parentNode!.removeChild(head);
  });
  const rows: HTMLElement[] = Array.prototype.slice.call(list.querySelectorAll("label"));
  const grouped: Record<string, HTMLElement[]> = {};
  rows.forEach(function (row) {
    fold(row, false);
    const section = sectionFor(rowName(row));
    if (section) (grouped[section.key] = grouped[section.key] || []).push(row);
  });
  SECTIONS.forEach(function (section) {
    const members = grouped[section.key];
    if (!members || !members.length) return;
    const open = state.panel.sections[section.key];
    members.forEach(function (row) {
      fold(row, !open);
    });
    const head = sectionHead(section, members);
    list!.insertBefore(head, members[0]!);
    if (focused && focused.key === section.key) {
      const again = head.querySelector<HTMLElement>(
        focused.part === "box" ? ".layer-section-box" : ".layer-fold"
      );
      if (again) again.focus();
    }
  });
  panelHead(rows);
  decorators.emit();
  const whole = container.querySelector<HTMLElement>(".leaflet-control-layers-list");
  if (whole) dockLegend(whole);
  fold(whole, !state.panel.open);
}

(function () {
  const update = control._update;
  control._update = function (this: L.Control.Layers, ...args: unknown[]) {
    pendingFocus = focusMark() || pendingFocus; // before the wipe; see focusMark
    const result = (update as (...a: unknown[]) => unknown).apply(this, args);
    decorateControl();
    return result as void;
  };
  // A checkbox click does not re-render the list, so the "n of m" counts would go stale
  // the moment anyone used the thing they are counting.
  map.on("overlayadd overlayremove", decorateControl);
  decorateControl();
})();

/* Flying to a factory label is one click; getting back out was zoom-out spam. One
 * house-shaped button under the zoom control reframes the whole world. */
(function () {
  const home = new L.Control({ position: "topleft" });
  home.onAdd = function () {
    const bar = L.DomUtil.create("div", "leaflet-bar");
    const a = L.DomUtil.create("a", "", bar);
    a.href = "#";
    a.innerHTML = "&#8962;";
    a.title = "whole world";
    a.setAttribute("aria-label", "whole world");
    a.setAttribute("role", "button");
    L.DomEvent.on(a, "click", function (event) {
      L.DomEvent.preventDefault(event);
      fitWorld();
    });
    return bar;
  };
  home.addTo(map);
})();
