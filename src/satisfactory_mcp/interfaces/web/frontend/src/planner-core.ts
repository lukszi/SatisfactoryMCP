/* The open plan as the page knows it, and every write it makes. See docs/planner_slice_contract.md §12. */

import { get, latest, missing, push, send } from "./api";
import { go } from "./nav";
import { setting } from "./settings";
import { state } from "./state";
import { fail, friendly, note } from "./toast";
import { W } from "./words";

import type { ApiError, ApiPath, ApiUrl, StatusError } from "./api";
import type {
  ActivityRow,
  ActorBody,
  AlreadyUndoneResponse,
  CommitBody,
  DeltaResponse,
  FeedersResponse,
  TrackResponse,
  FocusSelection,
  ItemsResponse,
  NameTakenResponse,
  OutdatedResponse,
  PlanOpBody,
  PlanAlternatesResponse,
  PlanOpsResponse,
  PlanStateBody,
  PushedResponse,
  SolveResponse,
  VersionsResponse,
} from "./api-shapes";

export type Op = PlanOpBody & { op: string };

export type Selection = FocusSelection;

export interface PlansEvent {
  world: string;
  key: string;
  name: string;
  rev: number;
  from_rev: number;
  actors: ActorBody[];
  text: string;
  ts: number;
  forgotten: boolean;
}

export type ActivityEvent = Pick<ActivityRow, "id" | "ts" | "actor" | "kind" | "plan" | "rev" | "text" | "args"> & {
  world: string;
  name?: string | null;
};

export interface StripRow {
  rev: number;
  text: string;
  who: string;
  undone: boolean;
}

export interface Chip {
  id: number;
  gesture: number;
  field: string;
  who: string;
  text: string;
  retry: () => void;
}

type Refusal = Partial<OutdatedResponse & AlreadyUndoneResponse & NameTakenResponse>;

export type ResultTab = "build list" | "graph" | "track";

export interface Partition {
  partition_id: string;
  current: number;
  count: number;
  rev: number;
  save_id: string;
  headroom_mw: number | null;
}

export interface FeedersView {
  data: FeedersResponse | null;
  error: string;
  busy: boolean;
}

export interface TrackView {
  data: TrackResponse | null;
  error: string;
  seq: number;
  asked: number;
  stage: number;
  notice: string;
  feeders: FeedersView | null;
}

function freshTrack(): TrackView {
  return { data: null, error: "", seq: 0, asked: 0, stage: 0, notice: "", feeders: null };
}

export interface AltView {
  item: string;
  data: PlanAlternatesResponse | null;
  error: string;
  seq: number;
  asked: number;
  opener: string;
  enter: boolean;
}

var ALTERNATES: ApiPath = "/api/plan/alternates";

export var bench = {
  world: "",
  key: "",
  plan: null as PlanStateBody | null,
  error: "",
  missing: false,
  gone: false,
  last: null as { who: string; ts: number } | null,
  result: null as SolveResponse | null,
  resultRev: 0,
  feasible: null as { rev: number; data: SolveResponse } | null,
  solving: 0,
  solveError: "",
  strip: [] as StripRow[],
  chips: [] as Chip[],
  done: [] as number[],
  redo: [] as { target: number; by: number }[],
  own: {} as Record<number, boolean>,
  selection: null as Selection | null,
  picked: "",
  tab: "build list" as ResultTab,
  alt: null as AltView | null,
  altBack: false,
  chatRows: {} as Record<string, true>,
  versions: null as VersionsResponse | null,
  versionsOpen: false,
  versionsError: "",
  view: 0,
  viewPlan: null as PlanStateBody | null,
  viewResult: null as SolveResponse | null,
  viewError: "",
  viewDelta: null as DeltaResponse | null,
  stripDelta: null as DeltaResponse | null,
  track: freshTrack(),
  seen: {} as Record<string, Partition>,
};

export var inbox = { card: null as ActivityEvent | null };

export var NAME_MAX = 80;
export var NOTES_MAX = 2000;

var itemNames: string[] = [];
var itemsAsked = false;

var inflight = 0;
var gestures = 0;
var chipIds = 0;
var solveSeq = 0;
var solved: Record<string, SolveResponse> = {};
var chain: Promise<void> = Promise.resolve();
var listeners: Array<() => void> = [];

export function onBench(listener: () => void): void {
  listeners.push(listener);
}

export function changed(): void {
  listeners.forEach(function (listener) {
    listener();
  });
}

function queue(task: () => Promise<void> | void): void {
  chain = chain.then(task).catch(function (error) {
    fail(friendly(error));
  });
}

export function actorWord(actor: ActorBody): string {
  if (actor.kind === "page") return W.actorYou;
  if (actor.kind === "chat") return W.actorChat;
  return actor.display;
}

export function commitWords(text: string): string {
  return text.replace(/^v\d+ [^:]*: /, "");
}

export function loadItems(): void {
  if (itemsAsked) return;
  itemsAsked = true;
  get<ItemsResponse>("/api/gamedata/items?limit=1000")
    .then(function (data) {
      itemNames = data.items.map(function (i) {
        return i.name;
      });
      changed();
    })
    .catch(function () {
      itemsAsked = false;
    });
}

export function itemList(): HTMLDataListElement {
  var list = document.createElement("datalist");
  list.id = "plan-items";
  itemNames.forEach(function (name) {
    var option = document.createElement("option");
    option.value = name;
    list.appendChild(option);
  });
  return list;
}

export function knownItem(text: string): string | null {
  if (!itemNames.length) return text;
  var want = text.trim().toLowerCase();
  var hit = itemNames.filter(function (name) {
    return name.toLowerCase() === want;
  })[0];
  return hit || null;
}

export function pushing(): boolean {
  return inflight > 0;
}

export function status(): string {
  if (inflight) return "pushing…";
  if (bench.chips.length) return "conflict";
  return "saved";
}

export function sav(): string {
  return bench.result ? bench.result.token : "";
}

export function now(): number {
  return Date.now() / 1000;
}

export function age(ts: number): string {
  var s = Math.max(0, Math.round(now() - ts));
  if (s < 60) return s + "s";
  if (s < 3600) return Math.round(s / 60) + "m";
  if (s < 86400) return Math.round(s / 3600) + "h";
  return Math.round(s / 86400) + "d";
}

export function reset(key: string): void {
  bench.world = state.world;
  bench.key = key;
  bench.plan = null;
  bench.error = "";
  bench.missing = false;
  bench.gone = false;
  bench.last = null;
  bench.result = null;
  bench.resultRev = 0;
  bench.feasible = null;
  bench.solving = 0;
  bench.solveError = "";
  bench.strip = [];
  bench.chips = [];
  bench.done = [];
  bench.redo = [];
  bench.own = {};
  bench.selection = null;
  bench.picked = "";
  bench.alt = null;
  bench.altBack = false;
  bench.chatRows = {};
  bench.versions = null;
  bench.versionsError = "";
  bench.view = 0;
  bench.viewPlan = null;
  bench.viewResult = null;
  bench.viewError = "";
  bench.viewDelta = null;
  bench.stripDelta = null;
  bench.track = freshTrack();
}

export function openPlan(key: string): void {
  reset(key);
  changed();
  queue(function () {
    return get<PlanStateBody>("/api/plans/{key}", key)
      .then(function (plan) {
        if (bench.key !== key) return;
        adopt(plan);
        return history(key);
      })
      .catch(function (error) {
        if (bench.key !== key) return;
        bench.error = friendly(error);
        bench.missing = missing(error);
        changed();
      });
  });
}

function history(key: string): Promise<void> {
  return get<PlanOpsResponse>("/api/plans/{key}/ops?since=0", key).then(function (ops) {
    if (bench.key !== key) return;
    var undone: Record<number, boolean> = {};
    ops.commits.forEach(function (c) {
      if (c.undoes) undone[c.undoes] = true;
    });
    bench.done = ops.commits
      .filter(function (c) {
        return c.rev > 1 && c.actor.kind === "page" && !c.undoes && !undone[c.rev];
      })
      .map(function (c) {
        return c.rev;
      });
    var last = ops.commits[ops.commits.length - 1];
    if (last) bench.last = { who: actorWord(last.actor), ts: last.ts };
    changed();
  });
}

function adopt(plan: PlanStateBody): void {
  if (bench.plan && plan.rev < bench.plan.rev) return;
  var moved = !bench.plan || bench.plan.rev !== plan.rev;
  bench.plan = plan;
  bench.gone = plan.forgotten;
  changed();
  solveHead();
  if (moved) {
    if (bench.versionsOpen) loadVersions();
    deltaForStrip();
    if (bench.view) deltaForView();
    if (bench.alt) loadAlternates();
    loadTrack();
  }
}

var TRACK = "/api/plan/track" as ApiPath;
var FEEDERS = "/api/plan/feeders" as ApiPath;

function biomassFlag(): string {
  return "biomass=" + (setting("biomass") ? "true" : "false");
}

export function trackDash(key: string, stage: number): string {
  return "planner/" + key + "/track" + (stage ? "/" + stage : "");
}

function partition(data: TrackResponse): Partition {
  return {
    partition_id: data.partition_id,
    current: data.current,
    count: data.count,
    rev: data.rev,
    save_id: data.save_id,
    headroom_mw: data.headroom_mw,
  };
}

function wasWords(p: Partition): string {
  if (!p.count) return "no startup order fitted";
  if (!p.current) return "every stage of " + p.count + " was built";
  return "you were in " + W.stage(p.current, p.count);
}

function nowWords(p: Partition): string {
  if (!p.count) return "now no startup order fits the headroom";
  if (!p.current) return "now every stage of " + p.count + " is built";
  return "now " + W.stage(p.current, p.count);
}

function renumber(data: TrackResponse): void {
  if (!data.feasible || data.scope_error) return;
  var now = partition(data);
  var was = bench.seen[data.key];
  bench.seen[data.key] = now;
  if (!was || was.partition_id === now.partition_id) return;
  var cause =
    was.rev !== now.rev
      ? "v" + now.rev + " changed the stages"
      : was.headroom_mw !== now.headroom_mw
        ? "the new headroom changed the stages"
        : was.save_id !== now.save_id
          ? "the new save changed the stages"
          : "the stages changed";
  bench.track.notice = cause + ": " + wasWords(was) + ", " + nowWords(now);
}

export function loadTrack(): void {
  var plan = bench.plan;
  if (!plan || bench.tab !== "track" || bench.view) return;
  var key = bench.key;
  var view = bench.track;
  var ticket = latest("planner-track");
  view.seq++;
  view.asked = plan.rev;
  changed();
  get<TrackResponse>(`${TRACK}?key=${encodeURIComponent(key)}&${biomassFlag()}` as ApiUrl)
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
      view.error = friendly(reason);
      view.asked = 0;
      changed();
    });
}

export function dropFeeders(): void {
  bench.track.feeders = null;
}

export function loadFeeders(): void {
  var view = bench.track;
  var key = bench.key;
  var feeders: FeedersView = { data: null, error: "", busy: true };
  view.feeders = feeders;
  changed();
  get<FeedersResponse>(`${FEEDERS}?${biomassFlag()}` as ApiUrl)
    .then(function (data) {
      if (bench.key !== key || view.feeders !== feeders) return;
      feeders.data = data;
      feeders.busy = false;
      changed();
    })
    .catch(function (reason) {
      if (bench.key !== key || view.feeders !== feeders) return;
      feeders.error = friendly(reason);
      feeders.busy = false;
      changed();
    });
}

export function pickStage(n: number): void {
  var view = bench.track;
  var total = view.data ? view.data.count : 0;
  view.stage = view.stage === n ? 0 : n;
  bench.selection = view.stage ? { kind: "stage", label: W.stage(view.stage, total), ref: String(view.stage) } : null;
  go(trackDash(bench.key, view.stage), true);
  changed();
}

export function pickTab(tab: ResultTab): void {
  var was = bench.tab;
  bench.tab = tab;
  if (tab === "track") {
    go(trackDash(bench.key, bench.track.stage), true);
    loadTrack();
  } else if (was === "track") go("planner/" + bench.key, true);
  changed();
}

export function showAlternates(item: string, opener?: string): void {
  if (bench.alt && bench.alt.item === item) {
    if (opener) {
      bench.alt.opener = opener;
      bench.alt.enter = true;
    }
    return;
  }
  bench.alt = { item: item, data: null, error: "", seq: 0, asked: 0, opener: opener || "", enter: !!opener };
  loadAlternates();
}

export function hideAlternates(): string {
  var opener = bench.alt ? bench.alt.opener : "";
  bench.alt = null;
  bench.altBack = false;
  return opener;
}

var altSeq = 0;

export function loadAlternates(): void {
  var alt = bench.alt;
  var plan = bench.plan;
  if (!alt || !plan) return;
  var key = bench.key;
  var rev = plan.rev;
  var seq = ++altSeq;
  alt.seq = seq;
  alt.asked = rev;
  changed();
  send<PlanAlternatesResponse>("POST", ALTERNATES, { key: key, rev: rev, item: alt.item }, undefined, "spoilers=" + (setting("spoilers") ? 1 : 0))
    .then(function (data) {
      var now = bench.alt;
      if (!now || now.seq !== seq || bench.key !== key) return;
      now.data = data;
      now.error = "";
      now.asked = 0;
      changed();
    })
    .catch(function (reason) {
      var now = bench.alt;
      if (!now || now.seq !== seq || bench.key !== key) return;
      now.error = friendly(reason);
      now.asked = 0;
      changed();
    });
}

function chatTouched(d: DeltaResponse): Record<string, true> {
  var out: Record<string, true> = {};
  var fromChat = bench.strip.some(function (row) {
    return row.who === W.actorChat;
  });
  if (!fromChat) return out;
  (d.rows || []).forEach(function (r) {
    if (r.change !== "removed") out[r.id] = true;
  });
  return out;
}

function strip(commit: CommitBody): void {
  if (bench.own[commit.rev] || !commit.text) return;
  var seen = bench.strip.some(function (row) {
    return row.rev === commit.rev;
  });
  if (seen) return;
  bench.strip.push({ rev: commit.rev, text: commitWords(commit.text), who: actorWord(commit.actor), undone: false });
  bench.last = { who: actorWord(commit.actor), ts: commit.ts };
  deltaForStrip();
}

export function dismissStrip(rev: number | null): void {
  bench.strip = bench.strip.filter(function (row) {
    return rev !== null && row.rev !== rev;
  });
  if (!bench.strip.length) {
    bench.stripDelta = null;
    bench.chatRows = {};
  } else deltaForStrip();
  changed();
}

function delta(key: string, from: number, to: number): Promise<DeltaResponse> {
  return get<DeltaResponse>(`/api/plan/delta?key=${encodeURIComponent(key)}&from_rev=${from}&to_rev=${to}`);
}

function deltaForStrip(): void {
  var plan = bench.plan;
  if (!plan || !bench.strip.length) return;
  var from =
    Math.min.apply(
      null,
      bench.strip.map(function (row) {
        return row.rev;
      })
    ) - 1;
  var to = plan.rev;
  var have = bench.stripDelta;
  if (from < 1 || (have && have.from_rev === from && have.to_rev === to)) return;
  var key = bench.key;
  delta(key, from, to)
    .then(function (d) {
      if (bench.key !== key || !bench.plan || bench.plan.rev !== to || !bench.strip.length) return;
      bench.stripDelta = d;
      bench.chatRows = chatTouched(d);
      changed();
    })
    .catch(function () {});
}

export function loadVersions(): void {
  var key = bench.key;
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
      bench.versionsError = friendly(reason);
      changed();
    });
}

export function toggleVersions(): void {
  bench.versionsOpen = !bench.versionsOpen;
  if (bench.versionsOpen) loadVersions();
  changed();
}

function deltaForView(): void {
  var plan = bench.plan;
  var rev = bench.view;
  if (!plan || !rev || rev === plan.rev) {
    bench.viewDelta = null;
    return;
  }
  var key = bench.key;
  delta(key, rev, plan.rev)
    .then(function (d) {
      if (bench.key !== key || bench.view !== rev) return;
      bench.viewDelta = d;
      changed();
    })
    .catch(function () {});
}

export function viewRev(rev: number): void {
  if (bench.view === rev) return;
  bench.view = rev;
  bench.viewPlan = null;
  bench.viewResult = null;
  bench.viewError = "";
  bench.viewDelta = null;
  changed();
  if (!rev) return;
  var key = bench.key;
  get<PlanStateBody>(`/api/plans/{key}?rev=${rev}`, key)
    .then(function (plan) {
      if (bench.key !== key || bench.view !== rev) return;
      bench.viewPlan = plan;
      changed();
      return solveAt(key, rev);
    })
    .then(function (data) {
      if (!data || bench.key !== key || bench.view !== rev) return;
      bench.viewResult = data;
      changed();
    })
    .catch(function (reason) {
      if (bench.key !== key || bench.view !== rev) return;
      bench.viewError = friendly(reason);
      changed();
    });
  deltaForView();
}

function solveAt(key: string, rev: number): Promise<SolveResponse> {
  var id = key + ":" + rev;
  var cached = solved[id];
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
      if (seq !== solveSeq || bench.key !== key || bench.feasible) return;
      if (data.feasible) {
        bench.feasible = { rev: rev, data: data };
        changed();
      } else lookBack(key, rev - 1, seq, floor);
    })
    .catch(function () {});
}

export function solveHead(): void {
  var plan = bench.plan;
  if (!plan) return;
  var key = bench.key;
  var rev = plan.rev;
  var seq = ++solveSeq;
  bench.solving = rev;
  solveAt(key, rev)
    .then(function (data) {
      if (seq !== solveSeq || bench.key !== key) return;
      bench.result = data;
      bench.resultRev = rev;
      if (data.feasible) bench.feasible = { rev: rev, data: data };
      else if (!bench.feasible) lookBack(key, rev - 1, seq, rev - 20);
      bench.solving = 0;
      bench.solveError = "";
      changed();
    })
    .catch(function (error) {
      if (seq !== solveSeq) return;
      bench.solving = 0;
      bench.solveError = friendly(error);
      changed();
    });
}

export function forgetSolves(): void {
  solved = {};
  solveHead();
}

function refused(error: StatusError): void {
  if (error.status === 410) {
    bench.gone = true;
    fail("this plan was forgotten; nothing was written");
  } else {
    fail(friendly(error));
  }
}

function landed(reply: PushedResponse, record: "done" | "none"): number {
  if (!reply.noop) {
    bench.own[reply.rev] = true;
    if (record === "done") bench.done.push(reply.rev);
    bench.last = { who: W.actorYou, ts: now() };
  }
  reply.others.forEach(strip);
  adopt(reply.state);
  return reply.noop ? 0 : reply.rev;
}

function outdated(body: Refusal, retry: () => void): void {
  if (!body.outdated || !body.state) {
    fail(body.error || "the server refused this change");
    changed();
    return;
  }
  (body.since || []).forEach(strip);
  var gesture = ++gestures;
  (body.conflicts || []).forEach(function (c) {
    bench.chips.push({
      id: ++chipIds,
      gesture: gesture,
      field: c.mine.field || c.key.replace(/[[{].*$/, ""),
      who: actorWord(c.theirs_actor),
      text: c.text,
      retry: retry,
    });
  });
  adopt(body.state);
}

export function dropChip(id: number, retry: boolean): void {
  var chip = bench.chips.filter(function (c) {
    return c.id === id;
  })[0];
  if (!chip) return;
  var gesture = chip.gesture;
  bench.chips = bench.chips.filter(function (c) {
    return c.gesture !== gesture;
  });
  changed();
  if (retry) chip.retry();
}

function baseFor(seen: number): number {
  var head = bench.plan ? bench.plan.rev : seen;
  for (var rev = seen + 1; rev <= head; rev++) {
    if (!bench.own[rev]) return seen;
  }
  return head;
}

function onlyOwn(body: Refusal, base: number): boolean {
  var conflicts = body.conflicts || [];
  return (
    !!body.outdated &&
    !!body.state &&
    conflicts.length > 0 &&
    conflicts.every(function (c) {
      return c.theirs_rev > base && !!bench.own[c.theirs_rev];
    })
  );
}

function write(
  path: "/api/plans/{key}/ops" | "/api/plans/{key}/args" | "/api/plans/{key}/undo" | "/api/plans/{key}/restore",
  extra: Record<string, unknown>,
  settle: (reply: PushedResponse) => void,
  conflict: (body: Refusal) => void,
  pinned?: number
): void {
  var plan = bench.plan;
  if (!plan) return;
  var key = bench.key;
  var name = plan.name;
  var seen = pinned === undefined ? plan.rev : pinned;
  var lost = function (why: string) {
    fail("your change to “" + name + "” was not saved: " + why);
  };
  var attempt = function (base: number): Promise<void> {
    var here = bench.key === key;
    var body: Record<string, unknown> = { base_rev: base, sav: here ? sav() : "" };
    Object.keys(extra).forEach(function (k) {
      body[k] = extra[k];
    });
    return push<PushedResponse, Refusal>(path, body, key).then(function (answer) {
      if (bench.key !== key) {
        if (answer.conflict) lost(answer.body.error || "the server refused it");
        return;
      }
      if (!answer.conflict) {
        settle(answer.body);
        return;
      }
      if (pinned === undefined && onlyOwn(answer.body, base)) {
        adopt(answer.body.state!);
        return attempt(answer.body.head!);
      }
      conflict(answer.body);
    });
  };
  inflight++;
  changed();
  queue(function () {
    var done = function () {
      inflight--;
      changed();
    };
    var start = bench.key === key && pinned === undefined ? baseFor(seen) : seen;
    return attempt(start)
      .catch(function (error) {
        if (bench.key === key) refused(error);
        else lost(friendly(error));
      })
      .then(done, done);
  });
}

export function gesture(ops: Op[]): void {
  if (!ops.length || !bench.plan) return;
  bench.redo = [];
  var again = function () {
    gesture(ops);
  };
  write(
    "/api/plans/{key}/ops",
    { ops: ops },
    function (reply) {
      landed(reply, "done");
    },
    function (body) {
      outdated(body, again);
    }
  );
}

export function applyArgs(args: Record<string, unknown>, fromEntry: string, base?: number): void {
  if (!bench.plan) return;
  bench.redo = [];
  var again = function () {
    applyArgs(args, fromEntry);
  };
  write(
    "/api/plans/{key}/args",
    { args: args, from_entry: fromEntry },
    function (reply) {
      landed(reply, "done");
      if (reply.noop) note("nothing changed: the plan already matches chat's request");
    },
    function (body) {
      outdated(body, again);
    },
    base
  );
}

function undo(rev: number, ok: (by: number) => void, already: () => void, drop: () => void): void {
  write(
    "/api/plans/{key}/undo",
    { rev: rev },
    function (reply) {
      var by = landed(reply, "none");
      if (by) ok(by);
      else drop();
    },
    function (body) {
      if (body.already_undone) {
        already();
        return;
      }
      drop();
      if (body.outdated && body.state) {
        (body.since || []).forEach(strip);
        var why = (body.conflicts || [])
          .map(function (c) {
            return c.text;
          })
          .join("; ");
        fail("v" + rev + " cannot be undone: it was changed again since" + (why ? " (" + why + ")" : ""));
        adopt(body.state);
        return;
      }
      fail(body.error || "the server refused the undo");
    }
  );
}

function without<T>(list: T[], item: T): void {
  var at = list.lastIndexOf(item);
  if (at >= 0) list.splice(at, 1);
}

export function undoLast(): void {
  var target = bench.done[bench.done.length - 1];
  if (target === undefined) {
    note("nothing of yours to undo on this plan");
    return;
  }
  var take = function () {
    without(bench.done, target!);
  };
  undo(
    target,
    function (by) {
      take();
      bench.redo.push({ target: target!, by: by });
    },
    function () {
      take();
      undoLast();
    },
    take
  );
}

export function redoLast(): void {
  var entry = bench.redo[bench.redo.length - 1];
  if (!entry) {
    note("nothing to redo");
    return;
  }
  var take = function () {
    without(bench.redo, entry!);
  };
  undo(
    entry.by,
    function (by) {
      take();
      bench.done.push(by);
    },
    function () {
      take();
      redoLast();
    },
    take
  );
}

function markUndone(rev: number): void {
  bench.strip.forEach(function (row) {
    if (row.rev === rev) row.undone = true;
  });
}

export function undoRev(rev: number): void {
  undo(
    rev,
    function (by) {
      bench.done.push(by);
      markUndone(rev);
    },
    function () {
      markUndone(rev);
      note("v" + rev + " is already undone");
      changed();
    },
    function () {}
  );
}

export function followHead(event: PlansEvent): void {
  if (event.key !== bench.key || !bench.plan || event.rev <= bench.plan.rev) return;
  pull();
}

export function resyncHead(): void {
  if (bench.plan) pull();
}

function pull(): void {
  var key = bench.key;
  queue(function () {
    if (bench.key !== key || !bench.plan) return;
    return get<PlanOpsResponse>(`/api/plans/{key}/ops?since=${bench.plan.rev}`, key)
      .then(function (ops) {
        if (bench.key !== key) return;
        ops.commits.forEach(strip);
        return get<PlanStateBody>("/api/plans/{key}", key);
      })
      .then(function (plan) {
        if (plan && bench.key === key) adopt(plan);
      });
  });
}

export function restoreRev(rev: number, done?: () => void): void {
  if (!bench.plan) return;
  bench.redo = [];
  write(
    "/api/plans/{key}/restore",
    { rev: rev },
    function (reply) {
      landed(reply, "done");
      if (reply.noop) note("nothing changed: the plan already equals v" + rev);
      if (done) done();
    },
    function (body) {
      if (body.name_taken) {
        fail("v" + rev + " cannot be restored: " + (body.error || "its name is taken"));
        changed();
        return;
      }
      outdated(body, function () {
        restoreRev(rev, done);
      });
    }
  );
}

export function duplicatePlan(rev?: number): Promise<string> {
  var body: Record<string, unknown> = {};
  if (rev) body.rev = rev;
  return push<PushedResponse, Refusal & ApiError>("/api/plans/{key}/duplicate", body, bench.key).then(function (answer) {
    if (answer.conflict) throw new Error(answer.body.error || "the copy could not be named");
    return answer.body.key;
  });
}

export function undoIn(key: string, rev: number): Promise<string> {
  return get<PlanStateBody>("/api/plans/{key}", key).then(function (head) {
    return push<PushedResponse, Refusal & ApiError>("/api/plans/{key}/undo", { base_rev: head.rev, rev: rev }, key).then(function (answer) {
      if (!answer.conflict) return answer.body.noop ? "nothing to undo in v" + rev : "undid v" + rev + " of “" + head.name + "”";
      if (answer.body.already_undone) return "v" + rev + " is already undone";
      var why = (answer.body.conflicts || [])
        .map(function (c) {
          return c.text;
        })
        .join("; ");
      throw new Error("v" + rev + " cannot be undone: it was changed again since" + (why ? " (" + why + ")" : ""));
    });
  });
}

export function renamePlan(name: string): void {
  gesture([{ op: "rename", name: name }]);
}

export function forgetPlan(): void {
  gesture([{ op: "forget" }]);
}

export function restorePlan(): void {
  gesture([{ op: "restore" }]);
}

export function createPlan(base: string, args: Record<string, unknown>, fromEntry: string, n?: number): Promise<string> {
  var tries = n || 1;
  var name = tries === 1 ? base : base + " (" + tries + ")";
  return push<PushedResponse, Refusal & ApiError>("/api/plans", { name: name, args: args, from_entry: fromEntry }).then(function (answer) {
    if (!answer.conflict) return answer.body.key;
    if (answer.body.name_taken && tries < 20) return createPlan(base, args, fromEntry, tries + 1);
    throw new Error(answer.body.error || "a plan named “" + name + "” already exists");
  });
}
