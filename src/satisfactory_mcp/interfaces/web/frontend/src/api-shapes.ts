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

export type NodeRow = Schema["NodeRow"] & { spoiler: boolean };
export type NodesResponse = Omit<Body<"NodesResponse">, "nodes"> & { nodes: NodeRow[] };

/* --------------------------------------------------------------- /api/inspect */

export type Elevation = Schema["Elevation"];
export type NearestNode = Schema["NearestNode"] & { spoiler: boolean };
export type InspectResponse = Omit<Body<"InspectResponse">, "nearest"> & {
  nearest: NearestNode[];
  grid: string;
  direction: string;
  conduits: ConduitCount | null;
  fields: FoundField[];
  pickups: NearPickup[];
  stale: TableAge[];
};

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
export type PlanStateBody = Body<"PlanStateBody">;
export type PlanOpsResponse = Body<"PlanOpsResponse">;
export type PushedResponse = Body<"PushedResponse">;
export type ConflictBody = Schema["ConflictBody"];
export type OutdatedResponse = Body<"OutdatedResponse">;
export type AlreadyUndoneResponse = Body<"AlreadyUndoneResponse">;
export type NameTakenResponse = Body<"NameTakenResponse">;

/* ------------------------------------------ /api/plan/solve, /api/ui/focus, /api/activity */

export type SolveRow = Schema["SolveRow"];
export type SolveRate = Schema["SolveRate"];
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

/* ---------------------------------------------------------- /api/collectibles */

export type CollectibleRow = Schema["CollectibleRow"] & { spoiler: boolean };
export type CollectiblesResponse = Omit<Body<"CollectiblesResponse">, "rows"> & {
  rows: CollectibleRow[];
  census: CensusRow[];
  found: string[];
  hidden_spoilers: number;
  stale: TableAge | null;
};

/* ------------------------------------------------------------------ /api/world/*
 * Hand-written from docs/world-finders_contract.md §3.2 until the schema is regenerated.
 * RankedSite(s) are the contract's SiteRow and SitesResponse; the factory sites own those names. */

export interface TableAge {
  table: "nodes" | "collectibles";
  behind: boolean;
  gap: string | null;
  moved: number;
  unjoinable: number;
  observed_from: string | null;
  observed_matches: boolean | null;
  notes: string[];
}

export interface FoundNode {
  id: string;
  name: string;
  resource: string;
  resource_name: string;
  purity: string;
  kind: string;
  x_m: number;
  y_m: number;
  z_m: number;
  grid: string;
  rate: number;
  status: "free" | "tapped" | "locked";
  occupant: string | null;
  occupant_off: boolean | null;
  region: Region | null;
  distance_m: number | null;
  moved: boolean;
  spoiler: boolean;
}

export interface FoundField {
  key: string;
  selector: string;
  members: string[];
  region: string | null;
  grid: string;
  direction: string;
  x_m: number;
  y_m: number;
  bbox_m: [number, number, number, number];
  size: number;
  purities: Record<string, number>;
  resources: string[];
  total: number;
  free: number;
  spread_m: number;
  locked: boolean;
  distance_m: number | null;
  spoiler: boolean;
}

export interface WaterBlock {
  bodies: Record<string, number>;
  pumps: number;
  per_pump_m3_min: number | null;
  sea_level_m: number | null;
}

export interface NodeChoices {
  resources: { id: string; name: string; nodes: number }[];
  purities: string[];
  kinds: string[];
}

export interface NodeFindResponse extends ApiError {
  view: "nodes" | "fields" | "nearest";
  description: string;
  selectors: string[];
  where: string;
  nodes: FoundNode[];
  fields: FoundField[];
  count: number;
  total: number;
  free: number;
  unit: string;
  elevation: [number, number] | null;
  water: WaterBlock | null;
  choices: NodeChoices;
  notes: string[];
  hidden_spoilers: number;
  stale: TableAge | null;
  save_error: string | null;
}

export interface RankedSite {
  rank: number;
  score: number;
  region: string | null;
  grid: string;
  x_m: number;
  y_m: number;
  selector: string;
  nodes: number;
  untapped: number;
  spread_m: number;
  to_infra_m: number | null;
  purity: number;
  alt_m: number | null;
  rough_m: number | null;
  slope_deg: number | null;
  wet_pct: number | null;
}

export interface RankedSitesResponse extends ApiError {
  resource: string;
  resource_name: string;
  description: string;
  sites: RankedSite[];
  count: number;
  weights: Record<string, number>;
  notes: string[];
  stale: TableAge | null;
}

export interface RunEnd {
  x_m: number;
  y_m: number;
  z_m: number;
  plugs: string | null;
}

export interface RunRow {
  id: string;
  kind: "belt" | "lift" | "pipe";
  label: string;
  pieces: number;
  length_m: number;
  a: RunEnd;
  b: RunEnd;
  z_min_m: number;
  z_max_m: number;
  directed: boolean;
  basis: string | null;
  carries: string | null;
  rate: number | null;
  network: number | null;
  via: string[];
  distance_m: number;
  lines_m: [number, number][][];
}

export interface NetworkRow {
  network: number | null;
  carries: string | null;
  pieces: number;
  length_m: number;
  x_m: number;
  y_m: number;
  z_min_m: number;
  z_max_m: number;
  distance_m: number;
  touches: string[];
}

export interface ConduitsResponse extends ApiError {
  view: "runs" | "networks";
  where: string;
  where_to: string;
  radius_m: number;
  to_radius_m: number | null;
  runs: RunRow[];
  networks: NetworkRow[];
  total: number;
  offset: number;
  belts: number;
  pipes: number;
  belt_m: number;
  pipe_m: number;
  fluids: string[];
  bridged: string[];
  notes: string[];
  age_note: string;
}

export interface HereResponse extends ApiError {
  age_note: string;
  save_token: string;
  player: { x_m: number; y_m: number; z_m: number } | null;
  region: Region | null;
  grid: string | null;
  direction: string | null;
  radius_m: number;
  nodes: FoundNode[];
  nodes_total: number;
  nearest_building: { name: string; distance_m: number } | null;
  pawns: number;
  stale: TableAge[];
}

export interface RegionRow {
  name: string;
  direction: string;
  grid: string;
  anchor_m: [number, number];
  area_km2: number;
  nodes: number;
}

export interface RegionTableResponse extends ApiError {
  resource: string | null;
  resource_name: string | null;
  rows: RegionRow[];
  accuracy_m: number;
  hidden_spoilers: number;
}

export interface ConduitCount {
  belt: number;
  pipe: number;
  radius_m: number;
}

export type NearPickup = CollectibleRow & { label: string };

export interface CensusRow {
  category: string;
  label: string;
  placed: number;
  collected: number;
  remaining: number | null;
  standing: number;
  never_streamed: number;
  looted_standing: number;
  state_tracked: boolean;
  pedestal_of: string | null;
  spoiler: boolean;
}

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
