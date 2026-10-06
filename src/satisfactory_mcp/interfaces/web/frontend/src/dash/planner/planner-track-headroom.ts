/* Track's startup headroom: the measured and nameplate buttons and the given field, each one
 * stored on the plan as a versioned write. See docs/planner-p4_contract.md §2 F3. */

import { fieldError, toggleButton } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count, mw } from "../../kit/format";
import { stageHeadroom } from "./planner-reads";
import { bench } from "./planner-state";
import { applyOps } from "./planner-writes";
import { WORDS } from "../../kit/words";

import type { TrackResponse } from "../../api/shapes";

var HEADROOM_MAX = 1000000;
var HEADROOM_WHAT: Record<string, string> = {
  measured: "what the grid has free now",
  nameplate: "generation minus every built machine running at once",
};

var headroomProblem = { key: "", text: "", raw: "" };

function setHeadroom(value: number | null): void {
  var plan = bench.plan;
  if (!plan || (plan.headroom_mw === undefined ? null : plan.headroom_mw) === value) return;
  applyOps([{ op: "set", field: "headroom_mw", value: value }]);
}

function clearHeadroomProblem(field: HTMLInputElement): void {
  headroomProblem = { key: "", text: "", raw: "" };
  fieldError(field, "");
}

/** The typed headroom; it shows the stored value only when no button stands for it. */
function givenField(parent: HTMLElement, stored: number | null, pressable: number[]): void {
  var field = make("input", "dash-number");
  field.type = "number";
  field.step = "any";
  field.min = "0";
  var shown = stored !== null && pressable.indexOf(stored) < 0 ? String(stored) : "";
  var key = bench.key;
  var refused = headroomProblem.key === key && headroomProblem.text;
  field.value = refused ? headroomProblem.raw : shown;
  field.defaultValue = shown;
  field.setAttribute("data-ctl", "track-headroom");
  field.setAttribute("aria-label", "given startup headroom in MW");
  var commit = function () {
    var raw = field.value.trim();
    if (raw === field.defaultValue && !headroomProblem.text) return;
    var n = Number(raw);
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

function headroomButton(parent: HTMLElement, which: string, value: number, stored: number | null): void {
  var fallback = which === stageHeadroom();
  var kept = floorTen(value);
  var title = fallback
    ? "use the save's " + which + " headroom, " + HEADROOM_WHAT[which] + ": the stage headroom setting"
    : kept > 0
      ? "store the " + which + " headroom, " + mw(kept)
      : "the " + which + " headroom is not above 0 MW in this save";
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
  var plan = bench.plan!;
  var stored = plan.headroom_mw === undefined ? d.headroom_mw : plan.headroom_mw;
  var card = make("section", "dash-card plan-bench track-controls");
  var row = make("div", "plan-row");
  row.appendChild(make("span", "plan-label", WORDS.startupHeadroom));
  var body = make("div", "plan-controls");
  var measured = d.power.measured_headroom_mw;
  var nameplate = d.power.headroom_mw;
  headroomButton(body, "measured", measured, stored);
  headroomButton(body, "nameplate", nameplate, stored);
  givenField(body, stored, [floorTen(measured), floorTen(nameplate)]);
  row.appendChild(body);
  card.appendChild(row);
  card.appendChild(make("p", "dash-note", "startup order uses " + mw(d.startup.headroom_mw) + ", " + d.startup.headroom_source));
  parent.appendChild(card);
}
