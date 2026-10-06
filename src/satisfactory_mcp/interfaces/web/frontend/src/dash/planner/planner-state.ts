/* The open plan as the page holds it: the bench every planner view reads, its listeners, and the
 * words the views share. Reads are planner-reads.ts, writes planner-writes.ts. See
 * docs/planner_slice_contract.md §12. */

import { get } from "../../api/client";
import { createListeners } from "../../app/listeners";
import { state } from "../../app/state";
import { WORDS } from "../../kit/words";

import type {
  ActivityRow,
  ActorBody,
  AlreadyUndoneResponse,
  DeltaResponse,
  FeedersResponse,
  FocusSelection,
  ItemsResponse,
  NameTakenResponse,
  OutdatedResponse,
  PlanAlternatesResponse,
  PlanOpBody,
  PlanStateBody,
  SolveResponse,
  TrackResponse,
  VersionsResponse,
} from "../../api/shapes";

export type Op = PlanOpBody & { op: string };

export interface PlansEvent {
  world: string;
  key: string;
  name: string;
  rev: number;
  from_rev: number;
  actors: ActorBody[];
  text: string;
  ts: number;
  forgotten: boolean;
}

export type ActivityEvent = Pick<ActivityRow, "id" | "ts" | "actor" | "kind" | "plan" | "rev" | "text" | "args"> & {
  world: string;
  name?: string | null;
};

/** A version someone else made since this page opened the plan. */
export interface OthersCommit {
  rev: number;
  text: string;
  who: string;
  undone: boolean;
}

/** One field of a refused push; the chips of one refusal share `gesture` and go together. */
export interface ConflictChip {
  id: number;
  gesture: number;
  field: string;
  who: string;
  text: string;
  retry: () => void;
}

export type Refusal = Partial<OutdatedResponse & AlreadyUndoneResponse & NameTakenResponse>;

export type ResultTab = "build list" | "graph" | "track" | "site";

export interface Partition {
  partition_id: string;
  current: number;
  count: number;
  rev: number;
  save_id: string;
  headroom_mw: number | null;
  headroom_source: string;
}

export interface FeedersView {
  data: FeedersResponse | null;
  error: string;
  busy: boolean;
}

export interface TrackView {
  data: TrackResponse | null;
  error: string;
  seq: number;
  asked: number;
  stage: number;
  notice: string;
  feeders: FeedersView | null;
}

export function freshTrack(): TrackView {
  return { data: null, error: "", seq: 0, asked: 0, stage: 0, notice: "", feeders: null };
}

export interface AlternatesView {
  item: string;
  data: PlanAlternatesResponse | null;
  error: string;
  asked: number;
  opener: string;
  enter: boolean;
}

/* Everything that belongs to one open plan; the tab, the versions toggle and the stage
 * partitions outlive a switch to another plan. */
function freshBench(key: string) {
  return {
    world: state.world,
    key: key,
    plan: null as PlanStateBody | null,
    error: "",
    missing: false,
    gone: false,
    lastChange: null as { who: string; ts: number } | null,
    result: null as SolveResponse | null,
    resultRev: 0,
    lastFeasible: null as { rev: number; data: SolveResponse } | null,
    solvingRev: 0,
    solveError: "",
    othersCommits: [] as OthersCommit[],
    conflictChips: [] as ConflictChip[],
    undoStack: [] as number[],
    redoStack: [] as { target: number; by: number }[],
    ownRevs: {} as Record<number, boolean>,
    selection: null as FocusSelection | null,
    picked: "",
    alternates: null as AlternatesView | null,
    alternatesCloseGoesBack: false,
    chatChangedRows: {} as Record<string, true>,
    versions: null as VersionsResponse | null,
    versionsError: "",
    viewedRev: 0,
    viewedPlan: null as PlanStateBody | null,
    viewedResult: null as SolveResponse | null,
    viewedError: "",
    viewedDelta: null as DeltaResponse | null,
    othersDelta: null as DeltaResponse | null,
    track: freshTrack(),
  };
}

export var bench = Object.assign(
  { tab: "build list" as ResultTab, versionsOpen: false, partitionByPlan: {} as Record<string, Partition> },
  freshBench("")
);

export function resetBench(key: string): void {
  Object.assign(bench, freshBench(key));
}

export var pendingFocus = { ctl: "", until: 0 };

export var inbox = { card: null as ActivityEvent | null };

export var NAME_MAX = 80;
export var NOTES_MAX = 2000;

var benchListeners = createListeners();

export function onBench(listener: () => void): void {
  benchListeners.on(listener);
}

export function changed(): void {
  benchListeners.emit();
}

export function actorWord(actor: ActorBody): string {
  if (actor.kind === "page") return WORDS.actorYou;
  if (actor.kind === "chat") return WORDS.actorChat;
  return actor.display;
}

export function commitWords(text: string): string {
  return text.replace(/^v\d+ [^:]*: /, "");
}

/** An item or recipe id as the open plan names it, or the id itself. */
export function displayName(id: string): string {
  return (bench.plan && bench.plan.names[id]) || id;
}

var itemNames: string[] = [];
var itemsAsked = false;

export function loadItems(): void {
  if (itemsAsked) return;
  itemsAsked = true;
  get<ItemsResponse>("/api/gamedata/items?limit=1000")
    .then(function (data) {
      itemNames = data.items.map(function (item) {
        return item.name;
      });
      changed();
    })
    .catch(function () {
      itemsAsked = false;
    });
}

export function itemList(): HTMLDataListElement {
  const list = document.createElement("datalist");
  list.id = "plan-items";
  itemNames.forEach(function (name) {
    const option = document.createElement("option");
    option.value = name;
    list.appendChild(option);
  });
  return list;
}

export function knownItem(text: string): string | null {
  if (!itemNames.length) return text;
  const want = text.trim().toLowerCase();
  const hit = itemNames.filter(function (name) {
    return name.toLowerCase() === want;
  })[0];
  return hit || null;
}
