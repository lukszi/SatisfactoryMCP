/* The floor picker: the MODE radios' sibling, drawn here and decided in floors.ts through
 * `onFloorPick` and `onFloorExit`, so the control never learns what a storey is. A floor is a
 * word plus a measurement, a MINOR band reads as subordinate to its storey, and the section
 * names what is being sliced and offers a way out.
 *
 * The section only exists while the page is in floor mode: `hideFloors` REMOVES it, because an
 * empty picker over a world map is a question with no subject. */

import { esc } from "../../kit/dom";
import { L } from "../leaflet";
import { state } from "../../app/state";
import { onDecorate } from "./control";
import { radioSection } from "./radio-section";

/** One row of the FLOOR section: a storey, and what makes it worth picking. */
export interface FloorChoice {
  key: string;
  label: string;
  /** The measurement under the name: height, area, and what stands on it. */
  detail: string;
  /** A mezzanine rather than a storey, by its share of its platform's largest band. */
  minor: boolean;
  note: string;
}

var FLOOR_SECTION = "floors";

/** What to say instead of rows when there are none. The API's own sentence, never a blank. */
var floorMessage = "";

var pickFloor: (key: string) => void = function () {};
var leaveFloor: () => void = function () {};

export function onFloorPick(pick: (key: string) => void): void {
  pickFloor = pick;
}

export function onFloorExit(leave: () => void): void {
  leaveFloor = leave;
}

var floors = radioSection<FloorChoice>({
  sectionKey: FLOOR_SECTION,
  groupName: "floor-band",
  ariaLabel: "floor",
  className: "layer-floors",
  title: "",
  emptyNote: "nothing to show one floor of",
  rowKey: function (choice) {
    return choice.key + ":" + choice.detail;
  },
  rowHtml: function (choice) {
    return " " + esc(choice.label) + '<span class="layer-floor-detail">' + esc(choice.detail) + "</span>";
  },
  rowClass: function (choice) {
    return choice.minor ? "layer-floor layer-floor-minor" : "layer-floor";
  },
  decorateHead: function (head) {
    // The way out, on the head: leaving is not one of the floors, so it is not a row among
    // them. ESC does the same thing and is not discoverable.
    const out = L.DomUtil.create("button", "layer-floor-exit", head) as HTMLButtonElement;
    out.type = "button";
    out.innerHTML = "&#10005;";
    out.title = "leave floor mode (Esc): the whole world again";
    out.setAttribute("aria-label", "leave floor mode");
    L.DomEvent.on(out, "click", function (event) {
      L.DomEvent.stop(event);
      leaveFloor();
    });
  },
  // A sentence, not an empty list: a save too old to record foundations, or a factory on bare
  // terrain, is an answer, and an empty picker would read as a failure to load.
  message: {
    className: "layer-floor-note",
    text: function () {
      return floorMessage;
    },
  },
  onPick: function (key) {
    pickFloor(key);
  },
});

/** The rows, the subject they are of, and which one is drawn. `message` replaces the rows
 *  when the answer is a sentence rather than a list. */
export function showFloors(title: string, rows: FloorChoice[], active: string, message: string): void {
  floorMessage = message;
  state.panel.sections[FLOOR_SECTION] = true; // arriving in floor mode opens the picker
  floors.show(rows, active, title);
}

export function hideFloors(): void {
  floorMessage = "";
  floors.hide();
}

onDecorate(floors.render);
