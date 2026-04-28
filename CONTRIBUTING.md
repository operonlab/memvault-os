# Contributing to memvault-os

Thanks for considering a contribution. This is a small project — the bar for changes is **does it pass the existing tests + lint + a code review** rather than a long process.

## Repo layout

```
apps/api            FastAPI service, alembic migrations, pytest suite
apps/web            React 19 + Vite + Tailwind frontend
apps/worker         Async cron worker (dream loop, embeddings backfill)
apps/embed-gateway  Embedding router (MLX | vLLM | ONNX)
infra/              docker-compose stack + per-backend overlays
scripts/            install / lifecycle / preflight shell + PowerShell scripts
docs/               manifests, design plan, quickstarts, API reference
```

## Dev environment

You need Docker 24+, Python 3.12 (via [uv](https://github.com/astral-sh/uv)) for the api dev loop, Node 22 + pnpm for the web app.

```bash
git clone https://github.com/operonlab/memvault-os.git
cd memvault-os
bash scripts/generate-secrets.sh

# Bring up postgres / redis / qdrant only — for local api dev
docker compose -f infra/docker-compose.yml --env-file .env up -d postgres redis qdrant

# api dev loop
cd apps/api
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e .[dev]
alembic upgrade head
uvicorn src.main:app --reload --port 8080

# web dev loop
cd apps/web
pnpm install
pnpm dev
```

## Lint

Both lints are CI-enforced (`.github/workflows/lint.yml`).

```bash
# Python
ruff check apps/api
ruff format --check apps/api

# TypeScript / React
cd apps/web
biome check src/
```

Fix automatically:

```bash
ruff check --fix apps/api
ruff format apps/api
biome check --apply src/
```

## Tests

```bash
# Smoke (no docker needed)
cd apps/api
pytest tests/test_smoke.py

# Real-stack E2E — requires the full compose stack to be up
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml \
  --env-file .env up -d
MEMVAULT_TEST_BASE_URL=http://localhost:8080 \
  pytest tests/test_e2e_api.py -v
```

Current baseline: **39 / 41** E2E tests pass. The two failing tests are tracked in [issues](https://github.com/operonlab/memvault-os/issues) and are test-contract drift, not API regressions. PRs are expected to keep that number at 39 or improve it.

## Six-iron rules of test writing

When a test fails, the default is **fix the test** (or document why the production code is wrong). Do not modify the production code to make a test pass without an accompanying explanation in the PR description.

1. The test asserts the contract; if the contract changes, both must change.
2. A test that was added to reproduce a bug never gets deleted; it gets fixed when the bug is fixed.
3. Production code does not branch on a test-only flag.
4. Empty / null / boundary cases get explicit tests.
5. Test fixtures hard-delete; soft-delete leaks state across runs.
6. Six-iron rule meta: test + production code live in different commits when possible.

## Pull-request flow

1. Open an issue first if the change is non-trivial (>50 lines, new endpoint, schema change).
2. Branch from `main`. Use prefixes: `feat/`, `fix/`, `docs/`, `test/`, `refactor/`, `chore/`.
3. Write or update tests for behavioural changes.
4. Run lint + tests locally.
5. PR title follows [Conventional Commits](https://www.conventionalcommits.org/) — the title becomes the squash-merge commit subject.
6. Fill out the PR template (what / why / how tested).
7. CI must be green before review. Maintainer will squash-merge.

## Code style

- **Python**: ruff defaults; type hints encouraged but not required for internal helpers.
- **TypeScript**: biome defaults; React function components only; no class components.
- **SQL**: alembic migrations only — never modify `models.py` without a migration.
- **Comments**: explain *why*, not *what*. The diff already shows what.
- **Commits**: short subject (≤ 72 char), present tense ("add", not "added"). Body explains rationale and trade-offs.

## Schema changes

Any change to `apps/api/src/memvault/models.py` (or `kg_models.py`, `llm_models.py`) requires:

1. A new alembic migration in `apps/api/alembic/versions/`.
2. Regenerated `docs/schema_manifest.yaml` (if the script exists in your branch).
3. Tests verifying the migration runs forward + backward cleanly.

## Reporting bugs

Please include:

- OS + Docker version (`docker version`).
- Embedding backend (`grep EMBED_BACKEND .env`).
- Output of `bash scripts/doctor.sh`.
- Minimal reproduction (curl command preferred).

## License

By contributing you agree that your contributions are licensed under the MIT License (see `LICENSE`).
