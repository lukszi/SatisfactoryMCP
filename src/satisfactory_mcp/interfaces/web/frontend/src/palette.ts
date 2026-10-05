/* Where a colour is DECLARED, and where the claim that the colours were chosen against each
 * other is CHECKED.
 *
 * Every value used to be here, on the argument that a table of colours picked against each
 * other only holds together while it is in one place to be compared. Half of that was right,
 * and the half it got wrong is the half that mattered: what has to be in one place is the
 * COMPARISON, not the values. Keeping the values here as well left every measured warrant --
 * "dE 34 from the terrain", "dE 4.5 from the extractors, which is why that one was rejected"
 * -- in a comment no build has ever read, and put the pipe rust and the storage magenta in a
 * different file from the width, the tier ramp and the reasoning that produced them. The file
 * even had to carve out an exception for the two networks that had done it the other way.
 *
 * So the values went to the features and the comparison stayed. Every colour on this page is
 * now declared by the module that draws with it, beside its warrant, through `declareColours`
 * below. This file holds no hex at all, which `test_architecture.py` checks. What it holds
 * instead is the discipline the old header could only describe: hex to CIE Lab, pairwise dE
 * across every pair of colours declared by two DIFFERENT owners, and a console.error in dev
 * mode for any pair under the threshold that is not written down below with a reason and a
 * measured distance.
 *
 * A new layer's colour is still a decision about the whole table. The difference is that the
 * table now answers back.
 */

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
 * deliberate and small -- the belts' 15.6 between slowest and fastest, the pipes' 15.1 between
 * Mk1 and Mk2, the storage pair's 15.8, the poles' 16.0, the grounds' 17.1 where No Man's Land
 * borders the Rocky Desert -- and a rule that flagged those is a rule everybody switches off.
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
    var table = colours as Record<string, string>;
    Object.keys(table).forEach(function (name) {
      var hex = table[name]!;
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
 * than a preference. Every dE already written down on this page was computed this way: the
 * numbers quoted in the feature files reproduce to the decimal under this function and under
 * no other. CIEDE2000 put the chevron cream 18.7 from the old extractor amber where the
 * warrant said 45.5 -- so adopting it would mean re-deriving every published number and
 * throwing away the only record of what anybody actually measured. The threshold below is
 * calibrated against those numbers, so the formula and the threshold travel together.
 */
function lab(hex: string): [number, number, number] {
  var channel = function (at: number): number {
    var c = parseInt(hex.slice(at, at + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  var r = channel(1);
  var g = channel(3);
  var b = channel(5);
  // Linear sRGB to XYZ, each axis already divided by the D65 white point.
  var x = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047;
  var y = r * 0.2126729 + g * 0.7151522 + b * 0.072175;
  var z = (r * 0.0193339 + g * 0.119192 + b * 0.9503041) / 1.08883;
  var f = function (t: number): number {
    return t > 216 / 24389 ? Math.cbrt(t) : (841 / 108) * t + 4 / 29;
  };
  var fx = f(x);
  var fy = f(y);
  var fz = f(z);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}

/** The distance, at the one decimal every warrant on this page is written to. */
function deltaE(a: string, b: string): number {
  var one = lab(a);
  var two = lab(b);
  var d = Math.sqrt(
    (one[0] - two[0]) * (one[0] - two[0]) +
      (one[1] - two[1]) * (one[1] - two[1]) +
      (one[2] - two[2]) * (one[2] - two[2])
  );
  return Math.round(d * 10) / 10;
}

/* dE 15, and it is the house step read off the page rather than a number from a standard.
 *
 * The four ramps this page draws inside one family -- the belts' 15.6, the pipes' 15.1, the
 * storage pair's 15.8, the poles' 16.0 -- are the smallest steps anyone here has looked at and
 * accepted, and the smallest ground step accepted is 17.1. All five are same-owner and none of
 * them reaches this test. Fifteen sits just under the lot, so that two colours from different
 * modules landing as close as a deliberate ramp is exactly the thing that gets called out.
 */
var LIMIT = 15;

/** `owner/name`, which is how a colour is named below and in every message the audit prints. */
function key(owner: string, name: string): string {
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

/* The pairs a measurement answers. Two eras are in here. The chevron pair was discharged the
 * day the cream was chosen, which for a long time made it the only entry -- the finding this
 * file's first version reported was that of all the pairs under the threshold, one had ever
 * been measured on purpose. The other ten came out of the recolour that paid the STANDING
 * debt down (see below): they are the pairs where NEITHER colour can move -- the game's own
 * ore tints on one side, the biome grounds and the two oldest network families on the other
 * -- so each carries the map fact that keeps it from being a confusion, at the distance the
 * audit re-derives every dev boot. Of the 1,411 cross-owner pairs this page compares (the
 * old intro said 1,349, which was a miscount of the same table), these eleven sit under the
 * threshold on purpose; the other 1,400 clear it, none by less than 15.2 (which is the coal
 * dot again, against Northern Forest -- the coal warrant's point exactly).
 */
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
   * 256 m flat fill that REGION_BLEND fades to 0.45 wherever there is imagery -- which is the
   * point-against-area axis the first storage warrant established, applied to the one
   * family where nothing else was available. Eight entries rather than one line so that a
   * ground edit that closes any single gap still trips the drift check. */
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
];

/* And the debt: pairs that are under the threshold, that no warrant defends, and that are
 * written down here so that making the discipline executable does not quietly turn into
 * making it optional.
 *
 * Paid down to NOTHING in the 2026-07 recolour, and kept as a mechanism rather than deleted:
 * the next colour that lands under the threshold while "which one moves" is being decided
 * against the map needs somewhere honest to stand, and the boot warning below prints
 * whatever is in here. Today it prints nothing.
 *
 * What was here, for the record, and where it went. Twenty-seven pairs stood when this file
 * first became executable. Seventeen were cleared by moving eight colours -- the ones with a
 * free side: the concrete (dE 6.6 from Abyss Cliffs, against its own written warrant) went
 * to a measured slate violet; the pipe family (4.9 from the bauxite dot, against a warrant
 * that claimed copper at 22) went dark oxide, taking the geyser and tape pairs with it; the
 * crashed drop pod left the belt steel (2.1, the closest pair the map ever had) for olive,
 * the somersloop left the generator red (4.7) for rose, the hard drive left the machine blue
 * (8.7) for indigo, and the lift fill dropped to near-black, clearing the concrete (10.4)
 * and Abyss Cliffs (11.0). Every new value sits beside a fresh measurement in its own file.
 * The ten that remained -- coal's eight grounds, the water dot against the machine blue, the
 * limestone dot against the fast belt -- are pairs where BOTH sides are anchored, and they
 * moved to DISCHARGED above with the map facts that answer them.
 */
interface Standing {
  /** What these pairs have in common, and how far the argument for tolerating them goes. */
  note: string;
  /** `[owner/name, owner/name, dE as measured today]`. */
  pairs: [string, string, number][];
}

var STANDING: Standing[] = [];

/** Every listed pair, keyed the way the audit's loop will name it, and which list it came
 *  from -- because "answered" and "owed" are counted differently at the end. */
function allowed(): Record<string, { de: number; owed: boolean }> {
  var out: Record<string, { de: number; owed: boolean }> = {};
  DISCHARGED.forEach(function (entry) {
    out[pairKey(entry.a, entry.b)] = { de: entry.de, owed: false };
  });
  STANDING.forEach(function (group) {
    group.pairs.forEach(function (pair) {
      out[pairKey(pair[0], pair[1])] = { de: pair[2], owed: true };
    });
  });
  return out;
}

/* The check itself, run once, in dev, after the whole table has declared.
 *
 * Three failures, and they are three different mistakes. A NEW pair under the threshold is a
 * colour chosen without looking at the rest of the page. A listed pair whose distance has
 * MOVED is a reason that now describes two other colours. A listed pair that is no longer
 * close is an entry outliving its subject, which is how a ledger turns back into a mute
 * button. The count of undefended pairs is said once, as a number, for the same reason: a debt
 * nobody is reminded of is a debt nobody pays.
 */
function audit(): void {
  var known = allowed();
  var seen: Record<string, boolean> = {};
  var owed = 0;
  var closest = LIMIT;
  var worst = "";
  for (var i = 0; i < declared.length; i++) {
    for (var j = i + 1; j < declared.length; j++) {
      var one = declared[i]!;
      var two = declared[j]!;
      if (one.owner === two.owner) continue;
      var pair = pairKey(key(one.owner, one.name), key(two.owner, two.name));
      var distance = deltaE(one.hex, two.hex);
      var listed = known[pair];
      if (listed === undefined) {
        if (distance < LIMIT) {
          console.error(
            "palette: " + pair + " is dE " + distance + ", under " + LIMIT + " — two modules " +
              "chose colours that cannot be told apart. Move one, or list the pair in " +
              "DISCHARGED in palette.ts with what makes it safe."
          );
        }
        continue;
      }
      seen[pair] = true;
      if (distance !== listed.de) {
        console.error(
          "palette: " + pair + " is dE " + distance + ", listed at " + listed.de + " — the " +
            "reason written beside it was measured about a colour that has since changed."
        );
      } else if (distance >= LIMIT) {
        console.error(
          "palette: " + pair + " is dE " + distance + " and needs no exception any more — " +
            "delete the entry from palette.ts."
        );
      }
      if (listed.owed && distance < LIMIT) {
        owed++;
        if (distance < closest) {
          closest = distance;
          worst = pair;
        }
      }
    }
  }
  Object.keys(known).forEach(function (pair) {
    if (!seen[pair]) {
      console.error(
        "palette: " + pair + " is listed in palette.ts but one of those colours is no longer " +
          "declared — the entry outlived its subject."
      );
    }
  });
  // Everything above is a defect. This is not: it is the size of the debt the executable
  // version of this file found already in place, said once so that it stays visible.
  if (owed) {
    console.warn(
      "palette: " + owed + " cross-owner pairs are under dE " + LIMIT + " with no warrant " +
        "(nearest " + closest + ", " + worst + ") — see STANDING in palette.ts."
    );
  }
}
