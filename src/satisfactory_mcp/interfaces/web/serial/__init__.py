"""The vocabulary every JSON endpoint speaks: units, refusals and shared shapes.

The three conventions it serves are rules 8 to 10 of docs/web-wire.md.
"""

from .responses import (
    RequestRefused,
    busy_response,
    error_response,
    newer_schema_response,
    require_world,
    world_state,
)
from .shapes import (
    ActorBody,
    Biomass,
    CollectibleRow,
    Flow,
    FoundField,
    MachineSpot,
    PlanOpBody,
    Region,
    TableAge,
    actor_json,
    collectible_json,
    flow_json,
    found_field_json,
    machine_spots,
    region_json,
    resource_name,
    settings_json,
)
from .units import cm_to_m, xyz_m, yaw_deg

__all__ = [
    "ActorBody",
    "Biomass",
    "CollectibleRow",
    "Flow",
    "FoundField",
    "MachineSpot",
    "PlanOpBody",
    "Region",
    "RequestRefused",
    "TableAge",
    "actor_json",
    "busy_response",
    "cm_to_m",
    "collectible_json",
    "error_response",
    "flow_json",
    "found_field_json",
    "machine_spots",
    "newer_schema_response",
    "region_json",
    "require_world",
    "resource_name",
    "settings_json",
    "world_state",
    "xyz_m",
    "yaw_deg",
]
