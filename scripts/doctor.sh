#!/usr/bin/env bash
# memvault-os — doctor: end-to-end stack health check
# Usage: ./scripts/doctor.sh
# Exit code: 0 = all green, 1 = one or more checks failed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

FAILED=0
WARNED=0

mark_fail() { FAILED=$((FAILED + 1)); }
mark_warn() { WARNED=$((WARNED + 1)); }

EXPECTED_SERVICES=(postgres redis qdrant litellm embed-gateway api worker web)

section "Docker daemon"
if docker info >/dev/null 2>&1; then
    ok "Docker daemon reachable"
else
    fail "Docker daemon not reachable"
    hint "Start Docker Desktop / colima / dockerd, then rerun this script."
    mark_fail
    # Without docker, nothing else can run — bail out early.
    printf '\n'; hr
    fail "doctor aborted: docker daemon required"
    exit 1
fi

section "Compose stack containers"
RUNNING_LIST="$(dc ps --format '{{.Service}}\t{{.State}}' 2>/dev/null || true)"
for svc in "${EXPECTED_SERVICES[@]}"; do
    state="$(awk -v s="${svc}" '$1==s {print $2; exit}' <<<"${RUNNING_LIST}")"
    case "${state}" in
        running)
            ok "${svc} running"
            ;;
        "")
            fail "${svc} not present in compose stack"
            hint "Run: docker compose up -d ${svc}"
            mark_fail
            ;;
        *)
            fail "${svc} state=${state}"
            hint "Inspect: docker compose logs --tail 100 ${svc}"
            mark_fail
            ;;
    esac
done

section "Postgres connectivity"
if is_running postgres; then
    if dc exec -T postgres pg_isready -U "${POSTGRES_USER:-memvault}" -d "${POSTGRES_DB:-memvault}" >/dev/null 2>&1; then
        ok "postgres accepts connections"
    else
        fail "postgres pg_isready failed"
        hint "Check POSTGRES_PASSWORD in .env, then docker compose logs postgres"
        mark_fail
    fi
else
    warn "postgres not running — skipping"
    mark_warn
fi

section "Redis connectivity"
if is_running redis; then
    pong="$(dc exec -T redis sh -c "redis-cli -a \"\$REDIS_PASSWORD\" --no-auth-warning ping" 2>/dev/null | tr -d '\r' || true)"
    if [[ "${pong}" == "PONG" ]]; then
        ok "redis ping → PONG"
    else
        fail "redis ping failed (got: ${pong:-<empty>})"
        hint "Verify REDIS_PASSWORD matches the one used at first boot."
        mark_fail
    fi
else
    warn "redis not running — skipping"
    mark_warn
fi

section "Qdrant connectivity"
if is_running qdrant; then
    if dc exec -T qdrant sh -c 'wget -qO- http://localhost:6333/healthz >/dev/null 2>&1 \
        || curl -fsS http://localhost:6333/healthz >/dev/null 2>&1'; then
        ok "qdrant /healthz OK"
    else
        # qdrant alpine image lacks both wget and curl on some tags — fall back to TCP probe
        if dc exec -T qdrant sh -c 'echo > /dev/tcp/localhost/6333' >/dev/null 2>&1; then
            ok "qdrant tcp:6333 reachable"
        else
            fail "qdrant /healthz unreachable"
            hint "docker compose logs --tail 100 qdrant"
            mark_fail
        fi
    fi
else
    warn "qdrant not running — skipping"
    mark_warn
fi

section "LiteLLM connectivity"
# Read MEMVAULT_LLM_DEFERRED flag from .env — install.sh sets this to 1 when
# the user picked option 6 (offline mode) or set MEMVAULT_SKIP_LLM=1.
LLM_DEFERRED="$(awk -F= '$1=="MEMVAULT_LLM_DEFERRED"{print $2; exit}' "${REPO_ROOT:-.}/.env" 2>/dev/null | tr -d '[:space:]')"
if is_running litellm; then
    if dc exec -T litellm curl -fsS http://localhost:4000/health/liveliness >/dev/null 2>&1; then
        ok "litellm /health/liveliness OK"
    elif [[ "${LLM_DEFERRED}" == "1" ]]; then
        warn "litellm unhealthy — install ran in offline mode (MEMVAULT_LLM_DEFERRED=1)"
        hint "Add at least one provider key (e.g. OPENAI_API_KEY=sk-...) in .env, then:"
        hint "  docker compose restart litellm  &&  bash scripts/doctor.sh"
        mark_warn
    else
        fail "litellm health check failed"
        hint "docker compose logs --tail 100 litellm — ensure at least one LLM key is set in .env"
        mark_fail
    fi
else
    warn "litellm not running — skipping"
    mark_warn
fi

section "Embed gateway"
if is_running embed-gateway; then
    payload='{"input":["doctor health probe"]}'
    response="$(dc exec -T embed-gateway sh -c \
        "curl -fsS -X POST http://localhost:8081/embed -H 'Content-Type: application/json' -d '${payload}'" 2>/dev/null || true)"
    if [[ -z "${response}" ]]; then
        fail "embed-gateway POST /embed returned no body"
        hint "Backend: ${EMBED_BACKEND:-onnx}. docker compose logs embed-gateway"
        mark_fail
    else
        # Accept either {data:[{embedding:[...]}]} or {embeddings:[[...]]}
        dim="$(printf '%s' "${response}" | python3 -c '
import json, sys
try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(2)
emb = None
if isinstance(payload, dict):
    if "data" in payload and payload["data"]:
        emb = payload["data"][0].get("embedding")
    elif "embeddings" in payload and payload["embeddings"]:
        emb = payload["embeddings"][0]
    elif "embedding" in payload:
        emb = payload["embedding"]
print(len(emb) if isinstance(emb, list) else 0)
' 2>/dev/null || echo 0)"
        if [[ "${dim}" == "1024" ]]; then
            ok "embed-gateway returned 1024-d vector (backend=${EMBED_BACKEND:-onnx})"
        elif [[ "${dim}" =~ ^[0-9]+$ ]] && [[ "${dim}" -gt 0 ]]; then
            warn "embed-gateway returned ${dim}-d vector (expected 1024)"
            hint "EMBED_DIM mismatch — Qdrant collection is locked at 1024."
            mark_warn
        else
            fail "embed-gateway response could not be parsed"
            hint "Sample response: $(printf '%s' "${response}" | head -c 200)"
            mark_fail
        fi
    fi
else
    fail "embed-gateway not running"
    mark_fail
fi

section "API readiness"
api_port="${API_PORT:-8080}"
if curl -fsS "http://127.0.0.1:${api_port}/health/readiness" >/dev/null 2>&1 \
    || curl -fsS "http://127.0.0.1:${api_port}/health" >/dev/null 2>&1; then
    ok "api http://127.0.0.1:${api_port}/health(/readiness) OK"
else
    fail "api not reachable on 127.0.0.1:${api_port}"
    hint "docker compose logs --tail 100 api"
    mark_fail
fi

section "Alembic migrations"
if is_running api; then
    current="$(dc exec -T api alembic current 2>/dev/null | awk '/\(head\)/{print $1; exit} /^[a-f0-9]{12,}/{print $1; exit}' || true)"
    heads="$(dc exec -T api alembic heads 2>/dev/null | awk '/^[a-f0-9]{12,}/{print $1}' | sort -u || true)"
    if [[ -z "${current}" ]] || [[ -z "${heads}" ]]; then
        warn "alembic state unknown (current='${current}' heads='${heads}')"
        hint "docker compose exec api alembic current && alembic heads"
        mark_warn
    elif [[ $(wc -l <<<"${heads}" | tr -d ' ') -gt 1 ]]; then
        fail "multiple alembic heads detected — merge migrations"
        hint "alembic merge -m 'merge heads' $(tr '\n' ' ' <<<"${heads}")"
        mark_fail
    elif [[ "${current}" == "${heads}" ]]; then
        ok "alembic at head (${current})"
    else
        fail "alembic drift: current=${current}, head=${heads}"
        hint "Run: ./scripts/upgrade.sh  (or: docker compose exec api alembic upgrade head)"
        mark_fail
    fi
else
    warn "api not running — skipping alembic check"
    mark_warn
fi

section "Volume usage"
vol_out="$(docker volume ls --filter name=memvault --format '{{.Name}}' 2>/dev/null || true)"
if [[ -z "${vol_out}" ]]; then
    warn "no memvault docker volumes found yet"
    mark_warn
else
    while read -r vol; do
        [[ -z "${vol}" ]] && continue
        size="$(docker run --rm -v "${vol}:/v:ro" alpine:3 sh -c 'du -sh /v 2>/dev/null | cut -f1' 2>/dev/null || echo "?")"
        info "${vol}: ${size}"
    done <<<"${vol_out}"
    # Disk pressure on the docker root
    if df -h 2>/dev/null | awk 'NR==1 || /memvault/{print}' | grep -q memvault; then
        df -h | awk 'NR==1 || /memvault/' | sed 's/^/   /'
    fi
fi

hr
if [[ ${FAILED} -gt 0 ]]; then
    fail "doctor: ${FAILED} failure(s), ${WARNED} warning(s)"
    exit 1
elif [[ ${WARNED} -gt 0 ]]; then
    warn "doctor: 0 failures, ${WARNED} warning(s)"
    exit 0
else
    ok "doctor: all checks passed"
    exit 0
fi
