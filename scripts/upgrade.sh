#!/usr/bin/env bash
# memvault-os — upgrade: pull latest code + images, rolling restart, run migrations.
# Usage: ./scripts/upgrade.sh [--dry-run]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DRY_RUN=0
for arg in "$@"; do
    case "${arg}" in
        --dry-run|-n) DRY_RUN=1 ;;
        --help|-h)
            cat <<EOF
Usage: $0 [--dry-run]

Steps:
  1. git pull --ff-only         (fast-forward; aborts on diverge)
  2. docker compose pull        (refresh pinned digests)
  3. docker compose up -d       (rolling restart of changed services)
  4. alembic upgrade head       (apply migrations inside the api container)
  5. doctor.sh                  (verify all-green)

Flags:
  --dry-run   Print actions without executing them.
EOF
            exit 0
            ;;
        *) fail "Unknown flag: ${arg}"; exit 64 ;;
    esac
done

run() {
    if [[ ${DRY_RUN} -eq 1 ]]; then
        printf '   %s[dry-run]%s %s\n' "${C_DIM}" "${C_RESET}" "$*"
    else
        info "$ $*"
        "$@"
    fi
}

require_cmd docker
require_cmd git

section "Step 1/5 — git pull --ff-only"
if [[ -d "${REPO_ROOT}/.git" ]]; then
    if ! git -C "${REPO_ROOT}" diff --quiet || ! git -C "${REPO_ROOT}" diff --cached --quiet; then
        warn "Working tree has uncommitted changes; ff-only pull may fail."
        hint "Stash or commit local edits before upgrading."
    fi
    run git -C "${REPO_ROOT}" pull --ff-only
else
    warn "Not a git checkout (.git missing) — skipping git pull"
fi

section "Step 2/5 — docker compose pull"
run docker compose pull

section "Step 3/5 — docker compose up -d (rolling)"
run docker compose up -d --remove-orphans

section "Step 4/5 — alembic upgrade head"
if [[ ${DRY_RUN} -eq 1 ]]; then
    printf '   %s[dry-run]%s docker compose exec -T api alembic upgrade head\n' "${C_DIM}" "${C_RESET}"
else
    # Wait briefly for api to come up before invoking alembic
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if is_running api; then break; fi
        info "waiting for api to start (${i}/10)…"
        sleep 3
    done
    run docker compose exec -T api alembic upgrade head
fi

section "Step 5/5 — doctor"
if [[ ${DRY_RUN} -eq 1 ]]; then
    printf '   %s[dry-run]%s ./scripts/doctor.sh\n' "${C_DIM}" "${C_RESET}"
    ok "Dry run complete."
else
    if "${SCRIPT_DIR}/doctor.sh"; then
        ok "upgrade: stack healthy"
    else
        fail "upgrade: doctor reported failures — review output above"
        exit 1
    fi
fi
