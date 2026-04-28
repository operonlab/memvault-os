#!/usr/bin/env bash
# Install memvault MLX embedding sidecar as a macOS LaunchAgent.
#
# 必要條件：
#   - macOS Apple Silicon
#   - Python 3.11+ available（pyenv / brew / uv 都可）
#
# 動作：
#   1. 建 ~/.venvs/memvault-mlx
#   2. pip install mlx-embeddings
#   3. 寫 ~/Library/LaunchAgents/dev.memvault.embed.plist（替換 __VENV__/__WORKER__/__HOME__）
#   4. launchctl load 啟動
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${MEMVAULT_MLX_VENV:-${HOME}/.venvs/memvault-mlx}"
WORKER="${SCRIPT_DIR}/embed_worker.py"
PLIST_TEMPLATE="${SCRIPT_DIR}/dev.memvault.embed.plist"
PLIST_DEST="${HOME}/Library/LaunchAgents/dev.memvault.embed.plist"
PYTHON_BIN="${PYTHON:-python3}"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "[install-launchagent] error: macOS only" >&2
    exit 1
fi

if [[ "$(uname -m)" != "arm64" ]]; then
    echo "[install-launchagent] warn: not Apple Silicon — MLX will fall back to CPU" >&2
fi

if [[ ! -f "${WORKER}" ]]; then
    echo "[install-launchagent] error: worker not found at ${WORKER}" >&2
    exit 1
fi

echo "[install-launchagent] creating venv at ${VENV}"
if [[ ! -d "${VENV}" ]]; then
    "${PYTHON_BIN}" -m venv "${VENV}"
fi

echo "[install-launchagent] installing mlx-embeddings"
"${VENV}/bin/pip" install --upgrade pip >/dev/null
"${VENV}/bin/pip" install "mlx>=0.21" "mlx-embeddings>=0.0.3" >/dev/null

mkdir -p "${HOME}/Library/LaunchAgents" "${HOME}/Library/Logs"

# 已存在 → 先 unload 再覆寫
if launchctl list | grep -q dev.memvault.embed; then
    echo "[install-launchagent] unloading existing agent"
    launchctl unload "${PLIST_DEST}" 2>/dev/null || true
fi

echo "[install-launchagent] writing plist to ${PLIST_DEST}"
sed \
    -e "s|__VENV__|${VENV}|g" \
    -e "s|__WORKER__|${WORKER}|g" \
    -e "s|__HOME__|${HOME}|g" \
    "${PLIST_TEMPLATE}" > "${PLIST_DEST}"

echo "[install-launchagent] loading agent"
launchctl load "${PLIST_DEST}"

sleep 1
if curl -sf "http://127.0.0.1:18081/health" >/dev/null; then
    echo "[install-launchagent] ✓ sidecar healthy at http://127.0.0.1:18081"
else
    echo "[install-launchagent] ⚠ sidecar not responding yet — check ~/Library/Logs/memvault-embed.{err,out}.log"
fi
