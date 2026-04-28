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
