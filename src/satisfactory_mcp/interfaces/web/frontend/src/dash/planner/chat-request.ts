/* A chat solve's request in words, and the name a plan made from it gets. */

import { perMin } from "../../kit/format";
import { hours } from "./power";
import { counted, OBJECTIVES } from "../../kit/words";

/** A plan request in one line: goal, exports, sources, recipe lists and power settings. */
export function describeArgs(args: Record<string, unknown>): string {
  return describeParts(args).join(" · ");
}

/** The parts `describeArgs` joins, one per setting. */
export function describeParts(args: Record<string, unknown>): string[] {
  const parts: string[] = [];
  const objective = args.objective as string | undefined;
  if (objective) parts.push("goal " + (OBJECTIVES[objective] || objective) + (args.target_item ? " of " + args.target_item : ""));
  const mins = (args.export_minimums as Record<string, number> | undefined) || {};
  const exported = ((args.exports as string[] | undefined) || []).slice();
  Object.keys(mins).forEach(function (item) {
    if (exported.indexOf(item) < 0) exported.push(item);
  });
  if (exported.length) {
    parts.push(
      "exports " +
        exported
          .map(function (item) {
            return item in mins ? item + " " + perMin(mins[item]!) : item;
          })
          .join(", ")
    );
  }
  const from = (args.sources as string[] | undefined) || [];
  const nodes = from.filter(function (source) {
    return source.indexOf("node:") === 0;
  }).length;
  if (!from.length) parts.push("from the whole map");
  else if (nodes === from.length) parts.push("from " + counted(nodes, "node"));
  else if (from.length < 3) parts.push("from " + from.join(", "));
  else parts.push("from " + counted(from.length, "selector"));
  const banned = ((args.exclude_recipes || args.banned) as string[] | undefined) || [];
  if (banned.length) parts.push(counted(banned.length, "banned recipe"));
  const required = (args.required as string[] | undefined) || [];
  if (required.length) parts.push(counted(required.length, "required recipe"));
  if (args.sloops) parts.push(counted(Number(args.sloops), "somersloop"));
  if (typeof args.payback_hours === "number") parts.push("payback " + hours(args.payback_hours));
  if (args.overclock_last === true) parts.push("overclock last");
  return parts;
}

/** A name for a plan made from chat's request: its first export at its rate, else chat's words. */
export function chatName(args: Record<string, unknown>, fallback: string): string {
  const mins = args.export_minimums as Record<string, number> | undefined;
  const items = mins ? Object.keys(mins) : [];
  if (items.length) return items[0] + " " + perMin(mins![items[0]!]!);
  return fallback.slice(0, 40) || "chat solve";
}
