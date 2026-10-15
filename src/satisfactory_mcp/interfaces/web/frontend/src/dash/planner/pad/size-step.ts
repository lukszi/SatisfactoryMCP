/* The pad's snap step, picked in the site card: whole foundations, half ones, or off. Kept per
 * browser; a blocked storage just starts on whole foundations. */

import { subTabs } from "../../../kit/dashkit";
import { make } from "../../../kit/dom";
import { FOUNDATION_M, HALF_M } from "./geometry";

const STORE_KEY = "siteSizeStep";
const STEPS = [String(FOUNDATION_M), String(HALF_M), "0"];

function load(): number {
  try {
    const saved = localStorage.getItem(STORE_KEY) || "";
    return STEPS.indexOf(saved) >= 0 ? Number(saved) : FOUNDATION_M;
  } catch {
    return FOUNDATION_M;
  }
}

let step = load();

/** 8, 4, or 0 when snapping is off. */
export function sizeStep(): number {
  return step;
}

/** `snap [8 m] [4 m] [off]`, repainting through `changed`. */
export function sizeStepRow(changed: () => void): HTMLElement {
  const row = make("div", "site-step");
  row.appendChild(make("span", "site-field-k", "snap"));
  const items = [
    { id: STEPS[0]!, label: FOUNDATION_M + " m" },
    { id: STEPS[1]!, label: HALF_M + " m" },
    { id: STEPS[2]!, label: "off" },
  ];
  row.appendChild(
    subTabs(items, String(step), function (id) {
      step = Number(id);
      try {
        localStorage.setItem(STORE_KEY, id);
      } catch {
        /* the pick still holds for this page */
      }
      changed();
    }, "pad snap step")
  );
  return row;
}
