"""Build jobs: a solution's rows grouped by what the player places, the plan side of a diff."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from ..solver.model import MW, Solution

__all__ = ["BuildJob", "JobKey", "group_key", "group_processes"]

#: What a plan row is matched on: ``("recipe", building, recipe)``, ``("generator",
#: building)`` or ``("extractor", building, resource, purity)``.
JobKey: TypeAlias = tuple[str, ...]


@dataclass
class BuildJob:
    """Solution rows that are one build job: the same machine doing the same thing."""

    key: JobKey
    kind: str
    building_id: str
    building: str
    recipe: str | None
    resource: str
    purity: str
    machines: int
    #: The rows' machines times their clocks, added up: the job's rate in full-speed machines.
    clock_sum: float
    mw: float
    #: (row label, machines) per merged solution row.
    labels: list[tuple[str, int]]
    rates: dict[str, float]
    depth: int = 0


def _resource_of(proc: dict) -> str:
    """The single item an extractor column produces."""
    produced = [item for item, rate in proc.get("rates", {}).items() if rate > 0]
    return produced[0] if produced else ""


def group_key(proc: dict) -> JobKey:
    """The identity a plan row is matched on, as a hashable tuple.

    Public because it is the join between the two things this package says about one
    machine: what to BUILD (the diff) and when to SWITCH IT ON (commission). Both must agree
    on what counts as the same build job, and the only way to guarantee that is for both
    to call this.
    """
    if proc["kind"] == "recipe":
        return ("recipe", proc["building_id"], proc["recipe"])
    if proc["kind"] == "generator":
        return ("generator", proc["building_id"])
    return ("extractor", proc["building_id"], _resource_of(proc), proc.get("purity", ""))


def group_processes(sol: Solution) -> list[BuildJob]:
    """Collapse solution rows into one entry per build job.

    Only generators actually merge, and that is the point: the plan runs 176 Fuel
    Generators on Fuel and 20 on Turbofuel, but that is 196 identical buildings and one
    plumbing decision, not two different machines to place.
    """
    jobs: dict[JobKey, BuildJob] = {}
    for proc in sol.processes:
        key = group_key(proc)
        job = jobs.get(key)
        if job is None:
            job = BuildJob(
                key=key,
                kind=proc["kind"],
                building_id=proc["building_id"] or "",
                building=proc["building"],
                recipe=proc.get("recipe"),
                resource=_resource_of(proc) if proc["kind"] == "extractor" else "",
                purity=proc.get("purity", ""),
                machines=0,
                clock_sum=0.0,
                mw=0.0,
                labels=[],
                rates={},
            )
            jobs[key] = job
        job.machines += proc["machines"]
        job.clock_sum += proc["machines"] * float(proc.get("clock") or 1.0)
        job.mw += proc["mw"]
        job.labels.append((proc["label"], proc["machines"]))
        for item, rate in proc.get("rates", {}).items():
            if item != MW:
                job.rates[item] = job.rates.get(item, 0.0) + rate
    return list(jobs.values())
