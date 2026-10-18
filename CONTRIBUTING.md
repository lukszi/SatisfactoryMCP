# Contributing

1. Branch from `master` with a topic branch (`feat/…`, `fix/…`, `docs/…`, `chore/…`). From a
   fork, use a topic branch, not the fork's default branch.
2. Run the tests (see [docs/DEVELOPING.md](docs/DEVELOPING.md) for the details):

   ```bash
   uv sync --all-extras
   uv run pytest -q
   ```

   For frontend changes, in `src/satisfactory_mcp/interfaces/web/frontend/` run `npm ci`,
   then `npm run check`, `npm test` and `npm run build`.
3. If the change touches the save parser, a data format or the MCP vocabulary, add upgrade
   notes to [CHANGELOG.md](CHANGELOG.md) under `[Unreleased]`.
4. Open a pull request with a conventional title (`feat(scope): …`, `fix: …`, `docs: …`).
   Commits must use an email linked to your GitHub account, or the CLA check can't pass.
5. Sign the [CLA](CLA.md): the bot comments on your first PR; post the sentence it gives as a
   comment, exactly. Once per account. You keep the copyright; the CLA grants the licensor
   (and anyone it transfers the rights to) a licence to use your contribution in any product,
   including closed-source commercial ones, and to relicense it.

The project is licensed under [PolyForm Shield 1.0.0](LICENSE). Branching and release rules
are in [docs/releasing.md](docs/releasing.md).
