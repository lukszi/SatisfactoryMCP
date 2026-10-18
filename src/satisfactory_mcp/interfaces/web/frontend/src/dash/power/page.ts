/* The Power page (`dash=power`), from /api/power/circuits; a circuit's page is circuit.ts.
 * docs/frontend_kit.md, "Power". */

import "./power.css";
import { html, mount, type Safe } from "../../kit/html";
import { blockHead, dashH, emptyLine, fig, figSub } from "../../kit/ui/text";
import { failedInto, loadingInto } from "../../kit/ui/states";
import { WORDS } from "../../kit/words";
import { loadOne } from "../../app/load";
import { vitals } from "../../app/vitals";
import { ratedWorld, readHeadroomFull, readHeadroomNow, unrated, type Rated } from "./readings";
import { problemsOf, worldUncounted } from "./model";
import { supplyBlock, type Supply } from "./supply";
import { circuitList, problemList, wirePowerLists } from "./lists";

import type { CircuitRow, CircuitsResponse } from "../../api/shapes";

/** The circuits heading's tooltip; the Overview's power block carries it too. */
export const CIRCUITS_TIP =
  "worst first · the bar is " + WORDS.consumption + " and " + WORDS.maxConsumption + " against the circuit's " + WORDS.capacity + ", the tick at 100% · what stands " + WORDS.behindSwitch + " counts in " + WORDS.maxConsumption + " only, batteries in neither";

export function retryCircuits(): void {
  loadOne("/api/power/circuits");
}

/** True when the circuits are not read yet; the loading or error line is drawn instead. */
export function missing(body: HTMLElement): boolean {
  const v = vitals();
  if (v.circuits) return false;
  if (v.circuitsError) failedInto(body, "power circuits", "", retryCircuits);
  else loadingInto(body, "power circuits");
  return true;
}

/** Headroom now as the figure, headroom at maximum consumption under it; `now` replaces the
 *  first where the caller reads it otherwise (a circuit with no generator). */
export function headroomFigs(rated: Rated, now = readHeadroomNow(rated)): Safe {
  const full = readHeadroomFull(rated);
  return html`${fig(now.value, WORDS.headroomNow, now.bad ? "bad" : undefined)}${figSub(html`<span class="${full.bad ? "bad-t" : ""}" title="${WORDS.headroomFull + ": " + WORDS.maxConsumptionTip}">${full.value} ${WORDS.atMaxConsumption}</span>`)}`;
}

export function supplyOf(gens: CircuitsResponse["generators"], ledger: CircuitRow["ledger"], rated: Rated): Supply {
  return { gens, gen: ledger.generation_mw, cap: ledger.capacity_mw, np: ledger.draw_mw, ms: ledger.measured_draw_mw, off: ledger.switched_off_draw_mw, unknown: unrated(rated) };
}

export function mountPage(body: HTMLElement, markup: Safe): HTMLElement {
  const root = document.createElement("div");
  root.className = "power-page";
  mount(root, markup);
  wirePowerLists(root);
  body.appendChild(root);
  return root;
}

export function renderPower(body: HTMLElement): void {
  if (missing(body)) return;
  const data = vitals().circuits!;
  const rated = ratedWorld(data);
  const problems = problemsOf(data);
  const supply = html`<section class="card">${blockHead("supply and demand", headroomFigs(rated))}${supplyBlock(supplyOf(data.generators, data.world, rated), worldUncounted(data))}</section>`;
  const problemPart = html`<section>${dashH(WORDS.powerProblems, problems.length)}${problems.length ? problemList(problems) : emptyLine("no " + WORDS.powerProblems)}</section>`;
  const circuits = html`<section>${dashH("circuits", data.circuits.length, "h2", CIRCUITS_TIP)}${data.circuits.length ? circuitList(data.circuits) : emptyLine("no circuits", "nothing stands on a wire yet")}</section>`;
  mountPage(body, html`<div class="pw">${supply}${problemPart}${circuits}</div>`);
}
