# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow the scheme in
[docs/releasing.md](docs/releasing.md). History before 0.1.0 is not reconstructed.

## [Unreleased]

Planned as 0.2.0.

### Upgrade notes

- **BREAKING: do not downgrade after running this version.** Plans move from
  `plans/<world>.json` to a versioned op log under `plans/<world>/`. The migration runs once,
  on first access, and leaves the old file in place untouched; no separate backup copy is
  made. An older version keeps reading that old file, so it will not see plans created or
  changed after the upgrade, and anything it writes there is not carried back into the log.
  Do not run an older MCP server next to a newer web server.
- Rebuild the web page (`npm ci && npm run build`) or unzip the release's `static.zip`.

### Added

- Plans are stored as an append-only op log with revisions, merge against the revision a
  writer saw, undo and restore. The MCP planning tools write through it and journal chat
  activity.
- Web: a planner tab with a versioned workbench that follows chat; routes for plan
  versions, merges, solves, focus and live plan events.
- Web: an inventory section (stock, containers, crates), a progress section (MAM, space
  elevator, hard drives, power shards, somersloops), a recipes codex with a header search
  box, and a supply-path trace drawn on the map for a machine or factory.

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
- Map clicks work again after a trace is closed; trace run tooltips are escaped.
- Leaving a factory rename unfinished no longer freezes the dashboard or side panel.
- A malformed `%` escape in the address no longer stops the page from loading.
- Power circuit names follow a factory rename without a reload.

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
