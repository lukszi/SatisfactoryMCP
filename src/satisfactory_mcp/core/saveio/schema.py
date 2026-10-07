"""The projection's shape: what ``extract`` writes and every reader of it reads.

The keys are ``extract.walk``'s, in its order; the row layouts and why they are positional are
in ``docs/save-projection.md`` §6.16. A trailing row column is additive, so a short row is an
older projection, never a tear. ``tests/core/saveio/test_schema.py`` holds the committed
projection fixture against these types.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypeAlias

from typing_extensions import TypedDict

__all__ = [
    "AttachmentRecord",
    "BeltsBlock",
    "BufferSide",
    "Buffers",
    "BuildableRecord",
    "ContainerRecord",
    "CrateKind",
    "CrateRecord",
    "ExtractorRecord",
    "FluidBufferRecord",
    "GeneratorRecord",
    "GraphTables",
    "HardDrive",
    "HeaderOnly",
    "InventoriesBlock",
    "ItemStack",
    "MachineRecord",
    "MaterialEdge",
    "NodeState",
    "OngoingResearch",
    "PipeNetwork",
    "PipeNetworkRecord",
    "PipesBlock",
    "PlayerRecord",
    "Point",
    "PoleRow",
    "PoleTable",
    "Position",
    "PowerBlock",
    "PowerEdge",
    "ProgressionBlock",
    "Projection",
    "RemovedBlock",
    "RemovedRow",
    "ResearchBlock",
    "RouteRow",
    "SaveHeader",
    "SaveScan",
    "SplineSpan",
    "StorageRecord",
    "StructureRow",
    "StructureTable",
    "UnlockFlags",
    "UnsupportedSave",
    "Uptime",
    "WireRow",
]

#: An actor header's ``[x, y, z]`` in world centimetres, rounded to millimetres.
Position: TypeAlias = list[float]
#: A route's control point, ``[x, y, z]`` in whole world centimetres.
Point: TypeAlias = list[int]
#: One span of a bent route: its two tangents as six integers, or ``0`` where it is flat.
SplineSpan: TypeAlias = int | list[int]
#: ``[classIndex, x, y, z, yaw]``: truncated centimetres, yaw in degrees or null.
StructureRow: TypeAlias = list[int | float | None]
#: ``[group, classIndex, points, actorIndex]``, plus a ``spans`` column where the route bends.
RouteRow: TypeAlias = list[int | list[Point] | list[SplineSpan]]
#: ``[classIndex, x, y, z, yaw, actorIndex]``.
PoleRow: TypeAlias = list[int | float | None]
#: ``[x0, y0, z0, x1, y1, z1]``, the span of the ``graph["power"]`` edge at the same index.
WireRow: TypeAlias = list[int]
#: ``[cellIndex, leaf]``.
RemovedRow: TypeAlias = list[int | str]
#: ``[fromActor, toActor, fromRole, toRole]``, indices into ``graph`` actors and roles.
MaterialEdge: TypeAlias = list[int]
#: ``[actorA, actorB]``, indices into ``graph["actors"]``.
PowerEdge: TypeAlias = list[int]
#: ``[itemClass, count]``.
ItemStack: TypeAlias = list[str | float]
CrateKind: TypeAlias = Literal["none", "dismantle", "death"]


class SaveHeader(TypedDict):
    """The save header the sidecar reads, and the file's own path, size and write time."""

    path: str
    filename: str
    session_name: str
    save_identifier: str
    save_header_version: int
    save_version: int
    build_version: int
    play_duration_s: int
    save_datetime_ticks: int
    is_modded: bool
    is_creative: bool
    mtime_ns: int
    size: int


class UnsupportedSave(TypedDict):
    """A file the header scan could not read, and the parser's reason."""

    path: str
    filename: str
    reason: str
    mtime_ns: int
    size: int


class SaveScan(TypedDict):
    """The sidecar's ``--list`` answer; ``missing_root`` only when the save root is absent."""

    schema_version: NotRequired[int]
    root: str
    saves: list[SaveHeader]
    unsupported: list[UnsupportedSave]
    missing_root: NotRequired[bool]


class HeaderOnly(TypedDict):
    """The sidecar's ``--header-only`` answer."""

    schema_version: int
    header: SaveHeader


class ProgressionBlock(TypedDict):
    """The recipe, schematic and game-phase managers; a manager the save lacks adds nothing."""

    available_recipes: NotRequired[list[str]]
    purchased_schematics: NotRequired[list[str]]
    last_active_schematic: NotRequired[str | None]
    game_phase: NotRequired[str]
    target_phase: NotRequired[str]
    #: Phase -> item class -> amount, deprecated and frozen (§6.4).
    phase_costs_remaining: NotRequired[dict[str, dict[str, float]]]
    paid_off_target: NotRequired[dict[str, float]]


class HardDrive(TypedDict):
    hard_drive_id: int | None
    options: list[str]
    rerolls_executed: int


class OngoingResearch(TypedDict):
    schematic: str | None
    seconds_left: float | None
    fields_seen: list[str]


class ResearchBlock(TypedDict):
    unclaimed_hard_drives: list[HardDrive]
    ongoing: list[OngoingResearch]
    unlocked_trees: list[str]
    last_used_hard_drive_id: NotRequired[int | None]


class UnlockFlags(TypedDict, total=False):
    """``BP_UnlockSubsystem_C``'s flags, written only once true, and its two slot counts."""

    mIsMapUnlocked: bool
    mIsBuildingOverclockUnlocked: bool
    mIsBuildingProductionBoostUnlocked: bool
    mIsBuildingEfficiencyUnlocked: bool
    mIsBlueprintsUnlocked: bool
    mIsCustomizerUnlocked: bool
    mNumTotalInventorySlots: int
    mNumTotalArmEquipmentSlots: int


class RemovedBlock(TypedDict):
    """Map-placed actors the save records as gone (§6.11); ``counts`` by recovered class."""

    cells: list[str]
    instances: list[RemovedRow]
    counts: dict[str, int]


class StructureTable(TypedDict):
    classes: list[str]
    instances: list[StructureRow]


class BeltsBlock(TypedDict):
    classes: list[str]
    segments: list[RouteRow]


class PipeNetwork(TypedDict):
    """One ``FGPipeNetwork``; a pipe row's ``networkIndex`` points here."""

    id: int | None
    fluid: str | None


class PipesBlock(TypedDict):
    classes: list[str]
    networks: list[PipeNetwork]
    segments: list[RouteRow]


class PoleTable(TypedDict):
    classes: list[str]
    instances: list[PoleRow]


class PowerBlock(TypedDict):
    """The poles, and every wire's drawn span or null (§6.12)."""

    poles: PoleTable
    wires: list[WireRow | None]


class Uptime(TypedDict):
    """The productivity monitor: a fixed window and the seconds produced in it."""

    window_s: float
    produce_s: float
    cur_window_s: float
    cur_produce_s: float
    producing: bool


class BufferSide(TypedDict):
    items: dict[str, float]
    slots: int


#: A machine's own inventories by the side each feeds; ``in`` is a keyword, hence the call form.
Buffers = TypedDict(
    "Buffers", {"in": BufferSide, "out": BufferSide, "fuel": BufferSide}, total=False
)


class BuildableRecord(TypedDict):
    """A placed ``Build_`` actor. ``yaw`` is always written, null where it would not read."""

    cls: str
    instance: str
    pos: Position | None
    yaw: float | None
    clock: NotRequired[float]
    pending_clock: NotRequired[float]
    paused: NotRequired[bool]
    #: Somersloop runtime property, and which of the candidate names carried it.
    production_boost: NotRequired[float]
    production_boost_field: NotRequired[str]
    uptime: NotRequired[Uptime]
    buffers: NotRequired[Buffers]
    #: Power Shards and Somersloops slotted, by item class.
    potential_slots: NotRequired[dict[str, float]]


class MachineRecord(BuildableRecord):
    recipe: str | None


class ExtractorRecord(BuildableRecord):
    #: The resource node's path.
    node: str | None


class GeneratorRecord(BuildableRecord):
    fuel: str | None


AttachmentRecord: TypeAlias = BuildableRecord


class ContainerRecord(TypedDict):
    """A storage container: its contents biggest first, and its slot count."""

    cls: str
    instance: str
    pos: Position | None
    yaw: float | None
    items: list[ItemStack]
    slots: int


class FluidBufferRecord(TypedDict):
    """A fluid buffer: cubic metres held, and the fluid of the network that claims it."""

    cls: str
    instance: str
    pos: Position | None
    yaw: float | None
    fluid: str | None
    stored_m3: float | None


StorageRecord: TypeAlias = ContainerRecord | FluidBufferRecord


class CrateRecord(TypedDict):
    cls: str
    instance: str
    pos: Position | None
    yaw: float | None
    kind: CrateKind
    items: list[ItemStack]
    slots: int


class PipeNetworkRecord(TypedDict):
    """A pipe network that names its fluid."""

    instance: str
    fluid: str


class InventoriesBlock(TypedDict):
    """Item class -> amount, split by owner; fluids are raw litres."""

    player: dict[str, float]
    storage: dict[str, float]
    machine: dict[str, float]
    crate: dict[str, float]


class NodeState(TypedDict):
    resources_left: int


class PlayerRecord(TypedDict):
    instance: str
    pos: Position | None
    has_build_gun: bool


class GraphTables(TypedDict):
    """Interned actor and role names, and the edges that index them."""

    actors: list[str]
    roles: list[str]
    material: list[MaterialEdge]
    power: list[PowerEdge]


class Projection(TypedDict):
    """One parsed save, as the sidecar writes it and the pickle cache keeps it."""

    schema_version: int
    header: SaveHeader
    progression: ProgressionBlock
    research: ResearchBlock
    unlock_flags: UnlockFlags
    building_counts: dict[str, int]
    lightweight_counts: dict[str, int]
    removed: RemovedBlock
    structures: StructureTable
    belts: BeltsBlock
    pipes: PipesBlock
    power: PowerBlock
    machines: list[MachineRecord]
    extractors: list[ExtractorRecord]
    generators: list[GeneratorRecord]
    attachments: list[AttachmentRecord]
    storage: list[StorageRecord]
    crates: list[CrateRecord]
    pipe_networks: list[PipeNetworkRecord]
    #: The Dimensional Depot: item class -> amount.
    depot: dict[str, float]
    inventories: InventoriesBlock
    #: Resource node path -> what is left of a depletable node.
    node_state: dict[str, NodeState]
    players: list[PlayerRecord]
    graph: GraphTables
    warnings: list[str]
    n_objects: int
