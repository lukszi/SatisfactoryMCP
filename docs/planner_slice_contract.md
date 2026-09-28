# Planner first slice: the contract

The build contract for phase P1 of [planner_vision.md](planner_vision.md) §8, cut down to the
smallest slice that still runs the loop in both directions. The vision note says what and why.
This file says exactly what gets built, so four people can build it at once without meeting.

Where this file and the vision note disagree, this file wins for the slice. Every departure is
listed in §14.

**Decided 2026-09-27** (binding, vision §9.1–9.2): one shared plan state, no drafts,
autosave per gesture; git-like versions on both sides (`base_rev` on writes, version in reads);
merge rule M1; op log + snapshots, where undo is a new inverse op; `required`/`banned` recipe
sets; the page follows the agent by default; one factory per plan; local stdio clients only;
the page never prompts the agent.

---

## 1. What the slice demonstrates

| Direction | Demonstration |
|---|---|
| chat → page | `plan_factory save_as=` over an existing plan with `base_rev` lands as vN+1. Within ~1 s the open workbench shows the new head and a strip reading *"Claude Code v13: +banned Bolted Frame [undo]"* |
| chat → page | `plan_factory` with no `save_as` becomes a **from-chat card**: **[apply to this plan] [new plan from it] [dismiss]** |
| page → chat | Every page gesture is a version. `list_plans` / `plan_log` show *"v12 page: rate Heavy Modular Frame 10→15/min"*. `ui_context` reports what the page has open |
| both | Concurrent edits to different keys auto-merge. The same key gives **outdated** (chat) or a **conflict chip** (page). Undo is an inverse op on either side |

**In the slice:** the op log store, merge, snapshots, migration, `required` (G4), the journal
(G0), `base_rev` and versions in the MCP tools, `plan_log`, `ui_context`, the `plans` and
`activity` SSE events, and the workbench (goal, rate, exports, sources, required/banned,
water, sloops, extractor clocks, summary, build list, power budget, undo/redo, conflict chip,
chat strip, chat card, follow setting, focus heartbeat).

**Not in the slice (later phases):** pins, asks, the `satisfactory://ui/context` resource
mirror, the Versions list and view/restore from the page, duplicate, rename/forget from the
page, result deltas in the chat strip, the graph tab, alternates drawer, Track, Site dragging,
Layout, byproducts, the list status column ("world moved"), labels on the log, elicitation,
and journalling of `show_on_map`/label tools.

---

## 2. The plan state

A plan is identified by a **key**: 8 lowercase hex chars from `secrets.token_hex(4)`, unique
within its world and never changed. Names are unique among live plans, compared
case-insensitively. A rename is an op.

`PlanState` (state at one rev). Every field is always present, with the defaults shown.

| Field | Type | Default | Notes |
|---|---|---|---|
| `key` | str | – | |
| `rev` | int | – | the rev this state is at |
| `name` | str | – | stripped, non-empty |
| `forgotten` | bool | false | |
| `notes` | str | "" | |
| `factory` | str | "" | factory label this plan is for |
| `created` | str | "" | save datetime or filename at create, as today |
| `plan_id` | str | "" | solve-input hash at the last stamp; "" = not stamped |
| `provenance` | dict | {} | `provenance.record` shape |
| `siting` | dict | {} | `Siting.to_dict()` shape; {} = not sited |
| `args` | `PlanArgs` | below | |

`PlanArgs`. The store keeps the full shape. `kwargs()` maps it to `build_scenario` arguments
and **drops values at their default**.

| Field | Type | Default | Kind (§3) | Maps to kwarg |
|---|---|---|---|---|
| `objective` | str, one of `max_mw max_item min_raw min_machines min_power` | `max_mw` | scalar | same |
| `target_item` | str \| null | null | scalar | same |
| `sources` | list[str] (selectors) | [] | set | same |
| `exports` | list[str] (tokens as typed) | [] | set | same |
| `export_minimums` | dict[str, float] | {} | map | same |
| `only_free_nodes` | bool | false | scalar | same |
| `allow_sinks` | bool | true | scalar | same |
| `clocks` | list[float] | [] | set | same |
| `extractor_clocks` | list[float] | [] | set | same |
| `machine_cost_mw` | float | 5.0 | scalar | same |
| `banned` | list[str] (recipe/process patterns) | [] | set | **`exclude_recipes`** |
| `required` | list[str] (recipe class ids) | [] | set | **`required`** (new, §6) |
| `only_recipes` | list[str] | [] | set | same |
| `water_extractors` | int \| null | null | scalar | same |
| `sloops` | int ≥ 0 | 0 | scalar | same |
| `belt_ipm` | float \| null | null | scalar | same |
| `pipe_m3min` | float \| null | null | scalar | same |
| `recycle_once` | list[str] | [] | set | same |
| `supplied` | dict[str, float] | {} | map | same |
| `logistics_items` | list[str] | [] | set | not a solve kwarg; presentation only |

Sets keep insertion order for display and have set semantics for merging. A float member of
`clocks`/`extractor_clocks` is compared by `f"{x:g}"`. Map keys are item names as typed.

`banned` is the stored name of `exclude_recipes`. The MCP tools keep the `exclude_recipes`
spelling and translate at the boundary. `required` members are **recipe class ids**. Writers
resolve names to ids before writing (§10.1), so the store needs no game data.

---

## 3. The op vocabulary

An **op** is a JSON object with an `op` field. A **commit** (one rev) holds one or more ops
that apply together or not at all. One gesture is one commit.

`was` fields are **filled by the store** at append time from the head state the commit
actually lands on, and writers never send them. That makes every inverse exact without a
replay.

| `op` | Fields (writer sends) | Store adds | Applies to | Merge key | Inverse |
|---|---|---|---|---|---|
| `set` | `field`, `value` | `was` | scalar args, plus `notes`, `factory` | `field` | `set field=was` |
| `put` | `field`, `item`, `value` | `was` (null if absent) | map args | `field[item]` | `was` null → `del`; else `put value=was` |
| `del` | `field`, `item` | `was` | map args | `field[item]` | `put value=was` |
| `add` | `field`, `member` | – | set args | `field{member}` | `remove` |
| `remove` | `field`, `member` | – | set args | `field{member}` | `add` |
| `site` | `value` (siting dict, or null = clear) | `was` | `siting` | `site` | `site value=was` |
| `rename` | `name` | `was` | `name` | `name` | `rename name=was` |
| `create` | `name`, `state` (full PlanState minus key/rev) | – | whole plan, rev 1 only | `lifecycle` | `forget` |
| `forget` | – | – | `forgotten=true` | `lifecycle` | `restore` |
| `restore` | – | – | `forgotten=false` | `lifecycle` | `forget` |
| `record` | `field` ∈ {`plan_id`, `provenance`}, `value` | – | derived fields | **none**: never conflicts | none; skipped by undo |

**No-ops are dropped before append.** Examples: `set` to the current value, `add` of a
member already present, `remove`/`del` of something absent. A commit whose ops all drop
appends nothing and returns `noop: true` at the current head.

**Validation** (store, no game data) raises `InvalidOp`:
- the `field` belongs to the op's kind in the table above;
- values are JSON types matching §2 (bool is not int, floats finite, `sloops` and
  `water_extractors` ≥ 0);
- `objective` is in the enum;
- a `name` is non-empty after strip and not taken by another live plan;
- `create` appears only at rev 1, and `forget` only on a live plan, `restore` only on a
  forgotten one.

`undo` is **not** an op kind. An undo is a commit of inverse ops with `undoes: <rev>` set
(§4.3).

### 3.1 Commit (one line of `ops.jsonl`)

```json
{"rev": 13, "base_rev": 11, "ts": 1790000000.123,
 "actor": {"kind": "chat", "client": "claude-code", "pid": 8248},
 "sav": "sav:3f2a91c0aa11", "ops": [{"op": "add", "field": "banned", "member": "Recipe_Alternate_BoltedFrame_C"}],
 "merged_over": [12], "undoes": null, "note": ""}
```

- `actor.kind` ∈ `chat`, `page`, `migration`, `system`. `client` = MCP `clientInfo.name`
  for chat, "" otherwise. `pid` = writing process.
- `merged_over`: the revs in (`base_rev`, landing rev) that this commit merged over.
- `sav`: the `pin.check(st.header, None)` token of the save the writer read, or "".
- `note`: free text ≤ 200 chars, e.g. "applied chat solve j:chat-8248:17".
- Each line is under ~1 kB, except `create` and `record provenance`.

### 3.2 Words for ops (shared by tools, web and activity)

`planlog.describe_op(op) -> str` and `planlog.describe_commit(c) -> str` return exactly
these forms. The page shows the server's `text` and never formats ops itself.

| op | text |
|---|---|
| set | `objective max_mw→min_machines`, `sloops 0→4`, `notes changed` |
| put export_minimums | `rate Heavy Modular Frame 10→15/min` (`+rate … 15/min` when `was` null) |
| del export_minimums | `−rate Heavy Modular Frame` |
| put/del supplied | `supplied Steel Beam 0→120/min` / `−supplied Steel Beam` |
| add/remove | `+banned Alternate: Bolted Frame`, `−sources region:Grass Fields` (recipe ids shown as the id; the web/tool layer may substitute display names) |
| site | `site moved` / `site set` / `site cleared` |
| rename | `renamed "a"→"b"` |
| create / forget / restore | `created` / `forgotten` / `restored` |
| record | "" (hidden) |

A commit reads as `v13 Claude Code: +banned Bolted Frame · sloops 0→4`. An undo commit reads
as `v15 page: undo v13 (−banned Bolted Frame)`. Actor words: chat → a client display name
(`claude-code` → "Claude Code", `claude-ai` → "Claude Desktop", else the raw client, else
"chat"), page → "page", migration → "migrated".

---

## 4. The merge (M1), undo and restore

### 4.1 Push

`push(key, base_rev, ops)` takes the plan lock, then:

1. If `base_rev` > head, raise `InvalidOp`. If `base_rev` < 1, raise `InvalidOp`.
2. If the plan was forgotten at or before `base_rev`, and the ops are not a `restore`,
   raise `Forgotten`.
3. **theirs** = every op of every commit with rev in (`base_rev`, head], excluding `record`.
4. For each op in **mine**, compare with each op in theirs:

| mine vs theirs | result |
|---|---|
| different merge keys, no semantic pair | clean |
| same key, same resulting value (`set`/`put`/`site`/`rename` equal value; `add`+`add`; `remove`+`remove`; `del`+`del`; `restore`+`restore`) | clean. Mine is **dropped** as already applied |
| same key, different value (`set`, `put`, `del` vs `put`, `site`, `rename`) | **conflict** |
| `add X` vs `remove X`, same field | **conflict** |
| `add required X` vs `add banned X`, either direction, same literal member | **conflict** (the one semantic pair) |
| any op vs a `forget` in theirs, or a `forget` in mine vs any op in theirs | **conflict** (key `lifecycle`) |

5. Any conflict: append nothing and raise `Outdated`.
6. Otherwise fill the `was` fields against the head state, drop no-ops, and append one
   commit at head+1 with `merged_over` = the theirs revs. Run `stamp` (§4.4), snapshot if due
   (§5.2), release.

A clean merge that solves INFEASIBLE is not a conflict (vision §2.6). N3 is still open, so the
slice takes the proposed answers: two `required` recipes for one item are allowed; a ban
that hits a recipe in use during a rate edit is clean; a merge that turns infeasible is
accepted and shown.

### 4.2 Push by arguments (the chat path)

`push_args(key, base_rev, args)` builds ops with `diff_args(state(key, base_rev).args, args)`
and then pushes them. Comparing against the **base** rather than the head is what makes it a
three-way merge.

`diff_args(base, new) -> list[op]`:
- normalise both sides to full `PlanArgs`. Absent, None, [] and {} equal the field default.
  `exclude_recipes` is accepted as a spelling of `banned`.
- scalars: `set` where different.
- sets: `add` for members only in new, `remove` for members only in base (compared as in
  §2).
- maps: `put` where the value is new or different, `del` where the key is gone.
- Fields absent from `new` are **not** treated as "set to default" when `partial=True`. The
  chat path passes `partial=False`: a full request.

### 4.3 Undo and restore

- `undo(key, base_rev, rev)`: the inverse of every non-`record` op of commit `rev`, in
  reverse order, as one commit with `undoes: rev`.
  - Raises `AlreadyUndone` when a later commit already has `undoes: rev` that has not itself
    been undone.
  - Raises `InvalidOp` for rev 1 (`create`). Forget the plan instead.
  - **Conflict window:** the inverse ops are checked against every commit with rev in
    (`rev`, head], not (`base_rev`, head]. That excludes commits that only `undo` this `rev`,
    and `record`. So undoing something changed again since is `Outdated`, as vision §2.6
    requires.
- **Redo** = `undo` of the undo commit.
- `restore_to(key, base_rev, rev)`: ops = `diff_args(head.args, state(rev).args)` plus `set`
  for notes and factory, `site` and `rename` where they differ, as one commit with note
  `restore v<rev>`. It merges like `push` against `base_rev`, and a snapshot is always
  written.
- Un-forgetting = `undo` of the forget commit (or a plain `restore` op).

### 4.4 Stamp

`stamp: Callable[[PlanState], dict] | None` runs **inside the lock** after a clean merge,
against the new head. It returns `{"plan_id": str, "provenance": dict}`. The store appends a
`record` op for each value that differs, **into the same commit**, before writing the line.
If `stamp` raises, it records `plan_id: ""` (unstamped) and keeps going: a bad selector must
not block an edit.

The tools pass a stamp built on `build_scenario` + `provenance.record` (~3 ms), and so does
the web. That keeps "world moved" in `list_plans` honest after page edits.

### 4.5 Push at head (mechanical follow-ups only)

`push_at_head(key, ops, actor)` applies against the current head with no merge. Use it only
for server-side follow-ups that are not a user's edit of something they read, such as
`edits.repoint_plans` setting `factory` after a factory rename. Validation still applies.

---

## 5. On-disk layout

All under `config.plans_dir()` (and so under `SATISFACTORY_USER_DATA` when set).

```
plans/
  <world>.json                    legacy PlanStore file: read once by migration, then never written
  <world>/                        <world> = the existing PlanStore.path_for sanitising
    world.lock                    world lock (filelock target "world"): create, rename, migration
    migrated.json                 {"schema":1, "from":"<world>.json", "at":ts, "keys":{name:key}}
    <key>/
      ops.jsonl                   one commit per line, append-only
      ops.jsonl.lock              plan lock (filelock target "ops.jsonl")
      snap/<rev>.json             {"schema":1, "key", "rev", "ts", "state": PlanState}
activity/<world>/<writer>.jsonl   journal (§8)
ui/<world>.json                   page focus (§9)
```

`config.activity_dir()` and `config.ui_dir()` are new siblings of `plans_dir()`/`labels_dir()`,
added by the store stage.

### 5.1 Locking and I/O

- Plan lock = `filelock.held(<key>/ops.jsonl)`. World lock = `filelock.held(<world>/world)`.
  Order: world lock first, then plan lock. Never the reverse.
- `create` and `rename` hold both locks (name uniqueness spans plans). Every other write holds
  only the plan lock.
- Append: open with `"ab"`, write one `json.dumps(commit, separators=(",", ":")) + "\n"`,
  flush, then `os.fsync`.
- Readers take **no lock**. A final line without `\n`, or one that fails to parse, is ignored
  (a write in progress or torn). The next writer under the lock **truncates** a torn tail
  before appending.
- Snapshots and `migrated.json` are written with `atomic.write_text`.
- `LockTimeout` propagates. The web maps it to 503, and the tools say "busy, nothing written".

### 5.2 Snapshots

- Written at rev 1 (`create`), at every rev where `rev % SNAPSHOT_EVERY == 0`
  (`SNAPSHOT_EVERY = 50`, N1 default), and after every `restore_to` commit.
- **Loading rev R** means the newest snapshot ≤ R, then a replay of the commits after it up
  to R. A missing or corrupt snapshot falls back to the next older one, then to a full replay
  from rev 1.
- Snapshots hold arguments only, never a solution.

### 5.3 Migration

`PlanLog(world_id)` checks for migration on first use. If `<world>.json` exists and
`migrated.json` does not, then under the world lock:

- each legacy `Plan` becomes a `create` commit with actor `{"kind":"migration"}`, a snapshot
  at v1, and a key. Its `exclude_recipes` becomes `banned`; `name`, `notes`, `factory`,
  `created`, `plan_id`, `provenance` and `siting` are kept.
- then `migrated.json` is written. The legacy file is never modified or deleted.

The migration is idempotent and one-shot. Legacy-file writes made after it (for example by a
master-branch server still running) are **not** picked up. It logs one warning when the legacy
mtime is newer than `migrated.json.at`.

**Never run against real user data during development.** Every test and manual run sets
`SATISFACTORY_USER_DATA` to a scratch directory.

---

## 6. `required` recipes (G4)

- `build_scenario(..., required: list[str] | None = None)`. Each entry is a recipe class id
  (an exact display name is also accepted and resolved).
- **Refused by name, before solving:** an unknown or locked recipe (`required: "X" is not
  unlocked in this save`), or one matched by a `banned`/`exclude_recipes` pattern
  (`required "X" is banned by "Recycled"`). These go through the existing refusal channel,
  the same way an unmatched ban pattern does.
- **Effect:** for each required recipe R with main product I (its first product), every
  *other* recipe whose main product is I is excluded, on top of `exclude_recipes`. A recipe
  unlocked later is therefore still excluded. Recipes that make I only as a byproduct are
  untouched. If two required recipes share I, both stay.
- When the LP is infeasible while `required` is non-empty, the failure notes gain one line:
  `required in force: <names> -- removing one may make this feasible`.
- `PLAN_ARGS`, `PLAN_DEFAULTS` and `recall_plan` carry `required`. It is part of `plan_id`
  only when non-empty, so existing plan ids do not move.

---

## 7. Python store API

Module `satisfactory_mcp/domain/planning/planlog.py` (store stage). All of it is importable
without game data.

```python
SNAPSHOT_EVERY = 50
OBJECTIVES = ("max_mw", "max_item", "min_raw", "min_machines", "min_power")

@dataclass(frozen=True)
class Actor:
    kind: str                     # "chat" | "page" | "migration" | "system"
    client: str = ""
    pid: int = 0
    def to_dict(self) -> dict: ...
    def display(self) -> str: ...  # §3.2 actor words

@dataclass
class PlanArgs: ...               # the §2 table, every field with its default
    def to_dict(self) -> dict: ...
    def kwargs(self) -> dict: ...  # build_scenario kwargs: banned->exclude_recipes, defaults dropped

@dataclass
class PlanState: ...              # the §2 table
    def kwargs(self) -> dict: ...  # == self.args.kwargs()
    def to_dict(self) -> dict: ...

@dataclass
class Commit:
    rev: int; base_rev: int; ts: float; actor: Actor; sav: str
    ops: list[dict]; merged_over: list[int]; undoes: int | None; note: str
    def to_dict(self) -> dict: ...
    def text(self) -> str: ...     # describe_commit(self)

@dataclass
class Conflict:
    key: str; mine: dict; theirs: dict; theirs_rev: int; theirs_actor: Actor
    def text(self) -> str: ...     # "rate Heavy Modular Frame: you 12, page set 15 in v13"

@dataclass
class Pushed:
    key: str; rev: int; base_rev: int
    applied: list[dict]            # ops as appended (with was)
    dropped: list[dict]            # mine dropped as already applied or no-op
    merged_over: list[int]
    others: list[Commit]           # the commits merged over, for "others changed ..."
    noop: bool
    state: PlanState               # head after the push
    def text(self, name: str) -> str: ...  # "merged onto v14 (you were on v11) -> v15; others changed: ..."

class PlanLogError(Exception): ...
class InvalidOp(PlanLogError, ValueError): ...
class UnknownPlan(PlanLogError, KeyError): ...          # .known: list[str] names
class NameTaken(PlanLogError): ...                      # .name
class Forgotten(PlanLogError): ...                      # .key, .rev (the forget commit)
class AlreadyUndone(PlanLogError): ...                  # .rev, .by (the undo commit's rev)
class BaseRevRequired(PlanLogError): ...                # .head
class Outdated(PlanLogError):
    head: int; base_rev: int
    since: list[Commit]            # every commit in (base_rev, head]
    conflicts: list[Conflict]
    state: PlanState               # the head
    def text(self, name: str) -> str: ...  # §10.3 chat wording

def describe_op(op: dict) -> str: ...
def describe_commit(commit: Commit) -> str: ...
def merge_key(op: dict) -> str | None: ...              # None for record
def inverse(ops: list[dict]) -> list[dict]: ...
def diff_args(base: PlanArgs | dict, new: dict, partial: bool = False) -> list[dict]: ...

Stamp = Callable[[PlanState], dict]

class PlanLog:
    def __init__(self, world_id: str, session_name: str = "") -> None: ...  # migrates if due
    @staticmethod
    def dir_for(world_id: str) -> Path: ...
    def keys(self) -> list[str]: ...
    def heads(self, include_forgotten: bool = False) -> list[PlanState]: ...
    def find(self, name_or_key: str, include_forgotten: bool = False) -> PlanState | None: ...
    def state(self, key: str, rev: int | None = None) -> PlanState: ...   # UnknownPlan, InvalidOp
    def head_rev(self, key: str) -> int: ...
    def commits(self, key: str, since: int = 0, until: int | None = None) -> list[Commit]: ...
    def create(self, name: str, args: dict, *, actor: Actor, sav: str = "", notes: str = "",
               factory: str = "", siting: dict | None = None, plan_id: str = "",
               provenance: dict | None = None, created: str = "") -> Pushed: ...   # NameTaken, InvalidOp
    def push(self, key: str, base_rev: int, ops: list[dict], *, actor: Actor, sav: str = "",
             stamp: Stamp | None = None, note: str = "") -> Pushed: ...
    def push_args(self, key: str, base_rev: int, args: dict, *, actor: Actor, sav: str = "",
                  extra: list[dict] | None = None, stamp: Stamp | None = None,
                  note: str = "") -> Pushed: ...   # extra: site/notes/factory ops in the same commit
    def undo(self, key: str, base_rev: int, rev: int, *, actor: Actor, sav: str = "",
             stamp: Stamp | None = None) -> Pushed: ...
    def restore_to(self, key: str, base_rev: int, rev: int, *, actor: Actor, sav: str = "",
                   stamp: Stamp | None = None) -> Pushed: ...
    def push_at_head(self, key: str, ops: list[dict], *, actor: Actor, note: str = "") -> Pushed: ...
    def migrate(self) -> dict[str, str]: ...   # name -> key; {} when nothing to do
```

Every write may also raise `filelock.LockTimeout`.

**Compatibility view.** `WorldState.plans` returns `PlanLog.view()`. It is a read-only object
with `.world_id`, `.plans` (live plans as `Plan`-compatible objects carrying `.key` and `.rev`
alongside `.name`, `.args`, `.kwargs()`, `.notes`, `.plan_id`, `.factory`, `.created`,
`.provenance` and `.siting`) and `.find(name)`. `recall`, `diff_service`, `layout_service`,
`siting`, `spatial.origin` and `routers/plans.py` keep working unchanged.

`PlanStore`'s write paths (`editing`, `save`, `put`, `remove`) are deleted. `store.py` keeps
`Plan` and `PLAN_ARGS` (+ `required`), and `PlanStore.load` only for migration.
`edits.repoint_plans` is rewritten on `push_at_head`, and `rename(...)` gains
`actor: Actor | None = None`.

**`recall_plan`** adds the note `recalled plan "<name>" v<rev>` as its **first** note, so every
tool that recalls a plan prints its version.

---

## 8. Activity journal (owned by "tools")

Module `satisfactory_mcp/domain/planning/journal.py`. One file per writing process:
`activity/<world>/<writer>.jsonl`, where `<writer>` is `chat-<pid>` for an MCP process and
`web-<pid>` for the web server. Append-only, and a single writer per file needs no lock. The
append pattern and torn-tail rule are those of §5.1.

Entry, one line, under 1 kB:

```json
{"id": "chat-8248:17", "seq": 17, "ts": 1790000000.2,
 "actor": {"kind": "chat", "client": "claude-code", "pid": 8248}, "sav": "sav:3f2a91c0aa11",
 "kind": "plan.solve", "tool": "plan_factory", "plan": null, "rev": null,
 "args": {"objective": "min_machines", "exports": ["Heavy Modular Frame"],
          "export_minimums": {"Heavy Modular Frame": 15}},
 "text": "solved HMF 15/min (min_machines)"}
```

| `kind` | Written by | When | `plan`/`rev` | `args` |
|---|---|---|---|---|
| `plan.solve` | tools | `plan_factory` **without** `save_as` (feasible or not) | the recalled plan's key/rev when `plan=` was given, else null | the solved request as `PlanArgs` fields, non-default only (`banned`, not `exclude_recipes`) |
| `plan.view` | tools | `plan_layout`, `diff_vs_save`, `commission_plan` called with `plan=` | key/rev | null |
| `plan.rejected` | web | a page push answered 409 | key/head | null; `text` = the conflicts |

`seq` counts up per file from 1 and `id` = `<writer>:<seq>`. A journal write that fails
(`OSError`) is swallowed: journalling never fails a tool.

```python
def writer_name() -> str: ...                               # "chat-<pid>" | "web-<pid>", set by the process
def set_writer(kind: str) -> None: ...                      # "chat" (MCP server start) | "web" (app lifespan)
def append(world_id: str, kind: str, *, actor: Actor, sav: str = "", tool: str = "",
           plan: str | None = None, rev: int | None = None, args: dict | None = None,
           text: str = "") -> dict | None: ...             # the entry, or None if not written
def read(world_id: str, since_ts: float = 0.0, limit: int = 200) -> list[dict]: ...  # all writers, ts-ascending
def files(world_id: str) -> list[Path]: ...
def tail(path: Path, offset: int) -> tuple[list[dict], int]: ...  # new complete entries + new byte offset
```

There is no retention in the slice (N2 open).

---

## 9. Page focus (owned by "web")

Module `satisfactory_mcp/domain/planning/focus.py`. File `ui/<world>.json`, with one writer
(the web process), written via `atomic.write_text` and no lock.

```json
{"schema": 1, "heartbeat": 1790000004.0, "view": "planner", "dash": "planner/a1b2c3d4",
 "plan": "a1b2c3d4", "rev": 14, "tab": "workbench",
 "selection": {"kind": "process", "label": "Blender · Diluted Fuel", "ref": "Recipe_..."},
 "follow": "follow", "sav": "sav:3f2a91c0aa11"}
```

- `view` ∈ `map`, `dashboard`, `planner`. `tab` is free text.
- `selection` is null or `{kind, label, ref}`.
- `follow` ∈ `follow`, `toasts`, `off`.
- `sav` is the save the page has loaded (the token the header shows), on every view.
- The page writes focus once the world and its save have loaded, after every save switch,
  whenever the view, tab, selection or the open plan's head rev changes, and on a 15 s
  heartbeat.

```python
OPEN_WITHIN_S = 45.0
def write(world_id: str, focus: dict) -> dict: ...   # validates, stamps heartbeat=time.time()
def read(world_id: str) -> dict | None: ...          # None when absent or unreadable
def is_open(focus: dict | None, now: float | None = None) -> bool: ...
```

---

## 10. MCP tools (owned by "tools")

### 10.1 Signatures

Unchanged arguments are elided as `…`. New parameters use `Annotated[..., Field(description=...)]`
with one-line descriptions.

```python
plan_factory(…, required: list[str] | None = None,        # "recipes that must make their item; others for it are excluded"
             base_rev: int | None = None, …)              # "the plan version you read; needed to save over an existing plan"
list_plans(name=None, save=None, world=None, as_of=None)                     # shows versions
forget_plan(name, base_rev: int | None = None, save=None, world=None, as_of=None)
rename_plan(name, to, base_rev: int | None = None, save=None, world=None, as_of=None)
site_plan(plan, at="", yaw_deg=None, footprint="", clear=False,
          base_rev: int | None = None, save=None, world=None, as_of=None)
plan_log(name: str,
         since: int | None = None,        # "list versions after this one"
         undo: int | None = None,         # "undo this version (needs base_rev)"
         restore: int | None = None,      # "make the head equal this version (needs base_rev)"
         base_rev: int | None = None,
         limit: Limit = 15, save=None, world=None, as_of=None) -> str
ui_context(save=None, world=None) -> str
```

`required` members are resolved to recipe class ids by the tool before the store sees them,
by exact class id or exact display name among **all** recipes. An ambiguous or unknown name is
refused by name. Locked recipes pass through, and the solve refuses them (§6).

Each tool that writes gains `ctx: Context` (FastMCP injects it, and it is not in the schema).
The actor is `Actor("chat", ctx.session.client_params.clientInfo.name or "", os.getpid())`,
falling back to `client=""` on any `AttributeError`.

`FastMCP("satisfactory", instructions=...)` gets one line: *"Plans are versioned: read one
(list_plans name=) and pass its version as base_rev when you change it. When the user says
'this', 'here' or 'what I have open', call ui_context first."*

### 10.2 Behaviour

| Tool | Rule |
|---|---|
| `plan_factory save_as=<new name>` | `create`. `base_rev` is ignored, with a note. The journal is not written (the `plans` event covers it) |
| `plan_factory save_as=<existing>` | `base_rev` missing: refuse, `! plan "x" exists at v14: read it (list_plans name="x") and pass base_rev=14; nothing saved`. Present: `push_args` with `partial=False` of the merged request. When `plan=` recalls the same plan, that request is the plan at `base_rev` plus this call's overrides, never the head, so edits made since stay theirs. `<existing>` is the live name, else (with `base_rev`) the plan that had that name at `base_rev` or has it as key, so a rename since merges instead of creating a second plan; two such plans refuse and ask for the key. Notes, factory, and a site from `site_at` go in as `extra` ops in the same commit. A stamp is passed. The reply ends with the merged note or the outdated text |
| `plan_factory` without `save_as` | journal `plan.solve` |
| `rename_plan`, `forget_plan`, `site_plan` | `base_rev` required on the same refusal pattern. Ops: `rename` / `forget` / `site`. `site_plan clear=True` → `site value=null` |
| `plan_log` | Without `undo`/`restore`: commits newest first, `limit` rows, `since` filter, as `describe_commit` lines plus the key and head. `undo=R` → `PlanLog.undo`. `restore=R` → `restore_to`. Both need `base_rev`. Finds forgotten plans too, so a forget can be undone |
| `list_plans` | Adds a `ver` column (`v14`) and a `last change` column (`Claude Code 2h: sloops 0→4`, cut to 36). `name=` detail header: `# plan "north hmf" v14 (key a1b2c3d4)` |
| `plan_layout`, `diff_vs_save`, `commission_plan` with `plan=` | Version via the recall note (§7). Journal `plan.view` |
| `rank_unlocks` with `plan=` | Version via the recall note. Its own "recalled saved plan" note is removed as a duplicate |
| `ui_context` | §10.4 |

The rename collision check, `find`, and "no saved plan named" wording stay as today, reading
through `PlanLog`. `LockTimeout` → `! plans are busy (another writer held the lock 10 s); nothing written`.

### 10.3 Outdated and merged wording (chat)

```
! outdated: plan "north hmf" is at v14; you wrote against v11. Nothing was applied.
conflicts: rate Heavy Modular Frame: you 12, page set 15 in v13
since v11: v12 page: sloops 0→4 · v13 page: rate Heavy Modular Frame 10→15/min · v14 Claude Code: +banned Bolted Frame
re-read (list_plans name="north hmf") and push again with base_rev=14
```

```
merged onto v14 (you were on v11) -> now v15; others changed: v12 sloops 0→4 (page), v13 rate Heavy Modular Frame 10→15/min (page)
```

A no-op push says `nothing changed: plan "north hmf" is still v14`.

### 10.4 `ui_context` output

```
# page open (heartbeat 4s ago) · world "Spire" · page sav:3f2a… = yours
focus: planner › "north hmf" v14 › workbench   selected: process "Blender · Diluted Fuel"
follow: follow
since you last looked: "north hmf" v11 -> v14 by page: v12 rate Heavy Modular Frame 10→15/min · v13 +banned Bolted Frame
  · journal: 14:06 Claude Code (other session) solved HMF 15/min
```

- `page closed (last heartbeat 3h ago)` / `page never opened for this world` when stale or
  absent.
- **Cursor:** per MCP process, in memory, `{world_id: (journal_ts, {key: rev})}`. It advances
  on each call.
  - First call in a process: the last 5 commits across plans and the last 5 journal entries,
    labelled `(first look this session: last 5)`.
  - This process's own commits and own journal entries are excluded (by `pid`).
- Stays under the 4000-character budget (`test_surface.BUDGET`); cut lists end in `(+N more)`.

---

## 11. Web (owned by "web")

A new router `routers/planner.py` goes at the **end** of `ALL_ROUTERS`. If it would pass 700
lines, split it into `planner.py` (solve, focus, activity) + `planlog.py` (plan CRUD and ops),
both appended. Every write passes `guard.py` unchanged. Every handler declares
`response_model`, and 409 bodies are declared with `responses={409: {"model": OutdatedResponse}}`
so they reach `api-schema.d.ts`. Handler names below are the operation ids.

Plans are addressed by `key`. `?world=`/`?save=` work as on every route (the world id comes
from `_state`). The page sends `actor` implicitly: the server stamps `Actor("page", "", os.getpid())`.

### 11.1 Routes

| Handler | Method, path | Request body | 2xx response | Errors |
|---|---|---|---|---|
| `plans` (existing, `routers/plans.py`) | GET `/api/plans` | – | `PlansResponse` **+ `index: list[PlanIndexRow]`**, and `PlanSiting` **+ `key`** (additive) | 404 |
| `create_plan` | POST `/api/plans` | `CreatePlanBody {name: str, args: dict, from_entry: str = ""}` | 201 `PushedResponse` | 400 invalid, 409 `{error, name_taken: true}`, 503 lock |
| `plan_state` | GET `/api/plans/{key}?rev=` | – | `PlanStateBody` | 404 unknown key or rev |
| `plan_ops` | GET `/api/plans/{key}/ops?since=0` | – | `PlanOpsResponse {key, head, commits: list[CommitBody]}` | 404 |
| `push_ops` | POST `/api/plans/{key}/ops` | `PushBody {base_rev: int, ops: list[dict], sav: str = ""}` | 200 `PushedResponse` | 400 `InvalidOp`, 404, **409 `OutdatedResponse`**, 410 forgotten, 503 |
| `push_args` | POST `/api/plans/{key}/args` | `PushArgsBody {base_rev: int, args: dict, sav: str = "", from_entry: str = ""}` | 200 `PushedResponse` | as `push_ops` |
| `undo_rev` | POST `/api/plans/{key}/undo` | `UndoBody {base_rev: int, rev: int, sav: str = ""}` | 200 `PushedResponse` | 400, 404, 409 `OutdatedResponse` or `{error, already_undone: true, by}`, 503 |
| `solve_plan` | POST `/api/plan/solve` | `SolveBody {args: dict | null = null, key: str | null = null, rev: int | null = null}` (exactly one of `args`, `key`) | 200 `SolveResponse` (an infeasible plan is a 200 with `feasible: false`) | 400, 404 |
| `put_focus` | PUT `/api/ui/focus` | `FocusBody` (§9 fields except `heartbeat`) | 200 `FocusResponse {ok: true, heartbeat: float}` | 400 |
| `activity` | GET `/api/activity?since=0&limit=50` | – | `ActivityResponse {now: float, entries: list[ActivityRow]}` | 404 |

`push_ops`, `push_args` and `undo_rev` pass a stamp (§4.4) built from the request's world
state. `from_entry` goes into the commit `note` as `applied chat solve <id>`. A 409 from
`push_ops`/`push_args` also journals `plan.rejected` (§8).

### 11.2 Response models (TypedDict, declared in emission order)

```python
class PlanOpBody(TypedDict, total=False):   # op, field, value, item, member, name, was, state
    op: str; field: str; value: Any; item: str; member: Any; name: str; was: Any
class ActorBody(TypedDict): kind: str; client: str; pid: int; display: str
class CommitBody(TypedDict):
    rev: int; base_rev: int; ts: float; actor: ActorBody; sav: str
    ops: list[PlanOpBody]; merged_over: list[int]; undoes: int | None; note: str; text: str
class PlanArgsBody(TypedDict): ...            # every §2 PlanArgs field, in §2 order, exact types
class PlanStateBody(TypedDict):
    key: str; rev: int; name: str; forgotten: bool; notes: str; factory: str; created: str
    plan_id: str; siting: dict | None; args: PlanArgsBody; names: dict[str, str]
    head: int; text: str   # text = head commit's describe
class PushedResponse(TypedDict):
    key: str; rev: int; base_rev: int; noop: bool; merged_over: list[int]
    applied: list[PlanOpBody]; dropped: list[PlanOpBody]; others: list[CommitBody]
    text: str; state: PlanStateBody
class ConflictBody(TypedDict):
    key: str; mine: PlanOpBody; theirs: PlanOpBody; theirs_rev: int; theirs_actor: ActorBody; text: str
class OutdatedResponse(TypedDict):
    error: str; outdated: bool; head: int; base_rev: int
    since: list[CommitBody]; conflicts: list[ConflictBody]; state: PlanStateBody
class PlanLast(TypedDict): rev: int; ts: float; actor: ActorBody; text: str
class PlanIndexRow(TypedDict):
    key: str; name: str; rev: int; objective: str; target_item: str | None
    exports: list[str]; rates: dict[str, float]; sited: bool; factory: str; plan_id: str; last: PlanLast
class SolveRow(TypedDict):
    building: str; recipe: str; recipe_id: str | None; item: str | None
    machines: int; clock: float; mw: float
    inputs: list[Rate]; outputs: list[Rate]; required: bool
class Rate(TypedDict): item: str; per_min: float
class SolveResponse(TypedDict):
    feasible: bool; headline: str; cause: str; plan_id: str; notes: list[str]; warnings: list[str]
    machines: int; processes: int; mw_draw: float; mw_generated: float; mw_net: float
    grid_import: bool; exports: list[Rate]; rows: list[SolveRow]
    shards: int | None; sloops_used: int; blockers: list[str]; token: str
class ActivityRow(TypedDict):
    id: str; ts: float; source: str          # "plan" | "journal"
    actor: ActorBody; kind: str              # "commit" | journal kinds
    plan: str | None; name: str | None; rev: int | None; text: str; args: dict | None
class ActivityResponse(TypedDict): now: float; entries: list[ActivityRow]
```

- `SolveResponse` is built by a new domain function
  `domain/planning/summary.py: solve_summary(g, st, kwargs: dict, required: list[str]) -> dict`
  (web-owned), from `build_plan_report`.
- `blockers` names required/banned entries from the §6 refusals.
- `cause` is one player sentence saying why an infeasible request has no answer (a source
  that matches nothing, an export that is not an item, a raw input the sources lack, or the
  rates asked for); it is empty when feasible. `headline` and `notes` keep the tool wording.
- `names` gives display words for the ids a plan holds: recipe class ids in
  `required`/`banned`/`only_recipes`, `node:` selectors (their resource), and item class ids
  in the exports. Patterns and names typed as words are not keyed.
- A plans or labels file with a `schema` newer than this version reads is a **503** on every
  route that touches it: `{"error": ..., "newer_schema": true}`. The error names what cannot
  be read and never the file's path; nothing is written.
- `rows` are sorted building, then recipe.
- `mw_*` are exact MW. `–`-style unknowns are null, never 0.

### 11.3 SSE (`/api/events`)

`watch.py` gains a **plan/journal tailer** polling every **0.5 s**. It stats each
`plans/<world>/*/ops.jsonl` and `activity/<world>/*.jsonl` and reads only the new bytes
(offsets kept in memory; at startup the offsets are the current sizes). The 3 s save poll is
unchanged. `QUEUE_MAX` rises to 32. A subscriber whose queue overflows is cut: it gets
nothing more, its stream ends once it has drained, and the browser reconnects. On any
reconnect after a lost connection the page resyncs: it refetches what every event kind would
have refetched, pulls the open plan's commits since its rev and adopts the head, and reads
`GET /api/activity?since=<newest entry it heard>` through the same handler as the `activity`
event (deduplicated by id). The replay of `latest` alone is one event per kind across all
plans, so it cannot stand in for this.

| Event | When | Data |
|---|---|---|
| `save` | unchanged | unchanged |
| `notes` | **narrowed**: `labels/**/*.json` and the legacy top-level `plans/*.json` only | unchanged |
| `plans` | new commits in one plan's `ops.jsonl` since the last tick; **one event per plan per tick** | `{"world", "key", "name", "rev", "from_rev", "actors": [ActorBody], "text": <newest commit text>, "ts", "forgotten"}` |
| `activity` | each new journal entry | `{"world", "id", "ts", "actor": ActorBody, "kind", "plan", "rev", "text", "args"}` |

The replay-on-connect of the newest event per kind stays. The page treats a replayed
`plans`/`activity` event as not-news when its `ts` is older than page open minus 2 s (the
existing `isNews` rule).

### 11.4 Schema regeneration

Never against ports 8712–8714. Either:

- offline: dump `create_app().openapi()` to a scratch JSON file, then
  `npx openapi-typescript <file> -o src/api-schema.d.ts && node scripts/stamp-schema.mjs`; or
- a throwaway server on a port in 8920–8999 (web-wire rule 6).

The web group commits the regenerated `api-schema.d.ts`.

---

## 12. Page behaviour (owned by "page")

Frontend only: TypeScript under `frontend/src/`, no new dependencies, no new hues. The chat
badge is the text "chat". Conflict chips and strips reuse existing classes and existing
palette tokens.

### 12.1 Where

- A **Planner** tab in the dashboard: `dash=planner` is the plans list, `dash=planner/<key>`
  is the workbench.
- The code lives in new modules (`planner.ts`, optionally `planner-*.ts`). `dashboard.ts`
  only adds the tab to `Tab`/`TABS` and delegates `render` for it.
- The planner modules never import `dashboard.ts` (no ring).
- A new module that calls `registerFetch` must be added to the FEATURES block in `main.ts`
  (test_architecture checks this).

### 12.2 Plans list

- A table from `GET /api/plans` `index`: name, objective, target and rate, `vN`, last change
  (`actor · age · text`), **[open]**.
- **New plan:** an item + rate form, which creates with the `design_factory` preset
  (`objective=min_machines`, `exports=[item]`, `export_minimums={item: rate}`) and auto-names
  it `<Item> <rate>/min`, adding ` (2)` on a 409 name clash. It then opens the new plan.

### 12.3 Workbench

- **Header:** `plan "name" vN · saved | pushing… | conflict`, then
  `last: <actor> <age> ago`, then **[copy as tool call]**, which copies
  `plan_factory(plan="name")  # base_rev=N`.
- **Controls → ops.** One commit per gesture. Text and number fields commit on Enter, blur or
  change, never per keystroke (N5).

| Control | Op(s) |
|---|---|
| objective select | `set objective` |
| target item (text) + rate (number) | `put export_minimums[item]`. A new item adds `add exports item` + `put`, and the old item's `del`/`remove` in the same commit |
| exports chips (MW is an explicit chip), add box | `add`/`remove exports` |
| sources chips + text add | `add`/`remove sources` |
| required chips; **[require]** on a build-list row | `add required <recipe_id>`, and the same commit `remove banned <recipe_id>` when present |
| banned chips + pattern add box; **[ban]** on a build-list row | `add banned <recipe_id or pattern>`, and the same commit `remove required` when present |
| water extractors (number, blank = auto) | `set water_extractors` |
| sloops (number) | `set sloops` |
| extractor clocks: toggles 100/150/200/250 % | `add`/`remove extractor_clocks` |

- **Result panel:** after every new head, re-solve with `POST /api/plan/solve {key, rev}`, and
  drop stale replies by sequence number. It shows:
  - a result card: one key/value line (machines/processes, MW draw/generation/net, exports,
    shards), the warnings, and the power budget below it; while a newer version solves, the
    card says "solving vN…" and the shown result is dimmed;
  - a build list (`rows`, each with [require]/[ban]);
  - a power budget: plan draw against the dashboard's existing headroom figures (nameplate
    and measured), before/after, red when negative, neither leading.
  - An infeasible head shows "not solvable: <cause>" and **[undo vN]** in the result card,
    with the last solvable version greyed below it. On open, that version is found by solving
    earlier revs (at most 20 back), so a reopened plan keeps it.
- **Undo** starts from the ops log: the page's own commits that nothing has undone. The
  buttons are disabled when there is nothing to undo or redo, and while the plan is
  forgotten (the controls are disabled too, with a **[restore]** button).
- **Push flow:** `POST /api/plans/{key}/ops {base_rev, ops}`. `base_rev` is the rev the
  user SAW when making the gesture, not the rev when the queued write runs. It is advanced to
  the current rev only when every rev in between is this tab's own commit; otherwise the
  commits in between go through M1. When a 409 lists only conflicts against this tab's own
  commits after that base, the write is re-sent against the head it returned (the other
  commits in between were already checked and do not clash). A queued write whose plan was
  left is still sent, to that plan; if it is refused the toast names the plan. Every exit of a
  write, including those, ends its "pushing…".
  - 200 → adopt `state`, set the rev, re-solve. If `others` is non-empty, show the chat strip
    for them.
  - 409 → adopt `state` (the head), re-solve, and put a **conflict chip** on each collided
    control: *"<conflict text>: [keep chat's] [use yours]"*.
    **Use mine** re-pushes the mine op with `base_rev = head`. Chips never block other
    editing.
  - 410 → toast plus a back-to-list link.
  - 503 → toast; the control reverts to the head value.
- `api.ts` gains `PUT` in `send` and a push helper that returns the 409 body instead of
  throwing.
- **Undo/redo:** Ctrl+Z (not while typing in a field) → `POST …/undo {base_rev, rev}` for
  this tab's newest own commit not yet undone. Ctrl+Shift+Z undoes that undo commit. The
  [undo]/[redo] buttons do the same. The stack is in memory, per tab and per plan. An entry
  leaves the stack only when the server answers: on success, on `already_undone` (then skip to
  the next), on a no-op, or on an outdated 409 (the toast says why it cannot be undone). A 503
  or a network failure leaves the stack as it was.
- **Chat strip:** when commits by other actors land on the open plan, one line per commit
  (`v13 Claude Code: +banned Bolted Frame`) with **[undo]** each. It stays until dismissed or
  the plan is left.

### 12.4 Following (settings registry)

- A new `settings.ts` entry, `choice` key `follow`, "Follow chat": `follow` (default) /
  `toasts` / `off`.
- On `plans` SSE:
  - open plan with `key` matching → `GET /api/plans/{key}/ops?since=<page rev>`, adopt the
    head (`GET /api/plans/{key}`), re-solve, strip.
  - plans list visible → refetch `GET /api/plans`.
  - **Mid-gesture** (a planner field focused with an uncommitted value) → hold until commit
    or blur, then apply.
- On `activity` from another actor:

| entry | follow | toasts | off |
|---|---|---|---|
| `plan.solve` | go to `dash=planner` (or stay on the open plan) and show the **from-chat card**: text, args summary, **[apply to this plan]** (only when a plan is open → `POST …/args {base_rev, args, from_entry: id}`; `base_rev` is the entry's `rev` when chat solved from this plan, so edits made since merge or conflict under M1; otherwise the head, and the card says the apply replaces the whole request), **[new plan from it]** (`POST /api/plans {name auto, args, from_entry}`), **[dismiss]** | toast with [open] | nothing |
| `plan.view` | open `dash=planner/<key>` | toast | nothing |

- Following never moves the screen mid-gesture. It waits for the gesture to end.
- The page ignores activity entries whose `actor.kind` is `page`.

### 12.5 Focus heartbeat

`PUT /api/ui/focus` on every planner navigation and selection change (debounced 1 s), and
every 15 s while `document.visibilityState === "visible"`. It carries `view`, `dash`,
`plan`, `rev`, `tab` (`workbench`/`list`), `selection` (the clicked build-list row as
`{kind: "process", label, ref: recipe_id}`), `follow` and `sav`. A failure is silent.

---

## 13. File ownership

Four owners. **Store stage** runs first and alone. **tools**, **web** and **page** then run
in parallel, with disjoint files. Anything not listed belongs to nobody in this slice: ask the
lead before touching it. This contract is read-only for all four.

| Group | Owns (create or edit) |
|---|---|
| **store** (next stage, before the three) | `src/satisfactory_mcp/domain/planning/planlog.py` (new); `domain/planning/store.py`; `domain/planning/recall.py`; `domain/planning/scenario.py` (G4 `required`); `domain/planning/prepare.py` and `domain/planning/report.py` only as far as G4's infeasibility note needs; `domain/factories/edits.py`; `domain/world/state.py` (`plans` property); `src/satisfactory_mcp/config.py` (`activity_dir`, `ui_dir`); `tests/test_planlog*.py` (new); `tests/test_plan_store.py`; `tests/test_required_recipes.py` (new); `tests/test_label_edits.py` (repoint only) |
| **tools** | `src/satisfactory_mcp/interfaces/mcp/tools/planning.py`; `interfaces/mcp/tools/factories.py` (pass `actor` to `edits.rename` only); `interfaces/mcp/tools/spatial.py` only if a `plan:` recall there needs the view; `interfaces/mcp/app.py` (`instructions`, actor helper); `src/satisfactory_mcp/server.py` (`journal.set_writer("chat")`); `src/satisfactory_mcp/domain/planning/journal.py` (new); `presenters/text/**` only where a planning presenter must print the version; `tests/test_plan_tools_log.py` (new); `tests/test_journal.py` (new); `tests/test_ui_context.py` (new); `tests/test_surface.py`; `tests/test_plan_provenance.py`, `tests/test_planner_gaps.py`, `tests/test_siting.py` (only where they call the plan tools' write paths); `docs/mcp-surface.md` |
| **web** | `src/satisfactory_mcp/interfaces/web/routers/planner.py` (new), `routers/planlog.py` (new, if split); `routers/plans.py`; `routers/naming.py` (pass `actor` to `edits.rename` only); `routers/events.py`; `routers/__init__.py` (append only); `interfaces/web/watch.py`; `interfaces/web/app.py` (lifespan: `journal.set_writer("web")`, tailer start); `interfaces/web/serial.py` (only if a shape is shared by two routers); `src/satisfactory_mcp/domain/planning/summary.py` (new); `src/satisfactory_mcp/domain/planning/focus.py` (new); `frontend/src/api-schema.d.ts` (regenerated, never hand-edited); `tests/test_web_planner.py` (new), `tests/test_web_plans.py`, `tests/test_web_events.py`, `tests/test_watch.py`, `tests/test_web_naming.py`, `tests/test_focus.py` (new); `docs/web-wire.md` |
| **page** | `src/satisfactory_mcp/interfaces/web/frontend/src/planner*.ts` (new); `frontend/src/dashboard.ts` (Planner tab wiring only); `frontend/src/settings.ts` (`follow` entry); `frontend/src/sse.ts` (`plans`/`activity` listeners); `frontend/src/api.ts` (PUT, 409-returning push); `frontend/src/api-shapes.ts` (aliases for §11.2 names); `frontend/src/main.ts` (FEATURES line, only if a planner module registers a fetch); `frontend/src/style.css`; `frontend/src/toast.ts` (only if a toast needs an action button); `frontend/index.html` (only if the tab needs markup) |

**Hand-over rule.** The store stage leaves the suite green. Any existing test it breaks by
removing `PlanStore`'s write paths, it fixes, whoever owns that file later. Ownership in the
table starts when the store stage has committed.

**Cross-group seams** (each side codes against this contract, never against the other's
unfinished code):

- tools → `planlog`, `journal` (own), `focus.read` (web's). tools may stub `focus.read` in its
  tests with a temp file in the §9 format.
- web → `planlog`, `journal.read/tail/append` (tools'), `focus` (own), `summary` (own).
- page → the §11 routes and models, via `api-schema.d.ts` as regenerated by web. Until web
  lands the schema, page may type against the §11.2 names with `api-shapes.ts` aliases, and
  `npm run check` goes green once web's schema lands.

**Test rules for every group:** run in the worktree with
`PYTHONPATH=<worktree>/src` and the main venv's python, and confirm
`satisfactory_mcp.__file__` points into the worktree. `SATISFACTORY_USER_DATA` points at a
scratch directory. Never touch ports 8712–8714. The comment budget holds, and prose goes in
docs. Conventional commits, no trailers.

---

## 14. Departures from planner_vision.md

| # | Vision says | Slice does | Why |
|---|---|---|---|
| S1 | One op per log line with its own `rev` | One **commit** (rev) per line holding one or more ops | "Nothing applied partially" and "one gesture = one version" need a multi-op atomic unit. `restore` already needed one |
| S2 | Snapshot every 50 **ops** | Every 50 **revs** | Follows from S1 |
| S3 | `plan_id` / provenance not discussed for page edits | `record` ops via a stamp inside the push | Without it every page edit makes `list_plans` say "world moved" |
| S4 | M1 "required vs banned of one recipe" | Conflict only on the **same literal member**. A banned *pattern* that happens to match a required id is caught at solve time and named (§6) | The store has no game data, by design |
| S5 | `/api/plans` GET gives head rev, status, last op | `index` added to the existing response. `status` (world moved) is deferred to P2/G2 | Keeps the map layer's payload stable and the list cheap |
| S6 | `plans`/`labels`/`activity` replace `notes` | `plans` + `activity` added. `notes` narrowed but kept for labels | Labels are not on the log yet (G10) |
| S7 | P1 includes Ask chat, pins, resource mirror | Deferred to slice 2 | Keep the slice small. The loop is shown without them |
| S8 | Rate slider solves while dragging | Number field, push on commit | No slider in the slice; N5 still holds |
