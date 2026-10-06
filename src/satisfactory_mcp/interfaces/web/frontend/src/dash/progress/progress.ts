/* The dashboard's Progress section: milestones, MAM, Space Elevator, hard drives, shards and
 * somersloops, facts only. See docs/frontend_vision.md, "Phase 4: Progress". */

import { make } from "../../kit/dom";
import { appendNote, checkbox, error, heading, link, loading, subTabs, table, tile } from "../../kit/dashkit";
import { flow, formatNumber, pct, phaseText } from "../../kit/format";
import { loadOne } from "../../app/load";
import { hashFor } from "../../map/map";
import { go } from "../../app/nav";
import { registerFetch } from "../../app/registry";
import { onSetting, setSetting, settingOn, claimSpoilerNotice } from "../../app/settings";
import { offer } from "../../kit/toast";

import type { ApiError, ApiUrl } from "../../api/client";
import type { Column, SortState } from "../../kit/dashkit";
import type { Fetcher } from "../../app/registry";
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
} from "../../api/shapes";

interface Slot<T> {
  data: T | null;
  failed: boolean;
  path: ApiUrl;
  label: string;
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

var PLACES: Record<string, string> = {
  carried: "carried",
  storage: "in storage containers",
  depot: "in the Dimensional Depot",
};

var STOCK = "checked against spendable stock: carried, storage containers and the Dimensional Depot";

var listeners: Array<() => void> = [];

var showDone = false;

var showMamDone = false;

var milestoneSort: SortState = { key: "tier", desc: false };
var mamSort: SortState = { key: "tree", desc: false };
var shardSort: SortState = { key: "clock", desc: true };

function changed(): void {
  listeners.forEach(function (listener) {
    listener();
  });
}

export function onProgress(listener: () => void): void {
  listeners.push(listener);
}

function slot<T>(path: ApiUrl, label: string): Slot<T> {
  return { data: null, failed: false, path: path, label: label };
}

function fetcher<T extends ApiError>(held: Slot<T>, rank: number): Fetcher<T> {
  return {
    wave: "live",
    rank: rank,
    path: held.path,
    label: held.label,
    clears: [],
    refilters: false,
    draw: function (data) {
      held.data = data;
      held.failed = false;
      changed();
    },
    failed: function () {
      held.data = null;
      held.failed = true;
      changed();
    },
  };
}

var milestones = slot<MilestonesResponse>("/api/progress/milestones", "milestones");
var mam = slot<MamResponse>("/api/progress/mam", "MAM research");
var phase = slot<PhaseResponse>("/api/progress/phase", "space elevator");
var drives = slot<HardDrivesResponse>("/api/progress/harddrives", "hard drives");
var shards = slot<ShardsResponse>("/api/progress/shards", "power shards");
var sloops = slot<SloopsResponse>("/api/progress/sloops", "somersloops");

registerFetch(fetcher(milestones, 60));
registerFetch(fetcher(mam, 61));
registerFetch(fetcher(phase, 62));
registerFetch(fetcher(drives, 63));
registerFetch(fetcher(shards, 64));
registerFetch(fetcher(sloops, 65));

onSetting(changed);

if (claimSpoilerNotice()) {
  offer("upcoming milestones, research and locked recipes are now hidden until the save reaches them", "show them", function () {
    setSetting("spoilers", true);
  });
}

function waiting<T>(body: HTMLElement, held: Slot<T>): void {
  if (held.failed) {
    error(body, held.label, null, function () {
      loadOne(held.path);
    });
  } else loading(body, held.label);
}

function visible<T extends { spoiler: boolean }>(rows: T[]): T[] {
  if (settingOn("spoilers")) return rows;
  return rows.filter(function (r) {
    return !r.spoiler;
  });
}

function amounts(rows: { name: string; amount: number }[]): string {
  return (
    rows
      .map(function (r) {
        return formatNumber(r.amount) + " " + r.name;
      })
      .join(", ") || "–"
  );
}

function placed(rows: { name: string; amount: number }[]): string {
  return rows
    .map(function (r) {
      return formatNumber(r.amount) + " " + (PLACES[r.name] || r.name);
    })
    .join(", ");
}

function spoilerNote(body: HTMLElement, text: string): void {
  var hid = make("p", "dash-note", text + "; ");
  hid.appendChild(link("settings", "Settings"));
  hid.appendChild(document.createTextNode(" can show them"));
  body.appendChild(hid);
}

function milestoneStatus(m: MilestoneRow): string {
  if (m.opens_at !== null && m.status !== "DONE") return "locked (phase " + m.opens_at + ")";
  if (m.status === "DONE") return "done";
  if (m.status === "READY") return "affordable";
  if (m.status === "BLOCKED") return "needs " + m.blocked_by.join(", ") + " first";
  return m.status.toLowerCase();
}

function affordable(rows: MilestoneRow[]): number {
  return rows.filter(function (m) {
    return milestoneStatus(m) === "affordable";
  }).length;
}

export function readyMilestones(): number | null {
  return milestones.data ? affordable(visible(milestones.data.milestones)) : null;
}

export function milestoneTile(): HTMLElement {
  if (!milestones.data) {
    return tile("milestones", "–", milestones.failed ? "could not be read" : "loading…", false, hashFor("progress"));
  }
  var ready = affordable(visible(milestones.data.milestones));
  var top = milestones.data.highest_complete_tier;
  return tile(
    "milestones",
    top === null ? "no tier complete" : "tier " + top + " complete",
    (phaseText(milestones.data.game_phase) || "no phase in this save") + " · " + ready + " affordable",
    false,
    hashFor("progress")
  );
}

var MILESTONE_COLUMNS: Column<MilestoneRow>[] = [
  {
    key: "name",
    label: "milestone",
    sort: function (m) {
      return m.name;
    },
    render: function (m) {
      return m.name;
    },
  },
  {
    key: "tier",
    label: "tier",
    align: "right",
    sort: function (m) {
      return m.tier * 1000 + (m.status === "DONE" ? 0 : 1);
    },
    render: function (m) {
      return m.tier;
    },
  },
  {
    key: "status",
    label: "status",
    sort: milestoneStatus,
    render: milestoneStatus,
    tone: function (m) {
      var s = milestoneStatus(m);
      return s === "affordable" ? "ok" : s.indexOf("locked") === 0 ? "dash-muted" : "";
    },
  },
  {
    key: "cost",
    label: "cost",
    title: "cost is " + STOCK,
    render: function (m) {
      return amounts(m.cost);
    },
  },
  {
    key: "short",
    label: "short by",
    render: function (m) {
      return m.status === "DONE" ? "" : amounts(m.short);
    },
    tone: function (m) {
      return m.short.length && m.status !== "DONE" && m.opens_at === null ? "bad" : "";
    },
  },
  {
    key: "recipes",
    label: "recipes",
    align: "right",
    title: "recipes the milestone newly grants",
    sort: function (m) {
      return m.unlocks;
    },
    render: function (m) {
      return m.unlocks || "";
    },
  },
];

function renderMilestones(body: HTMLElement): void {
  var full = milestones.data;
  if (!full) {
    waiting(body, milestones);
    return;
  }
  var rows = visible(full.milestones);
  var tiers = visible(full.tiers);
  var counts: Record<string, number> = { affordable: 0, short: 0, done: 0, locked: 0 };
  rows.forEach(function (m) {
    var s = milestoneStatus(m);
    var key = s.indexOf("locked") === 0 ? "locked" : s.indexOf("blocked") === 0 ? "short" : s;
    counts[key] = (counts[key] || 0) + 1;
  });
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(
    tile(
      "highest complete tier",
      full.highest_complete_tier === null ? "none" : String(full.highest_complete_tier),
      phaseText(full.game_phase) || "no phase in this save"
    )
  );
  tiles.appendChild(tile("affordable now", formatNumber(counts.affordable!), "bill covered by spendable stock"));
  tiles.appendChild(tile("short", formatNumber(counts.short!), counts.locked ? counts.locked + " more locked behind a phase" : "stock does not cover the bill"));
  tiles.appendChild(tile("done", formatNumber(counts.done!), "of " + rows.length + " milestones"));
  body.appendChild(tiles);

  var strip = make("div", "dash-tiers");
  tiers.forEach(function (t) {
    strip.appendChild(tally("tier " + t.tier, t.done, t.total));
  });
  body.appendChild(strip);
  if (tiers.length < full.tiers.length) spoilerNote(body, "tiers the HUB has not opened yet are hidden");

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
  var listed = rows.filter(function (m) {
    return showDone || m.status !== "DONE";
  });
  if (!listed.length) appendNote(card, "every milestone shown here is done");
  else card.appendChild(table(MILESTONE_COLUMNS, listed, { sort: milestoneSort, caption: "milestones" }));
  body.appendChild(card);
}

function tally(label: string, done: number, total: number): HTMLElement {
  var box = make("div", "dash-tier");
  box.appendChild(make("span", "dash-tier-k", label));
  var bar = make("div", "dash-hbar");
  var fill = make("span", "dash-mix-ok");
  fill.style.width = (total ? (done / total) * 100 : 0) + "%";
  bar.appendChild(fill);
  box.appendChild(bar);
  box.appendChild(make("span", "dash-tier-v", done + "/" + total));
  return box;
}

function duration(seconds: number): string {
  var minutes = Math.round(seconds / 60);
  return minutes < 60 ? minutes + " min" : Math.floor(minutes / 60) + " h " + (minutes % 60) + " min";
}

function mamStatus(r: MamRow): string {
  if (r.status === "RUNNING") return "running, " + duration(r.running_s || 0) + " left at save";
  if (r.status === "BLOCKED") return "needs " + r.blocked_by.join(", ") + " first";
  if (r.status === "TREE SHUT") return "tree not open";
  if (r.status === "READY") return "affordable";
  if (r.status === "DONE") return "done";
  return r.status.toLowerCase();
}

function owes(r: MamRow): boolean {
  return r.status !== "DONE" && r.status !== "RUNNING";
}

var MAM_COLUMNS: Column<MamRow>[] = [
  {
    key: "name",
    label: "research",
    sort: function (r) {
      return r.name;
    },
    render: function (r) {
      return r.name;
    },
  },
  {
    key: "tree",
    label: "tree",
    sort: function (r) {
      return (r.tree || "~") + "\u0000" + r.name;
    },
    render: function (r) {
      return r.tree || "–";
    },
  },
  {
    key: "status",
    label: "status",
    title: "running: paid for and under way; the time left is what the save recorded and does not run down while the game is closed",
    sort: mamStatus,
    render: mamStatus,
    tone: function (r) {
      return r.status === "READY" ? "ok" : r.status === "TREE SHUT" ? "dash-muted" : "";
    },
  },
  {
    key: "cost",
    label: "cost",
    title: "cost is " + STOCK,
    render: function (r) {
      return amounts(r.cost);
    },
  },
  {
    key: "short",
    label: "short by",
    render: function (r) {
      return owes(r) ? amounts(r.short) : "";
    },
    tone: function (r) {
      return r.short.length && owes(r) ? "bad" : "";
    },
  },
  {
    key: "recipes",
    label: "recipes",
    align: "right",
    title: "recipes the node newly grants",
    sort: function (r) {
      return r.unlocks;
    },
    render: function (r) {
      return r.unlocks || "";
    },
  },
];

function renderMam(body: HTMLElement): void {
  var data = mam.data;
  if (!data) {
    waiting(body, mam);
    return;
  }
  var rows = visible(data.research);
  var hidden = data.research.length - rows.length;
  var caps = visible(data.capabilities);
  if (caps.length) {
    appendNote(
      body,
      caps
        .map(function (c) {
          return (c.schematic_name || c.capability) + ": " + (c.researched ? "researched" : "not researched");
        })
        .join(" · ")
    );
  }
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
  if (hidden) spoilerNote(body, hidden + " research nodes in trees not opened yet are hidden");
  if (!data.knows_trees) appendNote(body, "this save predates the list of opened trees, so a node in an unopened tree reads as available");

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
  var listed = rows.filter(function (r) {
    return showMamDone || r.status !== "DONE";
  });
  if (!listed.length) appendNote(card, "every research node shown here is done");
  else card.appendChild(table(MAM_COLUMNS, listed, { sort: mamSort, caption: "MAM research" }));
  body.appendChild(card);
}

function phaseNumber(p: string | null): number | null {
  var match = /_(\d+)$/.exec(p || "");
  return match ? +match[1]! : null;
}

function phaseStatus(row: PhaseRow, data: PhaseResponse): string {
  var n = phaseNumber(row.phase);
  var current = phaseNumber(data.current_phase);
  var target = phaseNumber(data.target_phase);
  if (n === null || (current === null && target === null)) return "–";
  if (current !== null && n <= current) return "delivered";
  if (target !== null && n === target) return "next";
  return "not started";
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

type Part = PhaseRow["outstanding"][number];

var PART_COLUMNS: Column<Part>[] = [
  {
    key: "name",
    label: "part",
    render: function (i) {
      return i.name;
    },
  },
  {
    key: "amount",
    label: "needed",
    align: "right",
    render: function (i) {
      return formatNumber(i.amount);
    },
  },
  {
    key: "have",
    label: "have",
    align: "right",
    title: "spendable stock: carried, storage containers and the Dimensional Depot; parts in machines and on belts are not counted",
    render: function (i) {
      return formatNumber(i.have);
    },
  },
  {
    key: "short",
    label: "short by",
    align: "right",
    render: function (i) {
      return i.short > 0 ? formatNumber(i.short) : "";
    },
    tone: function (i) {
      return i.short > 0 ? "bad" : "";
    },
  },
];

function renderElevator(body: HTMLElement): void {
  var data = phase.data;
  if (!data) {
    waiting(body, phase);
    return;
  }
  var target = targetRow(data);
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("current phase", phaseText(data.current_phase) || "–", data.current_phase ? "the last phase delivered" : "no phase in this save"));
  tiles.appendChild(tile("target phase", phaseText(data.target_phase) || "–", data.target_phase ? "where deliveries go" : "no phase in this save"));
  var short = target ? shortParts(target) : 0;
  tiles.appendChild(
    tile(
      "deliverable now",
      data.deliverable === null ? "–" : data.deliverable ? "yes" : "no",
      data.deliverable === null ? "no record for the target phase" : data.deliverable ? "every part is in stock" : short + " of " + target!.outstanding.length + " parts short"
    )
  );
  body.appendChild(tiles);

  if (data.target_phase) {
    var card = make("section", "dash-card");
    heading(card, "target: " + phaseText(data.target_phase));
    if (!target) appendNote(card, "the save has no per-phase record for the target phase");
    else if (!target.outstanding.length) appendNote(card, "nothing outstanding on the target phase");
    else card.appendChild(table(PART_COLUMNS, target.outstanding, { caption: "parts owed to the target phase" }));
    var paid = "delivered so far: " + (data.delivered.length ? amounts(data.delivered) : "nothing");
    appendNote(card, target && target.trust === "derived" ? paid + "; the amounts owed are a lower bound" : paid);
    body.appendChild(card);
  }

  var named = data.phases.filter(function (p) {
    return !!p.phase;
  });
  var rows = visible(named);
  var all = make("section", "dash-card");
  heading(all, "phases");
  if (rows.length) {
    all.appendChild(
      table(
        [
          {
            key: "phase",
            label: "phase",
            render: function (r: PhaseRow) {
              return phaseText(r.phase) || "–";
            },
          },
          {
            key: "status",
            label: "status",
            render: function (r: PhaseRow) {
              return phaseStatus(r, data!);
            },
            tone: function (r: PhaseRow) {
              return phaseStatus(r, data!) === "not started" ? "dash-muted" : "";
            },
          },
          {
            key: "owed",
            label: "parts still owed",
            render: function (r: PhaseRow) {
              var s = phaseStatus(r, data!);
              return s === "next" || (s === "not started" && r.trust === "usable") ? amounts(r.outstanding) : "–";
            },
          },
        ],
        rows,
        { caption: "Space Elevator phases" }
      )
    );
  } else if (rows.length === named.length) appendNote(all, "this save records no Space Elevator phase");
  var hidden = named.length - rows.length;
  if (hidden) spoilerNote(all, data.target_phase ? hidden + " phases past the target are hidden" : "the save names no target phase, so its " + hidden + " phase records are hidden");
  body.appendChild(all);
}

type Option = DriveRow["options"][number];

interface OptionRow {
  drive: DriveRow;
  option: Option;
  first: boolean;
  last: boolean;
}

function grants(option: Option): string {
  if (option.slots) return "+" + option.slots + " inventory slots";
  if (!option.recipes.length) return "nothing new: its recipes are already unlocked";
  return option.recipes
    .map(function (r) {
      var made = r.products
        .map(function (f) {
          return flow(f.name, f.amount, 2);
        })
        .join(" + ");
      return (made || r.name) + (r.machine ? " · " + r.machine : "");
    })
    .join("; ");
}

var DRIVE_COLUMNS: Column<OptionRow>[] = [
  {
    key: "drive",
    label: "drive",
    align: "right",
    render: function (o) {
      return o.first ? (o.drive.hard_drive_id === null ? "?" : o.drive.hard_drive_id) : "";
    },
  },
  {
    key: "rerolls",
    label: "rerolls",
    align: "right",
    render: function (o) {
      return o.first ? o.drive.rerolls_left : "";
    },
  },
  {
    key: "option",
    label: "option",
    render: function (o) {
      return o.option.name;
    },
  },
  {
    key: "grants",
    label: "grants",
    title: "rates are per machine at 100%",
    render: function (o) {
      return grants(o.option);
    },
  },
];

function renderDrives(body: HTMLElement): void {
  var data = drives.data;
  if (!data) {
    waiting(body, drives);
    return;
  }
  var rerolls = 0;
  data.drives.forEach(function (d) {
    rerolls += d.rerolls_left;
  });
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("pending choices", formatNumber(data.drives.length), "analysed drives waiting for a pick"));
  tiles.appendChild(tile("rerolls left", formatNumber(rerolls), "across the pending drives"));
  tiles.appendChild(tile("unanalysed", formatNumber(data.spare), "hard drives on hand"));
  if (data.last_used !== null) tiles.appendChild(tile("last drive analysed", "drive " + data.last_used, "the newest drive the MAM has analysed"));
  body.appendChild(tiles);

  var card = make("section", "dash-card");
  heading(card, "pending hard drives");
  if (!data.drives.length) appendNote(card, "no hard drive is waiting for a pick");
  else {
    var rows: OptionRow[] = [];
    data.drives.forEach(function (d) {
      d.options.forEach(function (o, i) {
        rows.push({ drive: d, option: o, first: i === 0, last: i === d.options.length - 1 });
      });
    });
    card.appendChild(
      table(DRIVE_COLUMNS, rows, {
        caption: "pending hard drives",
        rowClass: function (o) {
          return o.last ? "" : "dash-lead";
        },
      })
    );
    appendNote(card, "the option not picked returns to the pool; only the drive is spent");
  }
  body.appendChild(card);
}

type Holder = ShardsResponse["holders"][number];

function shardColumns(point: PointButton): Column<Holder>[] {
  return [
    {
      key: "name",
      label: "building",
      sort: function (h) {
        return h.name || "";
      },
      render: function (h) {
        return h.name || "–";
      },
    },
    {
      key: "clock",
      label: "clock",
      align: "right",
      sort: function (h) {
        return h.clock;
      },
      render: function (h) {
        return formatNumber(h.clock * 100) + "%";
      },
    },
    {
      key: "slotted",
      label: "slotted",
      align: "right",
      title: "read from the building's shard slots",
      sort: function (h) {
        return h.slotted;
      },
      render: function (h) {
        return h.slotted;
      },
    },
    {
      key: "needed",
      label: "needed",
      align: "right",
      title: "what its clock requires",
      sort: function (h) {
        return h.needed;
      },
      render: function (h) {
        return h.needed;
      },
    },
    {
      key: "idle",
      label: "idle",
      align: "right",
      title: "slotted above what the clock needs",
      sort: function (h) {
        return h.idle;
      },
      render: function (h) {
        return h.idle || "";
      },
    },
    {
      key: "map",
      label: "",
      align: "right",
      render: function (h) {
        return point(h);
      },
    },
  ];
}

function renderShards(body: HTMLElement, point: PointButton): void {
  var data = shards.data;
  if (!data) {
    waiting(body, shards);
    return;
  }
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("free", formatNumber(data.free), "crafted shards in stock"));
  tiles.appendChild(tile("craftable", formatNumber(data.craftable), "from slugs on hand, once crafted"));
  tiles.appendChild(tile("slotted", data.measured ? formatNumber(data.committed) : "–", data.measured ? data.idle + " of them above what the clock needs" : "this save does not record slots"));
  tiles.appendChild(tile("owned", formatNumber(data.owned), "free plus slotted"));
  body.appendChild(tiles);
  appendNote(
    body,
    "a shard adds " +
      pct(data.per_shard) +
      " max clock and a building takes " +
      data.slots_per_building +
      ", so the ceiling is " +
      pct(data.max_clock)
  );
  data.by_place.forEach(function (p) {
    appendNote(body, (PLACES[p.place] || p.place) + ": " + amounts(p.items));
  });

  if (data.slugs.length) {
    var slugCard = make("section", "dash-card");
    heading(slugCard, "power slugs");
    slugCard.appendChild(
      table(
        [
          {
            key: "name",
            label: "slug",
            render: function (s: ShardsResponse["slugs"][number]) {
              return s.name;
            },
          },
          {
            key: "held",
            label: "held",
            align: "right",
            render: function (s: ShardsResponse["slugs"][number]) {
              return formatNumber(s.held);
            },
          },
          {
            key: "each",
            label: "shards each",
            align: "right",
            render: function (s: ShardsResponse["slugs"][number]) {
              return formatNumber(s.each);
            },
          },
          {
            key: "shards",
            label: "shards",
            align: "right",
            title: "craftable is potential, not free: crafting is a manual step",
            render: function (s: ShardsResponse["slugs"][number]) {
              return formatNumber(s.shards);
            },
          },
        ],
        data.slugs,
        { caption: "power slugs" }
      )
    );
    body.appendChild(slugCard);
  }
  var card = make("section", "dash-card");
  heading(card, "overclocked buildings (" + data.holders.length + ")");
  if (!data.holders.length) appendNote(card, "no building holds a shard or runs above 100%");
  else card.appendChild(table(shardColumns(point), data.holders, { sort: shardSort, caption: "overclocked buildings" }));
  body.appendChild(card);
}

type Amplified = SloopsResponse["holders"][number];

function disagrees(h: Amplified): boolean {
  return h.boost !== null && h.boost_in_save !== null && Math.abs(h.boost - h.boost_in_save) > 1e-6;
}

function renderSloops(body: HTMLElement, point: PointButton): void {
  var data = sloops.data;
  if (!data) {
    waiting(body, sloops);
    return;
  }
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("free", formatNumber(data.free), data.by_place.length ? placed(data.by_place) : "none in stock"));
  tiles.appendChild(tile("slotted", data.measured ? formatNumber(data.committed) : "–", data.measured ? "in production machines" : "this save does not record slots"));
  tiles.appendChild(tile("owned", formatNumber(data.owned), "free plus slotted"));
  if (data.mercer_spheres || settingOn("spoilers")) tiles.appendChild(tile("mercer spheres", formatNumber(data.mercer_spheres), "counted apart, never added in"));
  body.appendChild(tiles);
  if (!data.amplifier_researched && data.amplifier_spoiler && !settingOn("spoilers")) {
    appendNote(body, "no somersloop can go into a machine yet: the research for it is still locked");
  } else if (!data.amplifier_researched) {
    appendNote(
      body,
      (data.amplifier_research || "Production Amplifier") +
        " is not researched, so no somersloop can go into a machine yet. MAM cost: " +
        amounts(data.amplifier_cost)
    );
  }
  var card = make("section", "dash-card");
  heading(card, "amplified machines (" + data.holders.length + ")");
  if (!data.holders.length) appendNote(card, "no machine holds a somersloop");
  else {
    card.appendChild(
      table(
        [
          {
            key: "name",
            label: "building",
            render: function (h: Amplified) {
              return h.name;
            },
          },
          {
            key: "sloops",
            label: "somersloops",
            align: "right",
            render: function (h: Amplified) {
              return formatNumber(h.sloops);
            },
          },
          {
            key: "boost",
            label: "boost",
            align: "right",
            title: "what the plan model says the slots are worth",
            render: function (h: Amplified) {
              return h.boost === null ? "–" : formatNumber(h.boost, 2) + "×";
            },
          },
          {
            key: "saved",
            label: "boost in save",
            align: "right",
            title: "the multiplier the save carries",
            render: function (h: Amplified) {
              return h.boost_in_save === null ? "–" : formatNumber(h.boost_in_save, 2) + "×";
            },
            tone: function (h: Amplified) {
              return disagrees(h) ? "bad" : "";
            },
          },
          {
            key: "map",
            label: "",
            align: "right",
            render: function (h: Amplified) {
              return point(h);
            },
          },
        ],
        data.holders,
        { caption: "amplified machines" }
      )
    );
    var off = data.holders.filter(disagrees).length;
    if (off) appendNote(card, "boost and boost in save disagree on " + off + " machines");
  }
  body.appendChild(card);
}

function nextUp(body: HTMLElement): void {
  var line = make("p", "dash-note");
  var parts: HTMLElement[] = [];
  var add = function (dash: string, label: string, text: string | null): void {
    if (text === null) return;
    var span = make("span", "", label + ": ");
    span.appendChild(link(dash, text));
    parts.push(span);
  };
  var p = phase.data;
  var target = p ? targetRow(p) : null;
  add("progress/elevator", "space elevator", p ? (p.deliverable === null ? "no target" : p.deliverable ? "deliverable" : shortParts(target!) + " parts short") : null);
  var r = mam.data;
  if (r) {
    var rows = visible(r.research);
    var ready = rows.filter(function (x) {
      return x.status === "READY";
    }).length;
    var running = rows.filter(function (x) {
      return x.status === "RUNNING";
    }).length;
    add("progress/mam", "MAM", ready + " affordable" + (running ? ", " + running + " running" : ""));
  }
  var d = drives.data;
  add("progress/drives", "hard drives", d ? d.drives.length + " pending" : null);
  var s = shards.data;
  add("progress/shards", "power shards", s ? formatNumber(s.free) + " free" : null);
  var l = sloops.data;
  add("progress/sloops", "somersloops", l ? formatNumber(l.free) + " free" : null);
  if (!parts.length) return;
  parts.forEach(function (part, i) {
    if (i) line.appendChild(document.createTextNode(" · "));
    line.appendChild(part);
  });
  body.appendChild(line);
}

export function renderProgress(body: HTMLElement, subject: string, point: PointButton): void {
  var sub = SUBS.some(function (s) {
    return s[0] === subject;
  })
    ? subject
    : "";
  body.appendChild(
    subTabs(
      SUBS.map(function (s) {
        return { id: s[0], label: s[1], href: hashFor(s[0] ? "progress/" + s[0] : "progress") };
      }),
      sub,
      function (id) {
        go(id ? "progress/" + id : "progress");
      },
      "Progress sections"
    )
  );
  if (sub === "mam") renderMam(body);
  else if (sub === "elevator") renderElevator(body);
  else if (sub === "drives") renderDrives(body);
  else if (sub === "shards") renderShards(body, point);
  else if (sub === "sloops") renderSloops(body, point);
  else {
    nextUp(body);
    renderMilestones(body);
  }
}
