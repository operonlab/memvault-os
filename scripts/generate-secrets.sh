#!/usr/bin/env bash
# generate-secrets.sh — 為 memvault-os 產生 .env secrets
#
# 行為：
#   - 若 .env 不存在 → 從 .env.example 複製
#   - 已存在的 secret 值（非空）→ 保留不覆蓋
#   - 空值 secret → 用 openssl rand 產生
#
# 被 install.sh 呼叫，也可獨立執行。

set -euo pipefail

ROOT_DIR="${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${ROOT_DIR}/.env"
ENV_EXAMPLE="${ROOT_DIR}/.env.example"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

log()  { printf "${GREEN}✅${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}⚠️${RESET}  %s\n" "$*"; }
err()  { printf "${RED}❌${RESET} %s\n" "$*" >&2; }

require_openssl() {
  if ! command -v openssl >/dev/null 2>&1; then
    err "找不到 openssl，無法產生 secrets。請先安裝 openssl。"
    exit 1
  fi
}

ensure_env_file() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    if [[ ! -f "${ENV_EXAMPLE}" ]]; then
      err "找不到 ${ENV_EXAMPLE}，無法產生 .env"
      exit 1
    fi
    cp "${ENV_EXAMPLE}" "${ENV_FILE}"
    log "已從 .env.example 建立 .env"
  fi
}

# get_env KEY → 從 .env 讀第一筆非註解的 KEY=VALUE
get_env() {
  local key="$1"
  awk -F= -v k="${key}" '
    /^[[:space:]]*#/ { next }
    $1 == k { sub(/^[^=]*=/, ""); print; exit }
  ' "${ENV_FILE}"
}

# set_env KEY VALUE → 替換或追加 KEY=VALUE（非註解行）
set_env() {
  local key="$1"
  local value="$2"
  local tmp
  tmp="$(mktemp)"
  local replaced=0
  while IFS= read -r line || [[ -n "${line}" ]]; do
    if [[ "${line}" =~ ^[[:space:]]*# ]]; then
      printf '%s\n' "${line}" >>"${tmp}"
      continue
    fi
    if [[ "${line}" =~ ^${key}= ]]; then
      printf '%s=%s\n' "${key}" "${value}" >>"${tmp}"
      replaced=1
    else
      printf '%s\n' "${line}" >>"${tmp}"
    fi
  done <"${ENV_FILE}"
  if [[ "${replaced}" -eq 0 ]]; then
    printf '%s=%s\n' "${key}" "${value}" >>"${tmp}"
  fi
  mv "${tmp}" "${ENV_FILE}"
}

# fill_secret KEY BYTES → 若空才用 openssl rand -hex BYTES 填入
# WHY hex (not base64): POSTGRES_PASSWORD / REDIS_PASSWORD 直接被嵌入
# postgresql://user:PWD@host 與 redis://:PWD@host URL（見 docker-compose.yml）。
# base64 含 '+', '/', '=' 會破 URL parsing 的 user-info 段。hex 為 [0-9a-f]，URL-safe。
fill_secret() {
  local key="$1"
  local bytes="$2"
  local current
  current="$(get_env "${key}" || true)"
  if [[ -n "${current// /}" ]]; then
    log "${key} 已存在，保留現值"
    return 0
  fi
  local value
  value="$(openssl rand -hex "${bytes}")"
  set_env "${key}" "${value}"
  log "${key} 已產生 (hex ${bytes} bytes)"
}

main() {
  require_openssl
  ensure_env_file
  fill_secret POSTGRES_PASSWORD 24
  fill_secret REDIS_PASSWORD 18
  fill_secret MEMVAULT_SECRET_KEY 32
  fill_secret LITELLM_MASTER_KEY 24
  log "secrets 寫入完成 → ${ENV_FILE}"
}

main "$@"
