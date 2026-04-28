#!/usr/bin/env bash
# install.sh — memvault-os 一鍵安裝（macOS / Linux）
#
# 用法：
#   curl -fsSL https://raw.githubusercontent.com/operonlab/memvault-os/main/scripts/install.sh | bash
#   或在已 clone 的 repo 下：bash scripts/install.sh
#
# 流程（依 docs/plan-v3.2.md「跨 OS 安裝腳本設計」）：
#   1. OS / arch 偵測
#   2. Pre-flight 檢查（呼叫 preflight.sh）
#   3. Clone repo（curl-pipe-bash 模式才會做；本地執行則 skip）
#   4. 產生 .env（呼叫 generate-secrets.sh）
#   5. Embedding 三軌偵測 → 設定 EMBED_BACKEND + COMPOSE_FILE
#   6. LLM 強制配置（互動選單，無「跳過」選項）
#   7. docker compose pull + up -d
#   8. 健康檢查輪詢 90s
#   9. alembic upgrade head + 驗 17 張表
#   10. 開 post-install.html

set -euo pipefail

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
BOLD='\033[1m'
RESET='\033[0m'

REPO_URL="${MEMVAULT_REPO_URL:-https://github.com/operonlab/memvault-os.git}"
INSTALL_DIR="${MEMVAULT_INSTALL_DIR:-${HOME}/memvault-os}"

OS="$(uname -s)"
ARCH="$(uname -m)"

ROOT_DIR=""

log()   { printf "${GREEN}✅${RESET} %s\n" "$*"; }
warn()  { printf "${YELLOW}⚠️${RESET}  %s\n" "$*"; }
err()   { printf "${RED}❌${RESET} %s\n" "$*" >&2; }
info()  { printf "${BLUE}ℹ️${RESET}  %s\n" "$*"; }
title() { printf "\n${BOLD}== %s ==${RESET}\n" "$*"; }

die() {
  err "$*"
  exit 1
}

# ---------------------------------------------------------------------------
# .env helpers
# ---------------------------------------------------------------------------
get_env() {
  local key="$1"
  awk -F= -v k="${key}" '
    /^[[:space:]]*#/ { next }
    $1 == k { sub(/^[^=]*=/, ""); print; exit }
  ' "${ROOT_DIR}/.env"
}

set_env() {
  local key="$1"
  local value="$2"
  local env_file="${ROOT_DIR}/.env"
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

# ---------------------------------------------------------------------------
# Step 1. OS / arch
# ---------------------------------------------------------------------------
detect_os() {
  title "1. OS / arch 偵測"
  case "${OS}" in
    Darwin|Linux) ;;
    *) die "不支援的 OS：${OS}（本腳本僅支援 macOS / Linux；Windows 請用 install.ps1）" ;;
  esac
  log "OS=${OS} ARCH=${ARCH}"
}

# ---------------------------------------------------------------------------
# Step 2. Pre-flight
# ---------------------------------------------------------------------------
run_preflight() {
  title "2. Pre-flight 檢查"
  local pf="${ROOT_DIR}/scripts/preflight.sh"
  [[ -x "${pf}" ]] || chmod +x "${pf}" 2>/dev/null || true
  ROOT_DIR="${ROOT_DIR}" bash "${pf}"
}

# ---------------------------------------------------------------------------
# Step 3. Clone（僅 curl-pipe-bash 情境）
# ---------------------------------------------------------------------------
clone_or_use_local() {
  title "3. 取得 repo"
  local script_dir
  if [[ -n "${BASH_SOURCE[0]:-}" ]] && [[ -f "${BASH_SOURCE[0]}" ]]; then
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "${script_dir}/../infra/docker-compose.yml" ]]; then
      ROOT_DIR="$(cd "${script_dir}/.." && pwd)"
      log "在現有 repo 執行：${ROOT_DIR}"
      return 0
    fi
  fi

  command -v git >/dev/null 2>&1 || die "找不到 git，請先安裝"

  if [[ -d "${INSTALL_DIR}/.git" ]]; then
    info "目錄已存在：${INSTALL_DIR}（將直接使用，不重新 clone）"
  else
    info "git clone --depth 1 ${REPO_URL} → ${INSTALL_DIR}"
    git clone --depth 1 "${REPO_URL}" "${INSTALL_DIR}"
  fi

  ROOT_DIR="${INSTALL_DIR}"
  log "repo 就緒：${ROOT_DIR}"
}

# ---------------------------------------------------------------------------
# Step 4. .env
# ---------------------------------------------------------------------------
generate_secrets() {
  title "4. 產生 .env / secrets"
  local gs="${ROOT_DIR}/scripts/generate-secrets.sh"
  [[ -x "${gs}" ]] || chmod +x "${gs}" 2>/dev/null || true
  ROOT_DIR="${ROOT_DIR}" bash "${gs}"
}

# ---------------------------------------------------------------------------
# Step 5. Embedding 三軌偵測
# ---------------------------------------------------------------------------
detect_embedding_backend() {
  title "5. Embedding 後端偵測（三軌）"
  local backend=""
  local compose_file="infra/docker-compose.yml"

  if [[ "${OS}" == "Darwin" && "${ARCH}" == "arm64" ]]; then
    backend="mlx_proxy"
    compose_file="infra/docker-compose.yml:infra/docker-compose.mac.yml"
    info "偵測到 macOS Apple Silicon → MLX sidecar"
    local mlx_install="${ROOT_DIR}/infra/mlx-sidecar/install-launchagent.sh"
    if [[ -x "${mlx_install}" ]]; then
      info "安裝 MLX LaunchAgent..."
      if bash "${mlx_install}"; then
        log "MLX sidecar 已註冊 launchd"
      else
        warn "MLX LaunchAgent 安裝失敗，可稍後手動執行：${mlx_install}"
      fi
    else
      warn "找不到 MLX install-launchagent.sh，需手動處理"
    fi
  elif [[ "${OS}" == "Linux" ]] && command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    backend="vllm_proxy"
    compose_file="infra/docker-compose.yml:infra/docker-compose.gpu.yml"
    info "偵測到 NVIDIA GPU → vLLM container"
  else
    backend="onnx"
    compose_file="infra/docker-compose.yml"
    info "未偵測到 Apple Silicon / NVIDIA GPU → ONNX CPU fallback"
  fi

  set_env EMBED_BACKEND "${backend}"
  set_env COMPOSE_FILE "${compose_file}"
  log "EMBED_BACKEND=${backend}"
  log "COMPOSE_FILE=${compose_file}"
}

# ---------------------------------------------------------------------------
# Step 6. LLM 強制配置
# ---------------------------------------------------------------------------
prompt_llm_provider() {
  printf "\n%s\n" "請選擇 LLM provider（必選一條，無「跳過」選項）："
  printf "  1) OpenAI       (需 OPENAI_API_KEY)\n"
  printf "  2) Anthropic    (需 ANTHROPIC_API_KEY)\n"
  printf "  3) Google Gemini(需 GEMINI_API_KEY)\n"
  printf "  4) DeepSeek     (需 DEEPSEEK_API_KEY)\n"
  printf "  5) 本地 Ollama  (host.docker.internal:11434)\n"
  local choice
  while :; do
    read -r -p "選擇 [1-5]: " choice
    case "${choice}" in
      1|2|3|4|5) printf '%s\n' "${choice}"; return 0 ;;
      *) warn "請輸入 1-5" ;;
    esac
  done
}

read_api_key() {
  local label="$1"
  local key=""
  while [[ -z "${key// /}" ]]; do
    read -r -s -p "輸入 ${label}: " key
    printf "\n"
    [[ -n "${key// /}" ]] || warn "key 不可為空"
  done
  printf '%s' "${key}"
}

llm_smoke_test() {
  local model_alias="$1"
  local master_key
  master_key="$(get_env LITELLM_MASTER_KEY)"
  [[ -n "${master_key}" ]] || { err "LITELLM_MASTER_KEY 為空"; return 1; }

  info "啟動 litellm container 進行 smoke test..."
  ( cd "${ROOT_DIR}" && docker compose up -d litellm >/dev/null )

  info "等待 litellm health (最多 60s)..."
  local _i
  for _i in $(seq 1 30); do
    if docker compose -f "${ROOT_DIR}/infra/docker-compose.yml" exec -T litellm \
        curl -fsS http://localhost:4000/health/liveliness >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done

  info "對 ${model_alias} 發送真實 chat completion..."
  local body
  body=$(printf '{"model":"%s","messages":[{"role":"user","content":"ping"}],"max_tokens":4}' "${model_alias}")

  local resp
  resp="$(docker compose -f "${ROOT_DIR}/infra/docker-compose.yml" exec -T litellm \
    curl -sS -w '\n__HTTP__:%{http_code}' \
    -H "Authorization: Bearer ${master_key}" \
    -H 'Content-Type: application/json' \
    -d "${body}" \
    http://localhost:4000/v1/chat/completions 2>&1 || true)"

  local http_code
  http_code="$(printf '%s' "${resp}" | awk -F'__HTTP__:' '/__HTTP__:/ {print $2}' | tail -1)"
  local body_only
  body_only="$(printf '%s' "${resp}" | sed '/__HTTP__:/d')"

  if [[ "${http_code}" != "200" ]]; then
    err "smoke test 失敗（HTTP ${http_code:-?}）"
    case "${http_code}" in
      401|403) hint_msg "API key 無效或權限不足" ;;
      429)     hint_msg "配額用完或 rate limit" ;;
      000|"")  hint_msg "網路超時或 litellm 沒起來，看 docker compose logs litellm" ;;
      *)       hint_msg "回應內容：$(printf '%s' "${body_only}" | head -c 300)" ;;
    esac
    return 1
  fi

  if ! printf '%s' "${body_only}" | grep -q '"content"'; then
    err "回應 200 但無 content 欄位"
    return 1
  fi

  log "smoke test 通過 ✓"
  return 0
}

hint_msg() {
  printf "   ${BOLD}原因：${RESET}%s\n" "$*"
}

configure_llm() {
  title "6. LLM 配置（強制至少一條 smoke test 通過）"
  while :; do
    local choice
    choice="$(prompt_llm_provider)"
    local key_var=""
    local model_alias=""
    case "${choice}" in
      1) key_var="OPENAI_API_KEY";    model_alias="openai/gpt-4o-mini" ;;
      2) key_var="ANTHROPIC_API_KEY"; model_alias="anthropic/claude-haiku" ;;
      3) key_var="GEMINI_API_KEY";    model_alias="gemini/gemini-1.5-flash" ;;
      4) key_var="DEEPSEEK_API_KEY";  model_alias="deepseek/deepseek-chat" ;;
      5)
        info "本地 Ollama 模式 — 確保 host 已跑 ollama serve 並 pull qwen2.5:7b"
        model_alias="ollama/qwen2.5:7b"
        ;;
    esac

    if [[ -n "${key_var}" ]]; then
      local existing
      existing="$(get_env "${key_var}" || true)"
      if [[ -n "${existing}" ]]; then
        info "${key_var} 已存在於 .env，沿用"
      else
        local new_key
        new_key="$(read_api_key "${key_var}")"
        set_env "${key_var}" "${new_key}"
      fi
    fi

    if llm_smoke_test "${model_alias}"; then
      log "LLM provider 設定完成：${model_alias}"
      return 0
    fi

    warn "smoke test 未通過，請重新選擇或更換 key"
  done
}

# ---------------------------------------------------------------------------
# Step 7. compose pull + up
# ---------------------------------------------------------------------------
compose_up() {
  title "7. 拉 image + 啟動"
  ( cd "${ROOT_DIR}" && docker compose pull )
  ( cd "${ROOT_DIR}" && docker compose up -d )
  log "docker compose up -d 完成"
}

# ---------------------------------------------------------------------------
# Step 8. 健康檢查輪詢
# ---------------------------------------------------------------------------
wait_for_healthy() {
  title "8. 健康檢查（最多 90s）"
  local deadline=$(( $(date +%s) + 90 ))
  local services=("postgres" "redis" "qdrant" "litellm" "embed-gateway" "api")
  local all_ok=0
  while (( $(date +%s) < deadline )); do
    all_ok=1
    for svc in "${services[@]}"; do
      local cid
      cid="$(cd "${ROOT_DIR}" && docker compose ps -q "${svc}" 2>/dev/null || true)"
      if [[ -z "${cid}" ]]; then
        all_ok=0
        continue
      fi
      local state
      state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${cid}" 2>/dev/null || echo unknown)"
      if [[ "${state}" != "healthy" && "${state}" != "running" ]]; then
        all_ok=0
      fi
    done
    if (( all_ok == 1 )); then
      log "全部 service 健康"
      return 0
    fi
    sleep 3
  done
  err "90s 內未全部健康，請檢查：docker compose ps / docker compose logs"
  ( cd "${ROOT_DIR}" && docker compose ps )
  return 1
}

# ---------------------------------------------------------------------------
# Step 9. Alembic + 驗表
# ---------------------------------------------------------------------------
run_migrations() {
  title "9. Alembic upgrade head"
  ( cd "${ROOT_DIR}" && docker compose exec -T api alembic upgrade head )
  log "alembic 已升級到 head"

  info "檢查 memvault schema 表數量..."
  local pg_user pg_db
  pg_user="$(get_env POSTGRES_USER || echo memvault)"
  pg_db="$(get_env POSTGRES_DB || echo memvault)"
  local count
  count="$(docker compose -f "${ROOT_DIR}/infra/docker-compose.yml" exec -T postgres \
    psql -U "${pg_user}" -d "${pg_db}" -tA \
    -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='memvault';" \
    2>/dev/null | tr -d '[:space:]' || echo 0)"
  if [[ "${count}" =~ ^[0-9]+$ ]] && (( count >= 16 )); then
    log "memvault schema 共 ${count} 張表（預期 ≥16）"
  else
    warn "memvault schema 只有 ${count} 張表，請執行 scripts/doctor.sh 進一步診斷"
  fi
}

# ---------------------------------------------------------------------------
# Step 10. 完成導引
# ---------------------------------------------------------------------------
open_post_install() {
  title "10. 完成"
  local html="${ROOT_DIR}/scripts/post-install.html"
  if [[ ! -f "${html}" ]]; then
    log "安裝完成，前往 http://localhost:3000"
    return 0
  fi
  case "${OS}" in
    Darwin) command -v open >/dev/null 2>&1 && open "${html}" || true ;;
    Linux)  command -v xdg-open >/dev/null 2>&1 && xdg-open "${html}" >/dev/null 2>&1 || true ;;
  esac
  log "安裝完成 — Web UI: http://localhost:3000"
  log "詳細指引：${html}"
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
main() {
  printf '%bmemvault-os installer%b (macOS / Linux)\n' "${BOLD}" "${RESET}"
  detect_os
  clone_or_use_local
  run_preflight
  generate_secrets
  detect_embedding_backend
  configure_llm
  compose_up
  wait_for_healthy
  run_migrations
  open_post_install
}

main "$@"
