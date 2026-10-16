/* Plan history on the page: the Versions list and a version viewed read-only.
 * See docs/plan_management.md. */

import { button, error, loading, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { ageShort } from "../../kit/format";
import { go } from "../../app/nav";
import { describeArgs } from "./chat-card";
import { actorWord, bench, commitWords } from "./state";
import { duplicatePlan, restoreRev } from "./writes";
import { renderVersionResult } from "./result";
import { fail, friendlyError, notify } from "../../kit/toast";

import type { Column } from "../../kit/dashkit";
import type { FocusSelection, VersionRow } from "../../api/shapes";

function planDash(key: string, rev?: number): string {
  return "planner/" + key + (rev ? "/v" + rev : "");
}

function duplicateAndOpen(rev?: number): void {
  duplicatePlan(rev)
    .then(function (key) {
      notify("copied to a new plan");
      go(planDash(key));
    })
    .catch(function (reason) {
      fail(friendlyError(reason));
    });
}

export function duplicateButton(rev?: number): HTMLButtonElement {
  return button(
    rev ? "duplicate v" + rev : "duplicate",
    function () {
      duplicateAndOpen(rev);
    },
    { title: "a new plan at v1 with this version's request; this plan is untouched" }
  );
}

function versionStateText(row: VersionRow, head: number): string {
  const parts: string[] = [];
  if (row.rev === head) parts.push("head");
  if (row.undone_by) parts.push("undone in v" + row.undone_by);
  if (row.restores) parts.push("restores v" + row.restores);
  if (row.merged_over.length) parts.push("merged over " + row.merged_over.map(function (r) { return "v" + r; }).join(", "));
  return parts.join(" · ");
}

function versionActs(row: VersionRow, head: number): HTMLElement {
  const box = make("span", "dash-acts");
  const key = bench.key;
  box.appendChild(
    button(
      "view",
      function () {
        go(planDash(key, row.rev));
      },
      { title: "open v" + row.rev + " read-only", label: "view v" + row.rev, disabled: row.rev === bench.viewedRev }
    )
  );
  if (row.rev !== head && !bench.gone) {
    box.appendChild(
      button(
        "restore",
        function () {
          restoreRev(row.rev);
        },
        { title: "a new version equal to v" + row.rev + "; nothing after it is lost", label: "restore v" + row.rev }
      )
    );
  }
  return box;
}

export function renderVersions(parent: HTMLElement): void {
  if (!bench.versionsOpen) return;
  const card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "versions"));
  const data = bench.versions;
  if (bench.versionsError) error(card, "the versions", bench.versionsError);
  else if (data?.key !== bench.key) loading(card, "versions");
  else {
    const head = data.head;
    const columns: Column<VersionRow>[] = [
      { key: "acts", label: "", render: function (r) { return versionActs(r, head); } },
      {
        key: "rev",
        label: "version",
        align: "right",
        render: function (r) {
          return "v" + r.rev;
        },
      },
      {
        key: "who",
        label: "by",
        render: function (r) {
          return actorWord(r.actor);
        },
      },
      {
        key: "when",
        label: "when",
        className: "dash-sub",
        render: function (r) {
          return ageShort(r.ts) + " ago";
        },
      },
      {
        key: "what",
        label: "change",
        render: function (r) {
          return commitWords(r.text);
        },
      },
      {
        key: "state",
        label: "",
        className: "dash-sub",
        render: function (r) {
          return versionStateText(r, head);
        },
      },
    ];
    card.appendChild(
      table(columns, data.versions, {
        rowClass: function (r) {
          return r.rev === bench.viewedRev ? "plan-picked" : "";
        },
        caption: "versions of this plan",
      })
    );
    card.appendChild(make("p", "dash-note", "restore adds a new version equal to the old one; view opens a version read-only"));
  }
  parent.appendChild(card);
}

export function renderRevisionView(parent: HTMLElement, select: (s: FocusSelection) => void): void {
  const rev = bench.viewedRev;
  const plan = bench.plan!;
  const card = make("section", "dash-card plan-strip");
  const title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", "v" + rev + " · read-only"));
  title.appendChild(make("span", "plan-status", "the plan is at v" + plan.rev));
  card.appendChild(title);
  const acts = make("div", "dash-acts");
  acts.appendChild(
    button("back to v" + plan.rev, function () {
      go(planDash(bench.key));
    })
  );
  if (rev !== plan.rev && !bench.gone) {
    acts.appendChild(
      button(
        "restore v" + rev,
        function () {
          restoreRev(rev, function () {
            go(planDash(bench.key));
          });
        },
        { title: "a new version equal to v" + rev + "; nothing after it is lost" }
      )
    );
  }
  acts.appendChild(duplicateButton(rev));
  card.appendChild(acts);
  const shown = bench.viewedPlan;
  if (bench.viewedError) error(card, "v" + rev, bench.viewedError);
  else if (!shown) loading(card, "v" + rev);
  else {
    card.appendChild(make("p", "", (shown.name !== plan.name ? "named “" + shown.name + "” · " : "") + describeArgs(shown.args as unknown as Record<string, unknown>)));
    if (bench.viewedDelta?.from_rev === rev) {
      card.appendChild(make("p", "dash-note", "result from v" + rev + " to v" + bench.viewedDelta.to_rev + ": " + bench.viewedDelta.text));
    }
  }
  parent.appendChild(card);
  if (bench.viewedResult) renderVersionResult(parent, bench.viewedResult, rev, select);
  else if (shown && !bench.viewedError) loading(parent, "the result of v" + rev);
}
