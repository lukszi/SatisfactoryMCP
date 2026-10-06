"""The plan log: each plan an append-only list of commits, with snapshots, merged by rule M1.

docs/plan_log.md maps the modules: ``records`` (the shapes), ``ops`` (one op at a time),
``wording`` (the words), ``log`` (the files and the merge) and ``migrate`` (the legacy file).
"""

from .....core import schema
from ..plan_args import (
    FACTORY_SENTINELS,
    KINDS,
    OBJECTIVES,
    PAYBACK_MAX_H,
    POWER,
    ROW_CHOICES,
    InvalidOp,
    PlanArgs,
    PlanLogError,
    is_power,
    legacy_hours,
)
from .log import SNAPSHOT_EVERY, PlanLog, PlanView
from .ops import diff_args, inverse, merge_key
from .records import (
    SCHEMA,
    Actor,
    AlreadyUndone,
    BaseRevRequired,
    Commit,
    Conflict,
    Forgotten,
    NameTaken,
    Outdated,
    PlanState,
    Pushed,
    Stamp,
    UnknownPlan,
)
from .wording import describe_commit, describe_op, factory_words, use_recipe_names

__all__ = [
    "FACTORY_SENTINELS",
    "KINDS",
    "OBJECTIVES",
    "PAYBACK_MAX_H",
    "POWER",
    "ROW_CHOICES",
    "SCHEMA",
    "SNAPSHOT_EVERY",
    "Actor",
    "AlreadyUndone",
    "BaseRevRequired",
    "Commit",
    "Conflict",
    "Forgotten",
    "InvalidOp",
    "NameTaken",
    "Outdated",
    "PlanArgs",
    "PlanLog",
    "PlanLogError",
    "PlanState",
    "PlanView",
    "Pushed",
    "Stamp",
    "UnknownPlan",
    "describe_commit",
    "describe_op",
    "diff_args",
    "factory_words",
    "inverse",
    "is_power",
    "legacy_hours",
    "merge_key",
    "schema",
    "use_recipe_names",
]
