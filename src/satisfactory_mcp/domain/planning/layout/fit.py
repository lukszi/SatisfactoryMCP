"""Does a planned layout fit the factory that already exists, and what of it is standing?

The layout has no coordinates, so scoping it to a factory answers two questions: how many
tiles short the existing platform is at the peak floor, and which blocks already stand there,
matched by (building, recipe). A standing machine is reported as present, never as correct.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ....core.saveio.records import instance_leaf

__all__ = ["FitReport", "assess_fit"]


@dataclass
class FitReport:
    factory: str
    machines: int
    #: Slab tiles under this factory, summed over every platform it occupies.
    tiles: int = 0
    slabs: int = 0
    extent_m: tuple[float, float] = (0.0, 0.0)
    storeys: int = 1
    #: Peak-floor foundations the layout needs.
    needs: int = 0
    #: Blocks already standing, and those still to build.
    standing: list[str] = field(default_factory=list)
    to_build: list[str] = field(default_factory=list)
    machines_standing: int = 0
    machines_to_build: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def shortfall(self) -> int:
        return max(0, self.needs - self.tiles)

    @property
    def fits(self) -> bool:
        return self.shortfall == 0

    def headline(self) -> str:
        if not self.tiles:
            return (
                f"{self.factory} sits on no foundations at all, so there is nothing to "
                f"fit into -- the layout needs {self.needs} tiles of new platform"
            )
        verdict = (
            "fits on the existing platform"
            if self.fits
            else f"needs {self.shortfall} more tiles, or a floor above"
        )
        return (
            f"{self.factory}: {self.tiles} tiles across {self.slabs} platform(s), "
            f"{int(self.extent_m[0])}x{int(self.extent_m[1])}m; "
            f"layout needs {self.needs} at its widest floor -- {verdict}"
        )


def assess_fit(name: str, machines: list[str], layout, structures, projection: dict) -> FitReport:
    """Compare a layout against one factory's existing platform and machines."""
    wanted = set(machines)
    report = FitReport(factory=name, machines=len(wanted), needs=layout.foundations)

    slab_ids = {structures.slab_of[m] for m in wanted if m in structures.slab_of}
    report.slabs = len(slab_ids)
    for index in slab_ids:
        slab = structures.slabs[index]
        report.tiles += slab.tiles
        report.extent_m = (
            max(report.extent_m[0], slab.extent[0] / 100.0),
            max(report.extent_m[1], slab.extent[1] / 100.0),
        )
        report.storeys = max(report.storeys, slab.storeys)

    off_slab = len(wanted) - sum(1 for m in wanted if m in structures.slab_of)
    if off_slab:
        report.notes.append(
            f"{off_slab} of this factory's machines stand on no foundation, so the tile "
            "count understates the space actually in use"
        )

    # What is already there, keyed the way a block is: building class plus recipe.
    have: Counter = Counter()
    for record in projection.get("machines", ()):
        if instance_leaf(record["instance"]) in wanted and record.get("recipe"):
            have[(record["cls"], record["recipe"])] += 1

    for block in layout.blocks:
        key = (block.building_id, block.recipe)
        if have.get(key, 0) >= block.machines:
            have[key] -= block.machines
            report.standing.append(block.name)
            report.machines_standing += block.machines
        else:
            report.to_build.append(block.name)
            report.machines_to_build += block.machines

    if report.standing:
        report.notes.append(
            f"{len(report.standing)} block(s) already stand here and are counted as done; "
            "a standing machine may still be on the wrong clock, so this says present, "
            "not correct"
        )
    if not report.fits and report.storeys > 1:
        report.notes.append(
            f"this factory is already {report.storeys} storeys, so the shortfall may be "
            "met by building up rather than out"
        )
    return report
