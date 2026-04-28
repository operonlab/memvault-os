# Operations Guide

Day-2 runbook for self-hosted memvault-os. All scripts live in `scripts/` and are POSIX `bash` (Linux / macOS); the Windows equivalents are PowerShell-only for `install.ps1` + `generate-secrets.ps1`. For backup / restore / doctor on Windows, run the bash scripts inside WSL.

All commands assume CWD = repo root.

## Backup

```bash
bash scripts/backup.sh
```

Produces `backups/memvault-<UTC-timestamp>/` containing:

- `postgres.sql.gz` — `pg_dump --format=custom` of the memvault schema
- `qdrant.snapshot` — Qdrant snapshot of the embeddings collection
- `redis.rdb` — Redis snapshot (TTL state, prefetch metrics)
- `env.snapshot` — sanitized `.env` (API keys redacted)
- `MANIFEST.txt` — version, timestamp, image digests

Retention: keep the last 7 bundles; older ones move to `backups/.archive/`. Override with `MEMVAULT_BACKUP_KEEP=14`.

Recommended cadence: daily via cron / launchd / Task Scheduler. The script is idempotent and safe to re-run; only succeeds if all four data sources export cleanly.

### Off-host backup

The `backups/` directory is just files. Push to S3 / rsync.net / your NAS:

```bash
aws s3 sync backups/ s3://my-bucket/memvault/ --exclude '.archive/*'
```

## Restore

```bash
bash scripts/restore.sh backups/memvault-20260428-031200
```

The script:

1. Validates `MANIFEST.txt` against the current code version. Cross-major-version restores warn but proceed.
2. Stops the api / worker containers.
3. Drops + recreates the memvault Postgres schema, replays `postgres.sql.gz`.
4. Stages `qdrant.snapshot` into the qdrant container, triggers `recover-from-uploaded`.
5. Loads `redis.rdb` into the redis container.
6. Restarts api / worker.
7. Runs `alembic upgrade head` (no-op if bundle is current).

If `bundle_version != current_version`, `git checkout v<bundle-version>` first to guarantee a clean restore.

## Upgrade

```bash
bash scripts/upgrade.sh            # pull + restart + migrate
bash scripts/upgrade.sh --dry-run  # show what would change, no side effects
```

Steps:

1. `git pull --ff-only`.
2. `bash scripts/pin-images.sh` to refresh digests if any third-party image moved.
3. `docker compose pull` (downloads new images for changed services).
4. Rolling `docker compose up -d` (Compose handles container replacement).
5. `docker compose run --rm --no-deps api alembic upgrade head`.
6. `bash scripts/doctor.sh` for a post-upgrade check.

A pre-upgrade backup is strongly recommended:

```bash
bash scripts/backup.sh && bash scripts/upgrade.sh
```

If an upgrade fails mid-flight:

```bash
git reset --hard HEAD@{1}                            # revert code
bash scripts/restore.sh backups/memvault-<latest>    # revert data
```

## Doctor (health check)

```bash
bash scripts/doctor.sh
```

Runs ~15 checks across:

- Docker daemon + compose plugin version
- All 8 expected services (`postgres redis qdrant litellm embed-gateway api worker web`) reporting `healthy`
- Postgres connection + schema present + 17 tables
- Redis ping
- Qdrant `/health` + collection present
- Embed gateway `/embed` returns a non-zero vector
- LiteLLM `/v1/models` lists at least one provider
- api `/health/readiness` + `/health/liveliness`
- web responds with a non-empty HTML body
- Disk usage on the docker volume root
- Backup directory present and ≤ 7 bundles

Exit code: `0` if no failures (warnings allowed), `1` if any check failed. Pipe into a monitor / alert.

## Uninstall

```bash
bash scripts/uninstall.sh
```

Removes:

- All memvault docker containers and the `memvault-net` network
- Named volumes (`memvault_postgres_data`, `memvault_qdrant_data`, `memvault_redis_data`, `memvault_embed_models`)
- The macOS MLX LaunchAgent at `~/Library/LaunchAgents/dev.memvault.embed.plist`

Keeps:

- `.env` (so you can reinstall without losing API keys)
- `backups/` (the whole point — never auto-deleted)
- The cloned source tree

For a totally clean removal:

```bash
bash scripts/uninstall.sh
rm -rf .env backups/
cd .. && rm -rf memvault-os
```

## Common operational issues

### "api container is unhealthy"

```bash
docker compose logs api --tail 200
```

99% of the time: missing LLM key, or Postgres / Qdrant didn't finish initializing before api started polling. Wait 60 s; if still red, run `bash scripts/doctor.sh`.

### Disk filling up

The biggest spenders are `embed_models` (~600 MB, fixed), `postgres_data` (grows with blocks), and `qdrant_data` (grows with vectors).

```bash
docker system df
docker compose exec postgres psql -U memvault -d memvault -c \
  "SELECT pg_size_pretty(pg_total_relation_size('memvault.memory_blocks'));"
```

Periodic compaction:

```bash
docker compose exec postgres psql -U memvault -d memvault -c "VACUUM FULL ANALYZE memvault.memory_blocks;"
```

### Embedding latency suddenly 10× higher

Symptom: `/api/memvault/search` taking > 1 s on a stack that used to be < 100 ms.

Common causes:

- ONNX model fell back to CPU after host swap into vLLM was disabled. Check `EMBED_BACKEND` in `.env`.
- LiteLLM proxy health-checking dead providers. Check `docker compose logs litellm`.
- Qdrant index lost (collection recreated empty after a restore failure). Run:
  ```bash
  curl -s http://localhost:8080/api/memvault/kg/embeddings/backfill -X POST
  ```

### Forgotten LiteLLM API key

```bash
grep -E '^(OPENAI|ANTHROPIC|GEMINI|DEEPSEEK)_API_KEY=' .env
# Add or rotate, then:
docker compose restart litellm
```

### Migration fails after upgrade

```bash
docker compose run --rm --no-deps api alembic current     # what we're at
docker compose run --rm --no-deps api alembic history     # what's expected
```

If the chain is broken, restore the latest backup and re-pull a release branch known to be clean.

### Reset to factory state without losing config

```bash
docker compose down -v       # destroys volumes
bash scripts/install.sh      # re-runs everything; .env preserved
```

## Monitoring

There is no built-in metrics endpoint in v1. For external monitoring:

- Liveliness probe: `GET /health/liveliness` (cheap)
- Readiness probe: `GET /health/readiness` (checks Postgres / Redis / Qdrant)
- Prefetch metrics: `GET /api/memvault/prefetch/metrics`
- Operational status: `GET /api/memvault/status`

Wire those into Uptime Kuma / Prometheus blackbox / your monitor of choice.

## Cron / scheduled tasks

Recommended host crontab entries:

```cron
# Daily backup at 03:00 local
0 3 * * * cd /home/me/memvault-os && bash scripts/backup.sh >> backups/cron.log 2>&1

# Doctor check every 15 min
*/15 * * * * cd /home/me/memvault-os && bash scripts/doctor.sh > /tmp/memvault-doctor.log 2>&1

# Trigger dream consolidation hourly (via the api itself; not strictly needed)
0 * * * * curl -fsS -X POST http://localhost:8080/api/memvault/dream -H 'content-type: application/json' -d '{}' >/dev/null
```

The dream loop also runs internally inside the worker container; the cron entry above is only useful if you've disabled the worker.

## Links

- [`quickstart-linux.md`](./quickstart-linux.md)
- [`quickstart-windows.md`](./quickstart-windows.md)
- [`api-reference.md`](./api-reference.md)
- [`plan-v3.2.md`](./plan-v3.2.md) — design rationale
