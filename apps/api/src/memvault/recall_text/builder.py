"""recall_text.builder — Server-side port of mcp/memvault/scripts/recall.py.

Exposes a single function `build_recall_text(prompt, session_id, cwd)` that
returns the same markdown text that recall.py used to print to stdout. The
original script was spawned by the UserPromptSubmit hook on every prompt,
eating ~1s of Python startup each time. Hosting the logic inside core
eliminates the fork; the Go hook now calls POST /api/memvault/recall/text.

Parity notes:
    - Logic is a 1:1 port; HTTP fan-out (urllib) is preserved so the caller
      graph and retry semantics are identical.
    - Cache/session/dedup helpers live under the same package (see
      cache.py / dedup.py / session.py).
    - Environment-variable toggles (MEMVAULT_RECALL_* / MEMVAULT_SKIP_RECALL /
      MEMVAULT_SPACE_ID) remain honoured.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from . import cache as _cache_mod
from .dedup import dedup_cascade as _dedup_fn
from .session import extract_session_context as _session_fn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CORE_API_URL = os.environ.get("CORE_API_URL", "http://localhost:10000")
HOME_DIR = str(Path.home())
WORKSHOP_DIR = os.path.join(HOME_DIR, "workshop")

# File-path reference detector (same as original recall.py).
PATH_PATTERN = re.compile(
    r'(?<![`"\'])'  # not preceded by backtick / quote
    r"(?:"
    r"(?:~/|/Users/\w+/)"  # absolute home paths
    r"|(?:core/src/|stations/|libs/|workbench/src/"
    r"|mcp/|schedules/|bridges/|scripts/)"  # relative project paths
    r")"
    r"[\w/.\-]+"  # path continuation (no spaces)
    r'(?!["\'])'  # not followed by quote
)

SPACE_ID = os.environ.get("MEMVAULT_SPACE_ID", "default")
LOG_DIR = Path.home() / "Claude" / "memvault" / "logs"
LOG_FILE = LOG_DIR / "recall.log"
MAX_PROMPT_LEN = 2000
MAX_OUTPUT_CHARS = 4000
CURL_TIMEOUT = 10

LOG_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log(msg: str) -> None:
    """Write timestamped log to ~/Claude/memvault/logs/recall.log (silent)."""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[recall] {ts} {msg}"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _http_get(url: str, timeout: int = 10) -> tuple[int, str]:
    """GET url, return (status, body). (0, '') on error."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body
    except Exception:
        return 0, ""


def _should_inject_attitudes(prompt: str) -> bool:
    if len(prompt) < 10:
        return False
    if prompt.startswith("/"):
        return False
    if prompt.startswith("```"):
        return False
    return True


def _format_attitudes(raw_body: str) -> str:
    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, list) or not data:
        return ""

    lines = ["\n\n### 行為提醒"]
    for item in data:
        fact = item.get("fact", "")
        category = item.get("category", "")
        confidence = item.get("confidence", 0)
        if fact:
            lines.append(f"- [{category}] {fact} ({confidence:.2f})")
    return "\n".join(lines) if len(lines) > 1 else ""


def _validate_refs(text: str) -> set[str]:
    refs = PATH_PATTERN.findall(text)
    stale: set[str] = set()
    for ref in refs:
        if ref.startswith("~/"):
            full = os.path.join(HOME_DIR, ref[2:])
        elif ref.startswith("/"):
            full = ref
        else:
            full = os.path.join(WORKSHOP_DIR, ref)
        if not os.path.exists(full):
            stale.add(ref)
    return stale


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_recall_text(
    prompt: str,
    session_id: str | None = None,
    cwd: str | None = None,
) -> str:
    """Return the full recall text (may be empty). Never raises."""
    try:
        return _build(prompt or "", session_id or "", cwd or "")
    except Exception as e:
        _log(f"Unexpected error: {e}")
        return ""


def _build(prompt: str, session_id: str, cwd: str) -> str:
    prompt = (prompt or "").strip()
    session_id = (session_id or "").strip()
    cwd = (cwd or "").strip()

    if not prompt:
        _log("No prompt, skipping")
        return ""

    # ── Skip conditions ──────────────────────────────────────────────────
    if os.environ.get("MEMVAULT_SKIP_RECALL") == "1":
        _log("Skipping — MEMVAULT_SKIP_RECALL=1")
        return ""

    if prompt.startswith("<"):
        _log("Skipping system message")
        return ""

    if len(prompt) > MAX_PROMPT_LEN:
        _log(f"Skipping long prompt ({len(prompt)} chars)")
        return ""

    _log(f"Session: {session_id or 'unknown'} | Prompt: {prompt[:80]}")

    # ── Session Context Window ──────────────────────────────────────────
    session_context = ""
    if session_id and os.environ.get("MEMVAULT_RECALL_SESSION") != "0":
        try:
            session_context = _session_fn(session_id, cwd, prompt) or ""
            if session_context:
                _log(f"Session context: {len(session_context)} chars")
        except Exception:
            pass

    encoded_q = urllib.parse.quote(prompt)
    cascade_enabled = os.environ.get("MEMVAULT_RECALL_DEDUP") != "0"
    cache_enabled = os.environ.get("MEMVAULT_RECALL_CACHE") != "0"

    # ── Primary: Cascade Recall ──────────────────────────────────────────
    cascade_url = f"{CORE_API_URL}/api/memvault/kg/recall?q={encoded_q}&top_k=5&space_id={SPACE_ID}"
    _, cascade_body = _http_get(cascade_url, timeout=CURL_TIMEOUT)

    cache_stale = False
    formatted = ""
    normalized_q = ""
    q_hash = ""

    if cache_enabled:
        normalized_q = _cache_mod.normalize_query(prompt)
        q_hash = _cache_mod.query_hash(normalized_q)

    if cascade_body:
        try:
            cascade_data = json.loads(cascade_body)
        except json.JSONDecodeError:
            cascade_data = {}
        if cache_enabled and cascade_data.get("layers_searched"):
            _cache_mod.write_cache(
                _cache_mod.CASCADE_CACHE_DIR,
                q_hash,
                prompt,
                normalized_q,
                cascade_data,
                _cache_mod.CASCADE_TTL,
            )
    else:
        cascade_data = {}
        if cache_enabled:
            _log("API failed, trying cache fallback")
            cached, is_stale = _cache_mod.read_cache(
                _cache_mod.CASCADE_CACHE_DIR,
                q_hash,
                _cache_mod.CASCADE_TTL,
                _cache_mod.CASCADE_STALE_TTL,
            )
            if cached is None:
                cached, is_stale = _cache_mod.read_latest(
                    _cache_mod.CASCADE_CACHE_DIR,
                    _cache_mod.CASCADE_STALE_TTL,
                )
            if cached:
                cascade_data = cached
                cache_stale = is_stale
                _log(f"Cache {'stale ' if is_stale else ''}hit")

    # ── Dedup ────────────────────────────────────────────────────────────
    if cascade_enabled and cascade_data.get("layers_searched"):
        try:
            cascade_data = _dedup_fn(cascade_data)
            _log("Dedup applied")
        except Exception:
            pass

    if cascade_data and "layers_searched" in cascade_data:
        layers_list = cascade_data.get("layers_searched", [])
        layers = ", ".join(layers_list) if layers_list else ""

        if layers:
            formatted = f"## 相關記憶（cascade recall: {layers}）"

            # L2 summaries
            summaries = cascade_data.get("summaries", [])
            if summaries:
                formatted += "\n\n### 智慧節點"
                for s in summaries:
                    summary_text = s.get("summary", "")
                    key_findings = s.get("key_findings", [])
                    c_name = s.get("_community_name")
                    c_size = s.get("_community_size", 0)
                    if summary_text:
                        if c_name:
                            formatted += f"\n- **{c_name}** (size: {c_size}): {summary_text}"
                        else:
                            formatted += f"\n- {summary_text}"
                        if key_findings:
                            for kf in key_findings:
                                formatted += f"\n  - {kf}"

            # L1 communities
            communities = cascade_data.get("communities", [])
            if communities:
                formatted += "\n\n### 知識社群"
                for c in communities:
                    name = c.get("name", "")
                    size = c.get("size", 0)
                    summary = c.get("summary") or "—"
                    if name:
                        formatted += f"\n- **{name}** (size: {size}): {summary}"

            # L0 triples
            triples = cascade_data.get("triples", [])
            stale_refs: set[str] = set()
            if triples:
                formatted += "\n\n### Triples"
                for t in triples:
                    subj = t.get("subject", "")
                    pred = t.get("predicate", "")
                    obj = t.get("object", "")
                    if subj:
                        triple_text = f"{subj} --{pred}--> {obj}"
                        triple_stale = _validate_refs(triple_text)
                        stale_refs.update(triple_stale)
                        stale_suffix = " [stale ref]" if triple_stale else ""
                        formatted += f"\n- {triple_text}{stale_suffix}"

            # Blocks
            blocks = cascade_data.get("blocks", [])
            if blocks:
                formatted += "\n\n### Memory Blocks"
                for b in blocks:
                    topic = b.get("topic") or "untitled"
                    content = (b.get("content") or "—")[:200]
                    tags = b.get("tags", [])
                    tag_str = f" (tags: {', '.join(tags)})" if tags else ""
                    block_text = f"{topic}: {content}{tag_str}"
                    block_stale = _validate_refs(block_text)
                    stale_refs.update(block_stale)
                    stale_suffix = " ⚠️ stale" if block_stale else ""
                    formatted += f"\n- **{topic}**: {content}{tag_str}{stale_suffix}"

            for ref in stale_refs:
                _log(f"Stale ref: {ref}")

            summary_count = len(summaries)
            community_count = len(communities)
            triple_count = len(triples)
            block_count = len(blocks)
            _log(
                f"Cascade recall: {layers} "
                f"({summary_count} summaries, {community_count} communities, "
                f"{triple_count} triples, {block_count} blocks)"
            )

            # Skill profile injection
            skill_tags: set[str] = set()
            for t in triples:
                for tag in t.get("tags", []):
                    if tag.startswith("skill:"):
                        skill_tags.add(tag[6:])
            for t in triples:
                pred = t.get("predicate", "").lower()
                if "skill" in pred or "使用" in pred:
                    skill_tags.add(t.get("subject", ""))

            if skill_tags:
                skill_section = ""
                for skill_name in list(skill_tags)[:3]:
                    encoded_skill = urllib.parse.quote(skill_name)
                    sp_url = (
                        f"{CORE_API_URL}/api/memvault/kg/skill-profiles/{encoded_skill}"
                        f"?space_id={SPACE_ID}"
                    )
                    sp_status, sp_body = _http_get(sp_url, timeout=3)
                    if sp_status == 200 and sp_body:
                        try:
                            profile = json.loads(sp_body)
                            level_zh = {
                                "novice": "新手",
                                "proficient": "熟練",
                                "expert": "專家",
                            }
                            level = level_zh.get(
                                profile.get("proficiency_level", ""),
                                profile.get("proficiency_level", ""),
                            )
                            sr_pct = profile.get("success_rate", 0) * 100
                            skill_section += (
                                f"\n- **{skill_name}**: {profile.get('total_uses', 0)} 次使用, "
                                f"{sr_pct:.0f}% 成功率 ({level})"
                            )
                        except (json.JSONDecodeError, KeyError):
                            pass
                if skill_section:
                    formatted += f"\n\n### Skill 熟練度{skill_section}"
                    _log(f"Skill profile injected: {list(skill_tags)[:3]}")

            if stale_refs:
                formatted += f"\n\n⚠️ {len(stale_refs)} 個記憶參照的檔案已不存在，建議驗證後再行動"

    # ── Fallback: simple search ──────────────────────────────────────────
    if not formatted:
        search_url = f"{CORE_API_URL}/api/memvault/search?q={encoded_q}&top_k=5&space_id={SPACE_ID}"
        _, search_body = _http_get(search_url, timeout=CURL_TIMEOUT)

        if search_body:
            try:
                search_data = json.loads(search_body)
            except json.JSONDecodeError:
                search_data = []

            if isinstance(search_data, list) and search_data:
                result_count = len(search_data)
                formatted = f"## 相關記憶（search: {result_count} results）"
                search_stale_refs: set[str] = set()
                for item in search_data:
                    block = item.get("block", {}) if isinstance(item, dict) else {}
                    topic = block.get("topic") or "untitled"
                    content = (block.get("content") or "—")[:200]
                    tags = block.get("tags", [])
                    tag_str = f" (tags: {', '.join(tags)})" if tags else ""
                    block_text = f"{topic}: {content}{tag_str}"
                    block_stale = _validate_refs(block_text)
                    search_stale_refs.update(block_stale)
                    stale_suffix = " ⚠️ stale" if block_stale else ""
                    formatted += f"\n- **{topic}**: {content}{tag_str}{stale_suffix}"
                for ref in search_stale_refs:
                    _log(f"Stale ref: {ref}")
                if search_stale_refs:
                    formatted += (
                        f"\n\n⚠️ {len(search_stale_refs)} 個記憶參照的檔案已不存在，建議驗證後再行動"
                    )
                _log(f"Search fallback: {result_count} results")

    # ── Attitude autoRecall ──────────────────────────────────────────────
    if _should_inject_attitudes(prompt):
        att_url = (
            f"{CORE_API_URL}/api/memvault/kg/attitudes/relevant"
            f"?q={encoded_q}&top_k=3&space_id={SPACE_ID}"
        )
        _, att_body = _http_get(att_url, timeout=3)
        att_from_cache = False
        if not att_body and cache_enabled:
            att_hash = _cache_mod.query_hash(_cache_mod.normalize_query(prompt))
            cached_att, _ = _cache_mod.read_cache(
                _cache_mod.ATTITUDE_CACHE_DIR,
                att_hash,
                _cache_mod.ATTITUDE_TTL,
                _cache_mod.ATTITUDE_STALE_TTL,
            )
            if cached_att:
                att_body = json.dumps(cached_att)
                att_from_cache = True
        if att_body:
            att_section = _format_attitudes(att_body)
            if att_section:
                formatted += att_section
                _log("Attitude autoRecall injected" + (" (cache)" if att_from_cache else ""))
            if cache_enabled and not att_from_cache:
                try:
                    att_data = json.loads(att_body)
                    if isinstance(att_data, list) and att_data:
                        att_hash = _cache_mod.query_hash(_cache_mod.normalize_query(prompt))
                        _cache_mod.write_cache(
                            _cache_mod.ATTITUDE_CACHE_DIR,
                            att_hash,
                            prompt,
                            _cache_mod.normalize_query(prompt),
                            att_data,
                            _cache_mod.ATTITUDE_TTL,
                        )
                except Exception:
                    pass

    # ── No results ───────────────────────────────────────────────────────
    if not formatted and not session_context:
        _log("No results from API")
        return ""

    # ── Assemble output ──────────────────────────────────────────────────
    output_parts: list[str] = []
    if session_context:
        output_parts.append(session_context)
    if formatted:
        output_parts.append(formatted)
    if cache_stale:
        output_parts.append("\n> ⚠️ 此記憶來自快取（可能已過時），Core API 暫時無法連線")

    full_output = "\n\n".join(output_parts)

    if len(full_output) > MAX_OUTPUT_CHARS:
        full_output = full_output[:MAX_OUTPUT_CHARS]
        _log(f"Output truncated to {MAX_OUTPUT_CHARS} chars")

    # ── Skill suggestion (appended to output) ────────────────────────────
    triggers_file = Path.home() / ".claude" / "data" / "skill-index" / "triggers.json"
    if triggers_file.is_file():
        try:
            triggers_data = json.loads(triggers_file.read_text(encoding="utf-8"))
            prompt_lower = prompt.lower()
            matches = [
                s["name"]
                for s in triggers_data
                if any(t.lower() in prompt_lower for t in s.get("triggers", []))
            ]
            if matches:
                skill_list = ", ".join(matches[:3])
                full_output += "\n\n" + f"建議使用的 Skills: {skill_list}"
                _log(f"Skill suggestions: {skill_list}")
        except Exception:
            pass

    _log("Done")
    return full_output
