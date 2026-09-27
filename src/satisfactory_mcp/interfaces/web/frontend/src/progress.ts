/* The dashboard's Progress section: milestones, MAM, Space Elevator, hard drives, shards and
 * somersloops, facts only. See docs/frontend_vision.md, "Phase 4: Progress". */

import { count, make } from "./dom";
import { checkbox, cell, grid, heading, link, note, scroll, tile } from "./dashkit";
import { phaseText } from "./format";
import { hashFor } from "./map";
import { registerFetch } from "./registry";
import { setting } from "./settings";

import type { ApiError, ApiUrl } from "./api";
import type { Fetcher } from "./registry";
import type {
  DriveRow,
  HardDrivesResponse,
  MamResponse,
  MamRow,
  MilestoneRow,
  MilestonesResponse,
  PhaseResponse,
  PhaseRow,
  ShardsResponse,
  SloopsResponse,
} from "./api-shapes";

interface Slot<T> {
  data: T | null;
  error: string;
}

interface Placed {
  x_m: number | null;
  y_m: number | null;
}

export type PointButton = (row: Placed) => HTMLElement;

var SUBS: [string, string][] = [
  ["", "Milestones"],
  ["mam", "MAM"],
  ["elevator", "Space Elevator"],
  ["drives", "Hard drives"],
  ["shards", "Power shards"],
  ["sloops", "Somersloops"],
];

var listeners: Array<() => void> = [];

var showDone = false;

var showMamDone = false;

function changed(): void {
  listeners.forEach(function (listener) {
    listener();
  });
}

export function onProgress(listener: () => void): void {
  listeners.push(listener);
}

function empty<T>(): Slot<T> {
  return { data: null, error: "" };
}

function fetcher<T extends ApiError>(held: Slot<T>, path: ApiUrl, label: string, rank: number): Fetcher<T> {
  return {
    wave: "live",
    rank: rank,
    path: path,
    label: label,
    clears: [],
    refilters: false,
    draw: function (data) {
      held.data = data;
      held.error = "";
      changed();
    },
    failed: function () {
      held.data = null;
      held.error = label + " could not be read for this save";
      changed();
    },
  };
}

var milestones = empty<MilestonesResponse>();
var mam = empty<MamResponse>();
var phase = empty<PhaseResponse>();
var drives = empty<HardDrivesResponse>();
var shards = empty<ShardsResponse>();
var sloops = empty<SloopsResponse>();

registerFetch(fetcher(milestones, "/api/progress/milestones", "milestones", 60));
registerFetch(fetcher(mam, "/api/progress/mam", "MAM research", 61));
registerFetch(fetcher(phase, "/api/progress/phase", "space elevator", 62));
registerFetch(fetcher(drives, "/api/progress/harddrives", "hard drives", 63));
registerFetch(fetcher(shards, "/api/progress/shards", "power shards", 64));
registerFetch(fetcher(sloops, "/api/progress/sloops", "somersloops", 65));

function amounts(rows: { name: string; amount: number }[]): string {
  return (
    rows
      .map(function (r) {
        return count(r.amount) + " " + r.name;
      })
      .join(", ") || "–"
  );
}

function num(value: number): string {
  return count(Math.round(value * 10) / 10);
}

function spoilerNote(body: HTMLElement, text: string): void {
  var hid = make("p", "dash-note", text + " ");
  hid.appendChild(link("settings", "Settings"));
  hid.appendChild(document.createTextNode(" can show them."));
  body.appendChild(hid);
}

function reach(data: MilestonesResponse): number {
  var top = 0;
  data.milestones.forEach(function (m) {
    if (m.status === "DONE") top = Math.max(top, m.tier);
  });
  if (!top && data.tiers.length) top = data.tiers[0]!.tier;
  return top;
}

function shown(data: MilestonesResponse): MilestonesResponse {
  if (setting("spoilers")) return data;
  var top = reach(data);
  return {
    game_phase: data.game_phase,
    highest_complete_tier: data.highest_complete_tier,
    tiers: data.tiers.filter(function (t) {
      return t.tier <= top;
    }),
    milestones: data.milestones.filter(function (m) {
      return m.tier <= top;
    }),
  };
}

export function milestoneTile(): HTMLElement | null {
  if (!milestones.data) return null;
  var ready = shown(milestones.data).milestones.filter(function (m) {
    return m.status === "READY";
  }).length;
  var top = milestones.data.highest_complete_tier;
  return tile(
    "milestones",
    top === null ? "no tier complete" : "tier " + top + " complete",
    (phaseText(milestones.data.game_phase) || "no phase in this save") + " · " + ready + " affordable",
    false,
    hashFor("progress")
  );
}

function renderMilestones(body: HTMLElement): void {
  if (!milestones.data) {
    note(body, milestones.error || "loading…");
    return;
  }
  var data = shown(milestones.data);
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(
    tile(
      "highest complete tier",
      data.highest_complete_tier === null ? "none" : String(data.highest_complete_tier),
      phaseText(data.game_phase) || "no phase in this save"
    )
  );
  var counts: Record<string, number> = { READY: 0, short: 0, BLOCKED: 0, DONE: 0 };
  data.milestones.forEach(function (m) {
    counts[m.status] = (counts[m.status] || 0) + 1;
  });
  tiles.appendChild(tile("affordable now", count(counts.READY!), "bill covered by spendable stock"));
  tiles.appendChild(tile("short", count(counts.short!), "stock does not cover the bill"));
  tiles.appendChild(tile("done", count(counts.DONE!), "of " + data.milestones.length + " milestones" + (setting("spoilers") ? "" : " so far")));
  body.appendChild(tiles);

  var strip = make("div", "dash-tiers");
  data.tiers.forEach(function (t) {
    strip.appendChild(tally("tier " + t.tier, t.done, t.total));
  });
  body.appendChild(strip);
  if (!setting("spoilers")) spoilerNote(body, "Tiers you have not started are hidden.");

  var card = make("section", "dash-card");
  var bar = make("div", "dash-title");
  bar.appendChild(make("h2", "dash-h", "milestones"));
  bar.appendChild(
    checkbox("show done", showDone, function (on) {
      showDone = on;
      changed();
    })
  );
  card.appendChild(bar);
  var rows = data.milestones.filter(function (m: MilestoneRow) {
    return showDone || m.status !== "DONE";
  });
  if (!rows.length) note(card, "every milestone is done");
  else {
    var t = grid([
      ["tier", true],
      ["milestone", false],
      ["status", false],
      ["cost", false],
      ["short by", false],
      ["recipes", true],
    ]);
    var tb = t.tBodies[0]!;
    rows.forEach(function (m) {
      var tr = make("tr");
      cell(tr, m.tier, "num");
      cell(tr, m.name);
      cell(tr, m.status + (m.blocked_by.length ? " (" + m.blocked_by.join(", ") + ")" : ""), m.status === "READY" ? "ok" : "");
      cell(tr, amounts(m.cost));
      cell(tr, m.status === "DONE" ? "" : amounts(m.short), m.short.length && m.status !== "DONE" ? "bad" : "");
      cell(tr, m.unlocks || "", "num");
      tb.appendChild(tr);
    });
    scroll(card, t);
  }
  note(
    card,
    "cost is checked against spendable stock: carried, storage containers and the Dimensional Depot. " +
      "READY is about the bill, not about access: a HUB tier opens with Space Elevator deliveries, " +
      "which no milestone in the game data records. recipes counts what a milestone newly grants."
  );
  body.appendChild(card);
}

function tally(label: string, done: number, total: number): HTMLElement {
  var box = make("div", "dash-tier" + (done === total ? " full" : ""));
  box.appendChild(make("span", "dash-tier-k", label));
  var bar = make("div", "dash-hbar");
  var fill = make("span", "dash-mix-ok");
  fill.style.width = (total ? (done / total) * 100 : 0) + "%";
  bar.appendChild(fill);
  box.appendChild(bar);
  box.appendChild(make("span", "dash-tier-v", done + "/" + total));
  return box;
}

function visibleMam(data: MamResponse): MamRow[] {
  if (setting("spoilers")) return data.research;
  return data.research.filter(function (r) {
    return r.status !== "TREE SHUT";
  });
}

function duration(seconds: number): string {
  var minutes = Math.round(seconds / 60);
  return minutes < 60 ? minutes + " min" : Math.floor(minutes / 60) + " h " + (minutes % 60) + " min";
}

function mamStatus(r: MamRow): string {
  if (r.status === "RUNNING") return "RUNNING, " + duration(r.running_s || 0) + " left at save";
  if (r.status === "BLOCKED") return "BLOCKED (" + r.blocked_by.join(", ") + ")";
  return r.status;
}

function renderMam(body: HTMLElement): void {
  var data = mam.data;
  if (!data) {
    note(body, mam.error || "loading…");
    return;
  }
  var rows = visibleMam(data);
  var hidden = data.research.length - rows.length;
  note(
    body,
    data.capabilities
      .map(function (c) {
        return (c.schematic_name || c.capability) + ": " + (c.researched ? "researched" : "not researched");
      })
      .join(" · ")
  );
  var trees: Record<string, number[]> = {};
  rows.forEach(function (r) {
    var name = r.tree || "other";
    var t = trees[name] || (trees[name] = [0, 0]);
    if (r.status === "DONE") t[0]! += 1;
    t[1]! += 1;
  });
  var strip = make("div", "dash-tiers");
  Object.keys(trees)
    .sort()
    .forEach(function (name) {
      strip.appendChild(tally(name, trees[name]![0]!, trees[name]![1]!));
    });
  body.appendChild(strip);
  if (hidden) spoilerNote(body, hidden + " research nodes in trees not opened yet are hidden.");
  if (!data.knows_trees) note(body, "this save predates the list of opened trees, so a node in an unopened tree reads as available");

  var card = make("section", "dash-card");
  var bar = make("div", "dash-title");
  bar.appendChild(make("h2", "dash-h", "MAM research"));
  bar.appendChild(
    checkbox("show done", showMamDone, function (on) {
      showMamDone = on;
      changed();
    })
  );
  card.appendChild(bar);
  var listed = rows
    .filter(function (r) {
      return showMamDone || r.status !== "DONE";
    })
    .sort(function (a, b) {
      var x = (a.tree || "~") + "\u0000" + a.name;
      var y = (b.tree || "~") + "\u0000" + b.name;
      return x < y ? -1 : x > y ? 1 : 0;
    });
  if (!listed.length) note(card, "every research node shown here is done");
  else {
    var t = grid([
      ["tree", false],
      ["research", false],
      ["status", false],
      ["cost", false],
      ["short by", false],
      ["recipes", true],
    ]);
    var tb = t.tBodies[0]!;
    listed.forEach(function (r) {
      var tr = make("tr");
      cell(tr, r.tree || "–", r.tree ? "" : "dash-muted");
      var name = make("span", "", r.name);
      if (r.capability) name.appendChild(make("span", "dash-sub", " unlocks " + r.capability.replace(/_/g, " ")));
      cell(tr, name);
      cell(tr, mamStatus(r), r.status === "READY" ? "ok" : "");
      cell(tr, amounts(r.cost));
      cell(tr, r.status === "DONE" || r.status === "RUNNING" ? "" : amounts(r.short), r.short.length && r.status !== "DONE" && r.status !== "RUNNING" ? "bad" : "");
      cell(tr, r.unlocks || "", "num");
      tb.appendChild(tr);
    });
    scroll(card, t);
  }
  note(
    card,
    "cost is checked against spendable stock: carried, storage containers and the Dimensional Depot. " +
      "RUNNING is paid for and under way; the time left is what the save recorded and does not run down while the game is closed. " +
      "TREE SHUT: the tree that node lives in is not open yet, read off its class id. recipes counts what a node newly grants."
  );
  body.appendChild(card);
}

function phaseNumber(p: string | null): number {
  var match = /_(\d+)$/.exec(p || "");
  return match ? +match[1]! : Infinity;
}

function visiblePhases(data: PhaseResponse): PhaseRow[] {
  if (setting("spoilers")) return data.phases;
  var target = phaseNumber(data.target_phase);
  return data.phases.filter(function (p) {
    return phaseNumber(p.phase) <= target;
  });
}

function targetRow(data: PhaseResponse): PhaseRow | null {
  return (
    data.phases.filter(function (p) {
      return !!p.phase && p.phase === data.target_phase;
    })[0] || null
  );
}

function shortParts(row: PhaseRow): number {
  return row.outstanding.filter(function (i) {
    return i.short > 0;
  }).length;
}

var TRUST: Record<string, string> = {
  usable: "full cost, never delivered into",
  derived: "frozen record minus deliveries, a lower bound",
  complete: "nothing outstanding",
  stale: "frozen record, not believed",
  unmapped: "legacy key with no phase",
};

function renderElevator(body: HTMLElement): void {
  var data = phase.data;
  if (!data) {
    note(body, phase.error || "loading…");
    return;
  }
  var target = targetRow(data);
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("current phase", phaseText(data.current_phase) || "–", "what the save says is done"));
  tiles.appendChild(tile("target phase", phaseText(data.target_phase) || "–", "where deliveries go"));
  var short = target ? shortParts(target) : 0;
  tiles.appendChild(
    tile(
      "deliverable now",
      data.deliverable === null ? "–" : data.deliverable ? "yes" : "no",
      data.deliverable === null ? "no record for the target phase" : data.deliverable ? "every part is in stock" : short + " of " + target!.outstanding.length + " parts short"
    )
  );
  body.appendChild(tiles);

  var card = make("section", "dash-card");
  heading(card, "target: " + (phaseText(data.target_phase) || "none"));
  if (!target) note(card, "the save has no per-phase record for the target phase");
  else if (!target.outstanding.length) note(card, "nothing outstanding on the target phase");
  else {
    var t = grid([
      ["part", false],
      ["needed", true],
      ["have", true],
      ["short by", true],
    ]);
    var tb = t.tBodies[0]!;
    target.outstanding.forEach(function (i) {
      var tr = make("tr");
      cell(tr, i.name);
      cell(tr, count(i.amount), "num");
      cell(tr, count(i.have), "num");
      cell(tr, i.short > 0 ? count(i.short) : "", "num" + (i.short > 0 ? " bad" : ""));
      tb.appendChild(tr);
    });
    scroll(card, t);
    note(card, "record: " + (TRUST[target.trust] || target.trust));
  }
  note(card, "delivered to the target so far: " + (data.delivered.length ? amounts(data.delivered) : "nothing"));
  note(card, "have counts spendable stock: carried, storage containers and the Dimensional Depot. Parts in machines and on belts are not counted.");
  body.appendChild(card);

  var rows = visiblePhases(data);
  var all = make("section", "dash-card");
  heading(all, "every phase record");
  var p = grid([
    ["phase", false],
    ["record", false],
    ["outstanding", false],
    ["parts done", false],
  ]);
  var pb = p.tBodies[0]!;
  rows.forEach(function (r) {
    var tr = make("tr");
    cell(tr, phaseText(r.phase) || r.legacy_key);
    cell(tr, r.trust + ": " + (TRUST[r.trust] || ""), r.trust === "stale" ? "dash-muted" : "");
    cell(tr, amounts(r.outstanding), r.trust === "stale" ? "dash-muted" : "");
    cell(tr, r.complete.join(", ") || "–");
    pb.appendChild(tr);
  });
  scroll(all, p);
  note(
    all,
    "The per-phase amounts come from a record the game marks deprecated and no longer updates. " +
      "Only the target phase row is read as a cost; a stale row is shown for completeness and not believed."
  );
  if (rows.length < data.phases.length) spoilerNote(all, data.phases.length - rows.length + " phases past the target are hidden.");
  body.appendChild(all);
}

function grants(option: DriveRow["options"][number]): string {
  if (option.slots) return "+" + option.slots + " inventory slots";
  if (!option.recipes.length) return "nothing new";
  return option.recipes
    .map(function (r) {
      var made = r.products
        .map(function (f) {
          return num(f.amount) + " " + f.name + "/min";
        })
        .join(" + ");
      return (made || r.name) + (r.machine ? " in a " + r.machine : "");
    })
    .join("; ");
}

function renderDrives(body: HTMLElement): void {
  var data = drives.data;
  if (!data) {
    note(body, drives.error || "loading…");
    return;
  }
  var rerolls = 0;
  data.drives.forEach(function (d) {
    rerolls += d.rerolls_left;
  });
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("pending choices", count(data.drives.length), "analysed drives waiting for a pick"));
  tiles.appendChild(tile("rerolls left", count(rerolls), "across the pending drives"));
  tiles.appendChild(tile("unanalysed", count(data.spare), "hard drives on hand"));
  if (data.last_used !== null) tiles.appendChild(tile("last settled", "drive " + data.last_used, "the choice made most recently"));
  body.appendChild(tiles);

  var card = make("section", "dash-card");
  heading(card, "pending hard drives");
  if (!data.drives.length) note(card, "no hard drive is waiting for a pick");
  else {
    var t = grid([
      ["drive", true],
      ["rerolls", true],
      ["option", false],
      ["grants", false],
    ]);
    var tb = t.tBodies[0]!;
    data.drives.forEach(function (d) {
      d.options.forEach(function (o, i) {
        var tr = make("tr", i < d.options.length - 1 ? "dash-lead" : "");
        cell(tr, i ? "" : d.hard_drive_id === null ? "?" : d.hard_drive_id, "num");
        cell(tr, i ? "" : d.rerolls_left, "num");
        cell(tr, o.name);
        cell(tr, grants(o));
        tb.appendChild(tr);
      });
    });
    scroll(card, t);
  }
  note(
    card,
    "The option not picked returns to the pool and a later drive can offer it again; only the drive is spent. " +
      "Rates are per machine at 100%. Ranking the options is advise_hard_drive_pick, not this page."
  );
  body.appendChild(card);
}

function renderShards(body: HTMLElement, point: PointButton): void {
  var data = shards.data;
  if (!data) {
    note(body, shards.error || "loading…");
    return;
  }
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("free", num(data.free), "crafted shards in stock"));
  tiles.appendChild(tile("craftable", num(data.craftable), "from slugs on hand, once crafted"));
  tiles.appendChild(tile("slotted", data.measured ? count(data.committed) : "–", data.measured ? data.idle + " of them above what the clock needs" : "this save does not record slots"));
  tiles.appendChild(tile("owned", num(data.owned), "free plus slotted"));
  body.appendChild(tiles);
  note(
    body,
    "a shard adds " +
      Math.round(data.per_shard * 100) +
      "% max clock and a building takes " +
      data.slots_per_building +
      ", so the ceiling is " +
      Math.round(data.max_clock * 100) +
      "%"
  );
  data.by_place.forEach(function (p) {
    note(body, p.place + ": " + amounts(p.items));
  });

  var split = make("div", "dash-split");
  if (data.slugs.length) {
    var slugCard = make("section", "dash-card");
    heading(slugCard, "power slugs");
    var s = grid([
      ["slug", false],
      ["held", true],
      ["shards each", true],
      ["shards", true],
    ]);
    var sb = s.tBodies[0]!;
    data.slugs.forEach(function (slug) {
      var tr = make("tr");
      cell(tr, slug.name);
      cell(tr, num(slug.held), "num");
      cell(tr, num(slug.each), "num");
      cell(tr, num(slug.shards), "num");
      sb.appendChild(tr);
    });
    scroll(slugCard, s);
    note(slugCard, "craftable is potential, not free: crafting is a manual step");
    split.appendChild(slugCard);
  }
  var card = make("section", "dash-card");
  heading(card, "overclocked buildings (" + data.holders.length + ")");
  if (!data.holders.length) note(card, "no building holds a shard or runs above 100%");
  else {
    var t = grid([
      ["building", false],
      ["clock", true],
      ["slotted", true],
      ["needed", true],
      ["idle", true],
      ["", true],
    ]);
    var tb = t.tBodies[0]!;
    data.holders.forEach(function (h) {
      var tr = make("tr");
      cell(tr, h.name || "–");
      cell(tr, Math.round(h.clock * 1000) / 10 + "%", "num");
      cell(tr, h.slotted, "num");
      cell(tr, h.needed, "num");
      cell(tr, h.idle || "", "num");
      cell(tr, point(h), "num");
      tr.title = h.instance;
      tb.appendChild(tr);
    });
    scroll(card, t);
    note(card, "slotted is read from each building's shard slots; needed is what its clock requires; idle is the difference");
  }
  split.appendChild(card);
  body.appendChild(split);
}

function renderSloops(body: HTMLElement, point: PointButton): void {
  var data = sloops.data;
  if (!data) {
    note(body, sloops.error || "loading…");
    return;
  }
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("free", num(data.free), data.by_place.length ? amounts(data.by_place) : "none in stock"));
  tiles.appendChild(tile("slotted", data.measured ? num(data.committed) : "–", data.measured ? "in production machines" : "this save does not record slots"));
  tiles.appendChild(tile("owned", num(data.owned), "free plus slotted"));
  tiles.appendChild(tile("Mercer Spheres", num(data.mercer_spheres), "counted apart, never added in"));
  body.appendChild(tiles);
  if (!data.amplifier_researched) {
    note(
      body,
      (data.amplifier_research || "Production Amplifier") +
        " is not researched, so no somersloop can go into a machine yet. MAM cost: " +
        amounts(data.amplifier_cost)
    );
  }
  var card = make("section", "dash-card");
  heading(card, "amplified machines (" + data.holders.length + ")");
  if (!data.holders.length) note(card, "no machine holds a somersloop");
  else {
    var t = grid([
      ["building", false],
      ["somersloops", true],
      ["boost", true],
      ["boost in save", true],
      ["", true],
    ]);
    var tb = t.tBodies[0]!;
    var disagree = 0;
    data.holders.forEach(function (h) {
      var tr = make("tr");
      var off = h.boost !== null && h.boost_in_save !== null && Math.abs(h.boost - h.boost_in_save) > 1e-6;
      if (off) disagree += 1;
      cell(tr, h.name);
      cell(tr, num(h.sloops), "num");
      cell(tr, h.boost === null ? "–" : h.boost + "×", "num");
      cell(tr, h.boost_in_save === null ? "–" : h.boost_in_save + "×", "num" + (off ? " bad" : ""));
      cell(tr, point(h), "num");
      tr.title = h.instance;
      tb.appendChild(tr);
    });
    scroll(card, t);
    note(
      card,
      "boost is what the plan model says the slots are worth; boost in save is the multiplier the save carries" +
        (disagree ? "; they disagree on " + disagree + " machines" : "; they agree")
    );
  }
  body.appendChild(card);
}

function nextUp(body: HTMLElement): void {
  var tiles = make("div", "dash-tiles");
  var p = phase.data;
  var target = p ? targetRow(p) : null;
  tiles.appendChild(
    p
      ? tile(
          "space elevator",
          p.deliverable === null ? "–" : p.deliverable ? "deliverable" : count(target ? shortParts(target) : 0) + " parts short",
          "target " + (phaseText(p.target_phase) || "none"),
          false,
          hashFor("progress/elevator")
        )
      : tile("space elevator", "–", phase.error || "loading…")
  );
  var m = milestones.data;
  tiles.appendChild(
    m
      ? tile(
          "milestones",
          count(
            shown(m).milestones.filter(function (r) {
              return r.status === "READY";
            }).length
          ) + " affordable",
          m.highest_complete_tier === null ? "no tier complete" : "tier " + m.highest_complete_tier + " complete",
          false,
          hashFor("progress")
        )
      : tile("milestones", "–", milestones.error || "loading…")
  );
  var r = mam.data;
  if (r) {
    var rows = visibleMam(r);
    var running = rows.filter(function (x) {
      return x.status === "RUNNING";
    }).length;
    var left = rows.filter(function (x) {
      return x.status !== "DONE";
    }).length;
    tiles.appendChild(
      tile(
        "MAM research",
        count(
          rows.filter(function (x) {
            return x.status === "READY";
          }).length
        ) + " affordable",
        running + " running · " + left + " not done",
        false,
        hashFor("progress/mam")
      )
    );
  } else tiles.appendChild(tile("MAM research", "–", mam.error || "loading…"));
  var d = drives.data;
  tiles.appendChild(
    d
      ? tile("hard drives", count(d.drives.length) + " pending", d.spare + " unanalysed on hand", false, hashFor("progress/drives"))
      : tile("hard drives", "–", drives.error || "loading…")
  );
  var s = shards.data;
  tiles.appendChild(
    s
      ? tile("power shards", num(s.free) + " free", num(s.craftable) + " craftable · " + (s.measured ? count(s.committed) : "–") + " slotted", false, hashFor("progress/shards"))
      : tile("power shards", "–", shards.error || "loading…")
  );
  var l = sloops.data;
  tiles.appendChild(
    l
      ? tile("somersloops", num(l.free) + " free", (l.measured ? num(l.committed) : "–") + " slotted · " + num(l.owned) + " owned", false, hashFor("progress/sloops"))
      : tile("somersloops", "–", sloops.error || "loading…")
  );
  body.appendChild(tiles);
}

function subnav(body: HTMLElement, sub: string): void {
  var nav = make("nav", "dash-subnav");
  SUBS.forEach(function (s) {
    var a = link(s[0] ? "progress/" + s[0] : "progress", s[1], s[0] === sub ? "on" : "");
    if (s[0] === sub) a.setAttribute("aria-current", "page");
    nav.appendChild(a);
  });
  body.appendChild(nav);
}

export function renderProgress(body: HTMLElement, subject: string, point: PointButton): void {
  var sub = SUBS.some(function (s) {
    return s[0] === subject;
  })
    ? subject
    : "";
  nextUp(body);
  subnav(body, sub);
  if (sub === "mam") renderMam(body);
  else if (sub === "elevator") renderElevator(body);
  else if (sub === "drives") renderDrives(body);
  else if (sub === "shards") renderShards(body, point);
  else if (sub === "sloops") renderSloops(body, point);
  else renderMilestones(body);
}
