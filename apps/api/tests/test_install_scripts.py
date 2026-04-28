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
            tail = text[m.end() : m.end() + 600]
            # Heuristic: in tail, must contain both the var name and \r notation.
            if r"\r" not in tail or var not in tail:
                failures.append(
                    f"{path.name}: `read -r -p ... {var}` is not followed "
                    f"by any CR-strip on {var} within 600 chars"
                )
    assert not failures, (
        "missing \\r strip after interactive read calls:\n  - "
        + "\n  - ".join(failures)
    )


# ---------------------------------------------------------------------------
# Bug H — alembic env.py must run CREATE SCHEMA inside begin_transaction()
# ---------------------------------------------------------------------------
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

    Static check: do_run_migrations must contain `CREATE SCHEMA` only after
    the `with context.begin_transaction()` line, not before.
    """
    env_py = (REPO_ROOT / "apps" / "api" / "alembic" / "env.py").read_text()
    fn = re.search(
        r"def do_run_migrations\([^)]*\)[^:]*:(.+?)(?=^def |\Z)",
        env_py,
        re.M | re.S,
    )
    assert fn, "alembic/env.py: do_run_migrations() not found"
    body = fn.group(1)
    # Strip comments so we don't match the prose explanation that itself
    # mentions "CREATE SCHEMA" / "begin_transaction()" verbatim.
    body_no_comments = re.sub(r"#[^\n]*", "", body)
    txn_match = re.search(r"context\.begin_transaction\(\)", body_no_comments)
    schema_match = re.search(
        r"connection\.execute\([^)]*CREATE\s+SCHEMA",
        body_no_comments,
    )
    assert txn_match, (
        "do_run_migrations must call context.begin_transaction()"
    )
    assert schema_match, (
        "do_run_migrations must call connection.execute(... CREATE SCHEMA ...)"
    )
    assert schema_match.start() > txn_match.start(), (
        "CREATE SCHEMA must be INSIDE the begin_transaction() block, "
        "not before it. Otherwise the auto-begun transaction prevents "
        "alembic from owning a transaction it can commit, and the async "
        "connection release rolls back all migration DDL silently."
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
