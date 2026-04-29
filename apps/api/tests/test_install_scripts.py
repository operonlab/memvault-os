"""Regression tests for shell installer scripts.

These tests guard contractual invariants of `scripts/install.sh`,
`scripts/preflight.sh`, `scripts/generate-secrets.sh` (and the PowerShell
twin) without requiring Docker or a running stack. Each test maps to a
real bug found during v1.0.0 → v1.0.1 follow-up:

  * test_generated_passwords_are_url_safe       — bug B (base64 → hex)
  * test_install_main_creates_env_before_preflight — bug A (preflight race)
  * test_install_prepares_images_before_llm_smoke  — bug C (digest 0000 race)
  * test_configure_llm_supports_skip_or_offline    — bug D (no skip option)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "scripts"


# ---------------------------------------------------------------------------
# Bug B — secrets must be URL-safe hex (not base64)
# ---------------------------------------------------------------------------
def test_generated_passwords_are_url_safe(tmp_path: Path):
    """Secrets that go into postgresql:// or redis:// URLs must be hex.

    base64 includes '+', '/', '=' which break URL parsing in the user-info
    section. Both POSTGRES_PASSWORD and REDIS_PASSWORD are interpolated
    directly into URLs in infra/docker-compose.yml.
    """
    if not shutil.which("openssl") or not shutil.which("bash"):
        pytest.skip("openssl or bash not available")

    env_example = tmp_path / ".env.example"
    env_example.write_text(
        "POSTGRES_PASSWORD=\nREDIS_PASSWORD=\n"
        "MEMVAULT_SECRET_KEY=\nLITELLM_MASTER_KEY=\n"
    )

    subprocess.run(
        ["bash", str(SCRIPTS / "generate-secrets.sh")],
        env={**os.environ, "ROOT_DIR": str(tmp_path)},
        check=True,
        capture_output=True,
    )

    content = (tmp_path / ".env").read_text()
    pg = re.search(r"^POSTGRES_PASSWORD=(.+)$", content, re.M)
    redis = re.search(r"^REDIS_PASSWORD=(.+)$", content, re.M)
    assert pg and pg.group(1), "POSTGRES_PASSWORD not generated"
    assert redis and redis.group(1), "REDIS_PASSWORD not generated"
    hex_re = re.compile(r"^[0-9a-f]+$")
    assert hex_re.fullmatch(pg.group(1)), (
        f"POSTGRES_PASSWORD must be hex (URL-safe), got: {pg.group(1)!r}"
    )
    assert hex_re.fullmatch(redis.group(1)), (
        f"REDIS_PASSWORD must be hex (URL-safe), got: {redis.group(1)!r}"
    )


# ---------------------------------------------------------------------------
# Bug A — install.sh main() must ensure .env exists before run_preflight
# ---------------------------------------------------------------------------
def _read_main_body() -> list[str]:
    install_sh = (SCRIPTS / "install.sh").read_text()
    m = re.search(r"^main\(\)\s*\{(.+?)^\}", install_sh, re.M | re.S)
    assert m, "install.sh: main() block not found"
    return [ln.strip() for ln in m.group(1).splitlines()]


def _index_of(lines: list[str], pattern: str) -> int | None:
    for i, ln in enumerate(lines):
        if ln.startswith("#"):
            continue
        if re.search(pattern, ln):
            return i
    return None


def test_install_main_creates_env_before_preflight():
    """preflight.sh write_env_port silently bails when .env is missing.

    Either install.sh main() ensures .env exists before run_preflight, OR
    preflight.sh itself initializes .env from .env.example when missing.
    Defense-in-depth: we accept either layer present (both is fine).
    """
    lines = _read_main_body()
    clone_idx = _index_of(lines, r"^clone_or_use_local\b")
    preflight_idx = _index_of(lines, r"^run_preflight\b")
    ensure_idx = _index_of(lines, r"^ensure_env_exists\b")

    install_layer = (
        clone_idx is not None
        and preflight_idx is not None
        and ensure_idx is not None
        and clone_idx < ensure_idx < preflight_idx
    )

    preflight_sh = (SCRIPTS / "preflight.sh").read_text()
    preflight_layer = bool(
        re.search(
            r"write_env_port\(\)[^}]*?\.env\.example",
            preflight_sh,
            re.S,
        )
    )

    assert install_layer or preflight_layer, (
        "Either install.sh must call ensure_env_exists between "
        "clone_or_use_local and run_preflight, OR preflight.sh "
        "write_env_port must initialize .env from .env.example."
    )


# ---------------------------------------------------------------------------
# Bug C — pin-images / build self-images must run before LLM smoke test
# ---------------------------------------------------------------------------
def test_install_prepares_images_before_llm_smoke():
    """configure_llm starts the litellm container which references
    `ghcr.io/berriai/litellm:main-stable@${LITELLM_DIGEST}`. If LITELLM_DIGEST
    is still the placeholder 'sha256:000...' from .env.example, pull fails
    with 'manifest unknown'. detect_placeholder_digest / pin-images must
    therefore run BEFORE configure_llm in main().
    """
    lines = _read_main_body()
    prep_idx = _index_of(
        lines, r"^(prepare_compose_files|detect_placeholder_digest)\b"
    )
    llm_idx = _index_of(lines, r"^configure_llm\b")

    assert prep_idx is not None, (
        "install.sh main() must call prepare_compose_files (or "
        "detect_placeholder_digest) — needed to fix placeholder digests "
        "before any docker compose pull."
    )
    assert llm_idx is not None, "install.sh main() must call configure_llm"
    assert prep_idx < llm_idx, (
        f"prepare_compose_files must run BEFORE configure_llm "
        f"(prep_idx={prep_idx}, llm_idx={llm_idx})"
    )


# ---------------------------------------------------------------------------
# Bug E — pin-images.sh image refs must match docker-compose.yml tags
# ---------------------------------------------------------------------------
def test_pin_images_refs_match_compose_tags():
    """pin-images.sh image refs MUST match the tags in docker-compose.yml.

    docker-compose.yml uses `image: <name>:<tag>@${X_DIGEST}`. pin-images.sh
    resolves a digest by pulling `<name>:<tag>` and writes to .env.example.
    If the tag in pin-images.sh diverges from the compose tag, the digest
    written into .env applies to a DIFFERENT image variant — at best
    correct-by-coincidence, at worst (e.g. tag doesn't exist on registry)
    digest stays at placeholder 0000... and `docker compose pull` errors
    with 'manifest unknown'.

    Found: pin-images had `litellm:v1.55.10` but compose had
    `litellm:main-stable`; v1.55.10 did not exist on ghcr.
    """
    pin_sh = (SCRIPTS / "pin-images.sh").read_text()
    # Aggregate ALL compose files — vllm lives in gpu.yml, minio in frozen.yml.
    compose_yml = "\n".join(
        p.read_text() for p in sorted((REPO_ROOT / "infra").glob("docker-compose*.yml"))
    )

    # Extract pin-images IMAGES entries: "PREFIX|name:tag"
    block = re.search(r"declare -a IMAGES=\((.+?)\)", pin_sh, re.S)
    assert block, "pin-images.sh: IMAGES array block not found"
    pin_refs: dict[str, str] = {}
    for line in block.group(1).splitlines():
        m = re.search(r'"([A-Z]+)\|([^"]+)"', line)
        if m:
            pin_refs[m.group(1)] = m.group(2)

    assert pin_refs, "pin-images.sh: no IMAGES entries parsed"

    # For each prefix, check the compose file references the SAME image:tag
    # (digest is allowed to differ; that is what pin-images writes).
    failures: list[str] = []
    for prefix, ref in pin_refs.items():
        # ref examples: "redis:7.4.1-alpine", "ghcr.io/berriai/litellm:main-stable"
        # Look for `image: ${ref}@${PREFIX_DIGEST}` in compose (digest can be
        # a literal hash or a variable; we only care the name:tag matches).
        digest_var = f"${{{prefix}_DIGEST}}"
        ref_re = re.escape(ref)
        pattern = rf"image:\s*{ref_re}@(?:\$\{{{prefix}_DIGEST\}}|sha256:[0-9a-f]+)"
        if not re.search(pattern, compose_yml):
            # Also accept tag-only reference (no digest pinning yet).
            tag_only = rf"image:\s*{ref_re}\s*$"
            if not re.search(tag_only, compose_yml, re.M):
                failures.append(
                    f"{prefix}_DIGEST: pin-images uses {ref!r} but compose "
                    f"has no matching `image: {ref}@{digest_var}` line"
                )

    assert not failures, "pin-images / compose tag drift:\n  - " + "\n  - ".join(failures)


# ---------------------------------------------------------------------------
# Bug K — prompt_llm_provider menu must NOT pollute stdout
# ---------------------------------------------------------------------------
def test_prompt_llm_provider_menu_goes_to_stderr():
    """`prompt_llm_provider` is called via `choice=$(prompt_llm_provider)`.

    `$(...)` captures STDOUT. If the menu lines (`請選擇 LLM provider...`
    `1) OpenAI...` etc.) went to stdout, the entire menu text would end up
    inside ${choice}, and the subsequent `case ${choice} in 6) ...` would
    never match a clean "6" — the install would loop forever asking for
    LLM choice. The menu must therefore go to stderr; only the actual
    chosen number goes to stdout (via `printf '%s\\n' "${choice}"`).

    Found while running install.sh under expect (PTY) — option 6
    (offline mode) silently fell through to the smoke-test path because
    the captured choice was the entire menu text, not "6".
    """
    install_sh = (SCRIPTS / "install.sh").read_text()
    fn = re.search(
        r"prompt_llm_provider\(\)\s*\{(.+?)^\}", install_sh, re.M | re.S
    )
    assert fn, "install.sh: prompt_llm_provider() not found"
    body = fn.group(1)
    # Every printf line that draws the menu must redirect to stderr (>&2).
    # The single answer-emit `printf '%s\n' "${choice}"` MUST stay on stdout.
    failures: list[str] = []
    for m in re.finditer(r'^[\s]*(printf\s+"[^"]*[^&]\\n"[^\n]*)$', body, re.M):
        line = m.group(1).strip()
        # Skip the answer-emit line.
        if "${choice}" in line:
            continue
        if ">&2" not in line:
            failures.append(line)
    assert not failures, (
        "prompt_llm_provider menu printfs must redirect to stderr (>&2)\n"
        "to avoid polluting the $() capture in configure_llm. Offending:\n  - "
        + "\n  - ".join(failures)
    )


# ---------------------------------------------------------------------------
# Bug J — interactive `read -r` callers must strip CR from PTY / CRLF stdin
# ---------------------------------------------------------------------------
def test_interactive_reads_strip_cr():
    """All `read -r -p` callers must strip \\r from the captured value.

    Found while running install.sh under expect (PTY emulator): the LLM
    provider menu's `read -r -p "選擇 [1-6]:"` captured "6\\r" (PTY drivers
    do not strip the carriage return, and `read -r` only strips \\n).
    The case-match against "1|2|3|4|5|6" then silently failed, the prompt
    looped forever, and the install hung. Same issue surfaces with
    Windows-style CRLF stdin redirection.

    Convention: every interactive read whose value is matched against a
    pattern (case / regex) must strip CR (and LF) before matching.
    """
    targets = [
        SCRIPTS / "install.sh",      # configure_llm + read_api_key
        SCRIPTS / "preflight.sh",    # prompt_new_port
    ]
    failures: list[str] = []
    for path in targets:
        text = path.read_text()
        # For each `read -r [-s] -p "..." VAR` find the var, then in the
        # following ~600 chars require a reference to `\r` mentioning that
        # var. This is a heuristic — accepts $'\r' notation or any other
        # CR-stripping idiom that names the variable.
        for m in re.finditer(
            r"read\s+-r\s+(?:-s\s+)?-p\s+\"[^\"]*\"\s+(\w+)",
            text,
        ):
            var = m.group(1)
            # Window of 1200 chars accommodates the EOF-guard branch + WHY
            # comments that legitimately sit between the read and the strip.
            tail = text[m.end() : m.end() + 1200]
            # Heuristic: in tail, must contain both the var name and \r notation.
            if r"\r" not in tail or var not in tail:
                failures.append(
                    f"{path.name}: `read -r -p ... {var}` is not followed "
                    f"by any CR-strip on {var} within 1200 chars"
                )
    assert not failures, (
        "missing \\r strip after interactive read calls:\n  - "
        + "\n  - ".join(failures)
    )


# ---------------------------------------------------------------------------
# Bug L — api image must include apps/worker source for the worker container
# ---------------------------------------------------------------------------
def test_api_image_bundles_worker_module():
    """The worker compose service reuses the api image with a different CMD
    (`python -m apps.worker.main`). For that to resolve, apps/worker/ must
    be COPY'd into the image at /app/apps/worker/.

    Found while running install.sh fresh: worker container restart-loops
    with `ModuleNotFoundError: No module named 'apps.worker.main'`.
    The api Dockerfile was using `context: ../apps/api` so it could only
    see apps/api source — apps/worker was invisible to the build.

    Static check: api Dockerfile must COPY apps/worker, AND both api and
    worker compose services must use a build context that lets the
    Dockerfile see apps/worker (i.e. repo root, not apps/api).
    """
    dockerfile = (REPO_ROOT / "apps" / "api" / "Dockerfile").read_text()
    assert re.search(
        r"^\s*COPY\s+apps/worker/?\s+", dockerfile, re.M
    ), "apps/api/Dockerfile must `COPY apps/worker/ ...` so the worker container's `python -m apps.worker.main` can resolve."

    # Check EVERY docker-compose*.yml file — dev override could re-introduce
    # the bug if not kept consistent with the base file.
    for compose_path in sorted((REPO_ROOT / "infra").glob("docker-compose*.yml")):
        compose = compose_path.read_text()
        for svc in ("api", "worker"):
            # Match `^  <svc>:` and capture until the next sibling key.
            block = re.search(
                rf"^\s{{2}}{svc}:\n(.+?)(?=^\s{{0,2}}\w[^:]*:|\Z)",
                compose,
                re.M | re.S,
            )
            if not block:
                continue  # service not present in this override file
            ctx = re.search(
                r"build:\s*\n\s+(?:#[^\n]*\n\s+)*context:\s*(\S+)",
                block.group(1),
            )
            if not ctx:
                continue  # service present but has no build block
            assert ctx.group(1) != "../apps/api", (
                f"{compose_path.name}::{svc} uses context=../apps/api which "
                f"hides apps/worker from the Dockerfile build — worker "
                f"container will fail with ModuleNotFoundError. Use context=.."
            )


# ---------------------------------------------------------------------------
# Bug H — alembic env.py must run CREATE SCHEMA inside begin_transaction()
# ---------------------------------------------------------------------------
def _is_create_schema_call(node) -> bool:
    """True iff `node` is a call to `connection.execute(text("CREATE SCHEMA..."))`
    (or any execute() whose first arg's source contains 'CREATE SCHEMA').
    """
    import ast

    if not isinstance(node, ast.Call):
        return False
    func = node.func
    # Must be `<something>.execute(...)`.
    if not (isinstance(func, ast.Attribute) and func.attr == "execute"):
        return False
    if not node.args:
        return False
    arg = node.args[0]
    try:
        src = ast.unparse(arg)
    except Exception:
        return False
    return "CREATE SCHEMA" in src.upper()


def test_alembic_env_creates_schema_inside_transaction():
    """alembic env.py do_run_migrations must keep CREATE SCHEMA inside the
    `with context.begin_transaction():` block.

    Why: SQLAlchemy 2.x sync Connection auto-begins a transaction on the
    first execute() call. If we run `CREATE SCHEMA` before
    `begin_transaction()`, that auto-begun tx (T1) is already open, alembic's
    begin_transaction sees in-transaction and becomes a no-op, and when
    `async with connectable.connect()` releases the connection, SQLAlchemy
    rolls back any pending transaction — silently discarding all migration
    DDL. Symptom: `alembic upgrade head` exits 0 and prints "Running upgrade",
    but 0 tables get created and `alembic_version` does not exist.

    AST-based check (replaces the original line-order regex which a
    re-indent could trivially fool): walk do_run_migrations, find every
    `connection.execute(text("CREATE SCHEMA …"))`, and assert every single
    one is *inside* the `with context.begin_transaction():` block.
    """
    import ast

    env_py = (REPO_ROOT / "apps" / "api" / "alembic" / "env.py").read_text()
    tree = ast.parse(env_py)

    target_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "do_run_migrations":
            target_fn = node
            break
    assert target_fn is not None, "alembic/env.py: do_run_migrations() not found"

    # Find the `with context.begin_transaction():` block at any depth.
    txn_with: ast.With | None = None
    for node in ast.walk(target_fn):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            ctx = item.context_expr
            if (
                isinstance(ctx, ast.Call)
                and isinstance(ctx.func, ast.Attribute)
                and ctx.func.attr == "begin_transaction"
            ):
                txn_with = node
                break
        if txn_with is not None:
            break
    assert txn_with is not None, (
        "do_run_migrations must use `with context.begin_transaction():`"
    )

    inside_calls = [n for n in ast.walk(txn_with) if _is_create_schema_call(n)]

    # Walk everything in do_run_migrations EXCEPT the txn_with subtree.
    # Set lookup uses object identity (id()) which is the right semantic
    # here — we want to exclude the exact subtree, not value-equal nodes.
    inside_ids = {id(n) for n in ast.walk(txn_with)}
    outside_calls = [
        n
        for n in ast.walk(target_fn)
        if id(n) not in inside_ids and _is_create_schema_call(n)
    ]

    assert inside_calls, (
        "do_run_migrations must call `connection.execute(... CREATE SCHEMA ...)` "
        "inside the begin_transaction() block."
    )
    assert not outside_calls, (
        "CREATE SCHEMA must be INSIDE begin_transaction(); found "
        f"{len(outside_calls)} call(s) outside the transaction. The "
        "auto-begun transaction will swallow the migration DDL on close."
    )


# ---------------------------------------------------------------------------
# Bug G — wait_for_healthy must not require litellm
# ---------------------------------------------------------------------------
def test_wait_for_healthy_treats_litellm_as_optional():
    """install.sh wait_for_healthy must not treat litellm as required.

    litellm in main-stable currently fails its prisma DATABASE_URL
    validation at startup and stays at `health: starting` indefinitely.
    If wait_for_healthy requires litellm to be healthy, the 90s loop
    times out, install.sh aborts before run_migrations runs, and the
    user is left with an empty memvault schema (0 tables).

    The required list must therefore NOT include litellm. Optional / best
    effort reporting is fine.
    """
    install_sh = (SCRIPTS / "install.sh").read_text()
    fn = re.search(
        r"wait_for_healthy\(\)\s*\{(.+?)^\}", install_sh, re.M | re.S
    )
    assert fn, "install.sh: wait_for_healthy() not found"
    body = fn.group(1)

    required_match = re.search(
        r"local\s+required=\(([^)]+)\)", body
    )
    assert required_match, (
        "wait_for_healthy must declare a `local required=(...)` array "
        "of services that gate install completion"
    )
    required = required_match.group(1)
    assert '"litellm"' not in required, (
        "wait_for_healthy required services must NOT include litellm — "
        "litellm prisma is unhealthy on fresh installs and would block "
        "alembic upgrade. List litellm under optional instead."
    )


# ---------------------------------------------------------------------------
# Bug F — services must NOT block on litellm:service_healthy
# ---------------------------------------------------------------------------
def test_no_service_requires_litellm_healthy():
    """No service may declare `litellm: condition: service_healthy`.

    litellm requires a working LLM provider key + working prisma DATABASE_URL
    to pass health. Either of those failing (no API key on a fresh install,
    or a litellm prisma regression) leaves litellm in `health: starting`
    forever. Any dependent waiting on `service_healthy` then prevents
    `docker compose up -d` from succeeding, which causes install.sh to
    abort before alembic migrations run.

    Convention: dependents must use `condition: service_started` so the LLM
    layer can degrade gracefully (existing comment on api: "LLM is optional
    at runtime; degrade gracefully"). Found on `worker` service which still
    used service_healthy.
    """
    import yaml

    failures: list[str] = []
    for compose_path in sorted((REPO_ROOT / "infra").glob("docker-compose*.yml")):
        try:
            data = yaml.safe_load(compose_path.read_text())
        except Exception as exc:
            pytest.fail(f"could not parse {compose_path}: {exc}")
        for svc_name, svc in (data.get("services") or {}).items():
            if not isinstance(svc, dict):
                continue
            deps = svc.get("depends_on") or {}
            if not isinstance(deps, dict):
                continue
            litellm_dep = deps.get("litellm")
            if isinstance(litellm_dep, dict):
                if litellm_dep.get("condition") == "service_healthy":
                    failures.append(
                        f"{compose_path.name}::{svc_name} depends_on litellm "
                        f"with condition=service_healthy — must be service_started"
                    )

    assert not failures, "litellm health-gating violations:\n  - " + "\n  - ".join(
        failures
    )


# ---------------------------------------------------------------------------
# Bug D — configure_llm must support offline / skip mode
# ---------------------------------------------------------------------------
def test_configure_llm_supports_skip_or_offline():
    """Fresh users with zero LLM keys must not be hard-stuck.

    install.sh must support either:
      * an env-var bypass (e.g. MEMVAULT_SKIP_LLM=1 / OFFLINE_MODE=1), or
      * an interactive 'skip / offline' option in the provider menu.
    """
    install_sh = (SCRIPTS / "install.sh").read_text()
    has_env_skip = bool(
        re.search(
            r"MEMVAULT_SKIP_LLM|OFFLINE_MODE|SKIP_LLM_SMOKE",
            install_sh,
        )
    )
    has_interactive_skip = bool(
        re.search(r"6\)\s*(暫時)?跳過|6\)\s*skip", install_sh)
    )
    assert has_env_skip or has_interactive_skip, (
        "configure_llm must allow offline / skip via env var or "
        "interactive option — otherwise users without keys are blocked."
    )


# ===========================================================================
# v1.0.1-rc2 regression tests — codex review fallout
# ===========================================================================


def test_dockerignore_excludes_secrets_and_venvs():
    """`.dockerignore` at repo root must exclude .env*, .venv, .git, etc.

    Bug N (codex slice 2): api build context was switched to repo root so the
    Dockerfile can COPY apps/worker/. Without a .dockerignore, the context
    balloons (apps/api/.venv alone is 514 MB) AND `.env` ends up in build
    layers, leaking POSTGRES_PASSWORD / OPENAI_API_KEY into image history.
    """
    p = REPO_ROOT / ".dockerignore"
    assert p.exists(), ".dockerignore at repo root is required for safe build context"
    text = p.read_text()
    must_have = [".env", ".venv", ".git/", "__pycache__"]
    missing = [tok for tok in must_have if tok not in text]
    assert not missing, f".dockerignore missing critical entries: {missing}"


def test_api_dockerfile_pins_uv():
    """apps/api/Dockerfile must pin uv to a specific version.

    Bug O (codex slice 2): bare `pip install uv` resolves whatever PyPI has
    today, making fallback build mode non-reproducible — a future uv release
    that breaks `pip compile` would silently break new installs.
    """
    text = (REPO_ROOT / "apps" / "api" / "Dockerfile").read_text()
    assert re.search(r"pip install[^\n]*'uv==[\d.]+'", text), (
        "apps/api/Dockerfile must pin uv with `pip install 'uv==X.Y.Z'`"
    )


def test_compose_api_healthcheck_has_start_period():
    """api healthcheck must declare start_period so install.sh's 90s health
    deadline doesn't race the api's cold-start window (uvicorn boot + alembic
    deps wiring can easily exceed 30s).
    """
    import yaml
    data = yaml.safe_load(
        (REPO_ROOT / "infra" / "docker-compose.yml").read_text()
    )
    api_hc = (data.get("services") or {}).get("api", {}).get("healthcheck") or {}
    assert "start_period" in api_hc, (
        "infra/docker-compose.yml api.healthcheck must set start_period"
    )


def test_generate_secrets_includes_minio():
    """shell generate-secrets.sh must seed MINIO_ROOT_PASSWORD (PowerShell
    sibling already does). Frozen-tier compose override needs it; without
    seeding `docker compose --profile frozen up` fails."""
    text = (SCRIPTS / "generate-secrets.sh").read_text()
    assert re.search(r"fill_secret\s+MINIO_ROOT_PASSWORD\b", text), (
        "scripts/generate-secrets.sh must call `fill_secret MINIO_ROOT_PASSWORD`"
    )


def test_no_hardcoded_litellm_localhost_in_app_code():
    """No app-code site may hardcode `http://localhost:4000`.

    Bug M (codex slice 2): llm_config.py (and 3 sibling extractors) used
    a literal localhost:4000 + dev master key, ignoring compose-injected
    LITELLM_BASE / LITELLM_KEY. Result: even after the user supplied a
    valid OPENAI_API_KEY, every LLM call from api/worker hit the *current
    container's* localhost (nothing) and 503'd.
    """
    src_root = REPO_ROOT / "apps" / "api" / "src"
    bad: list[str] = []
    for py in src_root.rglob("*.py"):
        text = py.read_text()
        for m in re.finditer(r'"http://localhost:4000[^"]*"', text):
            # Allow comments-only references; exclude lines that start with #
            line_start = text.rfind("\n", 0, m.start()) + 1
            line = text[line_start : text.find("\n", m.start())]
            if line.lstrip().startswith("#"):
                continue
            bad.append(f"{py.relative_to(REPO_ROOT)}: {line.strip()[:100]}")
    assert not bad, (
        "literal http://localhost:4000 must not appear in app code:\n  - "
        + "\n  - ".join(bad)
    )


def test_llm_config_reads_litellm_base_env():
    """apps/api/src/memvault/llm_config.py must read LITELLM_BASE from env."""
    text = (
        REPO_ROOT / "apps" / "api" / "src" / "memvault" / "llm_config.py"
    ).read_text()
    assert re.search(
        r'os\.environ\.get\(\s*["\']LITELLM_BASE["\']', text
    ) or re.search(
        r'os\.getenv\(\s*["\']LITELLM_BASE["\']', text
    ), "llm_config.py must read LITELLM_BASE via os.environ.get/getenv"


def test_install_sh_smoke_uses_litellm_aliases():
    """install.sh's smoke test model_alias must match aliases declared in
    infra/litellm/config.yaml model_list. Otherwise the proxy returns
    `Model not found` even when the provider key works (codex must-fix 7).
    """
    import yaml
    cfg = yaml.safe_load(
        (REPO_ROOT / "infra" / "litellm" / "config.yaml").read_text()
    )
    aliases = {m["model_name"] for m in (cfg.get("model_list") or [])}
    text = (SCRIPTS / "install.sh").read_text()
    used = set()
    for m in re.finditer(r'model_alias="([^"]+)"', text):
        val = m.group(1)
        # Skip ollama/qwen2.5:7b — host-side fallback handled separately.
        # Skip "$1" — that's the function parameter binding in
        # `llm_smoke_test()`, not an actual alias.
        if val.startswith("ollama/") or val.startswith("$"):
            continue
        used.add(val)
    missing = used - aliases
    assert not missing, (
        f"install.sh smoke uses aliases not in litellm config: {missing}; "
        f"available: {sorted(aliases)}"
    )


def test_doctor_uses_dotenv_helper_for_llm_deferred():
    """scripts/doctor.sh must read MEMVAULT_LLM_DEFERRED via the
    read_dotenv_value helper (sources scripts/_dotenv.sh).

    The naïve `awk -F= '$1==KEY{print $2}'` parser was found to misjudge
    `=1`, `=` empty, `=1 # comment`, and `# KEY=1` (codex must-fix 9).
    """
    doctor = (SCRIPTS / "doctor.sh").read_text()
    assert re.search(r"source\s+[\"']?\$\{?SCRIPT_DIR\}?[/\"']?[^\n]*_dotenv\.sh", doctor) or \
           "_dotenv.sh" in doctor, (
        "scripts/doctor.sh must source scripts/_dotenv.sh"
    )
    assert re.search(
        r'LLM_DEFERRED="\$\(read_dotenv_value\s+MEMVAULT_LLM_DEFERRED\)"',
        doctor,
    ), "doctor.sh must read MEMVAULT_LLM_DEFERRED via read_dotenv_value, not raw awk"


def test_doctor_retries_litellm_when_provider_key_set():
    """When the user has supplied an LLM provider key, doctor.sh must retry
    litellm /health/liveliness instead of failing on the first 503.

    Otherwise the documented recovery flow (`docker compose restart litellm
    && bash scripts/doctor.sh`) gives a misleading red within the first
    few seconds before litellm finishes boot (codex must-fix 10).
    """
    doctor = (SCRIPTS / "doctor.sh").read_text()
    assert re.search(
        r"HAS_PROVIDER_KEY", doctor
    ), "doctor.sh must derive HAS_PROVIDER_KEY by scanning provider env keys"
    assert re.search(
        r"for\s+i\s+in\s+\$\(seq\s+1\s+\$\{?RETRIES\}?\)|sleep\s+\d+", doctor
    ), "doctor.sh must retry-with-sleep when probing litellm health"


def test_readme_blocker_count_matches_letter_set():
    """README must say `11 ... blockers / 阻塞點` (A-H + J/K/L) — earlier text
    said `12` which mismatches the actual eleven letters (we skip `I`).
    """
    patterns = {
        "README.md": r"11[^0-9].{0,40}blocker",
        "README.zh.md": r"11[^0-9].{0,40}阻塞點",
    }
    for fname, pat in patterns.items():
        text = (REPO_ROOT / fname).read_text()
        assert re.search(pat, text), (
            f"{fname}: must say 11 blockers / 阻塞點 (not 12); we use "
            f"letters A-H, J-L (skip I) → 11 total"
        )


def test_ci_verifies_table_count_after_alembic():
    """CI must assert table count after `alembic upgrade head`.

    Bug H regressed silently because alembic exited 0 with zero tables.
    A green upgrade step is therefore not enough — assert that the
    memvault schema actually populated.
    """
    text = (REPO_ROOT / ".github" / "workflows" / "test.yml").read_text()
    assert "information_schema.tables" in text, (
        ".github/workflows/test.yml must SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='memvault' after alembic upgrade head"
    )
