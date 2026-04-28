#!/usr/bin/env bash
# memvault-os — restore: rehydrate stack from a backup bundle.
# Usage: ./scripts/restore.sh backups/memvault-YYYYMMDD-HHMMSS/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_cmd docker

if [[ $# -lt 1 ]]; then
    fail "Usage: $0 <backup-dir>"
    info "Available bundles:"
    if [[ -d "${REPO_ROOT}/backups" ]]; then
        find "${REPO_ROOT}/backups" -mindepth 1 -maxdepth 1 -type d -name 'memvault-*' | sort | sed 's/^/  /'
    else
        echo "  (no backups/ directory yet)"
    fi
    exit 64
fi

BUNDLE="$1"
if [[ ! -d "${BUNDLE}" ]]; then
    fail "Not a directory: ${BUNDLE}"
    exit 66
fi
BUNDLE="$(cd "${BUNDLE}" && pwd)"

section "Bundle: ${BUNDLE}"
if [[ -f "${BUNDLE}/MANIFEST.txt" ]]; then
    cat "${BUNDLE}/MANIFEST.txt" | sed 's/^/   /'
    bundle_version="$(awk -F': ' '/^version:/ {print $2; exit}' "${BUNDLE}/MANIFEST.txt" || echo "unknown")"
else
    warn "No MANIFEST.txt — cannot verify version compatibility"
    bundle_version="unknown"
fi

current_version="${VERSION:-unknown}"
if [[ "${bundle_version}" != "unknown" ]] && [[ "${current_version}" != "unknown" ]] \
    && [[ "${bundle_version}" != "${current_version}" ]]; then
    warn "version mismatch: bundle=${bundle_version}  current=${current_version}"
    hint "Restoring across major versions can fail; consider checking out matching git ref first."
fi

section "DESTRUCTIVE OPERATION"
fail "This will DROP existing Postgres / Qdrant data and overwrite .env / infra/."
if ! confirm "Type 'y' to proceed: "; then
    info "aborted by user"
    exit 1
fi

# --- Stop everything except storage ---
section "Stopping stack (volumes preserved)"
dc down

# --- Restore .env / infra ---
if [[ -f "${BUNDLE}/env.tar.gz" ]]; then
    section "Restoring .env / infra/"
    if [[ -f "${REPO_ROOT}/.env" ]]; then
        backup_env="${REPO_ROOT}/.env.before-restore-$(date -u +%Y%m%d-%H%M%S)"
        cp "${REPO_ROOT}/.env" "${backup_env}"
        info "current .env saved → ${backup_env}"
    fi
    tar -C "${REPO_ROOT}" -xzf "${BUNDLE}/env.tar.gz"
    ok "env.tar.gz extracted"
    # Re-source .env so subsequent dc commands pick up restored values
    set -a
    # shellcheck disable=SC1091
    [[ -f "${REPO_ROOT}/.env" ]] && source "${REPO_ROOT}/.env"
    set +a
fi

# --- Bring up storage ---
section "Starting storage services"
dc up -d postgres redis qdrant

# Wait for postgres
for i in $(seq 1 30); do
    if dc exec -T postgres pg_isready -U "${POSTGRES_USER:-memvault}" -d "${POSTGRES_DB:-memvault}" >/dev/null 2>&1; then
        ok "postgres ready (after ${i}s)"
        break
    fi
    sleep 1
    if [[ ${i} -eq 30 ]]; then
        fail "postgres did not become ready in 30s"
        exit 1
    fi
done

# --- Postgres restore ---
section "Restoring Postgres"
if [[ -f "${BUNDLE}/pg_dump.sql.gz" ]]; then
    info "dropping & recreating database ${POSTGRES_DB:-memvault}"
    dc exec -T postgres sh -c \
        "psql -U \"\${POSTGRES_USER:-memvault}\" -d postgres -c \"DROP DATABASE IF EXISTS \\\"\${POSTGRES_DB:-memvault}\\\";\" \
         && psql -U \"\${POSTGRES_USER:-memvault}\" -d postgres -c \"CREATE DATABASE \\\"\${POSTGRES_DB:-memvault}\\\";\""
    gunzip -c "${BUNDLE}/pg_dump.sql.gz" \
        | dc exec -T postgres sh -c "psql -U \"\${POSTGRES_USER:-memvault}\" -d \"\${POSTGRES_DB:-memvault}\" -v ON_ERROR_STOP=1"
    ok "pg_dump.sql.gz restored"
else
    warn "no pg_dump.sql.gz in bundle — skipping postgres restore"
fi

# --- Qdrant restore ---
section "Restoring Qdrant"
if [[ -f "${BUNDLE}/qdrant-snapshot.tar.gz" ]]; then
    snap_workdir="$(mktemp -d)"
    # shellcheck disable=SC2064
    trap "rm -rf '${snap_workdir}'" EXIT
    tar -C "${snap_workdir}" -xzf "${BUNDLE}/qdrant-snapshot.tar.gz"

    # For each collection dir, upload its snapshot file via Qdrant snapshot recovery API.
    while IFS= read -r -d '' col_dir; do
        col="$(basename "${col_dir}")"
        snap_file="$(find "${col_dir}" -maxdepth 1 -type f | head -n1)"
        [[ -z "${snap_file}" ]] && continue
        info "restoring collection ${col} from $(basename "${snap_file}")"
        # Stage snapshot inside the qdrant container then trigger recover-from-uploaded
        dest="/qdrant/storage/snapshots/${col}"
        dc exec -T qdrant sh -c "mkdir -p '${dest}'"
        dc cp "${snap_file}" "qdrant:${dest}/$(basename "${snap_file}")"
        dc exec -T qdrant sh -c \
            "curl -fsS -X PUT 'http://localhost:6333/collections/${col}/snapshots/recover' \
                -H 'Content-Type: application/json' \
                -d '{\"location\":\"file://${dest}/$(basename "${snap_file}")\"}' >/dev/null"
        ok "qdrant collection ${col} restored"
    done < <(find "${snap_workdir}" -mindepth 1 -maxdepth 1 -type d -print0)
elif [[ -f "${BUNDLE}/qdrant-snapshot.empty" ]]; then
    info "bundle marked empty qdrant — nothing to restore"
else
    warn "no qdrant-snapshot.tar.gz in bundle — skipping vector restore"
fi

# --- Bring everything else up ---
section "Starting remaining services"
dc up -d

section "Verifying"
if "${SCRIPT_DIR}/doctor.sh"; then
    ok "restore complete — stack healthy"
else
    fail "restore complete but doctor reports issues"
    exit 1
fi
