# Releasing

How versions are numbered, how branches relate, and how a release is cut.

## Version scheme

SemVer, still in `0.x`. The single source of truth is the static `version` in
`pyproject.toml`; bump it with `uv version --bump minor|patch`. Tags are annotated `vX.Y.Z`.

| Bump | When |
|---|---|
| `0.MINOR` | an MCP tool or parameter is removed or renamed, or a `retired()` spelling is deleted; a web route changes incompatibly; a one-way data migration; an entry point or install step changes |
| `0.x.PATCH` | fixes; support for a new game build; a projection `SCHEMA_VERSION` bump (a cache, so it only re-extracts); additive tools and parameters; wording |
| `1.0` | after one full minor release with no MCP vocabulary break |

## Branches

- **`master`** is the trunk. A ruleset blocks force-push and deletion; history on it is
  never rewritten.
- **`stable`** only ever fast-forwards to a release tag. Clone users who want a safe
  `git pull` track it.
- **Feature branches** (`feat/…`, `fix/…`, `docs/…`, `chore/…`) are merged locally with
  `git merge --no-ff` and a conventional message (`merge: …`), then pushed. The merge commit
  records when the work landed.
- **Hotfix branches** (`hotfix/0.x-…`) are cut from a tag; see below.
- `v*` tags are protected by a ruleset: they cannot be moved or deleted.

## Contributions

- Contributors branch from `master` and open a PR with a conventional title. PRs from forks
  should come from a topic branch, not the fork's default branch.
- PRs land with a GitHub merge commit (or a squash, when the branch history is noise). A
  human `Co-authored-by` trailer is kept.
- A PR that touches the save parser, a data format or the MCP vocabulary adds its upgrade
  notes to `CHANGELOG.md` under `[Unreleased]`.
- Contributors are credited by GitHub handle on their changelog line ("thanks @handle").

## Cutting a release

1. On `master`, run both halves of the suite. The integration half needs the game and real
   saves, so it is a local gate that no CI replaces:
   ```bash
   uv run pytest -q
   uv run pytest -q -m integration
   ```
2. `uv version --bump minor|patch`.
3. Move `[Unreleased]` in `CHANGELOG.md` to `## [0.x.y] - YYYY-MM-DD` and update the links
   at the bottom.
4. `git commit -m "chore(release): v0.x.y"`, then `git tag -a v0.x.y -m "v0.x.y"`.
5. `git push origin master v0.x.y`, then fast-forward stable:
   `git push origin v0.x.y^{commit}:stable`.
6. Build the page in a clean worktree at the tag (`npm ci && npm run build` in
   `src/satisfactory_mcp/interfaces/web/frontend`) and zip the *contents* of `static/`.
7. `gh release create v0.x.y static.zip --title v0.x.y --notes-file <notes>`. The notes are
   the changelog section plus install steps and how to unzip `static.zip` into
   `src/satisfactory_mcp/interfaces/web/static`.

## Hotfixes for a new game build

- If `master` is releasable, fix it there and release a patch.
- If `master` holds unreleased breaking work:
  1. `git switch -c hotfix/0.x-<topic> v0.x.y`
  2. Fix, bump the patch, tag `v0.x.(y+1)`, push the tag, fast-forward `stable` to it.
  3. Merge the hotfix branch into `master` with `--no-ff`.

## Compatibility rules

**MCP contract**

- A removed or renamed parameter keeps its `retired()` error for at least one minor release.
- A removed or renamed tool keeps a stub that errors with "use X instead" for one minor
  release.
- Output wording is not a contract unless [mcp-surface.md](mcp-surface.md) pins it.

**Data formats**

- `load()` should check `schema` and refuse a newer one ("written by a newer version,
  upgrade"): labels, the plan store and the plan log.
- A migration should copy the old files to `<dir>/backup-v<version>/` first and stamp the
  writer's version into its marker (`migrated.json` for the plan log).
- A one-way migration means a minor release and a "do not downgrade" upgrade note.
- A projection `SCHEMA_VERSION` bump stays a patch change.
