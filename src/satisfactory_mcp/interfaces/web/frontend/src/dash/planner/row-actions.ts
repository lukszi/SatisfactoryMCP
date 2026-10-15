/* The actions a build row offers, shared by the build list and the graph's node card. */

import { button, copyButton } from "../../kit/dashkit";
import { pct } from "../../kit/format";
import { WORDS } from "../../kit/words";
import { askButton } from "../../chat/asks";
import { createPin } from "../../chat/pins";
import { bench, pendingFocus } from "./state";
import { applyOps, banOps } from "./writes";

import type { SolveRow } from "../../api/shapes";

export function clockText(row: { machines: number; clock: number; last_clock?: number | null }): string {
  if (row.last_clock === null || row.last_clock === undefined) return pct(row.clock, 1);
  if (row.machines === 1) return pct(row.last_clock, 1);
  return "100%, last " + pct(row.last_clock, 1);
}

export function banButton(row: SolveRow): HTMLButtonElement {
  return button(
    "ban",
    function () {
      const member = row.recipe_id || row.recipe;
      pendingFocus.ctl = "banned:" + member;
      pendingFocus.until = Date.now() + 5000;
      applyOps(banOps(member));
    },
    { title: "keep this recipe out of the plan", label: "ban " + row.recipe }
  );
}

export function pinProcessButton(row: SolveRow): HTMLButtonElement | null {
  const id = row.recipe_id;
  if (!id) return null;
  return button(
    WORDS.pin,
    function () {
      createPin("process", { plan: bench.key, recipe: id! });
    },
    { title: "pin this process and copy its pin:N for chat", label: "pin " + row.recipe }
  );
}

export function copyIdButton(text: string, what: string): HTMLButtonElement {
  return copyButton(text, "copy", { title: "copy " + text + " for a tool call", label: "copy " + what });
}

export function processAsk(row: SolveRow, where: string): HTMLButtonElement {
  const ref = row.recipe_id || row.recipe;
  return askButton({ kind: "process", label: row.building + " · " + row.recipe, ref: ref, plan: bench.key, rev: bench.plan ? bench.plan.rev : null }, where + ":" + row.id);
}
