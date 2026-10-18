# Contributing

## How to contribute

1. Branch from `master` with a topic branch (`feat/…`, `fix/…`, `docs/…`, `chore/…`). From a
   fork, use a topic branch, not the fork's default branch.
2. Run the tests (see [docs/DEVELOPING.md](docs/DEVELOPING.md) for the details):

   ```bash
   uv sync --all-extras
   uv run pytest -q
   ```

   Frontend changes also run `npm run check`, `npm test` and `npm run build` in
   `src/satisfactory_mcp/interfaces/web/frontend/`.
3. Open a pull request with a conventional title (`feat(scope): …`, `fix: …`, `docs: …`).

Branching and release rules are in [docs/releasing.md](docs/releasing.md).

## Licence

The project is licensed under [PolyForm Shield 1.0.0](LICENSE).

## Contributor licence agreement

Every contributor signs the [CLA](CLA.md) once before a first pull request can be merged. You
keep the copyright in your contribution; the CLA grants the licensor a licence to use and
relicense it.

On your first pull request a bot comments with the exact sentence to post. Post it as a
comment on that pull request and the check turns green; the signature covers all later pull
requests too.
