"""MCP prompts: multi-step procedure, surfaced as slash commands."""

from __future__ import annotations

from . import app

# Prompts also cost nothing until invoked, and they surface as slash commands. They
# are where multi-step PROCEDURE lives, which keeps tool descriptions to one line and
# the always-resident schema small.


@app.mcp.prompt(title="Design a factory")
def design_factory(target_item: str, rate_per_min: str = "300") -> str:
    """Plan a factory for a target item, respecting what this world has unlocked."""
    return (
        f"Design a factory producing {rate_per_min}/min of {target_item} in my current "
        "Satisfactory world.\n\n"
        "Work in this order:\n"
        f"1. alternates_for_item('{target_item}') to see every route and which I HAVE.\n"
        "2. world_summary() for tier, power headroom, and anything unlocked but never built.\n"
        f"3. plan_factory(objective='min_machines', target_item='{target_item}', "
        f"exports=['{target_item}'], export_minimums={{'{target_item}': {rate_per_min}}}) "
        "to get the real machine counts.\n"
        "4. If it comes back INFEASIBLE, that usually means a byproduct has no consumer. "
        "Identify it and either add it to exports or find a recipe that consumes it, then "
        "re-solve.\n\n"
        "Then tell me: the machine list, total power draw, raw inputs per minute, anything "
        "I must build first, and any byproduct needing an outlet. Flag it explicitly if the "
        "plan depends on exporting or sinking something."
    )


@app.mcp.prompt(title="Plan a power plant")
def plan_power_plant(fuel_resource: str = "Crude Oil", sources: str = "north") -> str:
    """Plan a power plant from a given resource and area."""
    return (
        f"Plan a power plant burning {fuel_resource} in my Satisfactory world, using "
        f"sources: {sources}.\n\n"
        f"1. search_resource_nodes(sources=['{sources}', 'resource:{fuel_resource}']) to see "
        "what is there and what is already tapped. Note that free capacity far from my "
        "existing base is not the same as usable capacity.\n"
        f"2. rank_build_sites('{fuel_resource}', sources=['{sources}']) if I need a new site.\n"
        f"3. plan_factory(objective='max_mw', sources=['{sources}'], exports=['MW']).\n"
        "4. If that abandons the resource or comes back infeasible, the byproducts have "
        "nowhere to go. Retry with exports=['MW','Plastic','Rubber'] and say plainly that "
        "the plant only works if those leave the site.\n\n"
        "Report net MW, the machine list, water demand, pipes and belts needed, what I must "
        "build first, and every binding constraint."
    )


@app.mcp.prompt(title="Which hard drive recipe?")
def pick_hard_drive(hard_drive_id: str = "") -> str:
    """Advise which alternate recipe to take from a pending hard drive."""
    which = (
        f"hard drive {hard_drive_id}"
        if hard_drive_id
        else "each pending hard drive worth deciding now"
    )
    return (
        f"Help me choose the alternate recipe for {which} in my Satisfactory world.\n\n"
        "1. list_pending_hard_drive_choices() to see the live offers and rerolls left.\n"
        "2. advise_hard_drive_pick(hard_drive_id=N) for the marginal value of each option.\n\n"
        "Read the deltas carefully. A 0 in d_MW means I already own a route that dominates "
        "it for power, NOT that the recipe is bad -- check d_own_output_mach, which measures "
        "it on what it actually makes. Tell me which to take and why, name the tradeoff "
        "rather than hiding it behind one score, and mention any new building type I would "
        "have to unlock or build."
    )
