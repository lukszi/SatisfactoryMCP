/* Where a colour is DECLARED, and where the claim that the colours were chosen against each
 * other is CHECKED: every colour is declared by the module that draws with it, and this file
 * holds the comparison and no value at all. The table and its reasons: docs/frontend_palette.md. */

/** One declared colour: who draws with it, what it is called there, and its value. */
interface Declared {
  owner: string;
  name: string;
  hex: string;
}

/* Dev-mode only, and the whole audit with it. The registry exists FOR the check and has no
 * other reader, so in a production build `declareColours` is a function that hands its
 * argument straight back and everything below this line folds out of the bundle -- which is
 * checkable, and checked: the built app.js contains none of the strings in this file. */
var declared: Declared[] = [];
var audited = false;

/* Record a feature's colours and hand them straight back, so that the declaration IS the
 * assignment and there is no second way for a colour to reach the page:
 *
 *   var RESOURCE_COLOUR: Record<string, string> = declareColours("markers", { … });
 *   var STORAGE_COLOUR = declareColours("placements", { storage: "#…" }).storage;
 *
 * `owner` is the drawing MODULE, not the layer, because that is the line the check needs.
 * Colours are compared across owners and never within one: a step inside a single family is
 * deliberate and small, and a rule that flagged those is a rule everybody switches off.
 * Sharing an owner is what says "these two are meant to look related". Not sharing one is what
 * says "these two must never be confused".
 *
 * There is no registration order to get right. Every module that declares is imported by
 * main.ts, an import graph is evaluated synchronously, and the microtask queued at the FIRST
 * declaration cannot run until that whole graph has finished -- so the audit always sees the
 * complete table, and a feature added tomorrow needs to do nothing but declare.
 */
export function declareColours<T extends Record<string, string>>(owner: string, colours: T): T {
  if (import.meta.env.DEV) {
    const table = colours as Record<string, string>;
    Object.keys(table).forEach(function (name) {
      const hex = table[name]!;
      // The audit reads these as six hex digits and nothing else. A short form or a named CSS
      // colour would drop silently out of every comparison, which is the one failure a colour
      // registry must not have.
      if (!/^#[0-9a-f]{6}$/i.test(hex)) {
        console.error(owner + "/" + name + ' is "' + hex + '", not a #rrggbb — it is not compared');
      }
      // Two colours under one name is one of them missing from the audit, and the pair it
      // would have caught is the pair the second one was added for.
      if (
        declared.some(function (other) {
          return other.owner === owner && other.name === name;
        })
      ) {
        console.error(owner + "/" + name + " is declared twice");
      }
      declared.push({ owner: owner, name: name, hex: hex });
    });
    if (!audited) {
      audited = true;
      queueMicrotask(audit);
    }
  }
  return colours;
}

/* sRGB to CIE Lab (D65, 2-degree observer), and CIE76 -- plain Euclidean distance in Lab.
 *
 * CIE76 rather than the later and better CIEDE2000, and that is a compatibility fact rather
 * than a preference: every dE written down for this page was computed this way and reproduces
 * to the decimal under this function and under no other. The threshold below is calibrated
 * against those numbers, so the formula and the threshold travel together.
 */
function lab(hex: string): [number, number, number] {
  const channel = function (at: number): number {
    const c = parseInt(hex.slice(at, at + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  const r = channel(1);
  const g = channel(3);
  const b = channel(5);
  // Linear sRGB to XYZ, each axis already divided by the D65 white point.
  const x = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047;
  const y = r * 0.2126729 + g * 0.7151522 + b * 0.072175;
  const z = (r * 0.0193339 + g * 0.119192 + b * 0.9503041) / 1.08883;
  const f = function (t: number): number {
    return t > 216 / 24389 ? Math.cbrt(t) : (841 / 108) * t + 4 / 29;
  };
  const fx = f(x);
  const fy = f(y);
  const fz = f(z);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}

/** The distance, at the one decimal every warrant on this page is written to. */
function deltaE(a: string, b: string): number {
  const one = lab(a);
  const two = lab(b);
  const d = Math.sqrt(
    (one[0] - two[0]) * (one[0] - two[0]) +
      (one[1] - two[1]) * (one[1] - two[1]) +
      (one[2] - two[2]) * (one[2] - two[2])
  );
  return Math.round(d * 10) / 10;
}

/* dE 15, the house step read off the page rather than a number from a standard: just under the
 * smallest step any deliberate ramp here takes, so two colours from different modules landing
 * as close as a ramp is exactly the thing that gets called out. */
var MIN_DELTA_E = 15;

/** `owner/name`, which is how a colour is named below and in every message the audit prints. */
function colourKey(owner: string, name: string): string {
  return owner + "/" + name;
}

/** Two names in a fixed order, so a pair reads the same however the loop reached it. */
function pairKey(a: string, b: string): string {
  return a < b ? a + " <-> " + b : b + " <-> " + a;
}

/* A pair under the threshold that a warrant answers, with the distance it was answered at.
 *
 * `de` is not decoration. An exception whose distance has MOVED is an exception whose reason
 * was written about two other colours, so the audit checks it and complains either way: a pair
 * that drifted, and a pair listed here that is no longer close at all. The list can only be a
 * ledger if it cannot quietly go stale.
 */
interface Exception {
  a: string;
  b: string;
  de: number;
  why: string;
}

/* The pairs a measurement answers: where NEITHER colour can move -- the game's ore tints on one
 * side, the biome grounds and the oldest network families on the other -- each carries the map
 * fact that keeps it from being a confusion, at the distance the audit re-derives every boot. */
var DISCHARGED: Exception[] = [
  {
    a: "markers/Desc_OreIron_C",
    b: "routes/chevrons",
    de: 10.1,
    why:
      "measured when the chevron cream was chosen, and discharged there: the mark is a thin V " +
      "drawn on a pipe at 0.7 opacity -- which composites to 14.3-17.0 from the iron dot now " +
      "that the pipes are oxide -- and the dot is a filled disc on open terrain. See " +
      "CHEVRON_COLOUR in routes.ts.",
  },
  /* Coal on the grounds it lies on: the game's own coal tint, near black because coal is,
   * over biome tints that are dark on purpose. This IS the same square metre -- the dot sits
   * ON the cell -- and it is the one collision hue cannot fix: the nineteen grounds cover the
   * whole dark-neutral range between them, so every near-black that clears one lands on
   * another, and a coal that is not near-black is not coal. What separates the marks is that
   * they are different KINDS of mark -- a 3-6 px disc, stroked at full opacity, against a
   * 256 m flat fill that REGION_BLEND fades to 0.45 wherever there is imagery. Eight entries
   * rather than one line so that a ground edit that closes any single gap still trips the
   * drift check. */
  {
    a: "markers/Desc_Coal_C",
    b: "regions/A",
    de: 6.3,
    why: "the coal warrant above -- a stroked disc on a flat faded fill, hue immovable on both sides (Abyss Cliffs).",
  },
  {
    a: "markers/Desc_Coal_C",
    b: "regions/N",
    de: 10.0,
    why: "the coal warrant above (Rocky Desert).",
  },
  {
    a: "markers/Desc_Coal_C",
    b: "regions/M",
    de: 11.9,
    why: "the coal warrant above (Red Jungle).",
  },
  {
    a: "markers/Desc_Coal_C",
    b: "regions/Q",
    de: 12.9,
    why: "the coal warrant above (Swamp).",
  },
  {
    a: "markers/Desc_Coal_C",
    b: "regions/I",
    de: 14.1,
    why: "the coal warrant above (Maze Canyons).",
  },
  {
    a: "markers/Desc_Coal_C",
    b: "regions/H",
    de: 14.3,
    why: "the coal warrant above (Lake Forest).",
  },
  {
    a: "markers/Desc_Coal_C",
    b: "regions/P",
    de: 14.8,
    why: "the coal warrant above (Spire Coast).",
  },
  {
    a: "markers/Desc_Coal_C",
    b: "regions/B",
    de: 14.9,
    why: "the coal warrant above (Blue Crater).",
  },
  {
    a: "markers/Desc_Water_C",
    b: "placements/machines",
    de: 8.6,
    why:
      "a disc on open water against a rectangle in a factory, and at the one place the two " +
      "could share a square metre -- a water extractor standing on a water node -- the " +
      "machine actually drawn there belongs to the extractors layer and is ultramarine, " +
      "dE 76.5 from the dot, with raiseNodeDots() keeping the dot on top of it. The water tint is the " +
      "game's and the machine blue is the page's oldest colour, with three warrants measured " +
      "against it. See KIND_COLOUR in placements.ts.",
  },
  {
    a: "markers/Desc_Stone_C",
    b: "routes/belt fast",
    de: 13.2,
    why:
      "a filled disc against a stroked line -- the shape split the chevron discharge above " +
      "rests on, at a distance those composites never reach. The two meet where a Mk4+ belt " +
      "leaves a limestone miner, and there the dot is raised, stroked at full opacity and " +
      "standing beside an ultramarine extractor; the belts' other tones are 19.3 and 26.4 from the " +
      "dot. The limestone tint is the game's, and the fast tone is one end of the belts' " +
      "published ramp -- moving it re-derives the house step every family here is measured " +
      "against.",
  },
  /* Dark-tone values, drawn only over a dark base (map-tone.ts), where the belts' steel and a
   * light grey are both chosen to read against near-black ground. Each pair is a different
   * kind of mark: a 1 px X or a filled disc against a stroked run. docs/frontend_vision.md §19. */
  {
    a: "markers/pickup collected dark",
    b: "routes/belts",
    de: 5.7,
    why: "the dark-tone X over a collected pickup: two crossed 8 px strokes, never a run.",
  },
  {
    a: "markers/pickup collected dark",
    b: "routes/belt slow",
    de: 8.6,
    why: "as above.",
  },
  {
    a: "markers/pickup collected dark",
    b: "routes/belt fast",
    de: 10.0,
    why: "as above.",
  },
  {
    a: "markers/coal dark",
    b: "routes/belt slow",
    de: 6.1,
    why: "coal on a dark base: a filled disc against a stroked line, the limestone warrant's split.",
  },
  {
    a: "markers/coal dark",
    b: "routes/belts",
    de: 10.0,
    why: "as above.",
  },
  {
    a: "markers/coal dark",
    b: "regions/J",
    de: 12.1,
    why: "the coal warrant above, on a dark base (No Man's Land).",
  },
  {
    a: "power/casing dark",
    b: "routes/lift fill",
    de: 3.5,
    why: "a 1 px rim either side of a lilac wire against the hole inside a steel ring.",
  },
];

/* And the debt: pairs that are under the threshold, that no warrant defends, and that are
 * written down here so that making the discipline executable does not quietly turn into
 * making it optional. Empty, and kept as a mechanism: the next colour that lands under the
 * threshold while "which one moves" is being decided needs somewhere honest to stand, and the
 * boot warning below prints whatever is in here.
 */
interface Standing {
  /** What these pairs have in common, and how far the argument for tolerating them goes. */
  note: string;
  /** `[owner/name, owner/name, dE as measured today]`. */
  pairs: [string, string, number][];
}

var STANDING: Standing[] = [];

/** One listed pair: the distance it was written down at, and whether it is owed or answered. */
interface Listed {
  de: number;
  owed: boolean;
}

/** Every listed pair, keyed the way the audit names it, and which list it came from --
 *  because "answered" and "owed" are counted differently. */
function allowed(): Record<string, Listed> {
  const listed: Record<string, Listed> = {};
  DISCHARGED.forEach(function (entry) {
    listed[pairKey(entry.a, entry.b)] = { de: entry.de, owed: false };
  });
  STANDING.forEach(function (group) {
    group.pairs.forEach(function (pair) {
      listed[pairKey(pair[0], pair[1])] = { de: pair[2], owed: true };
    });
  });
  return listed;
}

/** One pair of colours from two different owners, and how far apart they are. */
interface Measured {
  pair: string;
  distance: number;
}

function crossOwnerPairs(): Measured[] {
  const pairs: Measured[] = [];
  for (let i = 0; i < declared.length; i++) {
    for (let j = i + 1; j < declared.length; j++) {
      const one = declared[i]!;
      const two = declared[j]!;
      if (one.owner === two.owner) continue;
      pairs.push({
        pair: pairKey(colourKey(one.owner, one.name), colourKey(two.owner, two.name)),
        distance: deltaE(one.hex, two.hex),
      });
    }
  }
  return pairs;
}

/** A NEW pair under the threshold: a colour chosen without looking at the rest of the page. */
function checkUnlistedPairs(pairs: Measured[], known: Record<string, Listed>): void {
  pairs.forEach(function (measured) {
    if (known[measured.pair] !== undefined || measured.distance >= MIN_DELTA_E) return;
    console.error(
      "palette: " + measured.pair + " is dE " + measured.distance + ", under " + MIN_DELTA_E +
        " — two modules chose colours that cannot be told apart. Move one, or list the pair in " +
        "DISCHARGED in palette.ts with what makes it safe."
    );
  });
}

/** A listed pair that MOVED is a reason now describing two other colours; one no longer close
 *  needs no exception. */
function checkListedDrift(pairs: Measured[], known: Record<string, Listed>): void {
  pairs.forEach(function (measured) {
    const listed = known[measured.pair];
    if (listed === undefined) return;
    if (measured.distance !== listed.de) {
      console.error(
        "palette: " + measured.pair + " is dE " + measured.distance + ", listed at " + listed.de +
          " — the reason written beside it was measured about a colour that has since changed."
      );
    } else if (measured.distance >= MIN_DELTA_E) {
      console.error(
        "palette: " + measured.pair + " is dE " + measured.distance + " and needs no exception " +
          "any more — delete the entry from palette.ts."
      );
    }
  });
}

/** A listed pair one of whose colours is gone: the ledger entry outlived its subject. */
function reportOutlivedEntries(pairs: Measured[], known: Record<string, Listed>): void {
  const seen: Record<string, boolean> = {};
  pairs.forEach(function (measured) {
    seen[measured.pair] = true;
  });
  Object.keys(known).forEach(function (pair) {
    if (!seen[pair]) {
      console.error(
        "palette: " + pair + " is listed in palette.ts but one of those colours is no longer " +
          "declared — the entry outlived its subject."
      );
    }
  });
}

/** Not a defect: the size of the undefended debt, said once so that it stays visible. */
function reportStandingDebt(pairs: Measured[], known: Record<string, Listed>): void {
  let owed = 0;
  let closest = MIN_DELTA_E;
  let worst = "";
  pairs.forEach(function (measured) {
    const listed = known[measured.pair];
    if (!listed || !listed.owed || measured.distance >= MIN_DELTA_E) return;
    owed++;
    if (measured.distance < closest) {
      closest = measured.distance;
      worst = measured.pair;
    }
  });
  if (owed) {
    console.warn(
      "palette: " + owed + " cross-owner pairs are under dE " + MIN_DELTA_E + " with no warrant " +
        "(nearest " + closest + ", " + worst + ") — see STANDING in palette.ts."
    );
  }
}

/** The check itself, run once, in dev, after the whole table has declared. */
function audit(): void {
  const known = allowed();
  const pairs = crossOwnerPairs();
  checkUnlistedPairs(pairs, known);
  checkListedDrift(pairs, known);
  reportOutlivedEntries(pairs, known);
  reportStandingDebt(pairs, known);
}
