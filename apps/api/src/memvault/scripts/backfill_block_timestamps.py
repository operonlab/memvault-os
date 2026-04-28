#!/usr/bin/env python3
"""Backfill block created_at from actual session timestamps.

Strategy:
  1. Blocks with source_session (UUID) → read transcript JSONL first timestamp
  2. No transcript found → use git log closest commit as approximation
  3. No source_session → skip (real-time MCP calls, timestamp is correct)

Usage:
  python3 core/src/modules/memvault/scripts/backfill_block_timestamps.py [--dry-run] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

# DB connection via psql in Docker
DOCKER_CMD = [
    "docker",
    "exec",
    "ws-infra-postgres-1",
    "psql",
    "-U",
    "joneshong",
    "-d",
    "workshop",
    "-t",
    "-A",
]
PROJECTS_DIR = Path.home() / ".claude" / "projects"


def psql(sql: str) -> str:
    result = subprocess.run([*DOCKER_CMD, "-c", sql], capture_output=True, text=True)
    return result.stdout.strip()


def psql_exec(sql: str) -> None:
    subprocess.run([*DOCKER_CMD, "-c", sql], capture_output=True, text=True)


def build_transcript_map() -> dict[str, Path]:
    """Map session_id → transcript JSONL path."""
    m: dict[str, Path] = {}
    for jsonl in PROJECTS_DIR.rglob("*.jsonl"):
        sid = jsonl.stem
        if len(sid) == 36 and sid[8] == "-":
            m[sid] = jsonl
    return m


def get_first_timestamp(transcript: Path) -> str | None:
    """Read first timestamp from JSONL transcript."""
    try:
        with open(transcript, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("timestamp")
                if ts:
                    return ts
    except Exception:
        pass
    return None


def get_git_timestamp(session_id: str, current_created_at: str) -> str | None:
    """Use git log to find the closest commit around the block's current created_at.

    For blocks without transcripts (archived sessions), approximate using
    git commit timestamps around the same period.
    """
    # Parse the current created_at to get a date range
    try:
        dt = datetime.fromisoformat(current_created_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None

    # Search git log around that date (±7 days) for session-related commits
    since = (dt - __import__("datetime").timedelta(days=7)).strftime("%Y-%m-%d")
    until = (dt + __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        result = subprocess.run(
            ["git", "log", "--format=%aI", "--since", since, "--until", until, "--reverse"],
            capture_output=True,
            text=True,
            cwd=str(Path.home() / "workshop"),
            timeout=5,
        )
        commits = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        if commits:
            # Use the first commit in the range as a reasonable approximation
            return commits[0]
    except Exception:
        pass
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill block created_at from session timestamps"
    )
    parser.add_argument("--dry-run", action="store_true", help="Don't write, just report")
    parser.add_argument("--limit", type=int, default=0, help="Limit blocks to process (0=all)")
    args = parser.parse_args()

    print("Building transcript map...")
    transcript_map = build_transcript_map()
    print(f"  Found {len(transcript_map)} transcript files")

    # Get all blocks with source_session (UUID format only)
    print("Querying blocks with source_session...")
    limit_clause = f" LIMIT {args.limit}" if args.limit else ""
    rows = psql(f"""
        SELECT id, source_session, created_at::text
        FROM memvault.blocks
        WHERE source_session IS NOT NULL
          AND deleted_at IS NULL
          AND source_session ~ '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-'
        ORDER BY created_at DESC
        {limit_clause}
    """)

    if not rows:
        print("No blocks to backfill.")
        return

    blocks = []
    for line in rows.split("\n"):
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) == 3:
            blocks.append({"id": parts[0], "session": parts[1], "created_at": parts[2]})

    print(f"  Found {len(blocks)} blocks to check")

    # Group by session for efficiency
    from collections import defaultdict

    by_session: dict[str, list[dict]] = defaultdict(list)
    for b in blocks:
        by_session[b["session"]].append(b)

    print(f"  Across {len(by_session)} unique sessions")

    stats = {"transcript": 0, "git": 0, "skip": 0, "already_correct": 0, "error": 0}
    updates: list[tuple[str, str]] = []  # (block_id, new_timestamp)

    for session_id, session_blocks in by_session.items():
        # Try transcript first
        new_ts = None
        source = None

        transcript = transcript_map.get(session_id)
        if transcript:
            new_ts = get_first_timestamp(transcript)
            source = "transcript"

        # Fallback: git log
        if not new_ts:
            new_ts = get_git_timestamp(session_id, session_blocks[0]["created_at"])
            source = "git" if new_ts else None

        if not new_ts:
            stats["skip"] += len(session_blocks)
            continue

        # Normalize timestamp
        try:
            if "T" in new_ts:
                parsed = datetime.fromisoformat(new_ts.replace("Z", "+00:00"))
            else:
                parsed = datetime.fromisoformat(new_ts)
            normalized = parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError):
            stats["error"] += len(session_blocks)
            continue

        for b in session_blocks:
            # Check if timestamp is already close (within 60 seconds)
            try:
                existing = datetime.fromisoformat(
                    b["created_at"].replace("Z", "+00:00").replace("+00", "+00:00")
                )
                diff = abs((parsed - existing).total_seconds())
                if diff < 60:
                    stats["already_correct"] += 1
                    continue
            except (ValueError, TypeError):
                pass

            updates.append((b["id"], normalized))
            stats[source] += 1

    print("\nResults:")
    print(f"  From transcript: {stats['transcript']}")
    print(f"  From git log:    {stats['git']}")
    print(f"  Already correct: {stats['already_correct']}")
    print(f"  Skipped (no source): {stats['skip']}")
    print(f"  Errors:          {stats['error']}")
    print(f"  Total updates:   {len(updates)}")

    if args.dry_run:
        print("\n[DRY RUN] No changes written.")
        for block_id, ts in updates[:10]:
            print(f"  Would update {block_id} → {ts}")
        if len(updates) > 10:
            print(f"  ... and {len(updates) - 10} more")
        return

    if not updates:
        print("\nNothing to update.")
        return

    # Batch UPDATE via psql
    print(f"\nApplying {len(updates)} updates...")
    batch_size = 100
    applied = 0
    for i in range(0, len(updates), batch_size):
        batch = updates[i : i + batch_size]
        cases = " ".join(f"WHEN '{bid}' THEN '{ts}'::timestamptz" for bid, ts in batch)
        ids = ",".join(f"'{bid}'" for bid, _ in batch)
        sql = f"""
            UPDATE memvault.blocks
            SET created_at = CASE id {cases} END
            WHERE id IN ({ids});
        """
        psql_exec(sql)
        applied += len(batch)
        if applied % 500 == 0 or applied == len(updates):
            print(f"  {applied}/{len(updates)} applied")

    print(f"\nDone. {applied} blocks updated.")


if __name__ == "__main__":
    main()
