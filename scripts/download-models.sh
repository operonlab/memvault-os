#!/usr/bin/env bash
# download-models.sh — pull ONNX embedding model for embed-gateway CPU fallback.
#
# Usage:
#   bash scripts/download-models.sh                 # default target ./models/qwen3-embedding-0.6b
#   bash scripts/download-models.sh /custom/dir
#
# Strategy:
#   1. 主路：huggingface_hub.snapshot_download (透過 uv run，不污染系統 Python)
#      抓 Qwen/Qwen3-Embedding-0.6B 的 onnx/ 目錄。若 repo 沒有 onnx/，會 fail-soft。
#   2. Fallback：直接 curl 抓 mixedbread-ai/mxbai-embed-large-v1 ONNX（已 prebuilt）。
#      mxbai 也是 1024d，但中英效果差異需重新 reindex；README 已警示。
#
# Verify：完成後檢查 model.onnx + tokenizer.json 兩檔存在且非空。

set -euo pipefail

TARGET="${1:-./models/qwen3-embedding-0.6b}"
PRIMARY_REPO="${PRIMARY_REPO:-Qwen/Qwen3-Embedding-0.6B}"
FALLBACK_REPO="${FALLBACK_REPO:-mixedbread-ai/mxbai-embed-large-v1}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; RESET='\033[0m'
log()  { printf "${GREEN}✅${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}⚠️${RESET}  %s\n" "$*"; }
err()  { printf "${RED}❌${RESET} %s\n" "$*" >&2; }
info() { printf "${BLUE}ℹ️${RESET}  %s\n" "$*"; }

mkdir -p "${TARGET}"

verify() {
  local dir="$1"
  [[ -s "${dir}/model.onnx" && -s "${dir}/tokenizer.json" ]]
}

if verify "${TARGET}"; then
  log "model.onnx + tokenizer.json 已存在於 ${TARGET}，略過下載"
  exit 0
fi

# -- Strategy 1: snapshot_download（需 uv 與 huggingface_hub）------------------
try_hf_snapshot() {
  command -v uv >/dev/null 2>&1 || return 1
  info "嘗試 huggingface_hub.snapshot_download → ${PRIMARY_REPO}"
  uv run --quiet --with huggingface_hub python - "$PRIMARY_REPO" "$TARGET" <<'PY'
import os, shutil, sys
from pathlib import Path
from huggingface_hub import snapshot_download

repo = sys.argv[1]
target = Path(sys.argv[2])
target.mkdir(parents=True, exist_ok=True)

try:
    snap = snapshot_download(
        repo_id=repo,
        allow_patterns=["onnx/*", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"],
        local_dir=str(target),
    )
except Exception as exc:
    print(f"snapshot_download failed: {exc}", file=sys.stderr)
    sys.exit(2)

# 若 onnx/ 內有 model.onnx，平移到 target 根
onnx_dir = target / "onnx"
candidate = onnx_dir / "model.onnx"
if candidate.exists():
    shutil.copy2(candidate, target / "model.onnx")
    # 也把可能的 model.onnx_data 一起搬（>2GB ONNX 會分檔）
    for sibling in onnx_dir.glob("model.onnx*"):
        dst = target / sibling.name
        if not dst.exists():
            shutil.copy2(sibling, dst)
elif (target / "model.onnx").exists():
    pass
else:
    print("repo 沒有 onnx/model.onnx，需走 fallback", file=sys.stderr)
    sys.exit(3)

print("ok")
PY
}

# -- Strategy 2: curl mxbai ONNX 直裝 -----------------------------------------
try_mxbai_curl() {
  warn "退而求其次：抓 ${FALLBACK_REPO} ONNX（1024d，中英效果與 Qwen3 不同，需 reindex）"
  command -v curl >/dev/null 2>&1 || { err "找不到 curl"; return 1; }

  local base="https://huggingface.co/${FALLBACK_REPO}/resolve/main"
  curl -fL --retry 3 --retry-delay 2 \
       -o "${TARGET}/model.onnx" \
       "${base}/onnx/model.onnx" \
    || { err "下載 model.onnx 失敗"; return 1; }
  curl -fL --retry 3 --retry-delay 2 \
       -o "${TARGET}/tokenizer.json" \
       "${base}/tokenizer.json" \
    || { err "下載 tokenizer.json 失敗"; return 1; }
  printf 'mxbai-embed-large-v1\n' > "${TARGET}/.model-source"
  return 0
}

if try_hf_snapshot; then
  log "huggingface_hub.snapshot_download 完成"
elif try_mxbai_curl; then
  log "mxbai ONNX fallback 完成（請依 README 重 reindex）"
else
  err "兩條下載路徑皆失敗，請手動下載 model.onnx + tokenizer.json 到 ${TARGET}"
  exit 1
fi

if verify "${TARGET}"; then
  log "驗證通過：${TARGET}/model.onnx ($(du -h "${TARGET}/model.onnx" | awk '{print $1}'))"
else
  err "下載完成但檔案缺失或為空，請排查"
  exit 1
fi
