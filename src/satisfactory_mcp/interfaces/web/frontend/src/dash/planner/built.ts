/* What counts as a plan's build: the progress setting's switch, the scope the plan stores
 * (`factory`), and naming an unnamed cluster so it can be that scope. See
 * docs/planner-p4_contract.md §17. */

import { send } from "../../api/client";
import { setSetting, settingChoice } from "../../app/settings";
import { fail, friendlyError, notify } from "../../kit/toast";
import { factoryNameProblem, labelRefusal, labelsVersionToSend, recordLabelsVersion, refetchLabelledViews } from "../factories/rename";
import { bench, changed } from "./state";
import { applyOps } from "./writes";

import type { NamedResponse, TrackBuiltAt, TrackBuiltCandidate } from "../../api/shapes";

export const SCOPE_AUTO = "";
export const SCOPE_WORLD = "/world";
export const SCOPE_NONE = "/none";

/** The cluster being named on the to build tab, if any. */
export const naming = { key: "", proposal: -1, name: "", problem: "", busy: false };

export function toggleProgress(): void {
  setSetting("progress", settingChoice("progress") === "percent" ? "machines" : "percent");
  changed();
}

export function setBuiltScope(value: string): void {
  const plan = bench.plan;
  if (!plan || bench.gone || plan.factory === value) return;
  applyOps([{ op: "set", field: "factory", value: value }]);
}

/** A candidate picked: a factory becomes the scope, a cluster opens the name field. */
export function pickCandidate(c: TrackBuiltCandidate): void {
  if (c.kind === "factory") {
    setBuiltScope(c.name);
    return;
  }
  Object.assign(naming, { key: bench.key, proposal: c.proposal === null ? -1 : c.proposal, name: c.name, problem: "", busy: false });
  changed();
}

export function stopNaming(): void {
  naming.key = "";
  changed();
}

/** Names the cluster being named and makes it the plan's scope. */
export function saveName(b: TrackBuiltAt, typed: string): void {
  const name = typed.trim();
  const problem = factoryNameProblem(name);
  naming.name = typed;
  if (problem) {
    naming.problem = problem;
    changed();
    return;
  }
  naming.busy = true;
  changed();
  send<NamedResponse>("POST", "/api/labels", { name: name, proposal: naming.proposal, as_of: b.token, version: labelsVersionToSend(b.labels_version) })
    .then(function (reply) {
      recordLabelsVersion(reply.version);
      Object.assign(naming, { key: "", proposal: -1, name: "", problem: "", busy: false });
      notify("named “" + reply.name + "”; the plan now counts it as built");
      refetchLabelledViews();
      setBuiltScope(reply.name);
    })
    .catch(function (failure) {
      const why = labelRefusal(failure);
      naming.busy = false;
      if (why === "name_taken") naming.problem = "“" + name + "” is already a factory name";
      else if (why === "bad") naming.problem = friendlyError(failure);
      else if (why === "stale" || why === "pin") {
        naming.key = "";
        fail("the save or the factory names moved, so “" + name + "” was not written; look again");
      } else fail("naming “" + name + "”: " + friendlyError(failure));
      changed();
    });
}
