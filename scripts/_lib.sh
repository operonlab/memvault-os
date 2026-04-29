#!/usr/bin/env bash
# memvault-os — shared shell helpers (sourced by doctor/upgrade/backup/restore/uninstall/pin-images)
# shellcheck shell=bash

set -euo pipefail

# Locate repo root (parent of scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Compose wrapper — honors COMPOSE_FILE from .env
COMPOSE_FILE_DEFAULT="infra/docker-compose.yml"

# WHY safe_source_dotenv (not `set -a; source .env`):
#   `source` treats every value as a shell expression. POSTGRES_PASSWORD or
#   OPENAI_API_KEY containing `$`, backtick, or `\` would either be expanded
#   unpredictably or rejected by `set -u`. safe_source_dotenv (in _dotenv.sh)
#   iterates the file line-by-line and uses `printf -v` for assignment, which
#   never re-expands the RHS. See codex review slice 1 #5.
# shellcheck source=./_dotenv.sh
[[ -f "${SCRIPT_DIR}/_dotenv.sh" ]] && source "${SCRIPT_DIR}/_dotenv.sh"
if [[ -f "${REPO_ROOT}/.env" ]]; then
    if declare -F safe_source_dotenv >/dev/null 2>&1; then
        safe_source_dotenv "${REPO_ROOT}/.env"
    else
        # Fallback: legacy behaviour if _dotenv.sh is missing for any reason.
        set -a
        # shellcheck disable=SC1091
        source "${REPO_ROOT}/.env"
        set +a
    fi
fi

: "${COMPOSE_FILE:=${COMPOSE_FILE_DEFAULT}}"
export COMPOSE_FILE

# Color helpers
if [[ -t 1 ]]; then
    C_RED=$'\033[31m'
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_BLUE=$'\033[34m'
    C_DIM=$'\033[2m'
    C_BOLD=$'\033[1m'
    C_RESET=$'\033[0m'
else
    C_RED='' C_GREEN='' C_YELLOW='' C_BLUE='' C_DIM='' C_BOLD='' C_RESET=''
fi

ok()    { printf '%s✅%s %s\n' "${C_GREEN}" "${C_RESET}" "$*"; }
warn()  { printf '%s⚠️ %s %s\n' "${C_YELLOW}" "${C_RESET}" "$*"; }
fail()  { printf '%s❌%s %s\n' "${C_RED}" "${C_RESET}" "$*"; }
info()  { printf '%sℹ️ %s %s\n' "${C_BLUE}" "${C_RESET}" "$*"; }
hint()  { printf '   %s↳ %s%s\n' "${C_DIM}" "$*" "${C_RESET}"; }
hr()    { printf '%s%s%s\n' "${C_DIM}" "$(printf '%.s─' $(seq 1 60))" "${C_RESET}"; }
section() { printf '\n%s== %s ==%s\n' "${C_BOLD}" "$*" "${C_RESET}"; }

# dc <args...>  — invoke docker compose with the configured file(s)
dc() {
    docker compose "$@"
}

# require_cmd <cmd>  — abort if a binary is missing
require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        fail "Missing required command: $1"
        exit 127
    fi
}

# is_running <service>  — 0 if container is running
is_running() {
    local svc="$1"
    local state
    state="$(dc ps --format '{{.Service}}\t{{.State}}' 2>/dev/null | awk -v s="${svc}" '$1==s {print $2; exit}')"
    [[ "${state}" == "running" ]]
}

# confirm "<prompt>"  — interactive Y/n; respects assume-yes / env override
confirm() {
    local prompt="${1:-Proceed?} [y/N] "
    if [[ "${ASSUME_YES:-}" == "1" ]]; then
        echo "${prompt}y (ASSUME_YES=1)"
        return 0
    fi
    local reply=""
    read -r -p "${prompt}" reply || reply=""
    [[ "${reply}" =~ ^[Yy]([Ee][Ss])?$ ]]
}
