/* The base-map MODE radios above the overlays: "which picture is the base map" is one question
 * with one answer, while the overlays are two dozen independent ones -- which is why a mode
 * switch leaves every overlay as the player left it. Drawn here, decided in tiles.ts through
 * `onModePick`, so this widget never learns what a tile pyramid is. */

import { esc } from "../../kit/dom";
import { L } from "../leaflet";
import { onDecorate } from "./control";
import { partsBox } from "./part-picker";
import { radioSection } from "./radio-section";

/** One row of the MODE section: a radio, and why it can or cannot be picked. */
export interface ModeChoice {
  key: string;
  label: string;
  /** An amber word for a type whose data is outdated, and why. */
  flag?: string;
  flagTitle?: string;
  /** false greys the row out; `note` then says which generator would fill it. */
  ready: boolean;
  /** The row's tooltip either way: what this picture is, or what would draw it. */
  note: string;
}

/* Who to tell when a radio is picked. ONE owner rather than a list: "which picture is the base
 * map" has a single answer applied in a single place, and a second listener could only disagree. */
let pickMode: (key: string) => void = function () {};

export function onModePick(pick: (key: string) => void): void {
  pickMode = pick;
}

const modes = radioSection<ModeChoice>({
  sectionKey: "modes",
  groupName: "basemap-mode",
  ariaLabel: "base map",
  className: "layer-modes",
  title: "base map",
  emptyNote: "no base map chosen yet",
  rowKey: function (choice) {
    return [choice.key, choice.label, choice.flag || ""].join("|");
  },
  rowHtml: function (choice) {
    return " " + esc(choice.label);
  },
  rowClass: function () {
    return "layer-mode";
  },
  decorateRow: function (choice, holder) {
    if (!choice.flag) return;
    const flag = L.DomUtil.create("span", "layer-mode-flag", holder);
    flag.textContent = choice.flag;
    flag.title = choice.flagTitle || "";
  },
  refreshRow: function (choice, row, input) {
    // Disabled, not hidden: the row is where the page says which tool would draw this picture.
    input.disabled = !choice.ready;
    if (choice.ready) L.DomUtil.removeClass(row, "layer-mode-off");
    else L.DomUtil.addClass(row, "layer-mode-off");
  },
  tail: partsBox,
  onPick: function (key) {
    pickMode(key);
  },
});

/** The modes as they now stand, and which one is drawn. Called once when the probes land
 *  and again on every switch -- including the one a failed tile forces. */
export function showModes(rows: ModeChoice[], active: string): void {
  modes.show(rows, active);
}

onDecorate(modes.render);
