/* What the API sends, under the names the page uses -- one line per shape.
 *
 * THE POINT OF THIS FILE IS THAT IT HAS NO FIELDS IN IT. Every type below resolves to a
 * component of `api-schema.d.ts`, which is generated from the server's own `/openapi.json`;
 * a field spelled here would be a hand-written copy of a generated type.
 *
 * The indirection is what buys the page its own names. `components["schemas"]["…"]` at
 * fifteen call sites would put the generator's addressing scheme into every drawing module,
 * so a converted endpoint would be a rename across all of them; here it is one line in one
 * file. floors.ts predates this and reaches into the schema itself.
 */

import type { components } from "./api-schema";
import type { ApiError } from "./api";

type Schema = components["schemas"];

/** A response BODY: the server's own schema for it, plus the error branch any reply may
 *  carry instead. `ApiError` has only optional members, so this intersection is what makes a
 *  generated body assignable to `get<T extends ApiError>`. */
type Body<K extends keyof Schema> = Schema[K] & ApiError;

/* ----------------------------------------------------------------- /api/nodes */

export type NodeRow = Schema["NodeRow"];
export type NodesResponse = Body<"NodesResponse">;

/* --------------------------------------------------------------- /api/inspect */

export type Elevation = Schema["Elevation"];
export type NearestNode = Schema["NearestNode"];
export type InspectResponse = Body<"InspectResponse">;

/* --------------------------------------------------------------- /api/regions */

export type RegionsResponse = Body<"RegionsResponse">;

/* ---------------------------------------------------------------- /api/worlds */

/** The five keys the picker reads, which is all `/api/worlds` sends: a response model
 *  filters, so declaring this row kept the other eight save-header keys off the wire. */
export type SaveRow = Schema["SaveRow"];

/** One world: its saves, and the newest one's headline figures hoisted onto it.
 *  state.ts stores these (`state.worlds`) and re-exports nothing; import from here. */
export type WorldRow = Schema["WorldRow"];

export type WorldsResponse = Body<"WorldsResponse">;

/* --------------------------------------------------------------- /api/summary */

/** Its `header` is an open map; see `SummaryResponse` in routers/world.py. `player` is
 *  always sent and its three fields are what go null; markers.ts branches on that, and
 *  reads the branch off this type as `SummaryResponse["player"]`. */
export type SummaryResponse = Body<"SummaryResponse">;

/* -------------------------------------------- /api/machines and /api/structures */

export type PlacementRow = Schema["PlacementRow"];
export type MachinesResponse = Body<"MachinesResponse">;
export type StructureRow = Schema["StructureRow"];
export type StructuresResponse = Body<"StructuresResponse">;

/* ------------------------------------------------ /api/belts and /api/pipes */

export type BeltRow = Schema["BeltRow"];
export type AttachmentRow = Schema["AttachmentRow"];
export type BeltsResponse = Body<"BeltsResponse">;

export type PipeRow = Schema["PipeRow"];
export type PipesResponse = Body<"PipesResponse">;

/** The two closed vocabularies `/api/pipes` publishes, read off the row's own fields rather
 *  than restated. `PIPE_FLOW_BASIS` in routes.ts is a `Record` keyed by the second, so a
 *  fifth basis in `domain/world/flow.py` is a missing key and a compile error here. */
export type PipeDirection = PipeRow["direction"];
export type PipeFlowBasis = PipeRow["basis"];

/* --------------------------------------------------------------- /api/storage */

export type StoredItem = Schema["StoredItem"];

/** A container or a fluid buffer, discriminated by `kind`. The other kind's fields are
 *  ABSENT rather than null, so a reader branches on `kind` and gets the half it is looking
 *  at with every field required -- see the module docstring in routers/storage.py. */
export type StorageRow = Schema["StorageSolid"] | Schema["StorageFluid"];
export type StorageResponse = Body<"StorageResponse">;

/* ----------------------------------------------------------------- /api/power */

/** `cls` and `name` are nullable and the three coordinates are not: a pole is decoded out of
 *  an INTERNED table, so its class is an index into a legend that can point past the end,
 *  while `iter_power_poles` drops a row whose position will not read. */
export type PoleRow = Schema["PoleRow"];

/** Its ends are `[number, number, number]` rather than `number[]`, because the router spells
 *  them as tuples and typegen carries `prefixItems` through -- so `w.a_m[2]` needs no length
 *  guard. `from`/`to` are null for the endpoints that land on an actor no record list names.
 *  `a_pole`/`b_pole` are each end's pole as an index into the same payload's `poles`, and
 *  null wherever the end terminates at anything else. */
export type WireRow = Schema["WireRow"];

export type PowerResponse = Body<"PowerResponse">;

/* ------------------------------------------------------------- /api/crates */

export type CrateRow = Schema["CrateRow"];
export type CratesResponse = Body<"CratesResponse">;

/* ------------------------------------------------------------- /api/factories */

export type FactoryRow = Schema["FactoryRow"];
export type ProposalRow = Schema["ProposalRow"];
export type FactoriesResponse = Body<"FactoriesResponse">;

/* ------------------------------------- /api/factories/candidates and /api/labels */

export type CandidateRow = Schema["CandidateRow"];
export type Flow = Schema["Flow"];
export type CandidatesResponse = Body<"CandidatesResponse">;
export type NamedResponse = Body<"NamedResponse">;
export type ForgotResponse = Body<"ForgotResponse">;
export type RenamedResponse = Body<"RenamedResponse">;
export type LabelRefused = Body<"LabelRefusedResponse">;
export type GraphNode = Schema["GraphNode"];
export type FactoryGraphResponse = Body<"FactoryGraphResponse">;
export type MachineSpot = Schema["MachineSpot"];
export type FactoryMachinesResponse = Body<"FactoryMachinesResponse">;
export type AmendedResponse = Body<"AmendedResponse">;

/* ------------------------------------- /api/factories/health and /api/power/circuits */

export type MachineIssue = Schema["MachineIssue"];
export type FactoryHealthRow = Schema["FactoryHealthRow"];
export type FactoryHealthResponse = Body<"FactoryHealthResponse">;

export type Ledger = Schema["Ledger"];
export type StarvedGenerator = Schema["StarvedGenerator"];
export type MachineRef = Schema["MachineRef"];
export type CircuitRow = Schema["CircuitRow"];
export type CircuitsResponse = Body<"CircuitsResponse">;

/* ------------------------------------------------------- /api/progress/milestones */

export type MilestoneRow = Schema["MilestoneRow"];
export type MilestonesResponse = Body<"MilestonesResponse">;

/* ------------------------------------------- /api/progress/{mam,phase,shards,sloops,harddrives} */

export type MamRow = Schema["MamRow"];
export type MamResponse = Body<"MamResponse">;
export type PhaseRow = Schema["PhaseRow"];
export type PhaseResponse = Body<"PhaseResponse">;
export type ShardsResponse = Body<"ShardsResponse">;
export type SloopsResponse = Body<"SloopsResponse">;
export type DriveRow = Schema["DriveRow"];
export type HardDrivesResponse = Body<"HardDrivesResponse">;

/* ------------------------------------------------------------------ /api/stock */

export type StockPile = Schema["StockPile"];
export type StockPlace = Schema["StockPlace"];
export type StockResponse = Body<"StockResponse">;

/* ------------------------------------------------------ /api/gamedata and /api/search */

export type ItemRow = Schema["ItemRow"];
export type ItemsResponse = Body<"ItemsResponse">;
export type RecipeRow = Schema["RecipeRow"];
export type RecipesResponse = Body<"RecipesResponse">;
export type Rate = Schema["Rate"];
export type RecipeDetail = Body<"RecipeDetail">;
export type MakerRow = Schema["MakerRow"];
export type AlternatesResponse = Body<"AlternatesResponse">;
export type UnlockedResponse = Body<"UnlockedResponse">;
export type SearchResponse = Body<"SearchResponse">;

/* ------------------------------------------------------------------ /api/plans */

/** A stored plan's pad. Its coordinates are METRES already -- the siting is a statement the
 *  player typed, not a save reading -- so nothing on either side divides by 100. */
export type PlanSiting = Schema["PlanSiting"];
export type PlansResponse = Body<"PlansResponse">;
export type PlanIndexRow = Schema["PlanIndexRow"];

/* ------------------------------------------ /api/plans/{key}, /ops, /undo, /args */

export type PlanOpBody = Schema["PlanOpBody"];
export type ActorBody = Schema["ActorBody"];
export type CommitBody = Schema["CommitBody"];
export type PlanArgsBody = Schema["PlanArgsBody"];
export type PlanStateBody = Body<"PlanStateBody"> & { headroom_mw?: number | null };
export type PlanOpsResponse = Body<"PlanOpsResponse">;
export type PushedResponse = Body<"PushedResponse">;
export type ConflictBody = Schema["ConflictBody"];
export type OutdatedResponse = Body<"OutdatedResponse">;
export type AlreadyUndoneResponse = Body<"AlreadyUndoneResponse">;
export type NameTakenResponse = Body<"NameTakenResponse">;

/* ------------------------------------------ /api/plan/solve, /api/ui/focus, /api/activity */

export type SolveRate = Schema["SolveRate"];
export type SolveRow = Schema["SolveRow"];
export type SolveResponse = Body<"SolveResponse">;
export type FocusSelection = Schema["Selection"];
export type FocusResponse = Body<"FocusResponse">;
export type ActivityRow = Schema["ActivityRow"];
export type ActivityResponse = Body<"ActivityResponse">;

/* ------------------------------------ /api/plans/{key}/versions, /restore, /duplicate, /api/plan/delta */

export type VersionRow = Schema["VersionRow"];
export type VersionsResponse = Body<"VersionsResponse">;
export type DeltaRow = Schema["DeltaRow"];
export type DeltaResponse = Body<"DeltaResponse">;

/* ------------------------------ planner P3: graph, alternates, pins (docs/planner-p3_contract.md §5.2) */

export type PlanGraphNode = Schema["PlanGraphNode"];
export type PlanGraphEdge = Schema["PlanGraphEdge"];
export type PlanGraph = Schema["PlanGraph"];
export type RowChange = Schema["RowChange"];
export type ResultDelta = Schema["ResultDelta"];
export type SwapOption = Schema["SwapOption"];
export type PlanAlternatesResponse = Body<"PlanAlternatesResponse">;
export type PinRef = Schema["PinRef"];
export type PinRow = Schema["PinRow"];
export type PinsResponse = Body<"PinsResponse">;
export type PinCreated = Body<"PinCreated">;
export type PinDropped = Body<"PinDropped">;
export type PinStaleResponse = Body<"PinStaleResponse">;

/* ------------------------------ planner P4: track and asks (docs/planner-p4_contract.md §5.2) */

export interface TrackState {
  state: string;
  count: number;
}
export interface TrackMachine {
  instance: string;
  x_m: number | null;
  y_m: number | null;
}
export interface TrackTarget {
  node: string;
  x_m: number | null;
  y_m: number | null;
  m: number | null;
}
export interface TrackRow {
  id: string;
  kind: string;
  step: number;
  stages: number[];
  process: string;
  building: string;
  recipe_id: string | null;
  item: string | null;
  need: number;
  have: number;
  have_min: number | null;
  build: number;
  build_max: number | null;
  verb: string;
  count: number;
  reuse: number;
  running: number | null;
  states: TrackState[];
  new_building: boolean;
  note: string;
  delta_mw: number;
  act: TrackMachine[];
  targets: TrackTarget[];
  bbox_m: number[] | null;
  selectors: string;
}
export interface TrackStageRow {
  row: string;
  label: string;
  building: string;
  machines: number;
  total: number;
  built: number;
  built_max: number;
  running: number;
  states: TrackState[];
  draw_mw: number;
  generation_mw: number;
  to_build: number;
}
export interface TrackStage {
  index: number;
  machines: number;
  built: number;
  built_max: number;
  running: number;
  dark: number;
  complete: boolean;
  state: string;
  draw_mw: number;
  generation_mw: number;
  available_before: number;
  available_after: number;
  fill_s: number;
  waits_for_fill: boolean;
  states: TrackState[];
  rows: TrackStageRow[];
  bbox_m: number[] | null;
}
export interface TrackStartup {
  ok: boolean;
  headroom_mw: number;
  headroom_source: string;
  plant_draw_mw: number;
  plant_generation_mw: number;
  minimum_slice_mw: number;
  warnings: string[];
}
export interface TrackPower {
  generation_mw: number;
  draw_mw: number;
  headroom_mw: number;
  measured_headroom_mw: number;
  biomass: boolean;
}
export interface TrackCost {
  item: string;
  name: string;
  need: number;
  stock: number;
  short: number;
  lines: number;
}
export interface TrackNeighbour {
  label: string;
  count: number;
}
export interface TrackSiteRow {
  name: string;
  planned: number;
  standing: number;
}
export interface TrackSite {
  text: string;
  planned_total: number;
  standing_total: number;
  rows: TrackSiteRow[];
}
export type TrackResponse = ApiError & {
  key: string;
  rev: number;
  name: string;
  feasible: boolean;
  empty: boolean;
  headline: string;
  cause: string;
  save_id: string;
  age_note: string;
  plan_id: string;
  scope: string;
  scope_note: string;
  scope_error: string;
  drift_note: string;
  headroom_mw: number | null;
  current: number;
  count: number;
  partition_id: string;
  stage_text: string;
  to_build: number;
  to_build_max: number;
  actionable: number;
  unpause: number;
  setrecipe: number;
  rows: TrackRow[];
  stages: TrackStage[];
  startup: TrackStartup;
  power: TrackPower;
  cost: TrackCost[];
  neighbours: TrackNeighbour[];
  site: TrackSite | null;
  notes: string[];
  caveats: string[];
  monitored: number;
};
export interface Feeder {
  name: string;
  mw: number;
}
export type FeedersResponse = ApiError & { feeders: Feeder[]; text: string };
export interface AskAbout {
  kind: string;
  label: string;
  ref: string;
  plan?: string | null;
  rev?: number | null;
}
export interface AskRow {
  n: number;
  id: string;
  text: string;
  about: AskAbout;
  state: string;
  rev: number;
  created: number;
  seen: number | null;
  seen_by: string;
  answered: number | null;
  answered_by: string;
  plan_name: string | null;
  copy: string;
}
export type AsksResponse = ApiError & { version: number; asks: AskRow[] };
export interface AskCreateBody {
  text: string;
  about: AskAbout;
}
export interface AskDropBody {
  rev: number;
}
export type AskDropped = ApiError & { ok: boolean; n: number };
export type AskStaleResponse = ApiError & { error: string; stale: boolean; ask: AskRow };

/* ---------------------------------------------------------- /api/collectibles */

export type CollectibleRow = Schema["CollectibleRow"];
export type CollectiblesResponse = Body<"CollectiblesResponse">;

/* ------------------------------------------------------------------ both, and shared */

/** A region lookup, hung on a node row and answered for an inspected point. Declared once
 *  on the server too -- in `serial.py`, for the same reason it is one name here. */
export type Region = Schema["Region"];

/* ------------------------------------------------------------------ /api/trace */

export type TraceMachine = Schema["TraceMachine"];
export type TraceResponse = Body<"TraceResponse">;

/* ------------------------------------------- /api/factories/aspects and /sites */

export type AspectBalance = Schema["AspectBalance"];
export type AspectMachine = Schema["AspectMachine"];
export type AspectCount = Schema["AspectCount"];
export type AspectNode = Schema["AspectNode"];
export type AspectLink = Schema["AspectLink"];
export type FactoryAspectsResponse = Body<"FactoryAspectsResponse">;
export type SiteRow = Schema["SiteRow"];
export type SitesResponse = Body<"SitesResponse">;
export type FloorPlatform = Schema["FloorPlatform"];
export type FloorBand = Schema["FloorBand"];
export type FactoryFloorsResponse = Body<"FloorsResponse">;
