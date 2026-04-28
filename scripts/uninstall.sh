#!/usr/bin/env bash
# memvault-os — uninstall: remove containers, volumes, and host sidecars.
# Repository files are NOT deleted; final rm -rf is left to the user.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_cmd docker

LAUNCH_AGENT_PLIST="${HOME}/Library/LaunchAgents/dev.memvault.embed.plist"

# --- Show cleanup plan ---
section "Uninstall plan"
echo "The following will be removed:"
echo "  • docker compose stack (containers, network) — name: memvault"
echo "  • docker named volumes:"
docker volume ls --filter name=memvault --format '    - {{.Name}}' 2>/dev/null || true
echo "  • docker images built locally for memvault (api/web/embed-gateway)"
if [[ "$(uname -s)" == "Darwin" ]] && [[ -f "${LAUNCH_AGENT_PLIST}" ]]; then
    echo "  • macOS LaunchAgent: ${LAUNCH_AGENT_PLIST}"
fi
echo
echo "The following will NOT be touched:"
echo "  • backups/        (your snapshots)"
echo "  • .env / infra/   (config files in the repo)"
echo "  • the repository directory itself"
echo

# --- Two-stage confirmation ---
if [[ "${MEMVAULT_DESTROY_CONFIRM:-}" != "yes" ]]; then
    fail "Set MEMVAULT_DESTROY_CONFIRM=yes to acknowledge this is destructive."
    hint "Example: MEMVAULT_DESTROY_CONFIRM=yes ./scripts/uninstall.sh"
    exit 1
fi

if ! confirm "Final check — really uninstall? "; then
    info "aborted"
    exit 1
fi

# --- docker compose down -v ---
section "docker compose down -v"
if dc down -v --remove-orphans; then
    ok "compose stack removed"
else
    warn "docker compose down reported errors (continuing)"
fi

# --- Remove built images ---
section "Removing locally built images"
local_images=(
    "ghcr.io/operonlab/memvault-api"
    "ghcr.io/operonlab/memvault-web"
    "ghcr.io/operonlab/memvault-embed-gateway"
)
for img in "${local_images[@]}"; do
    matching="$(docker images --format '{{.Repository}}:{{.Tag}}' | awk -v p="${img}" '$0 ~ "^"p":"' || true)"
    if [[ -n "${matching}" ]]; then
        while read -r tag; do
            [[ -z "${tag}" ]] && continue
            if docker rmi -f "${tag}" >/dev/null 2>&1; then
                info "removed image: ${tag}"
            else
                warn "could not remove ${tag}"
            fi
        done <<<"${matching}"
    fi
done
ok "local image cleanup done"

# --- macOS LaunchAgent ---
if [[ "$(uname -s)" == "Darwin" ]] && [[ -f "${LAUNCH_AGENT_PLIST}" ]]; then
    section "Removing macOS LaunchAgent"
    if launchctl unload "${LAUNCH_AGENT_PLIST}" 2>/dev/null; then
        ok "launchctl unload"
    else
        warn "launchctl unload failed (agent may already be unloaded)"
    fi
    rm -f "${LAUNCH_AGENT_PLIST}"
    ok "removed ${LAUNCH_AGENT_PLIST}"
fi

hr
ok "memvault-os uninstalled."
echo "To finish:"
echo "  rm -rf '${REPO_ROOT}'        # only when you're sure you want the repo gone"
echo "  rm -rf '${REPO_ROOT}/backups' # only when you no longer need the snapshots"
