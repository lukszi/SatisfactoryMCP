"""FastMCP entry point.

Nothing is defined here. The app object lives in ``interfaces.mcp.app`` and every tool in
``interfaces/mcp/tools/``; importing ``tools`` is what registers them, since the decorators
run on import. The tools are re-exported by name so a caller need not know which module
holds one. The file stays at this path because the console script is
``satisfactory_mcp.server:main``.
"""

from __future__ import annotations

from .domain.factories.select import INDEX_WARNING as GRAPH_INDEX_WARNING
from .domain.factories.select import SELECTOR_HELP as GRAPH_SELECTOR_HELP
from .domain.planning import siting
from .domain.planning.stored import planlog
from .domain.planning.stored.recall import PLAN_DEFAULTS
from .domain.session import journal
from .interfaces.mcp import tools as _registers_every_tool
from .interfaces.mcp.app import game, mcp, recipe_names
from .interfaces.mcp.params import Limit
from .interfaces.mcp.prompts import design_factory, pick_hard_drive, plan_power_plant
from .interfaces.mcp.resources import current_save, docs_summary, factory_labels, map_regions
from .interfaces.mcp.tools.collectibles import collected_from_world
from .interfaces.mcp.tools.factories import (
    amend_factory,
    factory_floors,
    factory_health,
    factory_map,
    factory_query,
    forget_factory,
    list_factories,
    name_factory,
    propose_factories,
    rename_factory,
    select_machines,
    trace_upstream,
)
from .interfaces.mcp.tools.gamedata import (
    alternates_for_item,
    list_buildings,
    recipe_detail,
    search_items,
    search_recipes,
)
from .interfaces.mcp.tools.harddrives import advise_hard_drive_pick, list_pending_hard_drive_choices
from .interfaces.mcp.tools.inventory import crates, stock, storage
from .interfaces.mcp.tools.planning import (
    bom,
    commission_plan,
    compare_recipe_options,
    diff_vs_save,
    explain_byproducts,
    forget_plan,
    list_plans,
    plan_factory,
    plan_layout,
    plan_log,
    rank_unlocks,
    rename_plan,
    site_plan,
    ui_context,
)
from .interfaces.mcp.tools.progression import (
    mam_research,
    milestones,
    phase_requirements,
    power_shards,
    somersloops,
)
from .interfaces.mcp.tools.settings import settings
from .interfaces.mcp.tools.spatial import (
    describe_location,
    list_regions,
    rank_build_sites,
    search_conduits,
    search_resource_nodes,
    show_on_map,
    whereami,
)
from .interfaces.mcp.tools.world import (
    factory_sites,
    list_worlds,
    power_report,
    unlocked_recipes,
    world_summary,
)

del _registers_every_tool

__all__ = [
    "GRAPH_INDEX_WARNING",
    "GRAPH_SELECTOR_HELP",
    "PLAN_DEFAULTS",
    "Limit",
    "advise_hard_drive_pick",
    "alternates_for_item",
    "amend_factory",
    "bom",
    "collected_from_world",
    "commission_plan",
    "compare_recipe_options",
    "crates",
    "current_save",
    "describe_location",
    "design_factory",
    "diff_vs_save",
    "docs_summary",
    "explain_byproducts",
    "factory_floors",
    "factory_health",
    "factory_labels",
    "factory_map",
    "factory_query",
    "factory_sites",
    "forget_factory",
    "forget_plan",
    "game",
    "list_buildings",
    "list_factories",
    "list_pending_hard_drive_choices",
    "list_plans",
    "list_regions",
    "list_worlds",
    "main",
    "mam_research",
    "map_regions",
    "mcp",
    "milestones",
    "name_factory",
    "phase_requirements",
    "pick_hard_drive",
    "plan_factory",
    "plan_layout",
    "plan_log",
    "plan_power_plant",
    "power_report",
    "power_shards",
    "propose_factories",
    "rank_build_sites",
    "rank_unlocks",
    "recipe_detail",
    "rename_factory",
    "rename_plan",
    "search_conduits",
    "search_items",
    "search_recipes",
    "search_resource_nodes",
    "select_machines",
    "settings",
    "show_on_map",
    "site_plan",
    "somersloops",
    "stock",
    "storage",
    "trace_upstream",
    "ui_context",
    "unlocked_recipes",
    "whereami",
    "world_summary",
]


def main() -> None:
    journal.set_writer("chat")
    planlog.use_recipe_names(recipe_names)
    siting.set_ground_z(siting.terrain_provider())
    mcp.run()


if __name__ == "__main__":
    main()
