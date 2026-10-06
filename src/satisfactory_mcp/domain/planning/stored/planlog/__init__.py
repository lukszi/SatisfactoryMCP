"""The plan log: each plan an append-only list of commits, with snapshots, merged by rule M1.

docs/plan_log.md maps the modules: ``records`` (the shapes), ``ops`` (one op at a time),
``wording`` (the words), ``log`` (the files and the merge) and ``migrate`` (the legacy file).
"""

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
    "SCHEMA",
    "SNAPSHOT_EVERY",
    "Actor",
    "AlreadyUndone",
    "BaseRevRequired",
    "Commit",
    "Conflict",
    "Forgotten",
    "NameTaken",
    "Outdated",
    "PlanLog",
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
    "merge_key",
    "use_recipe_names",
]
