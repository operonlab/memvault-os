"""Adversary tests for `_ensure_block` — including REAL DB integration.

Unit cases (with FakeSession):
  - Insert when missing
  - Update when (space_id, block_type) exists (no duplicate row)
  - word_count handles None content
  - block_version monotonically increases on content change
  - block_version stable on identical content

Integration case (real PG, requires memvault.memory_block schema):
  - Partial unique index `uq_memory_block_space_type_active` actually blocks
    a second active row for the same (space_id, block_type)
  - Soft-deleted row does NOT block a new insert (deleted_at IS NULL filter)

Cleanup: every adversary_space row is hard-deleted via DELETE WHERE space_id LIKE.
"""
# ruff: noqa: S110

from __future__ import annotations

import pytest

pytest.skip(
    "DB-integration test with hardcoded monorepo path /Users/joneshong/workshop/core/src — needs rework for OSS",
    allow_module_level=True,
)

import sys
import uuid

from src.memvault.models import MemoryBlockSnapshot
from src.memvault.sleeptime import (
    _ensure_block,
)


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _ExecResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _Scalars(self._items)

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class FakeSession:
    def __init__(self):
        self.snapshots: dict[tuple[str, str], MemoryBlockSnapshot] = {}
        self._next_lookup_hint = None
        self.committed = False
        self.add_calls = 0

    async def execute(self, stmt):
        target = None
        try:
            target = stmt.column_descriptions[0]["entity"]
        except Exception:
            pass
        if target is MemoryBlockSnapshot:
            hint = self._next_lookup_hint
            if hint is not None:
                items = [
                    s for s in self.snapshots.values()
                    if (s.space_id, s.block_type) == hint
                ]
            else:
                items = list(self.snapshots.values())
            return _ExecResult(items)
        return _ExecResult([])

    def add(self, obj):
        self.add_calls += 1
        if isinstance(obj, MemoryBlockSnapshot):
            self.snapshots[(obj.space_id, obj.block_type)] = obj

    async def commit(self):
        self.committed = True


# ---- (a) insert when missing ----


@pytest.mark.asyncio
async def test_ensure_block_inserts_first_time():
    s = FakeSession()
    s._next_lookup_hint = ("space-A", "project")
    blk = await _ensure_block(s, "space-A", "project", "hello world content")
    assert blk.space_id == "space-A"
    assert blk.block_type == "project"
    assert blk.content == "hello world content"
    assert blk.block_version == 1
    assert s.add_calls == 1


# ---- (b) second call same (space, type) — must update, NOT insert ----


@pytest.mark.asyncio
async def test_ensure_block_updates_no_duplicate_insert():
    s = FakeSession()
    s._next_lookup_hint = ("space-B", "project")
    first = await _ensure_block(s, "space-B", "project", "v1 content")

    s._next_lookup_hint = ("space-B", "project")
    second = await _ensure_block(s, "space-B", "project", "v2 different content")

    assert second is first, "must mutate same row, not create new"
    assert second.content == "v2 different content"
    assert second.block_version == 2, "version must bump on change"
    assert s.add_calls == 1, (
        f"BUG: _ensure_block called add() {s.add_calls} times — "
        "should be 1 (no duplicate insert)"
    )


# ---- (c) identical content — version stable ----


@pytest.mark.asyncio
async def test_ensure_block_no_bump_on_identical_content():
    s = FakeSession()
    s._next_lookup_hint = ("space-C", "project")
    first = await _ensure_block(s, "space-C", "project", "same")
    s._next_lookup_hint = ("space-C", "project")
    second = await _ensure_block(s, "space-C", "project", "same")
    assert second is first
    assert second.block_version == 1, (
        f"BUG: identical content bumped version to {second.block_version}"
    )


# ---- (d) None content — word_count handling ----


@pytest.mark.asyncio
async def test_ensure_block_handles_none_content():
    s = FakeSession()
    s._next_lookup_hint = ("space-D", "persona")
    try:
        blk = await _ensure_block(s, "space-D", "persona", None)
    except Exception as exc:
        pytest.fail(f"_ensure_block must handle None content, got {exc!r}")
    assert blk.block_type == "persona"
    assert blk.content is None
    # word_count must be a valid int (0 acceptable)
    assert isinstance(blk.word_count, int), (
        f"BUG: word_count is {type(blk.word_count).__name__}, expected int"
    )
    assert blk.word_count == 0


# ---- (e) word_count basic correctness ----


@pytest.mark.asyncio
async def test_ensure_block_word_count_simple():
    s = FakeSession()
    s._next_lookup_hint = ("space-W", "project")
    blk = await _ensure_block(s, "space-W", "project", "one two three four")
    assert blk.word_count == 4, f"BUG: word_count={blk.word_count}, expected 4"


# ---- (f) version monotonic across multiple changes ----


@pytest.mark.asyncio
async def test_ensure_block_version_monotonic():
    s = FakeSession()
    contents = ["a", "b", "c", "d", "e"]
    versions = []
    for c in contents:
        s._next_lookup_hint = ("space-M", "project")
        blk = await _ensure_block(s, "space-M", "project", c)
        versions.append(blk.block_version)

    # Every change → bump
    assert versions == [1, 2, 3, 4, 5], f"BUG: non-monotonic versions {versions}"


# ===========================================================================
# Integration tests (REAL PostgreSQL via psycopg)
# ===========================================================================

# Connect to real DB (settings.db_url). Skip module if connection unavailable
# so unit suite still runs in CI without DB.

_DB_URL = None
_DB_AVAILABLE = False
_PSYCOPG_ERR = None

try:
    # settings module lives in core/src — bootstrap added /Users/joneshong/workshop
    # to path is NOT done; so add it now (read-only).
    _CORE_SRC = "/Users/joneshong/workshop/core/src"
    if _CORE_SRC not in sys.path:
        sys.path.insert(0, _CORE_SRC)
    # .env loading: core/.env is loaded by config.py via pydantic-settings if
    # CWD is core/. We don't os.chdir to avoid disturbing pytest collection;
    # instead we rely on default fields. settings.db_url has a default literal.
    from src.config_stub import settings  # type: ignore

    _DB_URL = settings.db_url
    import psycopg  # type: ignore

    # Probe connection (fast)
    with psycopg.connect(_DB_URL, connect_timeout=3) as _c:
        with _c.cursor() as _cur:
            _cur.execute("SELECT 1")
            _cur.fetchone()
    _DB_AVAILABLE = True
except Exception as exc:  # pragma: no cover
    _PSYCOPG_ERR = exc


pytestmark_db = pytest.mark.skipif(
    not _DB_AVAILABLE, reason=f"Real DB unavailable: {_PSYCOPG_ERR!r}"
)


@pytest.fixture
def adversary_space():
    """Yields a unique test-only space_id; cleans up rows after the test."""
    if not _DB_AVAILABLE:
        pytest.skip("DB not available")
    space_id = f"space-adversary-{uuid.uuid4().hex[:8]}"
    yield space_id
    import psycopg  # type: ignore

    with psycopg.connect(_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM memvault.memory_block WHERE space_id = %s",
                (space_id,),
            )
        conn.commit()


def _gen_id() -> str:
    """Generate a 32-char ID compatible with SpaceScopedModel.id (String(32))."""
    return uuid.uuid4().hex


@pytestmark_db
def test_real_db_partial_unique_blocks_double_active(adversary_space):
    """BUG check: partial unique index must reject a second active row for same
    (space_id, block_type). If this passes silently, the partial index is wrong.
    """
    import psycopg  # type: ignore

    space_id = adversary_space

    with psycopg.connect(_DB_URL) as conn:
        with conn.cursor() as cur:
            # First active row — should succeed
            cur.execute(
                """
                INSERT INTO memvault.memory_block
                    (id, space_id, block_type, content, word_count, block_version,
                     created_by, deleted_at)
                VALUES (%s, %s, 'project', 'first', 1, 1, 'adversary-test', NULL)
                """,
                (_gen_id(), space_id),
            )
            conn.commit()

        # Second active row for same (space_id, block_type) — should FAIL
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO memvault.memory_block
                        (id, space_id, block_type, content, word_count,
                         block_version, created_by, deleted_at)
                    VALUES (%s, %s, 'project', 'second', 1, 1,
                            'adversary-test', NULL)
                    """,
                    (_gen_id(), space_id),
                )
                conn.commit()
                pytest.fail(
                    "BUG: partial unique index did NOT block second active row "
                    f"for (space_id={space_id!r}, block_type='project')"
                )
        except psycopg.errors.UniqueViolation:
            # Expected — partial unique index works
            conn.rollback()


@pytestmark_db
def test_real_db_soft_delete_unblocks_new_insert(adversary_space):
    """Partial index `WHERE deleted_at IS NULL` means: after soft-delete, a new
    active row should be insertable for the same (space_id, block_type)."""
    import psycopg  # type: ignore

    space_id = adversary_space

    with psycopg.connect(_DB_URL) as conn:
        with conn.cursor() as cur:
            id1 = _gen_id()
            cur.execute(
                """
                INSERT INTO memvault.memory_block
                    (id, space_id, block_type, content, word_count, block_version,
                     created_by, deleted_at)
                VALUES (%s, %s, 'project', 'first', 1, 1, 'adversary-test', NULL)
                """,
                (id1, space_id),
            )
            # Soft-delete it
            cur.execute(
                """
                UPDATE memvault.memory_block
                SET deleted_at = NOW()
                WHERE id = %s
                """,
                (id1,),
            )
            conn.commit()

        with conn.cursor() as cur:
            id2 = _gen_id()
            try:
                cur.execute(
                    """
                    INSERT INTO memvault.memory_block
                        (id, space_id, block_type, content, word_count,
                         block_version, created_by, deleted_at)
                    VALUES (%s, %s, 'project', 'second', 1, 1,
                            'adversary-test', NULL)
                    """,
                    (id2, space_id),
                )
                conn.commit()
            except psycopg.errors.UniqueViolation as exc:
                pytest.fail(
                    "BUG: soft-deleted row blocked new insert — partial index "
                    f"WHERE clause may be missing or wrong: {exc}"
                )

            # Verify exactly one active row exists now
            cur.execute(
                """
                SELECT COUNT(*) FROM memvault.memory_block
                WHERE space_id = %s AND block_type = 'project'
                  AND deleted_at IS NULL
                """,
                (space_id,),
            )
            (count,) = cur.fetchone()
            assert count == 1, (
                f"expected 1 active row after soft-delete + reinsert, got {count}"
            )
