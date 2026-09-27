/* The open plan as the page knows it, and every write it makes. See docs/planner_slice_contract.md §12. */

import { get, push, send } from "./api";
import { state } from "./state";
import { fail, friendly, note } from "./toast";

import type { ApiError, StatusError } from "./api";
import type {
  ActivityRow,
  ActorBody,
  AlreadyUndoneResponse,
  CommitBody,
  FocusSelection,
  NameTakenResponse,
  OutdatedResponse,
  PlanOpBody,
  PlanOpsResponse,
  PlanStateBody,
  PushedResponse,
  SolveResponse,
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
};

export interface StripRow {
  rev: number;
  text: string;
  chat: boolean;
  undone: boolean;
}

export interface Chip {
  id: number;
  gesture: number;
  field: string;
  text: string;
  retry: () => void;
}

type Refusal = Partial<OutdatedResponse & AlreadyUndoneResponse & NameTakenResponse>;

export var bench = {
  world: "",
  key: "",
  plan: null as PlanStateBody | null,
  error: "",
  gone: false,
  last: null as { who: string; ts: number } | null,
  result: null as SolveResponse | null,
  resultRev: 0,
  feasible: null as { rev: number; data: SolveResponse } | null,
  solving: false,
  solveError: "",
  strip: [] as StripRow[],
  chips: [] as Chip[],
  done: [] as number[],
  redo: [] as { target: number; by: number }[],
  own: {} as Record<number, boolean>,
  selection: null as Selection | null,
  graph: false,
};

export var inbox = { card: null as ActivityEvent | null };

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
  bench.gone = false;
  bench.last = null;
  bench.result = null;
  bench.resultRev = 0;
  bench.feasible = null;
  bench.solving = false;
  bench.solveError = "";
  bench.strip = [];
  bench.chips = [];
  bench.done = [];
  bench.redo = [];
  bench.own = {};
  bench.selection = null;
}

export function openPlan(key: string): void {
  reset(key);
  changed();
  queue(function () {
    return get<PlanStateBody>("/api/plans/{key}", key)
      .then(function (plan) {
        if (bench.key !== key) return;
        adopt(plan);
        return lastCommit(key, plan.rev);
      })
      .catch(function (error) {
        if (bench.key !== key) return;
        bench.error = friendly(error);
        changed();
      });
  });
}

function lastCommit(key: string, rev: number): Promise<void> {
  return get<PlanOpsResponse>(`/api/plans/{key}/ops?since=${Math.max(0, rev - 1)}`, key).then(function (ops) {
    var last = ops.commits[ops.commits.length - 1];
    if (bench.key === key && last) {
      bench.last = { who: last.actor.display, ts: last.ts };
      changed();
    }
  });
}

function adopt(plan: PlanStateBody): void {
  if (bench.plan && plan.rev < bench.plan.rev) return;
  bench.plan = plan;
  if (plan.forgotten) bench.gone = true;
  changed();
  solveHead();
}

function strip(commit: CommitBody): void {
  if (bench.own[commit.rev] || !commit.text) return;
  var seen = bench.strip.some(function (row) {
    return row.rev === commit.rev;
  });
  if (seen) return;
  bench.strip.push({ rev: commit.rev, text: commit.text, chat: commit.actor.kind === "chat", undone: false });
  bench.last = { who: commit.actor.display, ts: commit.ts };
}

export function dismissStrip(rev: number | null): void {
  bench.strip = bench.strip.filter(function (row) {
    return rev !== null && row.rev !== rev;
  });
  changed();
}

export function solveHead(): void {
  var plan = bench.plan;
  if (!plan) return;
  var key = bench.key;
  var rev = plan.rev;
  var id = key + ":" + rev;
  var seq = ++solveSeq;
  var take = function (data: SolveResponse) {
    if (seq !== solveSeq || bench.key !== key) return;
    bench.result = data;
    bench.resultRev = rev;
    if (data.feasible) bench.feasible = { rev: rev, data: data };
    bench.solving = false;
    bench.solveError = "";
    changed();
  };
  var cached = solved[id];
  if (cached) {
    take(cached);
    return;
  }
  bench.solving = true;
  send<SolveResponse>("POST", "/api/plan/solve", { key: key, rev: rev })
    .then(function (data) {
      solved[id] = data;
      take(data);
    })
    .catch(function (error) {
      if (seq !== solveSeq) return;
      bench.solving = false;
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
  } else if (error.status === 503) {
    fail("plans are busy (another writer held the lock); nothing was written");
  } else {
    fail(friendly(error));
  }
}

function landed(reply: PushedResponse, record: "done" | "none"): number {
  if (!reply.noop) {
    bench.own[reply.rev] = true;
    if (record === "done") bench.done.push(reply.rev);
    bench.last = { who: "page", ts: now() };
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
      text: c.theirs_actor.display + " changed this in v" + c.theirs_rev + ": " + c.text,
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

function write(
  path: "/api/plans/{key}/ops" | "/api/plans/{key}/args" | "/api/plans/{key}/undo",
  extra: Record<string, unknown>,
  settle: (reply: PushedResponse) => void,
  conflict: (body: Refusal) => void
): void {
  var key = bench.key;
  inflight++;
  changed();
  queue(function () {
    if (bench.key !== key || !bench.plan) return;
    var body: Record<string, unknown> = { base_rev: bench.plan.rev, sav: sav() };
    Object.keys(extra).forEach(function (k) {
      body[k] = extra[k];
    });
    return push<PushedResponse, Refusal>(path, body, key)
      .then(function (answer) {
        if (bench.key !== key) return;
        if (answer.conflict) conflict(answer.body);
        else settle(answer.body);
      })
      .catch(function (error) {
        refused(error);
        changed();
      })
      .then(function () {
        inflight--;
        changed();
      });
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

export function applyArgs(args: Record<string, unknown>, fromEntry: string): void {
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
    }
  );
}

function undo(rev: number, ok: (by: number) => void, already: () => void): void {
  write(
    "/api/plans/{key}/undo",
    { rev: rev },
    function (reply) {
      var by = landed(reply, "none");
      if (by) ok(by);
    },
    function (body) {
      if (body.already_undone) {
        already();
        return;
      }
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

export function undoLast(): void {
  var target = bench.done.pop();
  if (target === undefined) {
    note("nothing of yours to undo on this plan");
    return;
  }
  undo(
    target,
    function (by) {
      bench.redo.push({ target: target!, by: by });
    },
    undoLast
  );
}

export function redoLast(): void {
  var entry = bench.redo.pop();
  if (!entry) {
    note("nothing to redo");
    return;
  }
  undo(
    entry.by,
    function (by) {
      bench.done.push(by);
    },
    redoLast
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
    }
  );
}

export function followHead(event: PlansEvent): void {
  if (event.key !== bench.key || !bench.plan || event.rev <= bench.plan.rev) return;
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

export function createPlan(base: string, args: Record<string, unknown>, fromEntry: string, n?: number): Promise<string> {
  var tries = n || 1;
  var name = tries === 1 ? base : base + " (" + tries + ")";
  return push<PushedResponse, Refusal & ApiError>("/api/plans", { name: name, args: args, from_entry: fromEntry }).then(function (answer) {
    if (!answer.conflict) return answer.body.key;
    if (answer.body.name_taken && tries < 20) return createPlan(base, args, fromEntry, tries + 1);
    throw new Error(answer.body.error || "a plan named “" + name + "” already exists");
  });
}

export function rateName(item: string, rate: number): string {
  return item + " " + (Math.round(rate * 100) / 100) + "/min";
}
