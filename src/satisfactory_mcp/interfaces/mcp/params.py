"""Parameter types more than one tool declares, so each schema says them one way."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

Limit = Annotated[int, Field(default=10, ge=1, le=25, description="max rows (hard cap 25)")]

#: The pin every save-reading tool accepts. Resident in every save-reading schema, so it names
#: the token's shape and nothing else; the contract is docs/mcp-surface.md 10.1i.
AsOf = Annotated[
    str | None,
    Field(default=None, description="pin to one world state: a sav:… token from an earlier answer"),
]

#: The shared ``biomass`` setting (docs/shared-settings.md), overridable per call.
Biomass = Annotated[
    bool | None,
    Field(
        default=None,
        description="count hand-fed biomass burners as generation; omitted: the shared setting",
    ),
]

BaseRev = Annotated[
    int | None,
    Field(description="the plan version you read; needed to change an existing plan"),
]

PlanName = Annotated[str | None, Field(description="recall a saved plan by name")]

WaterExtractors = Annotated[
    int | None,
    Field(description="how many Water Extractors your site can actually hold"),
]

Sloops = Annotated[int, Field(description="Somersloops the plan may spend; 0 spends none")]

Required = Annotated[
    list[str] | None,
    Field(description="recipes that must make their item; others for it are excluded"),
]

RecycleOnce = Annotated[
    list[str] | None,
    Field(description="recipes that may run but must not feed each other, e.g. ['Recycled']"),
]

Supplied = Annotated[
    dict[str, float] | None,
    Field(description="items another plan hands this one, {item: per-minute}"),
]

LogisticsItems = Annotated[
    list[str] | None,
    Field(description="items whose belt/pipe rows to pin, whatever their volume"),
]

PaybackHours = Annotated[
    float | Literal["default"] | None,
    Field(
        description="hours of play extra, slower machines must repay in saved power: 0 "
        "builds plainly; stops 1, 2, 5, 10, 20, 50, 100; 'default' follows the shared "
        "setting. Stored per plan"
    ),
]

OverclockLast = Annotated[
    bool | Literal["default"] | None,
    Field(
        description="build a row one machine short, the last one overclocked (1-2 Power "
        "Shards, checked against shards in hand plus those craftable from slugs); 'default' "
        "follows the shared setting"
    ),
]

PowerPrice = Annotated[
    float | Literal["default"] | None,
    Field(
        description="points per MWh the horizon prices power at; omit for the save's "
        "grid mix, 'default' puts a stored plan back on it"
    ),
]

RowOverclock = Annotated[
    dict[str, Literal["last", "spread", "default"]] | None,
    Field(
        description="per recipe (exact name or class id): 'last' overclocks that row's last "
        "machine, 'spread' builds one more underclocked machine instead, 'default' follows "
        "overclock_last again. Overrides overclock_last for that row; rows not named keep "
        "their stored choice"
    ),
]
