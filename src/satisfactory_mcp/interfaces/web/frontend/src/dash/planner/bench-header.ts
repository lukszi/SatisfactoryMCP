/* The workbench's head: the plan's name and sync state, its actions, the forgotten banner and
 * the versions others made since it was opened. */

import { askButton } from "../../chat/asks";
import { findPin, createPin } from "../../chat/pins";
import { button, chip, copyButton, inlineTextEdit, toggleButton } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { ageShort } from "../../kit/format";
import { WORDS } from "../../kit/words";
import { duplicateButton } from "./history";
import { toggleVersions } from "./reads";
import { bench, changed, NAME_MAX } from "./state";
import { dismissStrip, forgetPlan, redoLast, renamePlan, restorePlan, syncStatus, undoLast, undoRev } from "./writes";

const renaming = { on: false, fresh: false };

/** Leaves rename mode without a write, as opening another plan does. */
export function cancelRename(): void {
  renaming.on = false;
}

function titleLine(head: HTMLElement): void {
  const plan = bench.plan!;
  if (!renaming.on) {
    head.appendChild(make("h1", "", plan.name));
    return;
  }
  const focusNow = renaming.fresh;
  renaming.fresh = false;
  const box = inlineTextEdit({
    value: plan.name,
    ctl: "rename",
    label: "plan name",
    className: "dash-name plan-rename",
    maxLength: NAME_MAX,
    focusNow: focusNow,
    validate: function (text) {
      return text ? "" : "a plan name cannot be blank";
    },
    onCommit: function (text) {
      renaming.on = false;
      box.blur();
      if (text !== plan.name) renamePlan(text);
      else changed();
    },
    onCancel: function () {
      renaming.on = false;
      box.blur();
      changed();
    },
  });
  head.appendChild(box);
}

function planActions(parent: HTMLElement): void {
  const plan = bench.plan!;
  const acts = make("div", "dash-acts plan-acts");
  acts.appendChild(button("undo", undoLast, { title: "undo your last change on this plan (Ctrl+Z)", disabled: bench.gone || !bench.undoStack.length }));
  acts.appendChild(button("redo", redoLast, { title: "undo that undo (Ctrl+Shift+Z)", disabled: bench.gone || !bench.redoStack.length }));
  acts.appendChild(
    button(
      "rename",
      function () {
        renaming.on = true;
        renaming.fresh = true;
        changed();
      },
      { disabled: bench.gone || renaming.on }
    )
  );
  if (!bench.gone) acts.appendChild(button("forget", forgetPlan, { title: "hide this plan from the list; its history is kept and restore brings it back" }));
  acts.appendChild(toggleButton("versions", bench.versionsOpen, toggleVersions, { title: "every version of this plan: view one, or restore it as a new version" }));
  acts.appendChild(duplicateButton());
  const key = bench.key;
  const pinned = findPin("plan", function (ref) {
    return ref.plan === key;
  });
  acts.appendChild(
    button(
      pinned ? "copy " + pinned.id : WORDS.pin,
      function () {
        createPin("plan", { plan: key });
      },
      { title: "pin this plan and copy its pin:N for chat", label: pinned ? undefined : "pin this plan" }
    )
  );
  const call = "plan_factory(plan=" + JSON.stringify(plan.name) + ")  # base_rev=" + plan.rev;
  acts.appendChild(copyButton(call, "copy as tool call", { title: call }));
  if (!bench.gone) acts.appendChild(askButton({ kind: "plan", label: plan.name, ref: key, plan: key, rev: plan.rev }, "plan", WORDS.askChat));
  parent.appendChild(acts);
}

export function renderBenchHeader(parent: HTMLElement): void {
  const plan = bench.plan!;
  const head = make("div", "dash-title");
  titleLine(head);
  const status = syncStatus();
  head.appendChild(make("span", "plan-status" + (status === "conflict" ? " bad" : ""), "v" + plan.rev + " · " + status));
  if (bench.lastChange) head.appendChild(make("span", "plan-status", "last change: " + bench.lastChange.who + ", " + ageShort(bench.lastChange.ts) + " ago"));
  head.appendChild(make("span", "plan-held"));
  parent.appendChild(head);
  planActions(parent);
}

export function forgottenBanner(parent: HTMLElement): void {
  const line = make("div", "plan-warning plan-gone");
  line.appendChild(make("span", "", "this plan was forgotten: restore it to edit it"));
  line.appendChild(button("restore", restorePlan, { title: "bring the plan back as a new version" }));
  parent.appendChild(line);
}

/** The versions others made since this page opened the plan, each with its own undo. */
export function stripRows(parent: HTMLElement): void {
  if (!bench.othersCommits.length) return;
  const box = make("section", "dash-card");
  const title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", "changed since you opened it"));
  title.appendChild(
    button(
      "dismiss",
      function () {
        dismissStrip(null);
      },
      { title: "clear this list" }
    )
  );
  box.appendChild(title);
  bench.othersCommits.forEach(function (row) {
    const line = make("div", "plan-line");
    line.appendChild(chip(row.who, "muted"));
    line.appendChild(make("span", "dash-what" + (row.undone ? " dash-muted" : ""), "v" + row.rev + " " + row.text + (row.undone ? " (undone)" : "")));
    if (!row.undone && !bench.gone) {
      line.appendChild(
        button(
          "undo",
          function () {
            undoRev(row.rev);
          },
          { title: "undo v" + row.rev + " as a new version" }
        )
      );
    }
    box.appendChild(line);
  });
  const d = bench.othersDelta;
  if (d) box.appendChild(make("p", "dash-note", "result since v" + d.from_rev + ": " + d.text));
  parent.appendChild(box);
}
