/* Track's startup headroom: the measured and nameplate buttons and the given field, each one
 * stored on the plan as a versioned write. See docs/planner-p4_contract.md §2 F3. */

import { fieldError, toggleButton } from "../../../kit/dashkit";
import { make } from "../../../kit/dom";
import { count, mw } from "../../../kit/format";
import { stageHeadroom } from "../reads";
import { bench } from "../state";
import { applyOps } from "../writes";
import { WORDS } from "../../../kit/words";

import type { TrackResponse } from "../../../api/shapes";

const HEADROOM_MAX = 1000000;
const HEADROOM_WHAT: Record<string, string> = {
  measured: "what the grid has free now",
  nameplate: "generation minus every built machine running at once",
};

let headroomProblem = { key: "", text: "", raw: "" };

function setHeadroom(value: number | null): void {
  const plan = bench.plan;
  if (!plan || (plan.headroom_mw === undefined ? null : plan.headroom_mw) === value) return;
  applyOps([{ op: "set", field: "headroom_mw", value: value }]);
}

function clearHeadroomProblem(field: HTMLInputElement): void {
  headroomProblem = { key: "", text: "", raw: "" };
  fieldError(field, "");
}

/** The typed headroom; it shows the stored value only when no button stands for it. */
function givenField(parent: HTMLElement, stored: number | null, pressable: number[]): void {
  const field = make("input", "dash-number");
  field.type = "number";
  field.step = "any";
  field.min = "0";
  const shown = stored !== null && pressable.indexOf(stored) < 0 ? String(stored) : "";
  const key = bench.key;
  const refused = headroomProblem.key === key && headroomProblem.text;
  field.value = refused ? headroomProblem.raw : shown;
  field.defaultValue = shown;
  field.setAttribute("data-ctl", "track-headroom");
  field.setAttribute("aria-label", "given startup headroom in MW");
  const commit = function () {
    const raw = field.value.trim();
    if (raw === field.defaultValue && !headroomProblem.text) return;
    const n = Number(raw);
    if (raw === "" || !isFinite(n) || !(n > 0) || n > HEADROOM_MAX) {
      headroomProblem = { key: key, text: "a headroom is more than 0 and at most " + count(HEADROOM_MAX) + " MW", raw: field.value };
      fieldError(field, headroomProblem.text);
      return;
    }
    clearHeadroomProblem(field);
    field.defaultValue = field.value;
    setHeadroom(n);
  };
  field.onchange = commit;
  field.onkeydown = function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      commit();
    } else if (event.key === "Escape") {
      field.value = field.defaultValue;
      clearHeadroomProblem(field);
      field.blur();
    }
  };
  parent.appendChild(make("span", "plan-sub", "given (MW)"));
  parent.appendChild(field);
  if (headroomProblem.key === key && headroomProblem.text) fieldError(field, headroomProblem.text);
}

/** Headroom is stored in whole tens of MW. */
function floorTen(value: number): number {
  return Math.floor(value / 10) * 10;
}

function headroomTitle(which: string, kept: number, fallback: boolean): string {
  if (fallback) return "use the save's " + which + " headroom, " + HEADROOM_WHAT[which] + ": the stage headroom setting";
  if (kept > 0) return "store the " + which + " headroom, " + mw(kept);
  return "the " + which + " headroom is not above 0 MW in this save";
}

function headroomButton(parent: HTMLElement, which: string, value: number, stored: number | null): void {
  const fallback = which === stageHeadroom();
  const kept = floorTen(value);
  const title = headroomTitle(which, kept, fallback);
  parent.appendChild(
    toggleButton(
      which + " " + mw(value),
      stored === null ? fallback : stored === kept,
      function () {
        setHeadroom(fallback ? null : kept);
      },
      { title: title, disabled: bench.gone || (!fallback && kept <= 0) }
    )
  );
}

export function headroomControls(parent: HTMLElement, d: TrackResponse): void {
  const plan = bench.plan!;
  const stored = plan.headroom_mw === undefined ? d.headroom_mw : plan.headroom_mw;
  const card = make("section", "dash-card plan-bench track-controls");
  const row = make("div", "plan-row");
  row.appendChild(make("span", "plan-label", WORDS.startupHeadroom));
  const body = make("div", "plan-controls");
  const measured = d.power.measured_headroom_mw;
  const nameplate = d.power.headroom_mw;
  headroomButton(body, "measured", measured, stored);
  headroomButton(body, "nameplate", nameplate, stored);
  givenField(body, stored, [floorTen(measured), floorTen(nameplate)]);
  row.appendChild(body);
  card.appendChild(row);
  card.appendChild(make("p", "dash-note", "startup order uses " + mw(d.startup.headroom_mw) + ", " + d.startup.headroom_source));
  parent.appendChild(card);
}
