/* What the page reads about the open plan: solves, versions, deltas, the track and the
 * alternates drawer. A reply lands only while the bench still holds the plan it was asked for. */

import { get, latest, send } from "../../api/client";
import { settingChoice, spoilerQuery } from "../../app/settings";
import { friendlyError } from "../../kit/toast";
import { WORDS } from "../../kit/words";
import { biomassQuery } from "../power/readings";
import { bench, changed } from "./state";

import type { ApiPath, ApiUrl } from "../../api/client";
import type { DeltaResponse, PlanAlternatesResponse, PlanStateBody, SolveResponse, TrackResponse, VersionsResponse } from "../../api/shapes";
import type { Partition } from "./state";

const ALTERNATES: ApiPath = "/api/plan/alternates";
const TRACK: ApiPath = "/api/plan/track";
const LOOKBACK_REVS = 20;

let solveSeq = 0;
let solved: Record<string, SolveResponse> = {};
let solvedInputs: Record<string, string> = {};
let resultKey = "";

/** The save token the head was solved against, or "" before a result. */
export function saveToken(): string {
  return bench.result ? bench.result.token : "";
}

/* ------------------------------------------------------------------- solves */

function solveAt(key: string, rev: number): Promise<SolveResponse> {
  const id = key + ":" + rev;
  const cached = solved[id];
  if (cached) return Promise.resolve(cached);
  return send<SolveResponse>("POST", "/api/plan/solve", { key: key, rev: rev }).then(function (data) {
    solved[id] = data;
    return data;
  });
}

function lookBack(key: string, rev: number, seq: number, floor: number): void {
  if (rev < Math.max(1, floor)) return;
  solveAt(key, rev)
    .then(function (data) {
      if (seq !== solveSeq || bench.key !== key || bench.lastFeasible) return;
      if (data.feasible) {
        bench.lastFeasible = { rev: rev, data: data };
        changed();
      } else lookBack(key, rev - 1, seq, floor);
    })
    .catch(function () {});
}

export function solveHead(): void {
  const plan = bench.plan;
  if (!plan) return;
  const key = bench.key;
  const rev = plan.rev;
  const seq = ++solveSeq;
  const inputs = JSON.stringify(plan.args);
  // A layout-only version (a moved node) leaves the solve inputs alone: its answer is the last one.
  if (bench.result && resultKey === key && solvedInputs[key] === inputs) solved[key + ":" + rev] = solved[key + ":" + rev] || bench.result;
  bench.solvingRev = rev;
  solveAt(key, rev)
    .then(function (data) {
      if (seq !== solveSeq || bench.key !== key) return;
      solvedInputs[key] = inputs;
      resultKey = key;
      bench.result = data;
      bench.resultRev = rev;
      if (data.feasible) bench.lastFeasible = { rev: rev, data: data };
      else if (!bench.lastFeasible) lookBack(key, rev - 1, seq, rev - LOOKBACK_REVS);
      bench.solvingRev = 0;
      bench.solveError = "";
      changed();
    })
    .catch(function (error) {
      if (seq !== solveSeq) return;
      bench.solvingRev = 0;
      bench.solveError = friendlyError(error);
      changed();
    });
}

export function forgetSolves(): void {
  solved = {};
  solvedInputs = {};
  solveHead();
}

/* --------------------------------------------------------- versions and deltas */

export function delta(key: string, from: number, to: number): Promise<DeltaResponse> {
  return get<DeltaResponse>(`/api/plan/delta?key=${encodeURIComponent(key)}&from_rev=${from}&to_rev=${to}`);
}

export function deltaForView(): void {
  const plan = bench.plan;
  const rev = bench.viewedRev;
  if (!plan || !rev || rev === plan.rev) {
    bench.viewedDelta = null;
    return;
  }
  const key = bench.key;
  delta(key, rev, plan.rev)
    .then(function (d) {
      if (bench.key !== key || bench.viewedRev !== rev) return;
      bench.viewedDelta = d;
      changed();
    })
    .catch(function () {});
}

/** Shows `rev` read-only beside the head; 0 goes back to the head. */
export function openRevision(rev: number): void {
  if (bench.viewedRev === rev) return;
  bench.viewedRev = rev;
  bench.viewedPlan = null;
  bench.viewedResult = null;
  bench.viewedError = "";
  bench.viewedDelta = null;
  changed();
  if (!rev) return;
  const key = bench.key;
  get<PlanStateBody>(`/api/plans/{key}?rev=${rev}`, key)
    .then(function (plan) {
      if (bench.key !== key || bench.viewedRev !== rev) return;
      bench.viewedPlan = plan;
      changed();
      return solveAt(key, rev);
    })
    .then(function (data) {
      if (!data || bench.key !== key || bench.viewedRev !== rev) return;
      bench.viewedResult = data;
      changed();
    })
    .catch(function (reason) {
      if (bench.key !== key || bench.viewedRev !== rev) return;
      bench.viewedError = friendlyError(reason);
      changed();
    });
  deltaForView();
}

export function loadVersions(): void {
  const key = bench.key;
  if (!key) return;
  get<VersionsResponse>("/api/plans/{key}/versions", key)
    .then(function (body) {
      if (bench.key !== key) return;
      bench.versions = body;
      bench.versionsError = "";
      changed();
    })
    .catch(function (reason) {
      if (bench.key !== key) return;
      bench.versionsError = friendlyError(reason);
      changed();
    });
}

/* -------------------------------------------------------------------- track */

function partition(data: TrackResponse): Partition {
  return {
    partition_id: data.partition_id,
    current: data.current,
    count: data.count,
    rev: data.rev,
    save_id: data.save_id,
    headroom_mw: data.headroom_mw,
    headroom_source: data.headroom_mw === null ? data.startup.headroom_source : "",
  };
}

function wasWords(p: Partition): string {
  if (!p.count) return "no startup order fitted";
  if (!p.current) return "every stage of " + p.count + " was built";
  return "you were in " + WORDS.stage(p.current, p.count);
}

function nowWords(p: Partition): string {
  if (!p.count) return "now no startup order fits the headroom";
  if (!p.current) return "now every stage of " + p.count + " is built";
  return "now " + WORDS.stage(p.current, p.count);
}

function stillWords(p: Partition): string {
  if (!p.count) return "still no startup order fits the headroom";
  if (!p.current) return "every stage of " + p.count + " is still built";
  return "you are still in " + WORDS.stage(p.current, p.count);
}

/** What moved the stages, or "" when nothing the page can name did. */
function renumberCause(was: Partition, now: Partition): string {
  if (was.rev !== now.rev) return "v" + now.rev;
  if (was.headroom_mw !== now.headroom_mw || was.headroom_source !== now.headroom_source) return "the new headroom";
  if (was.save_id !== now.save_id) return "the new save";
  return "";
}

function renumber(data: TrackResponse): void {
  if (!data.feasible || data.scope_error) return;
  const now = partition(data);
  const was = bench.partitionByPlan[data.key];
  bench.partitionByPlan[data.key] = now;
  if (!was || was.partition_id === now.partition_id) return;
  const cause = renumberCause(was, now);
  if (was.current === now.current && was.count === now.count) {
    bench.track.notice = (cause || "the plan") + " moved machines between stages; " + stillWords(now);
    return;
  }
  bench.track.notice = (cause ? cause + " changed the stages" : "the stages changed") + ": " + wasWords(was) + ", " + nowWords(now);
}

export function stageHeadroom(): string {
  return settingChoice("stageHeadroom") || "measured";
}

export function loadTrack(): void {
  const plan = bench.plan;
  if (!plan || bench.tab !== "build" || bench.viewedRev) return;
  const key = bench.key;
  const view = bench.track;
  const ticket = latest("planner-track");
  view.seq++;
  view.asked = plan.rev;
  changed();
  get<TrackResponse>(`${TRACK}?key=${encodeURIComponent(key)}&${biomassQuery()}&headroom=${stageHeadroom()}` as ApiUrl)
    .then(function (data) {
      if (!ticket.fresh() || bench.key !== key || bench.track !== view) return;
      renumber(data);
      view.data = data;
      view.error = "";
      view.asked = 0;
      if (view.stage > data.stages.length) view.stage = 0;
      changed();
    })
    .catch(function (reason) {
      if (!ticket.fresh() || bench.key !== key || bench.track !== view) return;
      view.error = friendlyError(reason);
      view.asked = 0;
      changed();
    });
}

/* --------------------------------------------------------------- alternates */

export function showAlternates(item: string, opener?: string): void {
  const open = bench.alternates;
  if (open?.item === item) {
    if (opener) {
      open.opener = opener;
      open.enter = true;
    }
    return;
  }
  bench.alternates = { item: item, data: null, error: "", asked: 0, opener: opener || "", enter: !!opener };
  loadAlternates();
}

/** Closes the drawer and hands back the control that opened it. */
export function hideAlternates(): string {
  const opener = bench.alternates ? bench.alternates.opener : "";
  bench.alternates = null;
  return opener;
}

export function loadAlternates(): void {
  const drawer = bench.alternates;
  const plan = bench.plan;
  if (!drawer || !plan) return;
  const key = bench.key;
  const rev = plan.rev;
  const ticket = latest("planner-alternates");
  drawer.asked = rev;
  changed();
  send<PlanAlternatesResponse>("POST", ALTERNATES, { key: key, rev: rev, item: drawer.item }, undefined, spoilerQuery())
    .then(function (data) {
      const now = bench.alternates;
      if (!now || !ticket.fresh() || bench.key !== key) return;
      now.data = data;
      now.error = "";
      now.asked = 0;
      changed();
    })
    .catch(function (reason) {
      const now = bench.alternates;
      if (!now || !ticket.fresh() || bench.key !== key) return;
      now.error = friendlyError(reason);
      now.asked = 0;
      changed();
    });
}
