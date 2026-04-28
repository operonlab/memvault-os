#!/usr/bin/env bash
# memvault-os — pin-images: pull each third-party image, capture its digest,
# and write *_DIGEST=sha256:... back into .env.example.
#
# Usage:
#   ./scripts/pin-images.sh           # update .env.example in place (writes .env.example.bak)
#   ./scripts/pin-images.sh --check   # diff only; exit 1 if any digest is stale

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_cmd docker

CHECK_ONLY=0
for arg in "$@"; do
    case "${arg}" in
        --check) CHECK_ONLY=1 ;;
        --help|-h)
            sed -n '2,10p' "$0"
            exit 0
            ;;
        *) fail "Unknown flag: ${arg}"; exit 64 ;;
    esac
done

ENV_FILE="${REPO_ROOT}/.env.example"
if [[ ! -f "${ENV_FILE}" ]]; then
    fail "Missing ${ENV_FILE}"
    exit 1
fi

# (env_var_prefix, image_ref) — order matches .env.example block.
# WHY litellm:main-stable (not v1.55.10): infra/docker-compose.yml references
# `ghcr.io/berriai/litellm:main-stable@${LITELLM_DIGEST}`. v1.55.10 doesn't
# exist on ghcr → resolve_digest fails → LITELLM_DIGEST stays as placeholder
# 0000... → fresh `docker compose pull` errors with "manifest unknown".
declare -a IMAGES=(
    "PG|pgvector/pgvector:0.8.0-pg16"
    "REDIS|redis:7.4.1-alpine"
    "QDRANT|qdrant/qdrant:v1.12.4"
    "LITELLM|ghcr.io/berriai/litellm:main-stable"
    "VLLM|vllm/vllm-openai:v0.6.4.post1"
    "MINIO|minio/minio:RELEASE.2024-12-13T22-19-12Z"
)

# resolve_digest <image_ref>  — prints "sha256:..." (multi-arch index digest if available).
resolve_digest() {
    local ref="$1" digest=""
    if command -v docker >/dev/null 2>&1; then
        # Prefer buildx imagetools (works without a local pull and returns the manifest list digest).
        if docker buildx imagetools inspect "${ref}" >/dev/null 2>&1; then
            digest="$(docker buildx imagetools inspect "${ref}" 2>/dev/null \
                | awk '/^Digest:/ {print $2; exit}')"
        fi
        # Fallback: pull + parse RepoDigests (architecture-specific).
        if [[ -z "${digest}" ]]; then
            docker pull "${ref}" >/dev/null 2>&1 || true
            digest="$(docker image inspect "${ref}" --format '{{range .RepoDigests}}{{println .}}{{end}}' 2>/dev/null \
                | awk -F'@' '/sha256:/ {print $2; exit}')"
        fi
    fi
    printf '%s' "${digest}"
}

DIFFS=0
TMP="$(mktemp)"
cp "${ENV_FILE}" "${TMP}"

section "Resolving digests"
for entry in "${IMAGES[@]}"; do
    prefix="${entry%%|*}"
    ref="${entry##*|}"
    var="${prefix}_DIGEST"

    info "${var}  ←  ${ref}"
    digest="$(resolve_digest "${ref}")"
    if [[ -z "${digest}" ]]; then
        warn "could not resolve digest for ${ref} (skipped)"
        continue
    fi

    current="$(awk -F'=' -v v="${var}" '$1==v {print $2; exit}' "${ENV_FILE}" \
        | awk '{print $1}')"
    if [[ "${current}" == "${digest}" ]]; then
        ok "${var} already pinned (${digest:0:23}…)"
        continue
    fi

    DIFFS=$((DIFFS + 1))
    if [[ ${CHECK_ONLY} -eq 1 ]]; then
        printf '  %s≠%s %s\n' "${C_YELLOW}" "${C_RESET}" "${var}"
        printf '       was: %s\n' "${current:-<unset>}"
        printf '       new: %s\n' "${digest}"
    else
        # Replace the value while keeping any trailing comment intact.
        # Pattern: ^VAR=<value>(<spaces><# comment>)?
        python3 - "${TMP}" "${var}" "${digest}" <<'PY'
import io, re, sys
path, var, digest = sys.argv[1], sys.argv[2], sys.argv[3]
with io.open(path, "r", encoding="utf-8") as f:
    text = f.read()
pattern = re.compile(rf'^({re.escape(var)}=)([^\s#]*)(\s*#.*)?$', re.MULTILINE)
def repl(m):
    return f"{m.group(1)}{digest}{m.group(3) or ''}"
new_text, n = pattern.subn(repl, text)
if n == 0:
    sys.stderr.write(f"warning: {var} not found in {path}\n")
with io.open(path, "w", encoding="utf-8") as f:
    f.write(new_text)
PY
        ok "${var} updated"
    fi
done

if [[ ${CHECK_ONLY} -eq 1 ]]; then
    rm -f "${TMP}"
    if [[ ${DIFFS} -gt 0 ]]; then
        fail "${DIFFS} digest(s) stale — run scripts/pin-images.sh to refresh"
        exit 1
    fi
    ok "all digests up to date"
    exit 0
fi

if [[ ${DIFFS} -eq 0 ]]; then
    rm -f "${TMP}"
    ok "no changes needed"
    exit 0
fi

cp "${ENV_FILE}" "${ENV_FILE}.bak"
mv "${TMP}" "${ENV_FILE}"
ok "wrote ${ENV_FILE} (backup: ${ENV_FILE}.bak)"
