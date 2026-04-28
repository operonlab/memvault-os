#!/usr/bin/env bash
# memvault-os — backup: pg_dump + qdrant snapshot + .env/infra tarball.
# Output: backups/memvault-<UTC-timestamp>/
# Retention: keeps the latest 7; older bundles move to backups/.archive/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_cmd docker

KEEP="${MEMVAULT_BACKUP_KEEP:-7}"
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP_ROOT="${REPO_ROOT}/backups"
DEST="${BACKUP_ROOT}/memvault-${TIMESTAMP}"
ARCHIVE_DIR="${BACKUP_ROOT}/.archive"

mkdir -p "${DEST}" "${ARCHIVE_DIR}"

section "Backup target: ${DEST}"

# --- Postgres ---
section "pg_dump → pg_dump.sql.gz"
if is_running postgres; then
    if dc exec -T postgres sh -c \
        "pg_dump -U \"\${POSTGRES_USER:-memvault}\" \"\${POSTGRES_DB:-memvault}\"" \
        | gzip -9 >"${DEST}/pg_dump.sql.gz"; then
        ok "pg_dump.sql.gz ($(du -h "${DEST}/pg_dump.sql.gz" | cut -f1))"
    else
        fail "pg_dump failed"
        rm -f "${DEST}/pg_dump.sql.gz"
        exit 1
    fi
else
    fail "postgres not running — cannot backup"
    exit 1
fi

# --- Qdrant snapshot ---
section "Qdrant snapshot → qdrant-snapshot.tar.gz"
if is_running qdrant; then
    # 1. List collections
    collections="$(dc exec -T qdrant sh -c \
        'wget -qO- http://localhost:6333/collections 2>/dev/null \
         || curl -fsS http://localhost:6333/collections 2>/dev/null' \
        | python3 -c '
import json, sys
try:
    payload = json.load(sys.stdin)
    cols = payload.get("result", {}).get("collections", [])
    for c in cols:
        print(c["name"])
except Exception:
    pass
' || true)"

    if [[ -z "${collections}" ]]; then
        warn "no qdrant collections found — writing empty marker"
        echo "no_collections" >"${DEST}/qdrant-snapshot.empty"
    else
        snap_workdir="$(mktemp -d)"
        # shellcheck disable=SC2064
        trap "rm -rf '${snap_workdir}'" EXIT
        while read -r col; do
            [[ -z "${col}" ]] && continue
            info "snapshot: ${col}"
            snap_name="$(dc exec -T qdrant sh -c \
                "wget -qO- --post-data='' --header='Content-Type: application/json' \
                  http://localhost:6333/collections/${col}/snapshots 2>/dev/null \
                  || curl -fsS -X POST http://localhost:6333/collections/${col}/snapshots 2>/dev/null" \
                | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["name"])' || true)"
            if [[ -z "${snap_name}" ]]; then
                fail "snapshot create failed for ${col}"
                exit 1
            fi
            mkdir -p "${snap_workdir}/${col}"
            # The snapshot file lives at /qdrant/storage/snapshots/<col>/<snap_name>
            dc exec -T qdrant sh -c "cat /qdrant/storage/snapshots/${col}/${snap_name}" \
                >"${snap_workdir}/${col}/${snap_name}"
        done <<<"${collections}"

        tar -C "${snap_workdir}" -czf "${DEST}/qdrant-snapshot.tar.gz" .
        ok "qdrant-snapshot.tar.gz ($(du -h "${DEST}/qdrant-snapshot.tar.gz" | cut -f1))"
    fi
else
    warn "qdrant not running — skipping vector snapshot"
fi

# --- Env / infra ---
section "env+infra → env.tar.gz"
TAR_INPUTS=()
[[ -f "${REPO_ROOT}/.env" ]] && TAR_INPUTS+=(".env")
[[ -d "${REPO_ROOT}/infra" ]] && TAR_INPUTS+=("infra")
if [[ ${#TAR_INPUTS[@]} -gt 0 ]]; then
    tar -C "${REPO_ROOT}" -czf "${DEST}/env.tar.gz" "${TAR_INPUTS[@]}"
    ok "env.tar.gz ($(du -h "${DEST}/env.tar.gz" | cut -f1))"
else
    warn ".env / infra/ missing — skipping env tarball"
fi

# --- Manifest ---
section "Writing MANIFEST.txt"
GIT_REV="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
{
    echo "memvault-os backup manifest"
    echo "timestamp_utc: ${TIMESTAMP}"
    echo "host: $(hostname)"
    echo "git_rev: ${GIT_REV}"
    echo "version: ${VERSION:-unknown}"
    echo "compose_file: ${COMPOSE_FILE}"
    echo "embed_backend: ${EMBED_BACKEND:-onnx}"
    echo ""
    echo "files:"
    (cd "${DEST}" && find . -maxdepth 1 -type f -not -name MANIFEST.txt | sort | while read -r f; do
        printf '  %s  %s\n' "$(du -h "${f}" | cut -f1)" "${f#./}"
    done)
} >"${DEST}/MANIFEST.txt"
ok "MANIFEST.txt"

# --- Retention ---
section "Retention (keep latest ${KEEP})"
mapfile -t bundles < <(find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -name 'memvault-*' | sort)
total=${#bundles[@]}
if (( total > KEEP )); then
    moved=$(( total - KEEP ))
    for ((i=0; i<moved; i++)); do
        old="${bundles[$i]}"
        info "archiving: $(basename "${old}")"
        mv "${old}" "${ARCHIVE_DIR}/"
    done
    ok "moved ${moved} old bundle(s) to .archive/"
else
    ok "${total} bundle(s) on disk (≤ ${KEEP})"
fi

hr
ok "backup complete: ${DEST}"
echo "Total: $(du -sh "${DEST}" | cut -f1)"
