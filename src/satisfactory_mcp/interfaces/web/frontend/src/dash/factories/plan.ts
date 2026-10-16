/* The floor plan panel: the factory's machines as their outlines with height bands, belts as
 * thin lines, decks as a faint base, turned square to the grid; the floor panel down its left
 * edge, the floor below as a ghost. docs/frontend_kit.md, "Factories". */

import { cls, html, type Safe } from "../../kit/html";
import { emptyLine } from "../../kit/ui/text";
import { metres } from "../../kit/format";
import { runKey, runsOn } from "../../map/floors/model";
import { stateTone } from "../machine-states";
import { read } from "./data";
import { aspectHref, machineKeys, onFloor, type FCtx } from "./ctx";
import { floorBelow, type PlanMachine } from "./model";
import { beltPath, centreOf, deckCells, frameAround, gridOrigin, pageTurn, place, segmentInBox, shapeDefs, type DeckCells, type Frame, type PlanBelt, type PlanDeck, type PlanPiece, type XY } from "./plan-geometry";

import type { BeltRow, BeltsResponse, StructuresResponse } from "../../api/shapes";

/* Inline, because stylesheet rules do not reach the copies a <use> draws; custom properties
 * inherit into them, so the marks' state classes still set colour and width. */
const OUTLINE = "fill:inherit;stroke:currentColor;stroke-width:var(--plan-w);stroke-linejoin:round;vector-effect:non-scaling-stroke";
const BAND = "fill:var(--plan-band-ink);stroke:none;fill-opacity:";

/* Belt segments joined per chain, once per belts read. */
const chainsOf = new WeakMap<BeltsResponse, Map<number, BeltRow[]>>();

function chains(body: BeltsResponse): Map<number, BeltRow[]> {
  let by = chainsOf.get(body);
  if (by) return by;
  by = new Map();
  body.belts.forEach((b) => {
    const list = by!.get(b.chain);
    if (list) list.push(b);
    else by!.set(b.chain, [b]);
  });
  chainsOf.set(body, by);
  return by;
}

function decksOf(c: FCtx, structures: StructuresResponse | null): PlanDeck[] {
  if (!structures) return [];
  const out: PlanDeck[] = [];
  c.floors.forEach((f) =>
    f.bands.forEach((b) =>
      b.band.deck_rows.forEach((at) => {
        const s = structures.structures[at];
        if (s) out.push({ floor: f.ordinal, x: s.x_m, y: s.y_m, yaw: s.yaw });
      })
    )
  );
  return out;
}

function beltsOf(c: FCtx, body: BeltsResponse | null, ms: PlanMachine[]): PlanBelt[] {
  if (!body) return [];
  const by = chains(body);
  const out: PlanBelt[] = [];
  const floorsOfChain = new Map<number, number[]>();
  if (c.floorsBody) {
    const fb = c.floorsBody;
    c.floors.forEach((f) =>
      f.bands.forEach((b) => {
        const runs = runsOn(fb, b.band, b.platform);
        Object.keys(runs).forEach((k) => {
          const run = runs[k]!;
          if (run.kind !== "belt" || k !== runKey(run)) return;
          const list = floorsOfChain.get(run.key) || [];
          if (list.indexOf(f.ordinal) < 0) list.push(f.ordinal);
          floorsOfChain.set(run.key, list);
        });
      })
    );
  }
  if (floorsOfChain.size) {
    floorsOfChain.forEach((floors, chain) => (by.get(chain) || []).forEach((seg) => out.push({ floors, pts: seg.points_m.map((p): XY => [p[0], p[1]]) })));
    return out;
  }
  const xs = ms.map((m) => m.row.x_m!);
  const ys = ms.map((m) => m.row.y_m!);
  const box: [number, number, number, number] = [Math.min(...xs) - 4, Math.min(...ys) - 4, Math.max(...xs) + 4, Math.max(...ys) + 4];
  by.forEach((segs) => {
    const pts = segs.map((seg) => seg.points_m.map((p): XY => [p[0], p[1]]));
    if (pts.some((p) => segmentInBox(p, box))) pts.forEach((p) => out.push({ floors: null, pts: p }));
  });
  return out;
}

function floorNav(c: FCtx): Safe {
  if (c.floors.length < 2) return html``;
  const all = c.machines ? c.machines.length : 0;
  const button = (q: string, on: boolean, big: string, deck: number | null, n: number, label: string) =>
    html`<a class="${cls("ffl", on && "on")}" href="${aspectHref(c, c.aspect, q)}"${on ? html` aria-current="true"` : ""} title="${label}" aria-label="${label}" data-floor="${q}"><b>${big}</b><span class="n">${n}</span>${deck !== null ? html`<span class="d">${metres(deck, 0)}</span>` : ""}</a>`;
  const floors = c.floors.map((f) => button(String(f.ordinal), c.floor === f.ordinal, String(f.ordinal), f.deck, f.ids.length, "floor " + f.ordinal + (f.deck !== null ? " · deck " + metres(f.deck, 1) : "") + " · " + f.ids.length + " machines"));
  return html`<nav class="fflr" aria-label="floors">${button("all", c.floor < 0, "all", null, all, "all floors · " + all + " machines")}${floors}</nav>`;
}

function legendBox(cls: string): Safe {
  return html`<svg class="lg" viewBox="0 0 12 12" aria-hidden="true"><rect class="${cls}" x="1.5" y="1.5" width="9" height="9"/></svg>`;
}

function legend(shown: PlanMachine[], belts: number, ghost: boolean): Safe {
  const red = shown.some((m) => stateTone(m.row.state) === "bad");
  const blocked = shown.some((m) => stateTone(m.row.state) === "blocked");
  return html`<div class="fplan-leg">${red ? html`<span class="lgi">${legendBox("lg-bad")}need action</span>` : ""}${blocked ? html`<span class="lgi">${legendBox("lg-blk")}blocked</span>` : ""}${belts ? html`<span class="lgi"><svg class="lg" viewBox="0 0 12 12" aria-hidden="true"><path class="lg-ln" d="M1 9 6 4 11 6"/></svg>belt</span>` : ""}${ghost ? html`<span class="lgi">${legendBox("lg-gh")}floor below</span>` : ""}<span class="fplan-ph">layout from the save</span></div>`;
}

function view(f: Frame): string {
  return f.map((v) => (Math.round(v * 10) / 10).toString()).join(" ");
}

/** What a click on a machine picks on this aspect. */
function pickKey(c: FCtx, m: PlanMachine): string {
  if (c.aspect === "floors" && m.floor !== null) return "fl" + m.floor;
  if (c.aspect === "sites" && m.site !== null) return "s" + m.site;
  return "g" + m.group;
}

export function planPanel(c: FCtx): Safe {
  if (!c.machines) return html`<section class="fplan" aria-label="floor plan"><div class="fmap">${c.machinesWait || ""}</div></section>`;
  const ms = c.machines.filter((m) => m.row.x_m !== null && m.row.y_m !== null);
  if (!ms.length) return html`<section class="fplan" aria-label="floor plan"><div class="fmap">${emptyLine("no machines placed", "nothing of this factory stands in the save")}</div></section>`;
  const belts = beltsOf(c, read<BeltsResponse>("/api/belts").data, ms);
  const decks = decksOf(c, read<StructuresResponse>("/api/structures").data);
  const pieces: PlanPiece[] = ms.map((m) => ({ id: m.id, cls: m.row.cls, x: m.row.x_m!, y: m.row.y_m!, yaw: m.row.yaw, w: m.row.w_m, l: m.row.l_m }));
  const turn = pageTurn(decks, pieces);
  const centre = centreOf(pieces);
  const { defs, of } = shapeDefs(pieces, c.shapes);
  const placed = place(pieces, of, centre, turn);
  const at: Record<string, (typeof placed)[number]> = {};
  placed.forEach((p) => (at[p.id] = p));
  const all = c.floor < 0;
  const below = all ? null : floorBelow(c.floors, c.floor);
  const origin = gridOrigin(decks, centre, turn);
  const deckOf = (floor: number | null): DeckCells => deckCells(decks.filter((d) => floor === null || d.floor === floor), centre, turn, origin);
  const shown = ms.filter((m) => onFloor(c, m));
  const here = deckOf(all ? null : c.floor);
  const frame = frameAround(shown.map((m) => at[m.id]!), defs, shown.length ? undefined : here.corners) || frameAround(placed, defs)!;
  const tr = (p: (typeof placed)[number]) => "translate(" + p.x.toFixed(1) + " " + p.y.toFixed(1) + ")" + (p.rot ? " rotate(" + p.rot.toFixed(1) + ")" : "");
  const symbols = defs.map((d, i) => html`<g id="fpc-${i}"><path d="${d.outline}" style="${OUTLINE}"/>${d.bands.map((b) => html`<path d="${b.d}" style="${BAND + b.veil}"/>`)}</g>`);
  const beltD = belts.filter((b) => (all ? true : b.floors !== null && b.floors.indexOf(c.floor) >= 0)).map((b) => beltPath(b.pts, centre, turn)).join("");
  const ghost =
    below === null
      ? ""
      : html`<g class="pghost" aria-hidden="true"><path class="pg-edge" d="${deckOf(below).edge}"/>${ms.filter((m) => m.floor === below).map((m) => html`<use href="#fpc-${at[m.id]!.def}" class="pgm" transform="${tr(at[m.id]!)}"/>`)}</g>`;
  const order = shown.slice().sort((a, b) => (a.row.z_m ?? 0) - (b.row.z_m ?? 0));
  const goes = c.aspect === "machines" || c.aspect === "floors" || c.aspect === "sites" ? "" : aspectHref(c, "machines");
  const marks = order.map(function (m) {
    const tone = stateTone(m.row.state);
    const keys = machineKeys(m);
    const lit = c.picked && (" " + keys + " ").indexOf(" " + c.picked + " ") >= 0;
    return html`<use href="#fpc-${at[m.id]!.def}" class="${cls("pm", (tone === "bad" || tone === "blocked") && tone, lit && "on")}" data-k="${keys}" data-pk="${pickKey(c, m)}"${goes ? html` data-go="${goes}"` : ""} transform="${tr(at[m.id]!)}"><title>${m.row.name + " · " + (m.row.recipe_name || "no recipe") + " · " + m.row.state}</title></use>`;
  });
  const label = "floor plan of " + c.name + (all ? "" : ", floor " + c.floor);
  const svg = html`<svg class="${cls("fsvg", c.picked && "has-sel")}" tabindex="0" viewBox="${view(frame)}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${label}"><defs>${symbols}</defs>${here.cells ? html`<path class="pbase" d="${here.cells}"/>` : ""}${ghost}${beltD ? html`<path class="pbelt" d="${beltD}"/>` : ""}${marks}</svg>`;
  const nav = floorNav(c);
  return html`<section class="${cls("fplan", c.floors.length > 1 && "has-floors")}" aria-label="floor plan">${nav}<div class="fmap">${svg}${legend(shown, beltD ? 1 : 0, below !== null)}</div></section>`;
}

