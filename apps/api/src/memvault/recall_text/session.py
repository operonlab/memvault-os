"""recall_session.py — Extract recent session context from Claude Code transcript JSONL.

Called by the Memvault recall pipeline to inject近期對話 context into recall output.
Reads the last ~64KB of the current session's JSONL transcript, extracts user messages
and tool activity, and formats a concise markdown section for injection.

Public API:
    extract_session_context(session_id, cwd, current_prompt) -> str
"""

import glob
import json
import os
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_HOME = str(Path.home())
_PROJECTS_BASE = os.path.join(_HOME, ".claude", "projects")
_READ_SIZE = 65536  # 64 KB tail read
_TIMEOUT_S = 0.4  # bail if we exceed this
_MAX_USER_MSGS = 3
_MAX_FILE_PATHS = 10  # deduplicated; show up to 5
_MAX_SHOW_PATHS = 5
_MAX_SHOW_TOOLS = 4
_MSG_TRUNCATE = 100
_MIN_FILE_BYTES = 100


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _tail_read_lines(filepath: str, read_size: int = _READ_SIZE) -> list[str]:
    """Seek to end-read_size, decode, split lines, discard possibly-truncated first."""
    try:
        size = os.path.getsize(filepath)
        if size == 0:
            return []
        with open(filepath, "rb") as fh:
            fh.seek(max(0, size - read_size))
            raw = fh.read()
        text = raw.decode("utf-8", errors="replace")
        lines = text.split("\n")
        # Discard first line — may be cut in the middle by the seek
        if size > read_size:
            lines = lines[1:]
        return lines
    except Exception:
        return []


def _extract_user_text(entry: dict) -> str:
    """Return user text from a transcript entry, or '' if not applicable."""
    try:
        if entry.get("type") != "user":
            return ""
        msg = entry.get("message", {})
        if msg.get("role") != "user":
            return ""
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
        return ""
    except Exception:
        return ""


def _find_transcript(session_id: str, cwd: str) -> str:
    """Return the path to the session JSONL file, or '' if not found."""
    try:
        project_hash = cwd.replace("/", "-")
        candidate = os.path.join(_PROJECTS_BASE, project_hash, f"{session_id}.jsonl")
        if os.path.isfile(candidate):
            return candidate
        # Glob fallback — session may live under a different project hash
        pattern = os.path.join(_PROJECTS_BASE, "*", f"{session_id}.jsonl")
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    except Exception:
        pass
    return ""


def _shorten_path(p: str) -> str:
    """Replace home directory prefix with ~."""
    if p.startswith(_HOME):
        return "~" + p[len(_HOME) :]
    return p


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_session_context(session_id: str, cwd: str, current_prompt: str) -> str:
    """Extract recent session context for Memvault recall injection.

    Returns a markdown section (### 近期對話 ...) or '' if nothing useful found
    or if any error occurs. Always completes within ~500ms.
    """
    try:
        start = time.monotonic()

        transcript_path = _find_transcript(session_id, cwd)
        if not transcript_path:
            return ""

        # Skip tiny files (< 100 bytes) — not real transcripts
        try:
            if os.path.getsize(transcript_path) < _MIN_FILE_BYTES:
                return ""
        except OSError:
            return ""

        lines = _tail_read_lines(transcript_path)
        if not lines:
            return ""

        # Timeout guard after file read
        if time.monotonic() - start > _TIMEOUT_S:
            return ""

        # Iterate from END to START
        user_messages: list[str] = []  # collected newest-first, reversed before output
        file_paths: list[str] = []  # ordered by discovery (newest-first)
        seen_paths: set[str] = set()
        tool_counts: dict[str, int] = {}

        prompt_stripped = (current_prompt or "").strip()

        for raw_line in reversed(lines):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                entry = json.loads(raw_line)
            except Exception:
                continue

            entry_type = entry.get("type", "")

            # ---- User messages ------------------------------------------------
            if entry_type == "user":
                if len(user_messages) < _MAX_USER_MSGS:
                    text = _extract_user_text(entry)
                    if not text:
                        continue
                    text_stripped = text.strip()
                    # Skip current prompt duplicate
                    if prompt_stripped and text_stripped == prompt_stripped:
                        continue
                    # Skip interrupt markers and XML-like prefixes
                    if text_stripped.startswith("[Request interrupted") or text_stripped.startswith(
                        "<"
                    ):
                        continue
                    truncated = text_stripped[:_MSG_TRUNCATE]
                    user_messages.append(truncated)

            # ---- Assistant tool_use blocks ------------------------------------
            elif entry_type == "assistant":
                try:
                    msg = entry.get("message", {})
                    content_blocks = msg.get("content", [])
                    if not isinstance(content_blocks, list):
                        continue
                    for block in content_blocks:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") != "tool_use":
                            continue
                        name = block.get("name", "")
                        # Tally tool usage
                        tool_counts[name] = tool_counts.get(name, 0) + 1
                        # Collect file paths from Read/Edit/Write
                        if name in ("Read", "Edit", "Write"):
                            fp = block.get("input", {}).get("file_path", "")
                            if fp and fp not in seen_paths and len(file_paths) < _MAX_FILE_PATHS:
                                seen_paths.add(fp)
                                file_paths.append(fp)
                except Exception:
                    continue

        # Timeout guard after parse
        if time.monotonic() - start > _TIMEOUT_S:
            return ""

        # Nothing collected → return empty
        if not user_messages and not file_paths:
            return ""

        # Build output section
        lines_out: list[str] = ["### 近期對話"]

        # User messages in chronological order (oldest first)
        for msg in reversed(user_messages):
            lines_out.append(f"- 使用者: 「{msg}」")

        # File paths (newest first from our iteration; show up to 5, shortened)
        if file_paths:
            shown = [_shorten_path(p) for p in file_paths[:_MAX_SHOW_PATHS]]
            lines_out.append(f"- 最近編輯: {', '.join(shown)}")

        # Tool activity summary
        if tool_counts:
            total = sum(tool_counts.values())
            top = sorted(tool_counts.items(), key=lambda x: -x[1])[:_MAX_SHOW_TOOLS]
            detail = ", ".join(f"{n} ×{c}" for n, c in top)
            lines_out.append(f"- 工具活動: {total} 次 ({detail})")

        return "\n".join(lines_out)

    except Exception:
        return ""
