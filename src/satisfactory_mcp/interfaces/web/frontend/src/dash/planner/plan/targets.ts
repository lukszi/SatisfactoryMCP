/* The targets form under the result band: the plan's request, each change one version. Goal,
 * exports, sources, recipe lists, supply, power settings, stage headroom and notes. */

import { html, type Safe } from "../../../kit/html";
import { form, frow } from "../../../kit/ui/forms";
import { counted, OBJECTIVES } from "../../../kit/words";
import { hours, nearestStopIndex, PAYBACK_HOURS, paybackView, overclockWords, priceWords, snapshotRecipes, stopWords, switchLine } from "../power";
import { bench, changed, displayName, knownItem, NAME_MAX, NOTES_MAX } from "../state";
import { applyOps, banOps } from "../writes";
import { itemIcon, lookupOf } from "./parts";

import type { Op } from "../state";

const CLOCKS = [1, 1.5, 2, 2.5];
const POWER_ITEM = /^(mw|power|__mw__)$/i;
const HEADROOM_MAX = 1000000;

let fieldErrors: Record<string, string> = {};
let rejected: Record<string, string> = {};
let errorsFor = "";

function invalid(ctl: string, text: string, raw?: string): void {
  fieldErrors[ctl] = text;
  if (raw !== undefined) rejected[ctl] = raw;
  changed();
}

function problem(ctl: string): Safe {
  return fieldErrors[ctl] ? html`<p class="invalid" role="alert">${fieldErrors[ctl]}</p>` : html``;
}

function bad(ctl: string): Safe {
  return fieldErrors[ctl] ? html` aria-invalid="true"` : html``;
}

function numberBox(id: string, value: number | null, unit: string, label: string, placeholder?: string): Safe {
  const shown = rejected[id] ?? (value === null ? "" : String(value));
  return html`<span class="fnum"><input type="number" id="${id}" data-ctl="${id}" step="any" value="${shown}"${placeholder ? html` placeholder="${placeholder}"` : ""} aria-label="${label}"${bad(id)}><span class="u">${unit}</span></span>`;
}

function addBox(id: string, placeholder: string, items?: boolean): Safe {
  return html`<span class="fadd"><input type="text" id="${id}" data-ctl="${id}" placeholder="${placeholder}" aria-label="${placeholder.replace(/^\+ /, "")}" maxlength="${NAME_MAX}" value="${rejected[id] || ""}"${items ? html` list="plan-items"` : ""}${bad(id)}></span>`;
}

/** A removable chip; its × pushes the removal of every member it stands for. */
function chip(text: string, field: string, members: string[]): Safe {
  return html`<span class="pchip">${text}<button type="button" data-pt="rm" data-field="${field}" data-members="${members.join("\n")}" aria-label="${"remove " + text}">×</button></span>`;
}

function chipsOf(field: "required" | "banned"): Safe {
  const list = bench.plan!.args[field];
  if (!list.length) return html`<span class="note">none</span>`;
  return html`<span class="fchips">${list.map((m) => chip(displayName(m), field, [m]))}</span>`;
}

function goalRow(): Safe {
  const args = bench.plan!.args;
  const options = Object.keys(OBJECTIVES).map((k) => html`<option value="${k}"${k === args.objective ? html` selected` : ""}>${OBJECTIVES[k]}</option>`);
  const target =
    args.objective === "max_item"
      ? html`<input type="text" id="pt-target" data-ctl="pt-target" list="plan-items" placeholder="item to maximise" aria-label="item to maximise" value="${rejected["pt-target"] ?? args.target_item ?? ""}"${bad("pt-target")}>${problem("pt-target")}`
      : "";
  return frow("goal", html`<select id="pt-goal" data-ctl="pt-goal">${options}</select>${target}`, { forId: "pt-goal" });
}

function exportLine(id: string, rate: number | null): Safe {
  const look = lookupOf(bench.result);
  const name = displayName(id);
  const ctl = "pt-rate:" + id;
  const shown = rejected[ctl] ?? (rate === null ? "" : String(rate));
  return html`<div class="fx"><span class="fnum"><input type="number" id="${ctl}" data-ctl="${ctl}" data-item="${id}" step="any" min="0" placeholder="any" value="${shown}" aria-label="${"minimum " + name + " per minute"}"${bad(ctl)}><span class="u">/min</span></span><span class="fxn">${itemIcon(look, name)}${name}</span><button type="button" class="btn icon" data-pt="rm-export" data-item="${id}" aria-label="${"remove export " + name}" title="${"stop exporting " + name}">×</button></div>${problem(ctl)}`;
}

function exportsRow(): Safe {
  const args = bench.plan!.args;
  const ids = args.exports.filter((id) => id !== "MW");
  Object.keys(args.export_minimums).forEach((id) => {
    if (id !== "MW" && ids.indexOf(id) < 0) ids.push(id);
  });
  const power = args.exports.indexOf("MW") >= 0;
  const mwLine = power
    ? html`<div class="fx"><span class="fnum"><span class="u"></span></span><span class="fxn">power</span><button type="button" class="btn icon" data-pt="mw" aria-label="stop exporting MW" title="stop exporting power; the plan may then draw from the grid">×</button></div>`
    : "";
  const mwButton = ids.length && !power ? html`<button type="button" class="btn" data-pt="mw" title="export power too; the plan then may not draw from the grid">export MW</button>` : "";
  const none = !ids.length && !power ? html`<span class="note">power only (the default)</span>` : "";
  return frow("exports", html`${ids.map((id) => exportLine(id, id in args.export_minimums ? args.export_minimums[id]! : null))}${mwLine}${none}<span class="fadd">${addBox("pt-export-add", "+ export an item", true)}${mwButton}</span>${problem("pt-export-add")}`);
}

function sourcesRow(): Safe {
  const sources = bench.plan!.args.sources;
  const groups: Record<string, string[]> = {};
  const plain: string[] = [];
  sources.forEach(function (member) {
    const resource = member.indexOf("node:") === 0 && displayName(member) !== member ? displayName(member) : "";
    if (resource) (groups[resource] = groups[resource] || []).push(member);
    else plain.push(member);
  });
  const chips = plain.map((m) => chip(m, "sources", [m])).concat(Object.keys(groups).map((r) => chip(counted(groups[r]!.length, r + " node"), "sources", groups[r]!)));
  const shown = chips.length ? html`<span class="fchips">${chips}</span>` : html`<span class="note">the whole map</span>`;
  return frow("from", html`${shown}${addBox("pt-source-add", "+ selector, e.g. region:Grass Fields")}`);
}

function clockButtons(): Safe {
  const chosen = bench.plan!.args.extractor_clocks;
  const offered = chosen.length ? chosen : [1];
  return html`<span class="seg" role="group" aria-label="extractor clocks">${CLOCKS.map(function (c) {
    const on = offered.indexOf(c) >= 0;
    const only = on && offered.length === 1;
    return html`<button type="button" data-pt="clock" data-clock="${String(c)}" aria-pressed="${String(on)}"${on ? html` class="on"` : ""}${only ? html` disabled title="extractors need at least one clock"` : ""}>${c * 100}%</button>`;
  })}</span>`;
}

function useDefault(field: string): Safe {
  return html`<button type="button" class="btn" data-pt="default" data-field="${field}" title="follow the shared setting again">use default</button>`;
}

function powerRows(): Safe {
  const args = bench.plan!.args;
  const pay = paybackView();
  const at = pay ? pay.hours : (args.payback_hours ?? 0);
  const still = !!pay && !pay.splits;
  const words = (still ? pay!.reason : stopWords(pay, at)) + (pay?.inherited ? " · shared default" : "");
  const change = switchLine();
  const range = html`<span class="frange"><input type="range" id="pt-payback" data-ctl="pt-payback" min="0" max="${PAYBACK_HOURS.length - 1}" value="${nearestStopIndex(at)}" aria-label="payback horizon in hours of play" aria-valuetext="${hours(at)}"${still ? html` disabled` : ""}><output for="pt-payback" aria-live="polite">${words}</output></span>`;
  const on = pay ? pay.overclock.on : args.overclock_last === true;
  const ocWords = overclockWords(pay) + (pay?.overclock.inherited ? " · shared default" : "");
  return html`${frow(html`<span title="${"extra, slower machines must repay their build in saved power" + priceWords(pay)}">payback</span>`, html`${range}${args.payback_hours !== null ? useDefault("payback_hours") : ""}${change ? html`<p class="note" role="status">${change}</p>` : ""}`, { forId: "pt-payback" })}
    ${frow("overclock last", html`<span class="checks"><input type="checkbox" role="switch" class="switch" id="pt-oclast" data-ctl="pt-oclast"${on ? html` checked` : ""} aria-label="overclock the last machine"><span class="note">${ocWords}</span></span>${args.overclock_last !== null ? useDefault("overclock_last") : ""}`, { forId: "pt-oclast", sub: true })}`;
}

/** The form, with its errors kept until the plan changes. */
export function targetsForm(): Safe {
  if (errorsFor !== bench.key) {
    errorsFor = bench.key;
    fieldErrors = {};
    rejected = {};
  }
  const plan = bench.plan!;
  const args = plan.args;
  const headroom = plan.headroom_mw === undefined ? null : plan.headroom_mw;
  return html`<fieldset class="card ptargets"${bench.gone ? html` disabled` : ""}><legend class="sr">targets</legend>${form(html`${goalRow()}${exportsRow()}${sourcesRow()}
    ${frow("required recipes", chipsOf("required"))}
    ${frow("banned recipes", html`${chipsOf("banned")}${addBox("pt-ban-add", "+ ban a recipe or pattern")}`, { sub: true })}
    ${frow("water extractors", html`${numberBox("pt-water", args.water_extractors, "", "water extractors", "auto")}${problem("pt-water")}`, { forId: "pt-water" })}
    ${frow("somersloops", html`${numberBox("pt-sloops", args.sloops, "", "somersloops")}${problem("pt-sloops")}`, { forId: "pt-sloops", sub: true })}
    ${frow("extractor clocks", clockButtons(), { sub: true })}
    ${powerRows()}
    ${frow("stage headroom", html`${numberBox("pt-headroom", headroom, "MW", "startup headroom for the stages in MW", "setting")}${problem("pt-headroom")}`, { forId: "pt-headroom" })}
    ${frow("notes", html`<textarea id="pt-notes" data-ctl="pt-notes" rows="2" maxlength="${NOTES_MAX}" placeholder="notes for this plan" aria-label="notes">${plan.notes}</textarea>`, { forId: "pt-notes" })}`)}</fieldset>`;
}

/* ------------------------------------------------------------------ edits */

/** A typed number: null for blank, NaN for text that is not one. */
function numberOf(raw: string): number | null {
  if (raw === "") return null;
  const n = Number(raw);
  return isFinite(n) ? n : NaN;
}

/** The item `raw` names, "MW" for power, or null after marking `ctl` invalid. */
function itemOf(ctl: string, raw: string): string | null {
  if (POWER_ITEM.test(raw)) return "MW";
  const hit = knownItem(raw);
  if (!hit) invalid(ctl, "no item is called “" + raw + "”; pick one from the list", raw);
  return hit;
}

function setRate(id: string, raw: string, ctl: string): void {
  const n = numberOf(raw);
  const had = id in bench.plan!.args.export_minimums;
  if (n === null) {
    if (had) applyOps([{ op: "del", field: "export_minimums", item: id }]);
  } else if (!(n > 0)) invalid(ctl, "a rate is a positive number per minute, or blank for any", raw);
  else applyOps([{ op: "put", field: "export_minimums", item: id, value: n }]);
}

function setWhole(field: "water_extractors" | "sloops", ctl: string, raw: string, blank: number | null, words: string): void {
  const n = numberOf(raw);
  const value = n === null ? blank : n;
  if (value !== null && !(Number.isInteger(value) && value >= 0)) invalid(ctl, words, raw);
  else applyOps([{ op: "set", field: field, value: value }]);
}

function setHeadroom(raw: string): void {
  const n = numberOf(raw);
  if (n !== null && !(n > 0 && n <= HEADROOM_MAX)) invalid("pt-headroom", "a headroom is more than 0 and at most 1,000,000 MW, or blank for the setting", raw);
  else applyOps([{ op: "set", field: "headroom_mw", value: n }]);
}

function addText(ctl: string, raw: string): void {
  if (!raw) return;
  if (ctl === "pt-export-add") {
    const hit = itemOf(ctl, raw);
    if (hit) applyOps([{ op: "add", field: "exports", member: hit }]);
  } else if (ctl === "pt-source-add") applyOps([{ op: "add", field: "sources", member: raw }]);
  else if (ctl === "pt-ban-add") applyOps(banOps(raw));
}

/** A field of the form changed; false when `el` is not one of its fields. */
export function targetsChange(el: HTMLInputElement): boolean {
  const ctl = el.id;
  if (!/^pt-/.test(ctl) || !bench.plan) return false;
  const raw = el.value.trim();
  delete fieldErrors[ctl];
  delete rejected[ctl];
  el.defaultValue = el.value;
  if (ctl === "pt-goal") applyOps([{ op: "set", field: "objective", value: el.value }]);
  else if (ctl === "pt-target") {
    const hit = raw ? itemOf(ctl, raw) : "";
    if (hit !== null) applyOps([{ op: "set", field: "target_item", value: hit || null }]);
  } else if (ctl.indexOf("pt-rate:") === 0) setRate(el.dataset.item || "", raw, ctl);
  else if (ctl === "pt-water") setWhole("water_extractors", ctl, raw, null, "water extractors is a whole number, or blank for auto");
  else if (ctl === "pt-sloops") setWhole("sloops", ctl, raw, 0, "somersloops is a whole number");
  else if (ctl === "pt-headroom") setHeadroom(raw);
  else if (ctl === "pt-notes") applyOps([{ op: "set", field: "notes", value: el.value }]);
  else if (ctl === "pt-oclast") applyOps([{ op: "set", field: "overclock_last", value: el.checked }]);
  else if (ctl === "pt-payback") {
    const h = PAYBACK_HOURS[Number(el.value)]!;
    if (h === bench.plan.args.payback_hours) return true;
    snapshotRecipes();
    applyOps([{ op: "set", field: "payback_hours", value: h }]);
  } else addText(ctl, raw);
  return true;
}

/** The payback slider's words follow it before it is let go. */
export function targetsInput(el: HTMLInputElement): void {
  if (el.id !== "pt-payback") return;
  const out = el.parentElement?.querySelector("output");
  const pay = paybackView();
  if (out && !(pay && !pay.splits)) out.textContent = stopWords(pay, PAYBACK_HOURS[Number(el.value)]!);
}

function clockOps(clock: number): Op[] {
  const chosen = bench.plan!.args.extractor_clocks;
  const offered = chosen.length ? chosen : [1];
  const on = offered.indexOf(clock) >= 0;
  const ops: Op[] = [];
  if (!chosen.length && !on) ops.push({ op: "add", field: "extractor_clocks", member: 1 });
  ops.push({ op: on ? "remove" : "add", field: "extractor_clocks", member: clock });
  return ops;
}

/** The form's buttons; false when `target` is not one of them. */
export function targetsClick(target: Element): boolean {
  const b = target.closest<HTMLElement>("[data-pt]");
  if (!b || !bench.plan || bench.gone) return false;
  const args = bench.plan.args;
  const what = b.dataset.pt;
  if (what === "rm") {
    const field = b.dataset.field || "";
    applyOps((b.dataset.members || "").split("\n").map((m): Op => ({ op: "remove", field: field, member: m })));
  } else if (what === "rm-export") {
    const id = b.dataset.item || "";
    const ops: Op[] = [];
    if (args.exports.indexOf(id) >= 0) ops.push({ op: "remove", field: "exports", member: id });
    if (id in args.export_minimums) ops.push({ op: "del", field: "export_minimums", item: id });
    applyOps(ops);
  } else if (what === "mw") applyOps([{ op: args.exports.indexOf("MW") >= 0 ? "remove" : "add", field: "exports", member: "MW" }]);
  else if (what === "clock") applyOps(clockOps(Number(b.dataset.clock)));
  else if (what === "default") applyOps([{ op: "set", field: b.dataset.field || "", value: null }]);
  else return false;
  return true;
}
