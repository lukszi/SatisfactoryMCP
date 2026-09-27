/* Floor mode: the same map, one storey at a time.
 *
 * NOT A SECOND RENDERER. Everything on screen in floor mode was drawn by the module that always
 * drew it, and this file only decides which of those pieces are on the floor being looked at.
 * `/api/floors` answers the one thing a client cannot derive -- WHICH floor each thing is on --
 * so the only geometry rule here is `standsOn`, for the layers the payload does not cover.
 *
 * THE JOINS, because they are all different:
 *
 *   * a machine, extractor, generator or belt attachment -- BY INSTANCE ID. A band lists them.
 *   * a belt run BY CHAIN, a pipe run BY ITS ROW, keyed by the number the belt and pipe
 *     payloads already carry.
 *   * a foundation piece -- BY POSITION in `/api/structures`. A lightweight buildable has no
 *     instance name at all, so `deck_rows` is the only name a deck can be listed by.
 *   * storage -- BY WHERE IT STANDS, because the decomposition does not decompose it.
 *   * the power grid -- BY WHERE ITS ENDS STAND. `/api/floors` has never grouped a wire, so
 *     this file places them with `standsOn`, twice, because a wire has two ends. Each end is
 *     judged at its ANCHOR rather than at its drawn endpoint, because an endpoint is a
 *     connector 7 m over a Mk1 pole and 24 m over a tower. See `onThisFloor` and the power
 *     branch of `applyFilter`.
 *
 * THE GROUND is a pseudo-floor and not a band, because a miner stands on a resource node up to
 * 28 m off any deck and plumbing hugs the ground -- neither is on floor zero, and putting them
 * there would claim a measurement the page does not have. The row says WHICH claim it is,
 * because without a heightfield "on terrain" degrades to "off-deck".
 */

import { get, latest } from "./api";
import { code, popup } from "./dom";
import { pct } from "./format";
import { batch, hideFloors, onFloorExit, onFloorPick, showFloors } from "./layercontrol";
import { L } from "./leaflet";
import { flyPadded, map, writeHash } from "./map";
// Reached for a rule rather than a picture: `sinkRoutes` owns the stacking inside the three
// route layers, and rebuilding a group is exactly what disturbs it.
import { sinkRoutes } from "./routes";
import { state } from "./state";
import { friendly, note } from "./toast";

import type { components } from "./api-schema";
import type { Point3M } from "./geometry";
import type { Row } from "./dom";
import type { FloorMark } from "./leaflet-private";
import type { FloorChoice } from "./layercontrol";

type FloorsResponse = components["schemas"]["FloorsResponse"];
type FloorPlatform = components["schemas"]["FloorPlatform"];
type FloorBand = components["schemas"]["FloorBand"];
type FloorDeck = components["schemas"]["FloorDeck"];
type FloorRun = components["schemas"]["FloorRun"];

/** The pseudo-floor's key, in the picker and in the fragment alike. */
export var GROUND = "ground";

/* What "show me this factory" means, borrowed from labels.ts rather than restated. Storage is
 * still FILTERED below when the reader has ticked it, which is the difference between what a
 * gesture turns on and what a mode is about. */
var FLOOR_LAYERS = ["machines", "belts", "pipes"];

/* Every layer floor mode filters. THE ORDER IS LOAD-BEARING: `foundations` first, because the
 * storage rule needs this deck's own 8 m cells, and `power` needs them too -- a pole is placed
 * exactly the way a storage box is. */
var FILTERED = [
  "foundations",
  "machines",
  "extractors",
  "generators",
  "belts",
  "pipes",
  "storage",
  "power",
];

/* Breathing room around a platform, metres, and how close the flight may get -- the same two
 * numbers a factory-label flight already uses, for the same reason: a deck that exactly fills
 * the screen loses the surroundings that say where it is. */
var FLOOR_PAD_M = 40;
var FLOOR_MAX_ZOOM = 1;

/* How deep the concrete under a deck is, metres.
 *
 * The one number in this file that is not the server's, and it is used for one layer --
 * storage, which the decomposition does not decompose. Two metres is half the thickest
 * foundation the game builds (`8x4`), so it is the depth of the deck rather than a tolerance:
 * a thing lower than this is under the floor rather than on it. Everything else here is
 * joined by a name the server sent and needs no such number. */
var DECK_DEPTH_M = 2;

/* The connector glyph's box, in screen pixels, which is both how big the arrow is and how
 * big a target it is. Small, because a floor plan can carry twenty of them; not smaller,
 * because it has to stay hittable -- and it is in pixels rather than metres precisely so it
 * does not shrink to nothing when the reader zooms out to see the whole deck. */
var GLYPH_PX = 14;

/* ------------------------------------------------------------------ the view */

/** One floor mode session: what was asked for, what came back, and what it changed. */
interface FloorView {
  /** The query string `/api/floors` was asked with, kept so a save write can re-ask it. */
  query: string;
  /** The selected platform, or null when the answer was a sentence instead of a list. */
  platform: FloorPlatform | null;
  body: FloorsResponse;
  /** What the picker is called: the factory's own name where the player gave one. */
  title: string;
  /** What could not be shown, as the API said it. Empty when there is nothing to say. */
  message: string;
  /** Layers this mode turned on, minus any the reader has since taken ownership of. */
  turned: string[];
  /** Whether the map has been taken to the platform yet.
   *
   * Not a formality: on a pasted `#floor=` link the decomposition can land BEFORE the
   * concrete does, and the flight is computed from the drawn deck. So the flight is owed
   * until it can be made, and is made by whichever pass first has the pieces to make it. */
  flown: boolean;
}

var view: FloorView | null = null;

/* Programmatic layer ticks are not the reader's decisions, and Leaflet gives no way to tell
 * them apart afterwards -- `overlayadd` fires from the layer's own `add` event, so
 * `map.addLayer` and a click arrive identically. Same flag, same reason, as `applying` in
 * regions.ts. */
var applying = false;

/** Whether the page is currently slicing a factory. */
export function inFloorMode(): boolean {
  return view !== null;
}

/* ------------------------------------------------------------- reading the answer */

function bandOf(platform: FloorPlatform | null, key: string): FloorBand | null {
  if (!platform || key === GROUND) return null;
  var found: FloorBand | null = null;
  platform.bands.forEach(function (band) {
    if (String(band.ordinal) === key) found = band;
  });
  return found;
}

/* A band's ceiling: the next deck up, or nothing at all above the top floor.
 *
 * Open at the top on purpose. A roof, a wall and a lookout on the highest deck are all ON the
 * highest deck -- there is no floor above them to belong to instead -- so the top band's slab
 * runs to infinity and every piece over the platform lands on exactly one floor. */
function ceilingOf(platform: FloorPlatform, band: FloorBand): number {
  var top = Number.POSITIVE_INFINITY;
  var here = band.top_m === null ? 0 : band.top_m;
  platform.bands.forEach(function (other) {
    var level = other.top_m;
    if (level !== null && level > here && level < top) top = level;
  });
  return top;
}

/* The one geometric rule in this file: does a thing at `z` stand on this band?
 *
 * From this deck's top surface up to the next deck's, each less the concrete it is poured
 * into. Used for storage, which `/api/floors` says nothing about, and for nothing else. */
function standsOn(platform: FloorPlatform, band: FloorBand, z_m: number): boolean {
  var top = band.top_m;
  if (top === null) return false;
  return z_m >= top - DECK_DEPTH_M && z_m < ceilingOf(platform, band) - DECK_DEPTH_M;
}

/** Every instance id this floor lists, as a lookup. */
function idsOn(band: FloorBand | null, body: FloorsResponse): Record<string, boolean> {
  var out: Record<string, boolean> = {};
  if (band) {
    band.machines.forEach(function (id) {
      out[id] = true;
    });
    band.attachments.forEach(function (id) {
      out[id] = true;
    });
    return out;
  }
  // The ground: the three honest ways of not being on a floor, exactly as the API groups
  // them. `exempt` is a miner on a node or a pump on water, `terrain` is measured against
  // the heightfield, and `off-deck` is what `terrain` degrades to where there is no field.
  ["exempt", "terrain", "off-deck"].forEach(function (group) {
    (body.placements[group] || []).forEach(function (row) {
      out[row.instance_leaf] = true;
    });
  });
  return out;
}

/** Which foundation rows this floor's deck is made of. The ground gets none: it is the
 *  ground, and drawing the storey above it underneath it would be an invention. */
function deckRows(band: FloorBand | null): Record<number, boolean> {
  var out: Record<number, boolean> = {};
  if (band) {
    band.deck_rows.forEach(function (row) {
      out[row] = true;
    });
  }
  return out;
}

/** Is this end of a run this floor? */
function isHere(end: FloorDeck | null, platform: number, band: FloorBand): boolean {
  return !!end && end.platform === platform && end.ordinal === band.ordinal;
}

/* Which runs belong on this floor, keyed the way the drawn pieces are marked.
 *
 * Same-deck runs on this band, plus every CONNECTOR with an end on it -- a connector is drawn
 * on every floor it touches, which is the whole point of it. The ground takes the runs that
 * never reach a deck (`terrain`) and the ones with only one end on one (`mixed`).
 *
 * The runs themselves come back rather than a set of keys, because the glyph on a connector
 * has to say where the other end goes and only the run knows. */
function runsOn(
  body: FloorsResponse,
  band: FloorBand | null,
  platform: number
): Record<string, FloorRun> {
  var out: Record<string, FloorRun> = {};
  function put(run: FloorRun): void {
    out[run.kind + ":" + run.key] = run;
  }
  if (!band) {
    ["terrain", "mixed"].forEach(function (membership) {
      (body.runs[membership] || []).forEach(put);
    });
    return out;
  }
  (body.runs["same-deck"] || []).forEach(function (run) {
    if (isHere(run.ends[0] || null, platform, band)) put(run);
  });
  (body.runs["connector"] || []).forEach(function (run) {
    if (run.ends.some(function (end) {
        return isHere(end || null, platform, band);
      })) {
      put(run);
    }
  });
  return out;
}

/* --------------------------------------------------------------- the filtering */

/** Everything a group holds, snapshotted once per redraw so that leaving can put it back. */
function snapshot(group: L.LayerGroup): L.Layer[] {
  if (!group._floorAll) {
    var all: L.Layer[] = [];
    group.eachLayer(function (piece) {
      all.push(piece);
    });
    group._floorAll = all;
  }
  return group._floorAll;
}

/* Ghosting: a machine on a lower deck that comes up through this floor, restyled rather than
 * redrawn. A Refinery is 15 m tall on a 12 m storey module, so it is in the way of anything
 * built above it -- a fact about the floor above that only the floor below records. A restyle
 * means no second shape that could end up somewhere the machine is not.
 *
 * The original options are parked on the path, because unghosting has to be exact: guessing the
 * drawing module's colours back would put this file in the business of knowing what a
 * Constructor is painted. */
function ghost(piece: L.Path, rows: Row[]): void {
  if (!piece._floorStyle) {
    var was = piece.options;
    piece._floorStyle = {
      color: was.color,
      weight: was.weight,
      opacity: was.opacity,
      fillOpacity: was.fillOpacity,
      dashArray: was.dashArray,
    };
    // The CONTENT, not the popup object: `bindPopup` with a string REUSES the popup a layer
    // already has, so keeping the popup would keep a reference to the very thing the next line
    // overwrites, and the machine would carry the ghost's card for ever.
    var card = piece.getPopup();
    piece._floorCard = card ? (card.getContent() as string | HTMLElement) : null;
  }
  piece.setStyle({ weight: 1, opacity: 0.6, fillOpacity: 0.06, dashArray: "3,4" });
  piece.setPopupContent(popup(rows));
}

function unghost(piece: L.Path): void {
  var was = piece._floorStyle;
  if (!was) return;
  var card = piece._floorCard;
  delete piece._floorStyle;
  delete piece._floorCard;
  // `dashArray: undefined` does not clear a dash: Leaflet's setStyle copies the options it
  // is given and an absent key changes nothing. The one option that has to be UN-set is
  // therefore spelled empty rather than left out.
  piece.setStyle({ dashArray: "" });
  piece.setStyle(was);
  if (card !== null && card !== undefined) piece.setPopupContent(card);
}

/** Which band of this platform an id stands on, if any. */
function bandOfId(platform: FloorPlatform, id: string): FloorBand | null {
  var found: FloorBand | null = null;
  platform.bands.forEach(function (band) {
    if (band.machines.indexOf(id) >= 0) found = band;
  });
  return found;
}

/* One machine, seen from a floor it is not on: does it come up through this one?
 *
 * `h_m` is the clearance box's third side, and it is null for the buildings the docs dump
 * carries no clearance for. A null height is not a short machine -- it is a machine whose
 * height was never recorded -- so it draws nothing rather than a ghost that would be
 * indistinguishable from a measured one. */
function piercesFloor(platform: FloorPlatform, band: FloorBand, mark: FloorMark): boolean {
  if (mark.z_m === undefined || mark.h_m === undefined || mark.h_m === null) return false;
  if (mark.id === undefined) return false;
  var deck = band.top_m;
  if (deck === null) return false;
  var stands = bandOfId(platform, mark.id);
  if (!stands || stands.top_m === null || stands.top_m >= deck) return false;
  return mark.z_m + mark.h_m > deck;
}

function ghostRows(platform: FloorPlatform, band: FloorBand, mark: FloorMark): Row[] {
  var stands = mark.id === undefined ? null : bandOfId(platform, mark.id);
  var through = (mark.z_m || 0) + (mark.h_m || 0) - (band.top_m || 0);
  return [
    ["ghost", "not on this floor: it comes up through it"],
    ["stands on", stands ? floorName(stands) + ", " + metres(stands.top_m) : null],
    ["height", mark.h_m + " m above its own deck"],
    ["through this floor", Math.round(through * 10) / 10 + " m"],
    ["id", code(mark.id)],
  ];
}

/* The up/down glyph a connector gets on every floor it touches. A lift's ring says nothing
 * about which way it goes; the glyph does, and the popup names the far end as a FLOOR rather
 * than a height, because "it goes to floor 4" is the sentence a reader is after.
 *
 * The icon needs a REAL size, unlike the factory anchors, whose 0x0 works because their tooltip
 * is the clickable body. Here the arrow IS the body: sized 0x0 it looks identical and cannot be
 * clicked at all, so the popup would be unreachable. */
function otherEnd(run: FloorRun, platform: number, band: FloorBand): FloorDeck | null {
  var found: FloorDeck | null = null;
  run.ends.forEach(function (end) {
    if (end && !isHere(end, platform, band)) found = end;
  });
  return found;
}

/* An arrow at a point, pointing the way it goes. Shared by the two things that can leave a
 * floor, a run and a wire, so a reader learns one glyph: the difference between them is what
 * the popup says. */
function glyphMarker(at: Point3M, up: boolean): L.Marker {
  return L.marker([-at[1], at[0]], {
    icon: L.divIcon({
      className: "floor-connector" + (up ? " floor-connector-up" : " floor-connector-down"),
      html: up ? "&#9650;" : "&#9660;",
      iconSize: [GLYPH_PX, GLYPH_PX],
      iconAnchor: [GLYPH_PX / 2, GLYPH_PX / 2],
    }),
    title: up ? "leads up a floor" : "leads down a floor",
  });
}

function connectorGlyph(run: FloorRun, platform: number, band: FloorBand, at: Point3M): L.Marker {
  var away = otherEnd(run, platform, band);
  var up = !!away && (away.top_m || 0) > (band.top_m || 0);
  var marker = glyphMarker(at, up);
  marker.bindPopup(
    popup([
      [
        run.lift ? "conveyor lift" : run.kind === "pipe" ? "pipe riser" : "belt riser",
        up ? "goes up from this floor" : "goes down from this floor",
      ],
      ["to", away ? floorName(away) + ", " + metres(away.top_m) : "no deck: the ground"],
      ["rise", run.rise_m === null ? null : run.rise_m + " m"],
      // Which of the two claims this is. A lift is a class the docs dump names; a riser is a
      // run that climbs six metres or more -- and stage 0 measured that a quarter of lifts
      // are belt-height jogs on one deck, which is why the two are not the same word.
      ["kind", run.lift ? "a conveyor lift, by class" : "a run that climbs a storey or more"],
      [run.kind === "pipe" ? "pipe row" : "chain", "#" + run.key],
    ])
  );
  return marker;
}

/* ---------------------------------------------------------------- the power grid */

/* Which band of this platform a point stands on, BY HEIGHT ALONE -- the weaker of the two tests
 * the storage rule makes, because the deck cells are only ever collected for the band being
 * looked at and this has to be able to ask about the others. Enough for what it is used for:
 * naming the floor at the far end of a wire, and deciding whether a wire is over any deck at
 * all rather than which one. */
function bandAtHeight(platform: FloorPlatform, z_m: number): FloorBand | null {
  var found: FloorBand | null = null;
  platform.bands.forEach(function (band) {
    if (standsOn(platform, band, z_m)) found = band;
  });
  return found;
}

/* Is this point on the floor being looked at? Both halves, exactly as storage is judged: over
 * this deck's own 8 m cells AND between this deck and the next.
 *
 * `cells` is passed in rather than read from a closure because it is filled by the foundations
 * pass earlier in the same loop, and a function that silently depended on that order would be
 * a function nobody could move. */
function onThisFloor(
  platform: FloorPlatform,
  band: FloorBand,
  cells: Record<string, boolean>,
  cellKey: (x_m: number, y_m: number) => string,
  at: Point3M
): boolean {
  return cells[cellKey(at[0], at[1])] === true && standsOn(platform, band, at[2]);
}

/* The glyph a wire gets when it leaves the floor it is on: the same mark the belt and pipe
 * risers get, with a different sentence, because a cable that goes up through the ceiling is
 * neither a named lift class nor a run that climbs a storey.
 *
 * The floor CLAIMS -- which band the far end lands on, and whether the cable goes up or down --
 * are made at the two ANCHORS, the same points the filter judged the wire by, so the arrow can
 * never name a different storey than the filter used. The drawn coordinates and the rise stay
 * the wire's own ends: the rise is real cable, connector to connector.
 *
 * `awayAnchor` may be on no band at all, which is a real answer -- the wire running down to
 * something on the ground -- so it is spelled out rather than left blank. */
function wireGlyph(
  platform: FloorPlatform,
  here: Point3M,
  away: Point3M,
  hereAnchor: Point3M,
  awayAnchor: Point3M
): L.Marker {
  var up = awayAnchor[2] > hereAnchor[2];
  var lands = bandAtHeight(platform, awayAnchor[2]);
  var marker = glyphMarker(here, up);
  marker.bindPopup(
    popup([
      ["power line", up ? "goes up from this floor" : "goes down from this floor"],
      ["to", lands ? floorName(lands) + ", " + metres(lands.top_m) : "no deck: the ground"],
      ["rise", Math.round(Math.abs(away[2] - here[2]) * 10) / 10 + " m"],
      // Where the other end actually is, because a wire can leave a floor sideways as well as
      // vertically and "up" alone would not say which cable this is.
      ["other end", away[0] + ", " + away[1] + " m"],
      // Said here as well as on the wire itself, because the reader is looking at an arrow on a
      // floor plan, where a straight chord through three storeys is what needs the caveat.
      ["shape", "a straight chord; a wire sags and the save records no sag"],
    ])
  );
  return marker;
}

/** The end of one drawn piece nearest this floor's deck, in game metres. */
function endNearest(mark: FloorMark, deck: number): Point3M | null {
  if (!mark.ends) return null;
  var head = mark.ends[0];
  var tail = mark.ends[1];
  return Math.abs(head[2] - deck) <= Math.abs(tail[2] - deck) ? head : tail;
}

/* One pass over every filtered layer. Idempotent, and re-run after every redraw.
 *
 * The order INSIDE the loop is not arbitrary: `FILTERED` starts with the concrete because the
 * storage rule needs this deck's own 8 m cells, and the only place those exist on this side
 * of the wire is the foundation pieces this pass has just decided to keep. */
function applyFilter(): void {
  if (!view || !view.platform || !state.floor) return;
  var platform = view.platform;
  var band = bandOf(platform, state.floor.band);
  var ids = idsOn(band, view.body);
  var rows = deckRows(band);
  var runs = runsOn(view.body, band, platform.index);
  var tile = view.body.rules.tile_m || 8;
  var cells: Record<string, boolean> = {};
  var glyphs: Record<string, boolean> = {};
  // How far out this factory reaches, in plan. Measured once because it walks the whole
  // foundations snapshot, and only the power layer's ground row asks for it -- see there.
  var reach = band ? null : platformBounds(platform);

  function cellKey(x_m: number, y_m: number): string {
    return Math.floor(x_m / tile) + "," + Math.floor(y_m / tile);
  }

  applying = true;
  try {
    FILTERED.forEach(function (name) {
      var group = state.layers[name];
      if (!group) return;
      var all = snapshot(group);
      var keep: L.Layer[] = [];
      var ghosts: L.Path[] = [];
      var extra: L.Layer[] = [];
      all.forEach(function (piece) {
        var mark = piece._floor;
        if (!mark) return; // a piece nothing marked is a piece nothing can place
        if (mark.row !== undefined) {
          if (!rows[mark.row]) return;
          if (mark.x_m !== undefined && mark.y_m !== undefined) {
            cells[cellKey(mark.x_m, mark.y_m)] = true;
          }
          keep.push(piece);
          return;
        }
        if (mark.run) {
          var key = mark.run.kind + ":" + mark.run.key;
          var run = runs[key];
          if (!run) return;
          keep.push(piece);
          // One glyph per RUN and not per piece: a chain is several drawn pieces, and three
          // arrows stacked on one lift would be three claims about one thing.
          if (!band || band.top_m === null || glyphs[key]) return;
          if (!run.riser && !run.lift) return;
          var at = endNearest(mark, band.top_m);
          if (!at) return;
          glyphs[key] = true;
          extra.push(connectorGlyph(run, platform.index, band, at));
          return;
        }
        /* THE POWER GRID, placed by this file because nothing else can place it. A pole is a
         * point and is judged exactly as a storage box is; a wire has two ends and therefore
         * three answers:
         *
         *   both ends on this floor  -- a cable running along this deck. Shown.
         *   one end on this floor    -- it leaves. Shown, plus the arrow saying where to.
         *   neither                  -- not this storey's cable. Hidden.
         *
         * On the GROUND pseudo-floor the question flips, as it does for every layer: a wire is
         * kept when NEITHER end is at any band's height. That is the weaker test on purpose
         * (see bandAtHeight), because "not on a deck" is a claim about height alone.
         */
        if (mark.power) {
          var span = mark.ends;
          /* Where the two ends COUNT AS STANDING, against where they are drawn: the base of
           * the pole an end terminates at where the server names one -- the exact point the
           * pole's own mark is filtered by, so a wire and its pole cannot land on different
           * storeys -- and the drawn endpoint everywhere else. */
          var anchor = mark.anchors || span;
          if (!band) {
            /* THE GROUND, and the one place this layer has to invent a SCOPE. Every other
             * layer's ground row is the API's own answer, computed about THIS FACTORY; there is
             * no such grouping for power, so an unscoped height test would put every cable in
             * the world under this one factory's ground row.
             *
             * The scope is the platform's own extent, pad and all, borrowed from the flight so
             * there is one box and not two that can disagree -- a pole feeding a factory stands
             * just off its concrete more often than on it, so the 40 m is about right.
             *
             * Within it, the test is the runs' own two memberships said in heights: a piece
             * belongs to the ground when it TOUCHES the ground. */
            if (!reach) return;
            if (span && anchor) {
              if (!reach.contains([-anchor[0][1], anchor[0][0]]) &&
                  !reach.contains([-anchor[1][1], anchor[1][0]])) {
                return;
              }
              if (bandAtHeight(platform, anchor[0][2]) && bandAtHeight(platform, anchor[1][2])) {
                return;
              }
              keep.push(piece);
              return;
            }
            if (mark.x_m === undefined || mark.y_m === undefined || mark.z_m === undefined) return;
            if (!reach.contains([-mark.y_m, mark.x_m])) return;
            if (!bandAtHeight(platform, mark.z_m)) keep.push(piece);
            return;
          }
          if (!span) {
            // A pole: the storage rule, unchanged, because a pole IS a placement standing on
            // a deck and the only reason it is not joined by an instance id is that no band
            // lists one.
            if (
              mark.x_m !== undefined &&
              mark.y_m !== undefined &&
              mark.z_m !== undefined &&
              cells[cellKey(mark.x_m, mark.y_m)] &&
              standsOn(platform, band, mark.z_m)
            ) {
              keep.push(piece);
            }
            return;
          }
          if (!anchor) return;
          var headHere = onThisFloor(platform, band, cells, cellKey, anchor[0]);
          var tailHere = onThisFloor(platform, band, cells, cellKey, anchor[1]);
          if (!headHere && !tailHere) return;
          keep.push(piece);
          /* And the arrow, on a NARROWER test than the one that kept the wire: being kept only
           * means one end is on this deck, while a cable running off the edge of the platform
           * at the same height leaves the FACTORY rather than the STOREY. So it is asked of the
           * heights -- the two ends on different bands, or one on no band at all.
           *
           * One arrow per wire, which is what `casing` is marked for: the piece underneath has
           * the same two ends and would otherwise put a second arrow on the same pixel. */
          if (mark.power !== "wire") return;
          // The claims from the anchors -- the same points the keep above was decided at --
          // and the drawn geometry from the wire's own ends, so the arrow sits on the cable.
          var from = headHere ? span[0] : span[1];
          var to = headHere ? span[1] : span[0];
          var fromAnchor = headHere ? anchor[0] : anchor[1];
          var toAnchor = headHere ? anchor[1] : anchor[0];
          var landsOn = bandAtHeight(platform, toAnchor[2]);
          if (landsOn && landsOn.ordinal === band.ordinal) return;
          extra.push(wireGlyph(platform, from, to, fromAnchor, toAnchor));
          return;
        }
        if (mark.id !== undefined) {
          if (ids[mark.id]) {
            keep.push(piece);
            return;
          }
          if (band && piece instanceof L.Path && piercesFloor(platform, band, mark)) {
            ghost(piece, ghostRows(platform, band, mark));
            ghosts.push(piece);
            keep.push(piece);
          }
          return;
        }
        // Storage: the one layer joined by where it stands, because the decomposition never
        // claimed it. Both halves have to hold -- over this deck's own concrete, and between
        // this deck and the next.
        if (mark.z_m !== undefined && mark.x_m !== undefined && mark.y_m !== undefined) {
          if (band && cells[cellKey(mark.x_m, mark.y_m)] && standsOn(platform, band, mark.z_m)) {
            keep.push(piece);
          }
        }
      });
      all.forEach(function (piece) {
        if (piece instanceof L.Path && ghosts.indexOf(piece) < 0) unghost(piece);
      });
      group.clearLayers();
      // A `for` rather than a `forEach`, and it is not a style choice: `group` is narrowed
      // by the guard above and TypeScript drops that narrowing inside a closure, so the
      // loop that re-adds has to stay in the same scope as the check that it exists.
      var drawn = keep.concat(extra);
      for (var i = 0; i < drawn.length; i++) group.addLayer(drawn[i]!);
    });
    /* AND THE STACKING BACK, because rebuilding a group rebuilds its draw order. Clearing a
     * route layer and re-adding what survived puts every piece back at the top in the order
     * this loop happened to keep them, which is not the order `sinkRoutes` established -- and
     * a power casing left on top is two pixels wider than its core, so it hides it completely.
     * Cheap and idempotent, and it re-raises the node dots the same rebuild buried. */
    sinkRoutes();
  } finally {
    applying = false;
  }
}

/** Put every layer back the way its drawing module left it. */
function clearFilter(): void {
  applying = true;
  try {
    FILTERED.forEach(function (name) {
      var group = state.layers[name];
      if (!group || !group._floorAll) return;
      var all = group._floorAll;
      delete group._floorAll;
      group.clearLayers();
      for (var i = 0; i < all.length; i++) {
        var piece = all[i]!;
        if (piece instanceof L.Path) unghost(piece);
        group.addLayer(piece);
      }
    });
    // Putting the pieces back is not putting the PICTURE back: see the note in applyFilter.
    // `_floorAll` is captured in add order, so a wire and its casing come back as the pair
    // they were -- and this is what puts the casing under the core again rather than over it.
    sinkRoutes();
  } finally {
    applying = false;
  }
}

/** Re-apply the filter to whatever was just redrawn -- called by load.ts after every draw,
 *  because a refetch replaces a layer's CONTENTS and the filter is a fact about contents.
 *  It is also where a flight the view still owes finally becomes possible; see `flown`. */
export function refilterFloors(): void {
  if (!view) return;
  applyFilter();
  flyToPlatform();
}

/* A save write changes what is built, so it changes the decomposition, and the ids a band
 * lists are what this whole filter runs on. Without this a machine placed since the view was
 * opened would be on no floor at all -- drawn by `/api/machines`, listed by no band, and
 * therefore silently missing rather than visibly new.
 *
 * Same query, same band, no flight: this is a refresh of the answer and not a new question. */
export function refreshFloors(): void {
  if (!view) return;
  var was = view;
  get<FloorsResponse & { error?: string }>(url(was.query))
    .then(function (body) {
      if (view !== was) return; // left, or moved on, while this was in the air
      view.body = body;
      view.platform = (body.platforms || [])[0] || null;
      applyFilter();
      showPicker();
    })
    .catch(function () {
      /* the view on screen stays the view on screen; the next save write tries again */
    });
}

/* -------------------------------------------------------------------- the picker */

function metres(value: number | null): string {
  if (value === null) return "height not recorded";
  return (value >= 0 ? "+" : "") + value + " m";
}

function floorName(band: { ordinal: number }): string {
  return "Floor " + band.ordinal;
}

/* The ground row's own sentence, and it depends on whether the ground was MEASURED.
 *
 * `terrain_measured` says whether a heightfield was there to compare against. Without one the
 * API's honest answer is `off-deck` rather than `terrain`, and a picker that called both "on
 * the ground" would be quietly upgrading the weaker claim to the stronger one. */
function groundDetail(body: FloorsResponse): string {
  var placed = groundPlacements(body);
  var runs = groundRuns(body);
  var how = body.terrain_measured
    ? "on terrain, measured"
    : "off-deck: no heightfield here to measure against";
  return how + " · " + placed + " placed · " + runs + " runs";
}

function groundPlacements(body: FloorsResponse): number {
  var counts = body.counts.placements;
  return (counts["exempt"] || 0) + (counts["terrain"] || 0) + (counts["off-deck"] || 0);
}

function groundRuns(body: FloorsResponse): number {
  var counts = body.counts.membership;
  return (counts["terrain"] || 0) + (counts["mixed"] || 0);
}

function hasGround(body: FloorsResponse): boolean {
  return groundPlacements(body) + groundRuns(body) > 0;
}

function choices(body: FloorsResponse, platform: FloorPlatform | null): FloorChoice[] {
  var rows: FloorChoice[] = [];
  if (!platform) return rows;
  if (hasGround(body)) {
    rows.push({
      key: GROUND,
      label: "Ground",
      detail: groundDetail(body),
      // Not minor: it is a different KIND of answer rather than a small one, and dimming it
      // would say "mezzanine" about the one row that is not a band at all.
      minor: false,
      note:
        "everything over this factory's footprint that the decomposition put on no band: a " +
        "miner stands on a resource node and a pump on water, and plumbing hugs the ground",
    });
  }
  platform.bands.forEach(function (band) {
    rows.push({
      key: String(band.ordinal),
      label: floorName(band),
      detail: metres(band.top_m) + " · " + band.cells + " cells · " + band.machine_count + " machines",
      minor: band.minor,
      note: band.minor
        ? "a mezzanine: " +
          pct(band.share) +
          " of this platform's largest deck, so a ledge rather than a storey"
        : band.area_m2 +
          " m² of deck, " +
          band.pieces +
          " foundation pieces, " +
          band.attachment_count +
          " belt junctions",
    });
  });
  return rows;
}

/* Which floor to open on: the busiest real storey, so the first thing shown is a factory.
 *
 * Not floor zero. On the reference world the tallest platform's lowest deck holds no machines
 * at all -- it is the concrete the tower stands on -- and opening there would answer "show me
 * the floors of this factory" with an empty slab. */
function busiestBand(platform: FloorPlatform): FloorBand | null {
  var best: FloorBand | null = null;
  platform.bands.forEach(function (band) {
    if (best === null || band.machine_count > best.machine_count) best = band;
  });
  return best;
}

function busiest(platform: FloorPlatform): string {
  var band = busiestBand(platform);
  return band ? String(band.ordinal) : GROUND;
}

function showPicker(): void {
  if (!view) return;
  showFloors(
    "floors · " + view.title,
    choices(view.body, view.platform),
    state.floor ? state.floor.band : "",
    view.message
  );
}

/* --------------------------------------------------------------- entering, leaving */

/* A platform's own extent, from the decks this page has actually drawn.
 *
 * From the pieces rather than from `centre_m` and `extent_m`, and that is a correction rather
 * than a preference: the centre the API sends is the MEAN of a platform's pieces, which on
 * the reference world's tower sits 30 m from the middle of its own bounding box. A mean and a
 * span do not make a box, and flying to one made of them clips the deck at one edge. */
function platformBounds(platform: FloorPlatform): L.LatLngBounds | null {
  var group = state.layers["foundations"];
  if (!group) return null;
  var rows: Record<number, boolean> = {};
  platform.bands.forEach(function (band) {
    band.deck_rows.forEach(function (row) {
      rows[row] = true;
    });
  });
  var lat: number[] = [];
  var lng: number[] = [];
  snapshot(group).forEach(function (piece) {
    var mark = piece._floor;
    if (!mark || mark.row === undefined || !rows[mark.row]) return;
    if (mark.x_m === undefined || mark.y_m === undefined) return;
    lat.push(-mark.y_m);
    lng.push(mark.x_m);
  });
  if (!lat.length) return null;
  // Padded in METRES, which under this CRS is what a latlng unit is -- `pad` takes a fraction
  // of the box's own size, so a 600 m platform and a 40 m one would get different margins.
  return L.latLngBounds(
    [Math.min.apply(null, lat) - FLOOR_PAD_M, Math.min.apply(null, lng) - FLOOR_PAD_M],
    [Math.max.apply(null, lat) + FLOOR_PAD_M, Math.max.apply(null, lng) + FLOOR_PAD_M]
  );
}

/** Take the map to the platform, once, as soon as there is a drawn deck to measure it from. */
function flyToPlatform(): void {
  if (!view || view.flown || !view.platform) return;
  var bounds = platformBounds(view.platform);
  if (!bounds) return;
  view.flown = true;
  flyPadded(bounds, FLOOR_MAX_ZOOM);
}

/* Turn on what a floor is made of, once, and remember what this mode turned on. Often nothing,
 * which is why it returns a list rather than a count: the card is normally reached by clicking
 * a factory LABEL, and that click has already revealed these three layers, so they are the
 * reader's from then on. The list is non-empty only on a pasted `#floor=` link. */
function revealFor(): string[] {
  var turned: string[] = [];
  applying = true;
  try {
    batch(function () {
      FLOOR_LAYERS.forEach(function (name) {
        var group = state.layers[name];
        if (!group || map.hasLayer(group)) return;
        group.addTo(map);
        turned.push(name);
      });
    });
  } finally {
    applying = false;
  }
  return turned;
}

/* A tick the reader makes during floor mode is theirs, and leaving must not undo it: a layer
 * this mode turned on stops being this mode's the moment the reader touches its box, and
 * leaving puts back only what is still on loan. */
export function noteFloorChoice(event: L.LeafletEvent): void {
  if (!view || applying) return;
  var touched = (event as L.LayersControlEvent).layer;
  view.turned = view.turned.filter(function (name) {
    return state.layers[name] !== touched;
  });
  // A layer ticked ON mid-mode arrives holding every piece in it, so it owes the filter a pass.
  refilterFloors();
}

/** `/api/floors` with a query, spelled once so the template type stays checked. */
function url(query: string): `/api/floors?${string}` {
  return ("/api/floors?" + query) as `/api/floors?${string}`;
}

/* Enter floor mode for one factory or one platform.
 *
 * `band` is what the fragment asked for, where it asked for one; without it the busiest storey
 * is opened, because "show me the floors of this factory" should land on a factory.
 *
 * A 4xx IS NOT A FAILURE HERE. `/api/floors` answers a factory standing on no platform with a
 * sentence saying so, and both that and the 200-with-a-note are ANSWERS shown in the picker --
 * the mode is entered either way, so a reader gets the sentence and a way out rather than a
 * toast that disappears over a map that did not change. */
export function enterFloors(query: string, title: string, band?: string): void {
  var ticket = latest("floors");
  get<FloorsResponse & { error?: string }>(url(query))
    .then(function (body) {
      if (ticket.fresh()) open(query, body, title, band);
    })
    .catch(function (error) {
      if (!ticket.fresh()) return;
      // `get` throws with the server's own `error` string, which for a selection that matched
      // nothing is "no platform matches factory 'x'" -- the sentence to show, not to hide.
      open(query, { platforms: [], note: friendly(error) } as unknown as FloorsResponse, title, band);
    });
}

function open(query: string, body: FloorsResponse, title: string, band?: string): void {
  var platform = (body.platforms || [])[0] || null;
  view = {
    query: query,
    platform: platform,
    body: body,
    title: platform && platform.label ? platform.label : title,
    message: platform ? "" : body.note || "no floors here",
    turned: [],
    flown: false,
  };
  if (!platform) {
    // Nothing to slice, and a reason for it. `state.floor` stays null: there is no storey
    // being looked at, so the fragment must not claim one.
    state.floor = null;
    showPicker();
    return;
  }
  view.turned = revealFor();
  var wanted = band || busiest(platform);
  if (wanted === GROUND ? !hasGround(body) : !bandOf(platform, wanted)) wanted = busiest(platform);
  state.floor = { platform: platform.index, band: wanted };
  showPicker();
  applyFilter();
  flyToPlatform();
  writeHash();
}

/* Leave, putting back exactly what this mode took -- and nothing the reader has since claimed.
 *
 * The viewport is deliberately left where it is. The reader may have panned somewhere on
 * purpose, and flying back would be this mode having the last word about where to look; the
 * house button under the zoom control is the page's existing way of asking for the world. */
export function leaveFloors(): void {
  if (!view) return;
  var turned = view.turned;
  clearFilter();
  view = null;
  state.floor = null;
  hideFloors();
  applying = true;
  try {
    batch(function () {
      turned.forEach(function (name) {
        var group = state.layers[name];
        if (group && map.hasLayer(group)) map.removeLayer(group);
      });
    });
  } finally {
    applying = false;
  }
  writeHash();
}

/** Switch storey. Neither the layers nor the map move: one floor of a factory is the same
 *  place as the next one, and re-flying between them would be motion for its own sake. */
export function pickBand(key: string): void {
  if (!view || !view.platform || !state.floor) return;
  state.floor = { platform: state.floor.platform, band: key };
  applyFilter();
  showPicker();
  writeHash();
}

/* ---------------------------------------------------------------- the fragment */

/** `floor=<platform>/<band>`, where band is an ordinal or `ground`. Anything else is ignored
 *  rather than resolved to a guess -- the same refusal `askedMode` makes about a mode this
 *  server does not serve. */
export function parseFloorFragment(raw: string | undefined): { platform: number; band: string } | null {
  if (!raw) return null;
  var parts = raw.split("/");
  if (parts.length !== 2) return null;
  var platform = +parts[0]!;
  var band = parts[1]!;
  if (!isFinite(platform) || platform < 0 || Math.floor(platform) !== platform) return null;
  if (band !== GROUND && !/^\d+$/.test(band)) return null;
  return { platform: platform, band: band };
}

/* One fragment's floor half, applied. Returns whether anything moved, because the caller owes a
 * write if nothing else did.
 *
 * A platform INDEX rather than a factory name in the address: the index is what `/api/floors`
 * hands out and promises to be stable over one save, and a factory name would make the link
 * depend on the player not renaming anything. */
export function applyFloorFragment(asked: string | undefined): boolean {
  var want = parseFloorFragment(asked);
  var have = state.floor;
  if (!want) {
    if (!view) return false;
    leaveFloors();
    return true;
  }
  if (have && have.platform === want.platform && have.band === want.band) return false;
  if (have && have.platform === want.platform) {
    pickBand(want.band);
    return true;
  }
  enterFloors("platform=" + want.platform, "platform " + want.platform, want.band);
  return true;
}

/* ------------------------------------------------------------------- the card */

/* The action on a factory card, and the page's required way in.
 *
 * An HTMLElement rather than a string of markup, because the card now has to carry a LISTENER
 * and Leaflet keeps the element it is handed -- so the handler is bound once, at build time,
 * instead of being re-bound and stacked on every `popupopen`. The rows themselves still go
 * through `popup()`, which escapes everything; nothing here puts data in the DOM by hand. */
export function cardWithFloors(rows: Row[], factory: string): HTMLElement {
  var card = document.createElement("div");
  card.innerHTML = popup(rows);
  var action = document.createElement("button");
  action.type = "button";
  action.className = "card-action";
  action.textContent = "floors";
  action.title =
    "show this factory one storey at a time";
  L.DomEvent.on(action, "click", function (event) {
    L.DomEvent.stop(event);
    map.closePopup();
    enterFloors("factory=" + encodeURIComponent("label:" + factory), factory);
  });
  card.appendChild(action);
  return card;
}

/* ------------------------------------------------------------------- the wiring */

/** ESC leaves. Registered in main.ts beside the map's own listeners, because it is the same
 *  kind of fact -- an event the page reacts to, wired where a reader can see the whole set.
 *
 *  A popup first: ESC over an open card closes the card, which is what a reader who just
 *  opened one expects, and only a second press leaves the mode. */
export function escapeLeavesFloorMode(event: KeyboardEvent): void {
  if (event.key !== "Escape" || !view) return;
  if (document.querySelector(".leaflet-popup")) {
    map.closePopup();
    return;
  }
  leaveFloors();
  note("left floor mode: the whole world again");
}

onFloorPick(pickBand);
onFloorExit(leaveFloors);
