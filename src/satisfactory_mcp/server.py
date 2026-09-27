"""FastMCP entry point.

Nothing is defined here. The app object and the shared resolvers live in
``interfaces.mcp.app``, and every tool, resource and prompt lives in
``interfaces/mcp/tools/``, one module per concern. Importing ``tools`` is what
registers them -- the decorators run on import.

The file stays at this path whatever moves beneath it: the console script is
``satisfactory_mcp.server:main``.

This file was 3,467 lines with 36 tools in it before the split. The names below are
re-exported because tests and ad-hoc scripts reach for ``server.plan_factory`` and
friends, and because a caller should not have to know which module a tool ended up in.

Every tool passes ``structured_output=False``: a tool annotated ``-> str`` otherwise
gets an outputSchema AND has its whole payload echoed into ``structuredContent``, a
measured ~1.96x wire-size tax for no benefit.
"""

from __future__ import annotations

from .domain.factories.select import INDEX_WARNING as GRAPH_INDEX_WARNING
from .domain.factories.select import SELECTOR_HELP as GRAPH_SELECTOR_HELP
from .domain.planning import journal
from .interfaces.mcp import tools as _tools
from .interfaces.mcp.app import (
    Limit,
    _item_id,
    _origin_for,
    _player_xy,
    _resolve_factory,
    _state,
    game,
    mcp,
)
from .interfaces.mcp.tools.factories import (
    _cand_row,
    amend_factory,
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
from .interfaces.mcp.tools.floors import factory_floors
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
    PLAN_DEFAULTS,
    _plan_kwargs,
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
    collected_from_world,
    mam_research,
    milestones,
    phase_requirements,
    power_shards,
    somersloops,
)
from .interfaces.mcp.tools.prompts import design_factory, pick_hard_drive, plan_power_plant
from .interfaces.mcp.tools.resources import current_save, docs_summary, factory_labels, map_regions
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

#: Private helpers re-exported for tests and scripts that already reach for them.
_ = (_state, _item_id, _player_xy, _cand_row, _resolve_factory, _plan_kwargs, _origin_for, _tools)


def main() -> None:
    journal.set_writer("chat")
    mcp.run()


if __name__ == "__main__":
    main()
