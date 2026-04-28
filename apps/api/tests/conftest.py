"""E2E test fixtures — talk to a real docker-compose API.

This file is consumed by `test_e2e_api.py`. It deliberately makes ZERO
assumptions about implementation internals — only the public HTTP contract
(`docs/route_manifest.yaml` + `apps/api/src/memvault/schemas.py`).

Configuration via env:
    MEMVAULT_TEST_BASE_URL  default http://localhost:8080
    MEMVAULT_TEST_TIMEOUT   default 30 (seconds)
    MEMVAULT_TEST_PREFIX    default memvault_test_  (string used to tag
                            test-created blocks so clean_db can wipe them
                            without touching real data)
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_TIMEOUT = 30.0
TEST_PREFIX = os.environ.get("MEMVAULT_TEST_PREFIX", "memvault_test_")
TEST_TAG = "memvault_test"  # tag used on every block created by these tests


# --------------------------------------------------------------------------- #
# Session-scoped fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def api_url() -> str:
    """Base URL of the API under test (no trailing slash)."""
    return os.environ.get("MEMVAULT_TEST_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


@pytest.fixture(scope="session")
def api_timeout() -> float:
    return float(os.environ.get("MEMVAULT_TEST_TIMEOUT", DEFAULT_TIMEOUT))


@pytest_asyncio.fixture
async def httpx_client(api_url: str, api_timeout: float) -> AsyncIterator[httpx.AsyncClient]:
    """One async client per test — function-scoped to align with pytest-asyncio's
    per-test event loop. Earlier tried session-scoped + loop_scope=session but
    that conflicted with function-scoped fixtures like `clean_db` that share
    this client. Per-test cost is ~ms vs local docker, so safer wins over fast.
    """
    async with httpx.AsyncClient(
        base_url=api_url,
        timeout=api_timeout,
        follow_redirects=True,
    ) as client:
        yield client


# --------------------------------------------------------------------------- #
# Per-test helpers
# --------------------------------------------------------------------------- #


def _unique_marker() -> str:
    """A short, unique marker safe to embed in `content` and `tags`."""
    return f"{TEST_PREFIX}{uuid.uuid4().hex[:12]}"


@pytest.fixture
def unique_marker() -> str:
    return _unique_marker()


def _is_paginated(payload: object) -> bool:
    """Heuristic: PaginatedResponse-ish dict (has `items` list)."""
    return isinstance(payload, dict) and isinstance(payload.get("items"), list)


@pytest.fixture
def is_paginated():
    return _is_paginated


def _extract_items(payload: object) -> list:
    """Pull a list of items out of either a paginated response or a bare list."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


@pytest.fixture
def extract_items():
    return _extract_items


# --------------------------------------------------------------------------- #
# clean_db — best-effort isolation
# --------------------------------------------------------------------------- #


async def _purge_test_blocks(client: httpx.AsyncClient) -> int:
    """Best-effort purge of blocks created by previous test runs.

    We page through `/api/memvault/blocks`, pick anything whose content starts
    with TEST_PREFIX or whose tags include TEST_TAG, and DELETE it. Errors are
    swallowed — this fixture is ALWAYS best-effort.
    """
    purged = 0
    page = 1
    seen_pages = 0
    max_pages = 20  # safety cap — we don't want to spin forever

    while seen_pages < max_pages:
        try:
            r = await client.get(
                "/api/memvault/blocks",
                params={"page": page, "page_size": 100},
            )
        except Exception:
            return purged
        if r.status_code != 200:
            return purged
        items = _extract_items(r.json())
        if not items:
            return purged

        any_test_block = False
        for item in items:
            if not isinstance(item, dict):
                continue
            content = item.get("content") or ""
            tags = item.get("tags") or []
            block_id = item.get("id")
            is_test_block = (
                isinstance(content, str) and content.startswith(TEST_PREFIX)
            ) or (isinstance(tags, list) and TEST_TAG in tags)
            if is_test_block and block_id:
                any_test_block = True
                try:
                    await client.delete(f"/api/memvault/blocks/{block_id}")
                    purged += 1
                except Exception:
                    pass

        # If this page had no test blocks and we already deleted some on
        # earlier pages, the remaining test blocks may have shifted up — restart
        # from page 1 instead of advancing.
        if any_test_block:
            page = 1
        else:
            page += 1
        seen_pages += 1

    return purged


@pytest_asyncio.fixture
async def clean_db(httpx_client: httpx.AsyncClient) -> AsyncIterator[None]:
    """Wipe `memvault_test_*` blocks before each test that opts in.

    Tests that need a clean slate should request `clean_db`. Tests that only
    READ public state (status, search on shared corpus, etc.) can skip it for
    speed.
    """
    await _purge_test_blocks(httpx_client)
    yield
    # No teardown — the next test's `clean_db` will re-purge. Leaving artefacts
    # between tests gives us a free signal of cross-test pollution.


# --------------------------------------------------------------------------- #
# Convenience: block factory
# --------------------------------------------------------------------------- #


async def _make_block(
    client: httpx.AsyncClient,
    content: str | None = None,
    block_type: str = "general",
    tags: list[str] | None = None,
    source_session: str | None = None,
) -> dict:
    """Create a block via the public POST and return the response body.

    Raises AssertionError if the API does not return a 2xx body containing an
    `id`. Used by tests that need a block to operate on without re-asserting
    every CRUD invariant.
    """
    body = {
        "content": content if content is not None else f"{_unique_marker()} hello world",
        "block_type": block_type,
        "tags": list(tags) if tags is not None else [TEST_TAG],
    }
    if source_session is not None:
        body["source_session"] = source_session
    r = await client.post("/api/memvault/blocks", json=body)
    assert r.status_code in (200, 201), (
        f"create block failed: status={r.status_code} body={r.text[:300]}"
    )
    payload = r.json()
    assert isinstance(payload, dict) and payload.get("id"), (
        f"create block returned no id: {payload!r}"
    )
    return payload


@pytest_asyncio.fixture
async def make_block(httpx_client: httpx.AsyncClient):
    """Factory fixture — call inside a test to create N tagged test blocks."""

    async def _factory(**kwargs):
        return await _make_block(httpx_client, **kwargs)

    return _factory
