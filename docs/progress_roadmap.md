# Progress roadmap: gate, next steps, alternates

The server side of the Progress page (`docs/frontend_kit.md`, "Progress"): how to reach the
target Space Elevator phase, and what to do next. Code: `domain/progression/roadmap/`, routes
`/api/progress/gate` and `/api/progress/alternates` (`routers/dashboard/progress_roadmap.py`).
Both take `horizon_h` (hours to deliver in, default 20, at most 1000).

## Bill

The target phase's row of `phase_requirements`: each part still owed, held from spendable
stock, short = owed − held. A part's recipe is its default recipe (else its first maker); it
counts as unlocked when any recipe making it is. A locked recipe names the milestone or MAM
node that grants it (`Recipe.unlocked_by`, milestones and MAM preferred over other sources).

## Who makes what

`Makers` builds one factory view over every machine in the world and one per named factory.
A maker is a factory with a nameplate surplus of the item (net of what it uses itself), at the
saved clocks. The world figure covers unnamed clusters too, so "nothing makes it" holds for the
whole save, not just the named factories.

## Chains

A chain is the planner's least-raw solve (`chain_scenario`) for the bill at the horizon's rate
(short ÷ horizon minutes). The recipe pool is every recipe the save has unlocked plus every
default part recipe, and every building, so a part whose recipe is still locked is sized too.
Machines are whole machines, MW the nameplate draw, raw the resources per minute without
water. Each solve takes about 10 ms on a late-game save.

## Next steps

At most five, worked out from the bill:

- an unlocked part nothing makes → **line**; one made slower than the horizon needs → **grow**;
- a locked part → **unlock** its milestone or MAM node; that unlock in turn asks for its own
  unmet prerequisites (up to three levels) and, for each item its bill is short of that no
  machine in the world makes, a **make** step (amounts summed over the unlocks needing it);
- a make step for an item whose recipe is itself locked asks for that unlock first.

Order: prerequisites always first. Among steps free to go, work that can start now (make,
line, grow) comes before an unlock, then the step that opens more of the bill's parts, then
the order found. `after` lists the step numbers a step waits on. `inputs_held` says whether
stock covers a make step's inputs. Unlock steps carry the system, the status of the rung and
what its bill is still short of. Clocking choices and hand-fed setups are never steps, and
nothing counts drives waiting to be chosen.

## Alternates

For each alternate whose item the bill's chains make (event recipes left out): the whole
bill at the horizon's rates solved twice, with the alternate required for its item (its rivals
removed) and with it removed. The difference is what having it is worth: machines, MW, raw.
Only rows that cut at least one of the three are sent, sorted by machines. `source` is
`unlocked`, `drive` (offered by a drive on hand; `other` is that drive's other option) or
`pool` (on no drive on hand yet). `parts` names the bill parts whose own chain makes the item.
A late-game bill takes about 100 solves, about one second.

## Caching

Each answer is kept per projection, world, labels version and horizon (`Singleflight`, eight
entries per route): a new save, a renamed factory or another horizon solves again; a redraw
of the page does not.

## Milestones and MAM

`/api/progress/milestones` and `/api/progress/mam` rows carry `unlock_names`: the names of
the recipes a node grants that the save does not have yet (a building by its recipe's name),
and `unlock_icons`, one per name: the recipe's main product, which is the icon of an item, a
piece of equipment or a building alike (the Buy now pills show it).
MAM rows carry `elapsed_pct` for a running node: (research time − time left) ÷ research time.
