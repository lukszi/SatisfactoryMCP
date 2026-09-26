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

/* ------------------------------------------------------------------ /api/plans */

/** A stored plan's pad. Its coordinates are METRES already -- the siting is a statement the
 *  player typed, not a save reading -- so nothing on either side divides by 100. */
export type PlanSiting = Schema["PlanSiting"];
export type PlansResponse = Body<"PlansResponse">;

/* ---------------------------------------------------------- /api/collectibles */

export type CollectibleRow = Schema["CollectibleRow"];
export type CollectiblesResponse = Body<"CollectiblesResponse">;

/* ------------------------------------------------------------------ both, and shared */

/** A region lookup, hung on a node row and answered for an inspected point. Declared once
 *  on the server too -- in `serial.py`, for the same reason it is one name here. */
export type Region = Schema["Region"];
