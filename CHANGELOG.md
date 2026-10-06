# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow the scheme in
[docs/releasing.md](docs/releasing.md). History before 0.1.0 is not reconstructed.

## [Unreleased]

Planned as 0.2.0.

### Upgrade notes

- **BREAKING: do not downgrade after running this version.** Plans move from
  `plans/<world>.json` to a versioned op log under `plans/<world>/`. The migration runs once,
  on first access, leaves the old file in place untouched and first copies it to
  `plans/<world>/backup-v<version>/`. An older version keeps reading that old file, so it
  will not see plans created or changed after the upgrade, and anything it writes there is
  not carried back into the log.
  Do not run an older MCP server next to a newer web server.
- Rebuild the web page (`npm ci && npm run build`) or unzip the release's `static.zip`.
- `world_summary` reports `last_hard_drive_analysed` in place of `last_hard_drive_spent`: the
  save's counter names the drive analysed last, which can still be pending.
- The power ledger leaves out machines and generators on no wire, so `power_report` and the
  header can read lower draw than before; `/api/power/circuits` reports them under `off_grid`.
- Label writes answer a bad name with 400 and an unknown label with 404 (both were 409); a
  409 carries `stale`, `name_taken` and `pin` flags. Names with `/` or over 60 characters
  are refused for new labels.
- *Stage headroom* and *count biomass burners* are shared by the page and chat, in
  `settings.json` in the user data dir. A browser's own earlier value is adopted once. Chat's
  `biomass=` now defaults to that setting, and a new `settings` tool reads and writes it.

### Added

- Plans are stored as an append-only op log with revisions, merge against the revision a
  writer saw, undo and restore. The MCP planning tools write through it and journal chat
  activity.
- Web: a planner tab with a versioned workbench that follows chat; routes for plan
  versions, merges, solves, focus and live plan events.
- Web: an inventory section (stock, containers, crates), a progress section (MAM, space
  elevator, hard drives, power shards, somersloops), a recipes codex with a header search
  box, and a supply-path trace drawn on the map for a machine or factory.
- Planner: a payback horizon per plan spreads rows over more, slower machines while the
  saved power, priced at the save's grid mix, repays them; an overclock-last switch builds a
  row one machine short. Both default from shared settings and are on `plan_factory` and
  `plan_layout`.

### Changed

- Map renders bake the live-sun lighting by default, from `python -m mapgen renders` and from
  the Maps tab alike, so a new map can be relit for any sun. `--no-light`, or unticking
  "live sun", draws the hillshade into the colour as before. `--unlit`, the old opt-in, is
  still accepted. With the light a full-size render is budgeted at about 10 minutes more and
  needs 14.5 GB more scratch space.

### Deprecated

- The map generator scripts `tools/gen_map_renders.py`, `gen_map_image.py`,
  `gen_world_heightmap.py`, `gen_paint_layers.py` and `check_map_fill.py` are now shims for
  `python -m mapgen <command>` and warn when run. They will be removed in 0.3.0.

### Fixed

- Chat saves merge against their base revision and across renames.
- A recalled plan's pinned logistics are journalled with its solve.
- The page resyncs plans and chat activity after a lost or overflowed event stream, and
  pushes writes against the revision the user saw.
- Trace: a factory trace is seeded by label, so a building name cannot win; flows into
  alternate-recipe groups stay on the trace card.
- Search drops locked recipes on the server when spoilers are off.
- Progress hides capability research in an unopened MAM tree.
- Recipe icons are probed once instead of one 404 per item.
- The world power ledger is the sum of its circuits; unwired machines no longer eat headroom.
- Standing Biomass Burners count their 30 MW; generators on no wire have their own list.
- Fluid buffers count towards stock, so the piles add up to the containers holding them.
- Building and hand-craft recipes show amounts per build, not a per-minute figure.
- Chat and the planner quote one net power figure; a recalled plan keeps its objective.
- An old save with no phase pointer no longer marks every Space Elevator phase complete.
- Retired MAM nodes are not listed or counted; item and building ids read as names.
- The Overview action list shows only machines needing action, counted across factories.
- The map and chat name an unnamed cluster the way Detect does, not by its building class.
- Detect numbers its suggestions like the map when filters hide clusters, and a guessed
  name prefers a made item to an ore. A name written after a rename in the same tab no
  longer fails as stale; a world switch mid-detect shows the new world's clusters only.
- Factories: the need-action list leaves paused machines out, name and rename errors show
  under the field, and an old deep link after a rename shows one back link.
- Map clicks work again after a trace is closed; trace run tooltips are escaped.
- Leaving a factory rename unfinished no longer freezes the dashboard or side panel.
- A malformed `%` escape in the address no longer stops the page from loading.
- Power circuit names follow a factory rename without a reload.
- A label holding `/` can be renamed or forgotten; a rename writes the label before
  repointing plans and reports any plan it could not move.
- Files written by a newer version are refused, not overwritten; a plan exporting power
  under any spelling stores it as `MW`.
- `npm run typegen` takes the server port as an argument or from `SATISFACTORY_WEB_PORT`.
- `python -m mapgen renders` no longer overwrites a map the registry lists: a run into its
  folder, through a junction or link too, is refused and names it. `--renders-name` writes
  beside it, and `--overwrite-in-use` replaces it anyway.

## [0.1.0] - 2026-09-27

First tagged version. It records the state of the project at that point rather than a
change set.

### Added

- An MCP server (`satisfactory-mcp`, stdio) with 49 tools, 3 prompts and resources covering
  game data, the player's world read from local saves, stock and storage, factory detection
  and health, map queries, factory planning with an LP optimizer, and hard-drive advice.
- A local web map (`satisfactory-mcp-web`, optional `web` extra): terrain, factories, belts,
  pipes, power wiring, floors and crates, following the saves live.
- `pioneersav`, a first-party save parser run behind a subprocess boundary.
- Factory names and plans kept in the platform's user data directory.

### Fixed

- Save parsing on the anniversary build: the archive version header, the lightweight
  record's trailing count, `InventoryItem` layouts and a fourth conveyor-chain variant
  (thanks @ledairain, #1 and #2).

[Unreleased]: https://github.com/lukszi/SatisfactoryMCP/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lukszi/SatisfactoryMCP/releases/tag/v0.1.0
