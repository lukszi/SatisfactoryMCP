/* The to build tab, from /api/plan/track: the stages on the Progress band's rail, the job
 * checklist for the stage picked, the short items; or which factory counts as built.
 * docs/frontend_kit.md, "Plan page". */

import { go, goToMapThen } from "../../../app/nav";
import { vitals } from "../../../app/vitals";
import { busyButton } from "../../../kit/busy";
import { count, countRange, formatNumber, mw } from "../../../kit/format";
import { cls, html, type Safe } from "../../../kit/html";
import { seg } from "../../../kit/ui/controls";
import { acts, entry, group, list } from "../../../kit/ui/entry";
import { bar, chip } from "../../../kit/ui/marks";
import { loadingLine } from "../../../kit/ui/states";
import { dashH, emptyLine, fig, icon, toolrow } from "../../../kit/ui/text";
import { WORDS } from "../../../kit/words";
import { nodeLayers } from "../../../map/drawn/markers";
import { hashFor } from "../../../map/map";
import { showBox } from "../../../map/map-highlight";
import { stateTone } from "../../machine-states";
import { naming, pickCandidate, saveName, SCOPE_AUTO, SCOPE_NONE, SCOPE_WORLD, setBuiltScope, stopNaming } from "../built";
import { loadTrack } from "../reads";
import { ALL_STAGES, buildDash, planDash } from "../routes";
import { bench, changed, itemClass } from "../state";
import { writesSettled } from "../writes";
import { buildingIcon, lookupOf, sgn } from "./parts";

import type { TrackResponse, TrackRow, TrackStage } from "../../../api/shapes";
import type { BboxM } from "../../../map/geometry";

const VERB_ORDER = ["unpause", "setrecipe", "build"];

function starved(states: { state: string; count: number }[]): number {
  return states.filter((s) => s.state === "starved").reduce((n, s) => n + s.count, 0);
}

function stop(d: TrackResponse, s: TrackStage, shown: number, last: boolean): Safe {
  const on = s.index === d.current;
  const state = s.complete ? "done" : on ? "on" : "ahead";
  const rail = html`<span class="rail"><i class="node"></i>${last ? "" : html`<i class="${cls("ln", s.complete && "done")}"></i>`}</span>`;
  const hungry = starved(s.states);
  const draws = "draws " + mw(s.draw_mw) + (s.generation_mw ? " · makes " + mw(s.generation_mw) : "");
  const after = s.complete ? "" : html` · headroom <span class="${s.available_after < 0 ? "tone-bad" : ""}">${sgn(s.available_after)} MW</span>`;
  return html`<li class="${cls("stop", state, shown === s.index && "pick")}"${on ? html` aria-current="step"` : ""}><a class="nm" href="${hashFor(buildDash(bench.key, s.index))}">stage ${s.index}</a>${rail}<span class="st">${s.state}${hungry ? html` · <span class="tone-bad">${count(hungry)} starved</span>` : ""}</span><div class="segs"><span class="tseg"><span class="k">built</span><span class="v">${countRange(s.built, s.built_max)} of ${count(s.machines)}</span>${bar(s.machines ? (100 * s.built) / s.machines : 0)}</span></div><span class="st">${draws}${after}</span></li>`;
}

function stagesBlock(d: TrackResponse, shown: number): Safe {
  const b = d.built_at;
  const built = b.built === null ? "–" : countRange(b.built, b.built_max) + " of " + count(b.total);
  const room = d.power.measured_headroom_mw;
  const figs = html`${fig(built, "machines built", undefined, b.built !== null && b.total ? (100 * b.built) / b.total : undefined)}${fig(sgn(room) + " MW", WORDS.headroomNow, room < 0 ? "bad" : undefined)}`;
  const where = b.text ? html`<p class="note">${b.text} · <a href="${hashFor(buildDash(bench.key, 0, true))}">change</a></p>` : "";
  const notice = bench.track.notice ? html`<p class="note tone-blocked" role="status">${bench.track.notice} <button type="button" class="btn icon" data-pb="notice" aria-label="dismiss the stage notice">×</button></p>` : "";
  const warnings = d.startup.warnings.map((w) => html`<p class="note tone-blocked">${w}</p>`);
  const rail = d.stages.length
    ? html`<nav class="road proad" aria-label="startup stages"><ol class="road-in">${d.stages.map((s, i) => stop(d, s, shown, i === d.stages.length - 1))}</ol></nav>`
    : emptyLine("no startup order fits " + mw(d.startup.headroom_mw) + " of headroom", "give the plan its own stage headroom under targets");
  return html`<div class="bh">${dashH("stages", d.count)}${figs}</div>${where}${notice}${warnings}${rail}`;
}

function verbWords(r: TrackRow): [string, string] {
  const build = r.build > 0 || (r.build_max || 0) > 0 ? "build " + countRange(r.build, r.build_max) : "";
  if (r.verb === "unpause") return ["unpause " + count(r.count), build && "+ " + build];
  if (r.verb === "setrecipe") return ["set recipe " + count(r.count), build && "+ " + build];
  if (r.verb === "build") return [build, ""];
  return ["", ""];
}

function jobRow(r: TrackRow, look: ReturnType<typeof lookupOf>): Safe {
  const done = r.verb === "ok";
  const tone = r.states.some((s) => s.count && stateTone(s.state) === "bad") ? "bad" : r.states.some((s) => s.count && s.state === "paused") ? "mid" : "";
  const chips = r.states.filter((s) => s.count && stateTone(s.state) !== "ok").map((s) => chip(count(s.count) + " " + s.state, stateTone(s.state)));
  const where = r.stages.length ? " · " + (r.stages.length === 1 ? "stage " : "stages ") + r.stages.join(", ") : "";
  const [verb, then] = verbWords(r);
  const have = r.have_min === null ? count(r.have) : countRange(r.have_min, r.have);
  const map = r.bbox_m?.length === 4 ? html`<button type="button" class="btn icon" data-pb="map" data-row="${r.id}" aria-label="${"show " + r.process + " on the map"}" title="${"show " + r.process + " on the map"}">◎</button>` : html`<span class="btn-gap" aria-hidden="true"></span>`;
  return entry(
    { tone, title: r.note || r.process },
    html`<span class="lead-chk"><i class="${cls("chk", done && "ok")}" aria-hidden="true">${done ? "✓" : ""}</i>${buildingIcon(look, r.building)}</span><div class="id"><span class="name">${r.process}</span><div class="ctx">${chips}${r.new_building ? chip("new building", "muted", "not unlocked or not yet on the ground anywhere") : ""} ${r.building}${where}</div></div><div class="${cls("metric", done && "none")}"><b>${verb || "standing"}</b>${then ? html`<span class="line2">${then}</span>` : ""}</div><div class="metric"><b>${have}</b><span>of ${count(r.need)}</span>${bar(r.need ? (100 * r.have) / r.need : 0)}</div>${acts(map)}`
  );
}

function jobsBlock(d: TrackResponse, shown: number): Safe {
  const rows = shown === ALL_STAGES ? d.rows : d.rows.filter((r) => r.stages.indexOf(shown) >= 0 || !r.stages.length);
  const look = lookupOf(bench.result);
  const todo = rows.filter((r) => r.verb !== "ok").sort((a, b) => VERB_ORDER.indexOf(a.verb) - VERB_ORDER.indexOf(b.verb));
  const standing = rows.filter((r) => r.verb === "ok");
  const inner = html`${todo.length ? html`${group("to do", todo.length)}${todo.map((r) => jobRow(r, look))}` : ""}${standing.length ? html`${group("standing", standing.length)}${standing.map((r) => jobRow(r, look))}` : ""}`;
  const one = shown === ALL_STAGES ? d.current || 1 : shown;
  const control = d.count
    ? seg(
        "pstage",
        [
          { value: String(one), label: "stage " + one, href: hashFor(buildDash(bench.key, one)) },
          { value: "all", label: "all", href: hashFor(buildDash(bench.key, ALL_STAGES)) },
        ],
        shown === ALL_STAGES ? "all" : String(one),
        "which stage"
      )
    : "";
  return html`${toolrow(dashH("jobs", rows.length), control)}${rows.length ? list("pjob", "stacked", inner) : emptyLine("no jobs in this stage")}`;
}

function shortBlock(d: TrackResponse): Safe {
  const look = lookupOf(bench.result);
  const rows = d.cost.map(function (c) {
    const ic = look.itemIcon[c.name] || (c.item.indexOf("Desc_") === 0 ? c.item : itemClass(c.name));
    return entry(
      { title: c.name },
      html`${icon(ic, c.name)}<div class="id"><span class="name">${c.name}</span><div class="ctx">for ${count(c.lines)} ${c.lines === 1 ? "line" : "lines"}</div></div><div class="metric"><b>${formatNumber(c.stock, 0)}</b><span>of ${formatNumber(c.need, 0)} held</span>${bar(c.need ? (100 * c.stock) / c.need : 0)}</div><div class="metric bad"><b>${formatNumber(c.short, 0)}</b><span>short</span></div>`
    );
  });
  return html`${dashH("short", rows.length)}${rows.length ? list("pshort", "stacked", rows) : emptyLine("nothing short", "stock covers the build")}`;
}

function nameBar(): Safe {
  if (naming.key !== bench.key) return html``;
  return html`<div class="card pname"><label class="flab" for="pb-name">name it</label><input type="text" id="pb-name" data-ctl="pb-name" value="${naming.name}" aria-label="${"name for this " + WORDS.unnamedCluster}"${naming.busy ? html` disabled` : ""}${naming.problem ? html` aria-invalid="true"` : ""}> <button type="button" class="btn" data-pb="name"${naming.busy ? html` disabled` : ""}>name</button> <button type="button" class="btn" data-pb="name-cancel">cancel</button>${naming.problem ? html`<p class="invalid" role="alert">${naming.problem}</p>` : ""}</div>`;
}

function scopeSelect(): Safe {
  const current = bench.plan ? bench.plan.factory : "";
  const names = (vitals().health?.factories || []).map((f) => f.name).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true }));
  if (current && current[0] !== "/" && names.indexOf(current) < 0) names.push(current);
  const options: [string, string][] = [[SCOPE_AUTO, WORDS.foundAutomatically]].concat(names.map((n): [string, string] => [n, n]), [
    [SCOPE_WORLD, WORDS.wholeWorld],
    [SCOPE_NONE, WORDS.nothingBuiltYet],
  ]) as [string, string][];
  return html`<label class="sort">${WORDS.countAsBuilt} <select id="pb-scope" data-ctl="pb-scope"${bench.gone ? html` disabled` : ""}>${options.map((o) => html`<option value="${o[0]}"${o[0] === current ? html` selected` : ""}>${o[1]}</option>`)}</select></label>`;
}

function candidatesBlock(d: TrackResponse): Safe {
  const cs = d.built_at.candidates;
  const rows = cs.map((c, i) =>
    entry(
      { title: c.name },
      html`<div class="id"><span class="name">${c.name}</span><div class="ctx">${c.kind === "cluster" ? WORDS.unnamedCluster : WORDS.factory}</div></div><div class="metric"><b>${count(c.machines)}</b><span>machines match</span></div><div class="metric"><b>${Math.round(c.rate_share * 100)}%</b><span>of the rate</span></div>${acts(html`<button type="button" class="btn" data-pb="pick" data-i="${String(i)}"${bench.gone ? html` disabled` : ""}><span class="spinner" aria-hidden="true"></span>pick</button>`)}`
    )
  );
  const back = d.built_at.mode !== "auto" || d.built_at.confidence !== "unsure" ? html`<p class="note"><a href="${hashFor(buildDash(bench.key, 0))}">back to the stages</a></p>` : "";
  return html`${toolrow(dashH("which factory?", cs.length), scopeSelect())}${back}${nameBar()}${rows.length ? list("pcand", "stacked", rows, "nolead") : emptyLine("nothing built near the plan's site")}`;
}

export function buildTab(stage: number, pick: boolean): Safe {
  const v = bench.track;
  const d = v.data;
  if (v.error && !d) return html`${emptyLine("the stages could not be read", v.error)} <button type="button" class="btn" data-pb="retry">try again</button>`;
  if (!d) return loadingLine("the stages");
  const stale = v.asked ? "stale" : "";
  if (!d.feasible) return html`<p class="tone-bad">not solvable: ${d.cause || d.headline}</p>`;
  if (d.empty) return emptyLine("the plan builds nothing", "nothing to build");
  const b = d.built_at;
  if (b.confidence === "no site" && !pick) return html`${dashH("to build")}${emptyLine("no site")} <a href="${hashFor(planDash(bench.key, "place"))}">place the pad on the map</a>`;
  if (pick || (b.mode === "auto" && b.confidence === "unsure")) return html`<div class="${stale}">${candidatesBlock(d)}</div>`;
  if (d.scope_error) return emptyLine(d.scope_error);
  const shown = stage === ALL_STAGES ? ALL_STAGES : stage > 0 && stage <= d.count ? stage : d.current || ALL_STAGES;
  return html`<div class="${stale}">${stagesBlock(d, shown)}${jobsBlock(d, shown)}${shortBlock(d)}</div>`;
}

/* ------------------------------------------------------------------ actions */

function showJob(id: string): void {
  const r = bench.track.data?.rows.filter((x) => x.id === id)[0];
  if (!r || r.bbox_m?.length !== 4) return;
  const box = r.bbox_m as BboxM;
  const nodes = r.targets.map((t) => t.node);
  goToMapThen(() => showBox(box, { layers: ["machines"].concat(nodeLayers(nodes)) }));
}

export function buildClick(target: Element): boolean {
  const b = target.closest<HTMLElement>("[data-pb]");
  if (!b) return false;
  const what = b.dataset.pb;
  const d = bench.track.data;
  if (what === "retry") loadTrack();
  else if (what === "notice") {
    bench.track.notice = "";
    changed();
  } else if (what === "map") showJob(b.dataset.row || "");
  else if (what === "pick" && d) {
    const c = d.built_at.candidates[Number(b.dataset.i)];
    if (!c) return true;
    if (c.kind !== "factory") pickCandidate(c);
    else
      busyButton(b as HTMLButtonElement, function () {
        pickCandidate(c);
        return writesSettled();
      }).then(() => go(buildDash(bench.key, 0), true), () => undefined);
  } else if (what === "name" && d) {
    const field = document.getElementById("pb-name") as HTMLInputElement | null;
    if (field) saveName(d.built_at, field.value);
  } else if (what === "name-cancel") stopNaming();
  else return false;
  return true;
}

export function buildChange(el: HTMLSelectElement): boolean {
  if (el.id !== "pb-scope") return false;
  setBuiltScope(el.value);
  go(buildDash(bench.key, 0), true);
  return true;
}

/** Enter names the cluster, Escape stops naming. */
export function buildKey(event: KeyboardEvent): void {
  const el = event.target as HTMLInputElement;
  if (el.id !== "pb-name" || !bench.track.data) return;
  if (event.key === "Enter") {
    event.preventDefault();
    saveName(bench.track.data.built_at, el.value);
  } else if (event.key === "Escape") {
    event.preventDefault();
    stopNaming();
  }
}
