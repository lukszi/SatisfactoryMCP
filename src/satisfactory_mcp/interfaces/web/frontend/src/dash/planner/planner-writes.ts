/* Every write the page makes to a plan, through one queue so versions land in order, and the
 * conflict chips that keep a push from silently overwriting someone else's edit. */

import { get, isNotFound, postWithConflict } from "../../api/client";
import { nowSeconds } from "../../kit/format";
import { fail, friendlyError, notify } from "../../kit/toast";
import { WORDS } from "../../kit/words";
import { delta, deltaForView, loadAlternates, loadTrack, loadVersions, saveToken, solveHead } from "./planner-reads";
import { actorWord, bench, changed, commitWords, resetBench } from "./planner-state";

import type { ApiError, StatusError } from "../../api/client";
import type { CommitBody, DeltaResponse, PlanOpsResponse, PlanStateBody, PushedResponse } from "../../api/shapes";
import type { Op, PlansEvent, Refusal } from "./planner-state";

type WritePath = "/api/plans/{key}/ops" | "/api/plans/{key}/args" | "/api/plans/{key}/undo" | "/api/plans/{key}/restore";

var NAME_RETRIES = 20;

var inflight = 0;
var refusalSerial = 0;
var chipSerial = 0;
var chain: Promise<void> = Promise.resolve();

function queue(task: () => Promise<void> | void): void {
  chain = chain.then(task).catch(function (error) {
    fail(friendlyError(error));
  });
}

export function hasPushInFlight(): boolean {
  return inflight > 0;
}

export function syncStatus(): string {
  if (inflight) return "pushing…";
  if (bench.conflictChips.length) return "conflict";
  return "saved";
}

/* ------------------------------------------------------- opening and following */

export function openPlan(key: string): void {
  resetBench(key);
  changed();
  queue(function () {
    return get<PlanStateBody>("/api/plans/{key}", key)
      .then(function (plan) {
        if (bench.key !== key) return;
        adopt(plan);
        return loadOwnUndoStack(key);
      })
      .catch(function (error) {
        if (bench.key !== key) return;
        bench.error = friendlyError(error);
        bench.missing = isNotFound(error);
        changed();
      });
  });
}

/** This page's own versions that nothing has undone yet, oldest first: what Ctrl+Z walks back. */
function loadOwnUndoStack(key: string): Promise<void> {
  return get<PlanOpsResponse>("/api/plans/{key}/ops?since=0", key).then(function (ops) {
    if (bench.key !== key) return;
    const undone: Record<number, boolean> = {};
    ops.commits.forEach(function (commit) {
      if (commit.undoes) undone[commit.undoes] = true;
    });
    bench.undoStack = ops.commits
      .filter(function (commit) {
        return commit.rev > 1 && commit.actor.kind === "page" && !commit.undoes && !undone[commit.rev];
      })
      .map(function (commit) {
        return commit.rev;
      });
    const last = ops.commits[ops.commits.length - 1];
    if (last) bench.lastChange = { who: actorWord(last.actor), ts: last.ts };
    changed();
  });
}

function adopt(plan: PlanStateBody): void {
  if (bench.plan && plan.rev < bench.plan.rev) return;
  const moved = !bench.plan || bench.plan.rev !== plan.rev;
  bench.plan = plan;
  bench.gone = plan.forgotten;
  changed();
  solveHead();
  if (moved) {
    if (bench.versionsOpen) loadVersions();
    deltaForStrip();
    if (bench.viewedRev) deltaForView();
    if (bench.alternates) loadAlternates();
    loadTrack();
  }
}

function chatTouched(d: DeltaResponse): Record<string, true> {
  const out: Record<string, true> = {};
  const fromChat = bench.othersCommits.some(function (row) {
    return row.who === WORDS.actorChat;
  });
  if (!fromChat) return out;
  (d.rows || []).forEach(function (row) {
    if (row.change !== "removed") out[row.id] = true;
  });
  return out;
}

function noteOthersCommit(commit: CommitBody): void {
  if (bench.ownRevs[commit.rev] || !commit.text) return;
  const seen = bench.othersCommits.some(function (row) {
    return row.rev === commit.rev;
  });
  if (seen) return;
  bench.othersCommits.push({ rev: commit.rev, text: commitWords(commit.text), who: actorWord(commit.actor), undone: false });
  bench.lastChange = { who: actorWord(commit.actor), ts: commit.ts };
  deltaForStrip();
}

export function dismissStrip(rev: number | null): void {
  bench.othersCommits = bench.othersCommits.filter(function (row) {
    return rev !== null && row.rev !== rev;
  });
  if (!bench.othersCommits.length) {
    bench.othersDelta = null;
    bench.chatChangedRows = {};
  } else deltaForStrip();
  changed();
}

function deltaForStrip(): void {
  const plan = bench.plan;
  if (!plan || !bench.othersCommits.length) return;
  const from =
    Math.min.apply(
      null,
      bench.othersCommits.map(function (row) {
        return row.rev;
      })
    ) - 1;
  const to = plan.rev;
  const have = bench.othersDelta;
  if (from < 1 || (have && have.from_rev === from && have.to_rev === to)) return;
  const key = bench.key;
  delta(key, from, to)
    .then(function (d) {
      if (bench.key !== key || !bench.plan || bench.plan.rev !== to || !bench.othersCommits.length) return;
      bench.othersDelta = d;
      bench.chatChangedRows = chatTouched(d);
      changed();
    })
    .catch(function () {});
}

export function followHead(event: PlansEvent): void {
  if (event.key !== bench.key || !bench.plan || event.rev <= bench.plan.rev) return;
  pullHead();
}

export function resyncHead(): void {
  if (bench.plan) pullHead();
}

function pullHead(): void {
  const key = bench.key;
  queue(function () {
    if (bench.key !== key || !bench.plan) return;
    return get<PlanOpsResponse>(`/api/plans/{key}/ops?since=${bench.plan.rev}`, key)
      .then(function (ops) {
        if (bench.key !== key) return;
        ops.commits.forEach(noteOthersCommit);
        return get<PlanStateBody>("/api/plans/{key}", key);
      })
      .then(function (plan) {
        if (plan && bench.key === key) adopt(plan);
      });
  });
}

/* ------------------------------------------------------- pushing and conflicts */

function onWriteRefused(error: StatusError): void {
  if (error.status === 410) {
    bench.gone = true;
    fail("this plan was forgotten; nothing was written");
  } else {
    fail(friendlyError(error));
  }
}

/** Takes a landed push on board; returns the version it made, or 0 for a no-op. */
function adoptPushReply(reply: PushedResponse, undoable: boolean): number {
  if (!reply.noop) {
    bench.ownRevs[reply.rev] = true;
    if (undoable) bench.undoStack.push(reply.rev);
    bench.lastChange = { who: WORDS.actorYou, ts: nowSeconds() };
  }
  reply.others.forEach(noteOthersCommit);
  adopt(reply.state);
  return reply.noop ? 0 : reply.rev;
}

function raiseConflicts(body: Refusal, retry: () => void): void {
  if (!body.outdated || !body.state) {
    fail(body.error || "the server refused this change");
    changed();
    return;
  }
  (body.since || []).forEach(noteOthersCommit);
  const refusal = ++refusalSerial;
  (body.conflicts || []).forEach(function (conflict) {
    bench.conflictChips.push({
      id: ++chipSerial,
      gesture: refusal,
      field: conflict.mine.field || conflict.key.replace(/[[{].*$/, ""),
      who: actorWord(conflict.theirs_actor),
      text: conflict.text,
      retry: retry,
    });
  });
  adopt(body.state);
}

export function dropChip(id: number, retry: boolean): void {
  const chip = bench.conflictChips.filter(function (c) {
    return c.id === id;
  })[0];
  if (!chip) return;
  const refusal = chip.gesture;
  bench.conflictChips = bench.conflictChips.filter(function (c) {
    return c.gesture !== refusal;
  });
  changed();
  if (retry) chip.retry();
}

// Our own unacknowledged revisions cannot conflict with us, so base past them.
function baseFor(seenRev: number): number {
  const head = bench.plan ? bench.plan.rev : seenRev;
  for (let rev = seenRev + 1; rev <= head; rev++) {
    if (!bench.ownRevs[rev]) return seenRev;
  }
  return head;
}

function onlyOwn(body: Refusal, base: number): boolean {
  const conflicts = body.conflicts || [];
  return (
    !!body.outdated &&
    !!body.state &&
    conflicts.length > 0 &&
    conflicts.every(function (conflict) {
      return conflict.theirs_rev > base && !!bench.ownRevs[conflict.theirs_rev];
    })
  );
}

/** One push, queued; `fixedBase` sends it against that version exactly, never rebased. */
function write(
  path: WritePath,
  extra: Record<string, unknown>,
  settle: (reply: PushedResponse) => void,
  conflict: (body: Refusal) => void,
  fixedBase?: number
): void {
  const plan = bench.plan;
  if (!plan) return;
  const key = bench.key;
  const name = plan.name;
  const madeAgainst = fixedBase === undefined ? plan.rev : fixedBase;
  const lost = function (why: string) {
    fail("your change to “" + name + "” was not saved: " + why);
  };
  const attempt = function (base: number): Promise<void> {
    const here = bench.key === key;
    const body: Record<string, unknown> = { base_rev: base, sav: here ? saveToken() : "" };
    Object.keys(extra).forEach(function (field) {
      body[field] = extra[field];
    });
    return postWithConflict<PushedResponse, Refusal>(path, body, key).then(function (answer) {
      if (bench.key !== key) {
        if (answer.conflict) lost(answer.body.error || "the server refused it");
        return;
      }
      if (!answer.conflict) {
        settle(answer.body);
        return;
      }
      // A conflict made only of our own pushes is replayed on the new head.
      if (fixedBase === undefined && onlyOwn(answer.body, base)) {
        adopt(answer.body.state!);
        return attempt(answer.body.head!);
      }
      conflict(answer.body);
    });
  };
  inflight++;
  changed();
  queue(function () {
    const done = function () {
      inflight--;
      changed();
    };
    const start = bench.key === key && fixedBase === undefined ? baseFor(madeAgainst) : madeAgainst;
    return attempt(start)
      .catch(function (error) {
        if (bench.key === key) onWriteRefused(error);
        else lost(friendlyError(error));
      })
      .then(done, done);
  });
}

/** One gesture on the bench: its ops pushed as one version. */
export function applyOps(ops: Op[], requireItem?: string): void {
  if (!ops.length || !bench.plan) return;
  bench.redoStack = [];
  const again = function () {
    applyOps(ops, requireItem);
  };
  write(
    "/api/plans/{key}/ops",
    requireItem ? { ops: ops, require_item: requireItem } : { ops: ops },
    function (reply) {
      adoptPushReply(reply, true);
    },
    function (body) {
      raiseConflicts(body, again);
    }
  );
}

export function applyArgs(args: Record<string, unknown>, fromEntry: string, base?: number): void {
  if (!bench.plan) return;
  bench.redoStack = [];
  const again = function () {
    applyArgs(args, fromEntry);
  };
  write(
    "/api/plans/{key}/args",
    { args: args, from_entry: fromEntry },
    function (reply) {
      adoptPushReply(reply, true);
      if (reply.noop) notify("nothing changed: the plan already matches chat's request");
    },
    function (body) {
      raiseConflicts(body, again);
    },
    base
  );
}

function inArgList(field: "required" | "banned", member: string): boolean {
  return !!bench.plan && bench.plan.args[field].indexOf(member) >= 0;
}

/** Banning a required recipe also drops it from the required list, in the same version. */
export function banOps(member: string): Op[] {
  const ops: Op[] = [{ op: "add", field: "banned", member: member }];
  if (inArgList("required", member)) ops.push({ op: "remove", field: "required", member: member });
  return ops;
}

export function renamePlan(name: string): void {
  applyOps([{ op: "rename", name: name }]);
}

export function forgetPlan(): void {
  applyOps([{ op: "forget" }]);
}

export function restorePlan(): void {
  applyOps([{ op: "restore" }]);
}

/* --------------------------------------------------------------- undo and redo */

function conflictTexts(body: Refusal): string {
  return (body.conflicts || [])
    .map(function (conflict) {
      return conflict.text;
    })
    .join("; ");
}

function undo(rev: number, ok: (by: number) => void, already: () => void, drop: () => void): void {
  write(
    "/api/plans/{key}/undo",
    { rev: rev },
    function (reply) {
      const by = adoptPushReply(reply, false);
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
        (body.since || []).forEach(noteOthersCommit);
        const why = conflictTexts(body);
        fail("v" + rev + " cannot be undone: it was changed again since" + (why ? " (" + why + ")" : ""));
        adopt(body.state);
        return;
      }
      fail(body.error || "the server refused the undo");
    }
  );
}

function removeLast<T>(list: T[], item: T): void {
  const at = list.lastIndexOf(item);
  if (at >= 0) list.splice(at, 1);
}

export function undoLast(): void {
  const target = bench.undoStack[bench.undoStack.length - 1];
  if (target === undefined) {
    notify("nothing of yours to undo on this plan");
    return;
  }
  const take = function () {
    removeLast(bench.undoStack, target!);
  };
  undo(
    target,
    function (by) {
      take();
      bench.redoStack.push({ target: target!, by: by });
    },
    function () {
      take();
      undoLast();
    },
    take
  );
}

export function redoLast(): void {
  const entry = bench.redoStack[bench.redoStack.length - 1];
  if (!entry) {
    notify("nothing to redo");
    return;
  }
  const take = function () {
    removeLast(bench.redoStack, entry!);
  };
  undo(
    entry.by,
    function (by) {
      take();
      bench.undoStack.push(by);
    },
    function () {
      take();
      redoLast();
    },
    take
  );
}

function markUndone(rev: number): void {
  bench.othersCommits.forEach(function (row) {
    if (row.rev === rev) row.undone = true;
  });
}

export function undoRev(rev: number): void {
  undo(
    rev,
    function (by) {
      bench.undoStack.push(by);
      markUndone(rev);
    },
    function () {
      markUndone(rev);
      notify("v" + rev + " is already undone");
      changed();
    },
    function () {}
  );
}

export function restoreRev(rev: number, done?: () => void): void {
  if (!bench.plan) return;
  bench.redoStack = [];
  write(
    "/api/plans/{key}/restore",
    { rev: rev },
    function (reply) {
      adoptPushReply(reply, true);
      if (reply.noop) notify("nothing changed: the plan already equals v" + rev);
      if (done) done();
    },
    function (body) {
      if (body.name_taken) {
        fail("v" + rev + " cannot be restored: " + (body.error || "its name is taken"));
        changed();
        return;
      }
      raiseConflicts(body, function () {
        restoreRev(rev, done);
      });
    }
  );
}

/* ------------------------------------------------- writes to other plans */

export function duplicatePlan(rev?: number): Promise<string> {
  const body: Record<string, unknown> = {};
  if (rev) body.rev = rev;
  return postWithConflict<PushedResponse, Refusal & ApiError>("/api/plans/{key}/duplicate", body, bench.key).then(function (answer) {
    if (answer.conflict) throw new Error(answer.body.error || "the copy could not be named");
    return answer.body.key;
  });
}

/** Undoes `rev` of any plan from its head, outside the bench; resolves to what to say. */
export function undoRevisionOf(key: string, rev: number): Promise<string> {
  return get<PlanStateBody>("/api/plans/{key}", key).then(function (head) {
    return postWithConflict<PushedResponse, Refusal & ApiError>("/api/plans/{key}/undo", { base_rev: head.rev, rev: rev }, key).then(function (answer) {
      if (!answer.conflict) return answer.body.noop ? "nothing to undo in v" + rev : "undid v" + rev + " of “" + head.name + "”";
      if (answer.body.already_undone) return "v" + rev + " is already undone";
      const why = conflictTexts(answer.body);
      throw new Error("v" + rev + " cannot be undone: it was changed again since" + (why ? " (" + why + ")" : ""));
    });
  });
}

/** A new plan named `base`, or `base (2)` and on while that name is taken. */
export function createPlan(base: string, args: Record<string, unknown>, fromEntry: string, attempt?: number): Promise<string> {
  const tries = attempt || 1;
  const name = tries === 1 ? base : base + " (" + tries + ")";
  return postWithConflict<PushedResponse, Refusal & ApiError>("/api/plans", { name: name, args: args, from_entry: fromEntry }).then(function (answer) {
    if (!answer.conflict) return answer.body.key;
    if (answer.body.name_taken && tries < NAME_RETRIES) return createPlan(base, args, fromEntry, tries + 1);
    throw new Error(answer.body.error || "a plan named “" + name + "” already exists");
  });
}
