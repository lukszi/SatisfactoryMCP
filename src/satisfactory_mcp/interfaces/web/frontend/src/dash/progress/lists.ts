/* The supporting lists: alternates worth getting, buy now (and almost), the folded reference. */

import { formatNumber } from "../../kit/format";
import { html, type Safe } from "../../kit/html";
import { sortSelect } from "../../kit/ui/controls";
import { entry, group, list } from "../../kit/ui/entry";
import { chip, tagChip } from "../../kit/ui/marks";
import { dashH, emptyLine, fig, icon } from "../../kit/ui/text";
import { altGroups, ALT_SORTS, mamTrees, type AltSort, type Buy, type TierSeg } from "./model";
import type { SectionParts } from "./gate";

import type { AlternateRow, AlternatesView, MamRow, MilestoneRow } from "../../api/shapes";

function signed(n: number): string {
  return (n > 0 ? "+" : n < 0 ? "−" : "") + formatNumber(Math.abs(Math.round(n)));
}

function altRow(r: AlternateRow): Safe {
  const where = r.parts.length ? " · in the " + r.parts.join(", ") + " chain" + (r.parts.length > 1 ? "s" : "") : "";
  const drive = r.source === "drive" ? chip("drive " + (r.drive ?? "?"), "muted", r.other ? "the other option: " + r.other : "") : "";
  return entry(
    { title: r.name + " (" + r.item_name + (r.machine ? ", " + r.machine : "") + ")" + where },
    html`${icon(r.item, r.item_name)}<div class="id"><div class="nl"><span class="line1 name">${r.name}</span><span class="ictx">${r.machine || ""}</span>${drive}</div></div>
    <div class="metric"><b>${signed(r.machines)}</b><span>machines</span></div><div class="facts"><span><em>${signed(r.mw)}</em> MW</span></div><div class="facts"><span><em>${signed(r.raw)}</em> raw /min</span></div>`
  );
}

export function alternatesSection(view: AlternatesView | null, pendingLine: Safe | null, sort: AltSort): SectionParts {
  const tools = sortSelect("pg-alt", ALT_SORTS, sort);
  if (!view) return { tools, body: pendingLine || emptyLine("the alternates could not be read") };
  const groups = altGroups(view.alternates, sort);
  if (!groups.length) return { tools, body: emptyLine("no alternate cuts this bill", view.base ? "the chains already use the best recipes the game has" : "nothing is owed") };
  const inner = groups.map(([label, rows]) => html`${group(label)}${rows.map(altRow)}`);
  return {
    tools,
    tip: "the whole bill at the rates for " + view.horizon_h + " h against the save's recipes and the default ones · a drive's other option goes back to the pool",
    body: list("palt", "stacked", inner),
  };
}

function unlockPills(names: string[]): Safe {
  if (!names.length) return html`<span class="muted">nothing new</span>`;
  const shown = names.slice(0, 2).map((n) => html`<span class="pill"><span class="nm">${n}</span></span>`);
  const rest = names.slice(2);
  return html`${shown}${rest.length ? chip("+" + rest.length, "muted", rest.join(", ")) : ""}`;
}

function buyRow(b: Buy): Safe {
  return entry(
    { title: b.unlocks.length ? "unlocks " + b.unlocks.join(", ") : "unlocks no recipe or building" },
    html`<div class="id"><span class="name">${b.name}</span></div><div class="chips">${tagChip(b.system + " · " + b.group)}</div><div class="pills">${unlockPills(b.unlocks)}</div>
    <div class="facts">${b.short ? html`<span>short <em>${formatNumber(b.short.amount)}</em> ${b.short.name}</span>` : ""}</div>`
  );
}

export function buySection(lists: { now: Buy[]; almost: Buy[] } | null, almostOpen: boolean, pendingLine: Safe | null): SectionParts {
  if (!lists) return { body: pendingLine || emptyLine("milestones and research could not be read") };
  const now = lists.now.length ? list("buy nolead", "stacked", lists.now.map(buyRow)) : emptyLine("nothing affordable now", lists.almost.length ? "the ones short of one item are below" : "");
  const almost = lists.almost.length
    ? html`<details class="fold" id="pg-almost"${almostOpen ? html` open` : ""}><summary>almost: short of one item · ${lists.almost.length}</summary>${list("buy nolead", "stacked", lists.almost.map(buyRow))}</details>`
    : "";
  return { n: lists.now.length, body: html`${now}${almost}` };
}

function tierRows(tiers: TierSeg[], milestones: MilestoneRow[]): Safe {
  const full = tiers.filter((t) => t.done === t.total);
  const firstOpen = tiers.find((t) => t.done < t.total);
  const doneRun = firstOpen ? full.filter((t) => t.tier < firstOpen.tier) : full;
  const rows: Safe[] = [];
  if (doneRun.length) {
    const n = doneRun.reduce((s, t) => s + t.total, 0);
    const span = doneRun.length > 1 ? "tiers " + doneRun[0]!.tier + " to " + doneRun[doneRun.length - 1]!.tier : "tier " + doneRun[0]!.tier;
    rows.push(entry({}, html`<div class="id"><span class="name">${span}</span><div class="ctx">every milestone done</div></div><div class="metric"><b>${n}</b><span>of ${n}</span></div>`));
  }
  tiers
    .filter((t) => doneRun.indexOf(t) < 0)
    .forEach(function (t) {
      const open = milestones.filter((m) => m.tier === t.tier && m.status !== "DONE");
      const text = open.map((m) => m.name + (m.opens_at !== null ? " (phase " + m.opens_at + ")" : m.status === "READY" ? ": affordable" : m.short.length ? ": short " + m.short.map((s) => formatNumber(s.amount) + " " + s.name).join(", ") : "")).join(" · ");
      rows.push(entry({ title: text }, html`<div class="id"><span class="name">tier ${t.tier}</span><div class="ctx">${text || "every milestone done"}</div></div><div class="metric"><b>${t.done}</b><span>of ${t.total}</span></div>`));
    });
  return html`${rows}`;
}

function treeRows(rows: MamRow[]): { n: number; body: Safe } {
  const trees = mamTrees(rows).filter((t) => t.open.length || t.running);
  const body = trees.map(function (t) {
    const running = t.running ? chip("running " + t.running.name + (t.running.pct !== null ? " · " + Math.round(t.running.pct) + "%" : ""), "muted") : "";
    const text = t.open.length ? "open: " + t.open.join(", ") : "";
    return entry({ title: text }, html`<div class="id"><div class="nl"><span class="line1 name">${t.tree}</span>${running}</div><div class="ctx">${text}</div></div><div class="metric"><b>${t.done}</b><span>of ${t.total}</span></div>`);
  });
  return { n: trees.length, body: html`${body}` };
}

export function referenceSection(tiers: TierSeg[] | null, milestones: MilestoneRow[], mam: MamRow[] | null, open: boolean): SectionParts {
  if (!tiers || !mam) return { body: emptyLine("loading milestones and research…") };
  const tDone = tiers.filter((t) => t.done === t.total).length;
  const mDone = mam.filter((r) => r.status === "DONE").length;
  const trees = treeRows(mam);
  const n = tiers.reduce((s, t) => s + t.total, 0);
  return {
    figs: html`${fig(tDone + " of " + tiers.length, "tiers done")}${fig(mDone + " of " + mam.length, "MAM nodes")}`,
    body: html`<details class="fold" id="pg-all"${open ? html` open` : ""}><summary>show all milestones and MAM nodes</summary><div class="pref">
      <div>${dashH("milestones", n, "h3")}${list("ref nolead", "stacked", tierRows(tiers, milestones))}</div>
      <div>${dashH("MAM trees with open nodes", trees.n, "h3")}${trees.n ? list("ref nolead", "stacked", trees.body) : emptyLine("every MAM node done")}</div></div></details>`,
  };
}
