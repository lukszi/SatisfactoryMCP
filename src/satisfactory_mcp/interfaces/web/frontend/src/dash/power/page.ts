/* The Power page (`dash=power`) and a circuit's page (`dash=power/<n>`), from
 * /api/power/circuits and the factory health read. docs/frontend_kit.md, "Power". */

import "./power.css";
import { html, mount, type Safe } from "../../kit/html";
import { blockHead, dashH, emptyLine, fig, figSub, note, pageHead } from "../../kit/ui/text";
import { failedInto, loadingInto } from "../../kit/ui/states";
import { counted, WORDS } from "../../kit/words";
import { count } from "../../kit/format";
import { loadOne } from "../../app/load";
import { vitals } from "../../app/vitals";
import { hashFor } from "../../map/map";
import { ratedCircuit, ratedWorld, readHeadroomFull, readHeadroomNow, unrated, type Rated } from "./readings";
import { circuitUncounted, problemsOf, worldUncounted } from "./model";
import { supplyBlock } from "./supply";
import { circuitChips, circuitList, circuitMap, factoryList, problemList, wirePowerLists } from "./lists";

import type { CircuitRow, CircuitsResponse } from "../../api/shapes";

/** The circuits heading's tooltip; the Overview's power block carries it too. */
export const CIRCUITS_TIP = "worst first · the bar is drawing now and if all run against the circuit's generation, the tick at 100% · switches read as closed, batteries are not counted";

export function retryCircuits(): void {
  loadOne("/api/power/circuits");
}

/* True when the circuits are not read yet; the loading or error line is drawn instead. */
function missing(body: HTMLElement): boolean {
  const v = vitals();
  if (v.circuits) return false;
  if (v.circuitsError) failedInto(body, "power circuits", "", retryCircuits);
  else loadingInto(body, "power circuits");
  return true;
}

/** Headroom now as the figure, headroom if all run under it. */
export function headroomFigs(rated: Rated): Safe {
  const now = readHeadroomNow(rated);
  const full = readHeadroomFull(rated);
  return html`${fig(now.value, WORDS.headroomNow, now.bad ? "bad" : undefined)}${figSub(html`<span class="${full.bad ? "bad-t" : ""}" title="${"headroom " + WORDS.ifAllRun + ": " + WORDS.ifAllRunTip}">${full.value} ${WORDS.ifAllRun}</span>`)}`;
}

function supplyOf(gens: CircuitsResponse["generators"], ledger: CircuitRow["ledger"], rated: Rated): Parameters<typeof supplyBlock>[0] {
  return { gens, gen: ledger.generation_mw, np: ledger.draw_mw, ms: ledger.measured_draw_mw, unknown: unrated(rated) };
}

function mountPage(body: HTMLElement, markup: Safe): void {
  const root = document.createElement("div");
  root.className = "power-page";
  mount(root, markup);
  wirePowerLists(root);
  body.appendChild(root);
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

function circuitNote(row: CircuitRow): string {
  const paused = row.ledger.paused ? ", " + count(row.ledger.paused) + " " + WORDS.paused : "";
  return counted(row.consumers, "consumer") + paused + " · " + counted(row.poles, "pole or tower", WORDS.polesAndTowers);
}

export function renderCircuit(body: HTMLElement, subject: string): void {
  if (missing(body)) return;
  const data = vitals().circuits!;
  const row = data.circuits[+subject - 1];
  const crumbs: [string, string][] = [["Power", hashFor("power")]];
  if (!row) {
    mountPage(body, html`${pageHead({ crumbs, name: "circuit " + subject })}${emptyLine("no circuit " + subject + " in this world", "circuit numbers can change between saves; pick one on Power")}`);
    return;
  }
  const rated = ratedCircuit(row);
  const head = pageHead({ crumbs, name: "circuit " + (row.index + 1), chips: circuitChips(row), acts: circuitMap(row), figs: headroomFigs(rated), note: circuitNote(row) });
  const supply = html`<section class="card">${blockHead("supply and demand")}${supplyBlock(supplyOf(row.generators, row.ledger, rated), circuitUncounted(row))}</section>`;
  const problems = problemsOf(data, row.index);
  const problemPart = problems.length ? html`${dashH(WORDS.powerProblems, problems.length)}${problemList(problems)}` : "";
  const health = vitals().health;
  const factories = row.factories.length ? html`${dashH("factories on this circuit", row.factories.length)}${factoryList(row.factories, health ? health.factories : [])}` : "";
  mountPage(body, html`${head}${supply}${problemPart}${factories}${note("circuit numbers follow size and can change with the next save")}`);
}
