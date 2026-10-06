"""Planning tools: stored plans, solves, layouts, staging against the save, and the page."""

from .analysis import bom, compare_recipe_options, explain_byproducts, rank_unlocks
from .layout import plan_layout
from .page_context import ui_context
from .siting import site_plan
from .solve import plan_factory
from .staging import commission_plan, diff_vs_save
from .stored_plans import forget_plan, list_plans, plan_log, rename_plan

__all__ = [
    "bom",
    "commission_plan",
    "compare_recipe_options",
    "diff_vs_save",
    "explain_byproducts",
    "forget_plan",
    "list_plans",
    "plan_factory",
    "plan_layout",
    "plan_log",
    "rank_unlocks",
    "rename_plan",
    "site_plan",
    "ui_context",
]
