# The plan log

How `domain/planning/stored/planlog.py` stores plans. The specification is
[planner_slice_contract.md](planner_slice_contract.md) §2–§5 and §7; this note records where
the code had to choose something the contract leaves open, and why.

## Layout

```
plans/
  <world>.json            legacy file: read once by the migration, never written again
  <world>/
    world.lock            create, rename, restore and the migration hold this first
    migrated.json         {"schema":1, "version", "from", "at", "keys": {name: key}}
    backup-v<version>/    the legacy file as it was, copied before the migration
    <key>/
      ops.jsonl           one commit per line, append-only
      ops.jsonl.lock      the plan lock
      snap/<rev>.json     {"schema":1, "key", "rev", "ts", "state"}
```

`PlanLog.dir_for(world)` is `PlanStore.path_for(world)` without `.json`, so both sanitise the
world id the same way.

## Reading

- Readers take no lock. A line is used only if it parses **and** its `rev` is the next one
  (1, 2, 3 …). A torn final line, or anything after a gap, is invisible.
- `state(key, rev)` loads the newest snapshot at or below `rev` whose own `key` and `rev`
  match its file name, then replays the commits after it. A missing, unreadable or
  mismatched snapshot falls through to the next older one, and finally to a replay from v1.
- `heads()` orders plans by the time of their v1 commit, so the list reads in creation order
  as the old file did. `_now()` never hands out the same timestamp twice in one process.

## Writing

- One commit is appended under the plan lock, after a torn tail (if any) is truncated.
- A snapshot is written after v1, after every rev divisible by `SNAPSHOT_EVERY` and after a
  `restore_to`. A snapshot that fails to write is skipped: the log line is the record and a
  snapshot only a shortcut.
- Ops are type-checked before the lock is taken, into a canonical copy (floats for float
  fields, `was` discarded). The store fills `was` from the head the commit lands on.

## The merge, in detail

`_clash(mine, theirs)` compares one op of mine with one op of theirs. Every pair is checked;
any `conflict` makes the whole push `Outdated` and nothing is written.

- `record` ops never clash.
- A `forget` on either side clashes with everything, whatever its key.
- `add required X` and `add banned X` clash across the two fields (the one semantic pair),
  on the literal member only (contract S4).
- Otherwise ops clash only on the same merge key: the same kind with the same value is
  `same` (mine is already applied), anything else is a `conflict`.

Mine then applies in order to a copy of the head. An op that changes nothing there is
dropped (this also covers every `same`). A `restore` whose plan was already restored by
theirs is dropped too, rather than refused as "not forgotten".

Names are checked once per commit, after all its ops: a commit that leaves the plan live
with a name another live plan holds raises `NameTaken` (a subclass of `InvalidOp`). So
`restore` + `rename` in one commit can bring a plan back under a new name, and undoing a
forget is refused while another live plan has taken the name. Any push holding a `rename` or
`restore` takes the world lock before the plan lock.

## Undo

- The conflict window is every commit after the undone rev, minus that rev's **undo chain**:
  the commits that undo it, the commits that undo those, and so on. Without the chain, undo →
  redo → undo would collide with its own redo.
- A later commit that stands undone leaves the window too, with its whole chain: the pair
  cancels out, as `git revert` of a revert does. So Ctrl+Z, Ctrl+Z walks back like a stack
  (set 6,370 MW, set 2,000 MW, undo, undo lands on the save default). A later commit that was undone
  and then redone stays in the window, and so does any later edit to the same field.
- `AlreadyUndone` is raised when an undo of the rev still stands, that is, when it has not
  itself been undone by a standing commit.
- The inverse of a `put` whose `was` is null is a `del`; the inverse of `site` puts the old
  siting back, or clears it when there was none.

## Stamps

`stamp(state)` runs after the merge, inside the lock, on the new head. Values that differ
become `record` ops in the same commit. A stamp that raises records `plan_id: ""`, so a bad
selector never blocks an edit.

## Migration

`PlanLog(world)` migrates when the legacy file exists and `migrated.json` does not:

- the legacy file is copied to `backup-v<version>/` first, and `migrated.json` records the
  package version that migrated (docs/releasing.md, "Data formats");
- a legacy file, `migrated.json` or snapshot with a `schema` above the one this version
  knows is refused with `core.schema.NewerSchema` ("written by a newer version"), and
  nothing is written;
- every spelling of grid power in `exports` and `export_minimums` (`MW`, `mw`, `power`, the
  solver's `__MW__`) is stored as `MW`; `planlog.is_power` is the one test for it, and
  replaying an older log normalises the same way;
- one `create` commit per legacy plan, actor `migration`, snapshot at v1;
- a blank legacy name becomes `plan`; a name another live plan already holds gets ` (2)`,
  ` (3)` …;
- legacy argument keys outside `PLAN_ARGS` are dropped, as `Plan.kwargs()` always dropped
  them; a value that fails validation is kept in the plan's notes as
  `migration could not keep: field=value` rather than lost;
- a migration that died before writing `migrated.json` resumes: a live plan whose v1 is a
  migration commit and whose name matches is reused, not created twice.

Legacy writes after the migration (a server still on the old code) are not picked up; one
warning per process is logged when the legacy file is newer than `migrated.json`.

## The compatibility view

`WorldState.plans` is `PlanLog.view()`: live plans as `Plan` objects with `key` and `rev`.
`Plan.args` is the non-default kwargs (`exclude_recipes` spelling) plus `objective`, which is
always shown so a stored request still reads as what was asked. `find` matches a key, then a
case-insensitive name, then a unique substring, as the old store did.

## Until the tools take `base_rev`

The store stage leaves `interfaces/mcp/tools/planning.py` writing through the log **at the
head** (`_at_head`, `_save_request`): last writer wins, exactly as the old store behaved.
One difference: `save_as` now saves over a live plan only on an exact (case-insensitive)
name, where the old `put` also took a unique substring and could overwrite "north oil" when
asked to save "north".
The tools stage replaces this with `base_rev` and the refusal wording of contract §10.
