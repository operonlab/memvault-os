#!/usr/bin/env bash
# preflight.sh — memvault-os 安裝前置檢查
#
# 對應 docs/plan-v3.2.md「Pre-flight 共用矩陣」八項：
#   1. Docker binary 已裝（阻斷）
#   2. Docker daemon running（阻斷）
#   3. Host port 8080 / 3000 空閒（阻斷，可換 port）
#   4. 磁碟空間 ≥5GB（警告）
#   5. Docker version ≥24.0（警告）
#   6. RAM ≥4GB（警告）
#   7. Linux: host.docker.internal 可解析 / host-gateway 支援（警告）
#   8. 至少一條 LLM provider 已配置（由 install.sh 互動式選單把關，不在此處檢查）
#
# 環境變數：
#   WEB_PORT (default 3000)
#   API_PORT (default 8080)
#   PREFLIGHT_NONINTERACTIVE=1 → port 衝突直接失敗，不互動詢問
#
# Exit codes:
#   0 → 全部 OK 或僅有警告
#   1 → 有阻斷項目失敗

set -euo pipefail

ROOT_DIR="${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
RESET='\033[0m'

log()  { printf "${GREEN}✅${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}⚠️${RESET}  %s\n" "$*"; }
err()  { printf "${RED}❌${RESET} %s\n" "$*" >&2; }
hint() { printf "   ${BOLD}提示：${RESET}%s\n" "$*"; }

OS="$(uname -s)"
ARCH="$(uname -m)"

WEB_PORT="${WEB_PORT:-3000}"
API_PORT="${API_PORT:-8080}"

NONINTERACTIVE="${PREFLIGHT_NONINTERACTIVE:-0}"
HARD_FAIL=0

# ---------------------------------------------------------------------------
# 1. Docker binary
# ---------------------------------------------------------------------------
check_docker_binary() {
  if command -v docker >/dev/null 2>&1; then
    log "Docker CLI 已安裝（$(docker --version 2>/dev/null | head -1)）"
    return 0
  fi
  err "找不到 docker 指令"
  case "${OS}" in
    Darwin)
      hint "macOS 安裝：brew install --cask docker"
      hint "或下載：https://www.docker.com/products/docker-desktop"
      ;;
    Linux)
      hint "Linux 一行安裝：curl -fsSL https://get.docker.com | sh"
      ;;
    *)
      hint "請至 https://docs.docker.com/get-docker/ 下載對應版本"
      ;;
  esac
  HARD_FAIL=1
  return 1
}

# ---------------------------------------------------------------------------
# 2. Docker daemon
# ---------------------------------------------------------------------------
check_docker_daemon() {
  if docker info >/dev/null 2>&1; then
    log "Docker daemon 運作中"
    return 0
  fi
  err "Docker daemon 未啟動"
  case "${OS}" in
    Darwin) hint "請啟動 Docker Desktop（或 colima start）" ;;
    Linux)  hint "請執行：sudo systemctl start docker" ;;
    *)      hint "請啟動 Docker 服務" ;;
  esac
  HARD_FAIL=1
  return 1
}

# ---------------------------------------------------------------------------
# 3. Host port 空閒（互動換 port → 寫進 .env）
# ---------------------------------------------------------------------------
port_in_use() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    [[ -n "$(lsof -nP -iTCP:"${port}" -sTCP:LISTEN -t 2>/dev/null || true)" ]]
  elif command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq ":${port}\$"
  elif command -v netstat >/dev/null 2>&1; then
    netstat -an 2>/dev/null | awk '{print $4}' | grep -Eq ":${port}\$"
  else
    return 1
  fi
}

prompt_new_port() {
  local label="$1"
  local current="$2"
  local new_port=""
  if [[ "${NONINTERACTIVE}" == "1" ]]; then
    err "${label} port ${current} 被佔用，且當前為非互動模式"
    HARD_FAIL=1
    printf '%s\n' "${current}"
    return 1
  fi
  while :; do
    read -r -p "請輸入新的 ${label} port（1024-65535，留空中止）: " new_port
    if [[ -z "${new_port}" ]]; then
      err "${label} port 衝突未解決"
      HARD_FAIL=1
      printf '%s\n' "${current}"
      return 1
    fi
    if ! [[ "${new_port}" =~ ^[0-9]+$ ]] || (( new_port < 1024 || new_port > 65535 )); then
      warn "port 範圍需為 1024-65535"
      continue
    fi
    if port_in_use "${new_port}"; then
      warn "port ${new_port} 也被佔用"
      continue
    fi
    printf '%s\n' "${new_port}"
    return 0
  done
}

write_env_port() {
  local key="$1"
  local value="$2"
  local env_file="${ROOT_DIR}/.env"
  [[ -f "${env_file}" ]] || return 0
  local tmp
  tmp="$(mktemp)"
  local replaced=0
  while IFS= read -r line || [[ -n "${line}" ]]; do
    if [[ "${line}" =~ ^${key}= ]]; then
      printf '%s=%s\n' "${key}" "${value}" >>"${tmp}"
      replaced=1
    else
      printf '%s\n' "${line}" >>"${tmp}"
    fi
  done <"${env_file}"
  [[ "${replaced}" -eq 0 ]] && printf '%s=%s\n' "${key}" "${value}" >>"${tmp}"
  mv "${tmp}" "${env_file}"
}

check_port() {
  local label="$1"
  local var="$2"
  local current="$3"
  if ! port_in_use "${current}"; then
    log "${label} port ${current} 空閒"
    return 0
  fi
  warn "${label} port ${current} 已被佔用"
  local new_port
  new_port="$(prompt_new_port "${label}" "${current}")" || return 1
  write_env_port "${var}" "${new_port}"
  log "${label} port → ${new_port}（已寫入 .env）"
  case "${var}" in
    WEB_PORT) WEB_PORT="${new_port}" ;;
    API_PORT) API_PORT="${new_port}" ;;
  esac
}

# ---------------------------------------------------------------------------
# 4. 磁碟空間（警告）
# ---------------------------------------------------------------------------
check_disk_space() {
  local avail_kb
  avail_kb="$(df -k "${ROOT_DIR}" 2>/dev/null | awk 'NR==2 {print $4}')"
  if [[ -z "${avail_kb:-}" ]]; then
    warn "無法判斷可用磁碟空間，請手動確認 ≥5GB"
    return 0
  fi
  local avail_gb=$(( avail_kb / 1024 / 1024 ))
  if (( avail_gb < 5 )); then
    warn "可用磁碟空間 ${avail_gb}GB < 5GB，可能影響 image pull / volume"
  else
    log "可用磁碟空間 ${avail_gb}GB"
  fi
}

# ---------------------------------------------------------------------------
# 5. Docker version ≥24.0（警告）
# ---------------------------------------------------------------------------
check_docker_version() {
  local ver
  ver="$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
  if [[ -z "${ver}" ]]; then
    warn "無法取得 Docker server version"
    return 0
  fi
  local major
  major="${ver%%.*}"
  if ! [[ "${major}" =~ ^[0-9]+$ ]]; then
    warn "Docker version 格式無法解析：${ver}"
    return 0
  fi
  if (( major < 24 )); then
    warn "Docker version ${ver} < 24.0，compose v2 / host-gateway 行為可能不穩"
  else
    log "Docker version ${ver}"
  fi
}

# ---------------------------------------------------------------------------
# 6. RAM ≥4GB（警告）
# ---------------------------------------------------------------------------
check_ram() {
  local ram_gb=0
  case "${OS}" in
    Darwin)
      local bytes
      bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
      ram_gb=$(( bytes / 1024 / 1024 / 1024 ))
      ;;
    Linux)
      local kb
      kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
      ram_gb=$(( kb / 1024 / 1024 ))
      ;;
  esac
  if (( ram_gb == 0 )); then
    warn "無法判斷 RAM，請手動確認 ≥4GB"
    return 0
  fi
  if (( ram_gb < 4 )); then
    warn "RAM ${ram_gb}GB < 4GB，LiteLLM + Qdrant + Postgres 同時跑可能吃緊"
  else
    log "RAM ${ram_gb}GB"
  fi
}

# ---------------------------------------------------------------------------
# 7. Linux host.docker.internal / host-gateway（警告）
# ---------------------------------------------------------------------------
check_host_gateway() {
  if [[ "${OS}" != "Linux" ]]; then
    return 0
  fi
  local ver
  ver="$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
  local major="${ver%%.*}"
  if [[ "${major}" =~ ^[0-9]+$ ]] && (( major >= 24 )); then
    log "Linux Docker ${ver} 支援 host-gateway（compose 已設 extra_hosts）"
  else
    warn "Linux Docker version 無法確認，host.docker.internal 可能不通"
    hint "compose 已宣告 extra_hosts: host.docker.internal:host-gateway，需要 Docker ≥24.0"
  fi
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
main() {
  printf '\n%b== memvault-os pre-flight check ==%b\n' "${BOLD}" "${RESET}"
  printf "OS=%s ARCH=%s\n\n" "${OS}" "${ARCH}"

  check_docker_binary  || true
  check_docker_daemon  || true
  if (( HARD_FAIL == 0 )); then
    check_port "API" API_PORT "${API_PORT}" || true
    check_port "WEB" WEB_PORT "${WEB_PORT}" || true
  fi
  check_disk_space
  check_docker_version || true
  check_ram
  check_host_gateway

  printf "\n"
  if (( HARD_FAIL != 0 )); then
    err "pre-flight 失敗，請依上方提示修補後重跑"
    exit 1
  fi
  log "pre-flight 全部通過"
}

main "$@"
