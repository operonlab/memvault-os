"""Check 4: missing_entities — names that show up in ≥2 blocks but have no
EntityCanonical row.

Heuristic name extraction: pull capitalized multi-word phrases / CamelCase
identifiers from block.content. We do NOT call an LLM (cheap heuristic by
design — wiki-lint is supposed to be a fast pre-filter).
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..kg_models import EntityCanonical
from ..models import MemoryBlock

# Match either:
#   - Latin Capitalised Phrases (2-4 words, each word ≥3 chars): "Alice Cooper"
#   - Latin CamelCase identifiers (≥2 capitalised segments): "PostgreSQL", "AlpineLinux"
#   - CJK personal names: exactly 2 Han chars (most common; 3-char names handled by
#     a separate strict pattern that only matches when bounded by non-Han context).
# We intentionally do NOT match arbitrary runs of Han chars — bare regex
# `[一-鿿]{2,4}` greedily eats prose like 「李四約王」when the actual name
# is just 「李四」.
_NAME_PATTERN = re.compile(
    r"(?:"
    r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){1,3}\b"  # "Alice Cooper", up to 4 words
    r"|\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b"  # "PostgreSQL"
    r")"
)


# CJK candidates are extracted via a sliding 2-char window over Han runs (not
# regex). Lookbehind-anchored regex misses inner names because common particles
# (e.g. 的, 在, 了, 和) are all Han characters; any name preceded by another
# Han char (e.g. "今天和李四") would be dropped. The over-generation noise is
# filtered downstream by mention-count threshold (>= 2).
_HAN_RANGE = ("一", "鿿")


def _extract_cjk_pairs(text: str) -> list[str]:
    """Return all 2-char Han pairs as candidates (overlapping, sliding window)."""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for i in range(len(text) - 1):
        c1, c2 = text[i], text[i + 1]
        if _HAN_RANGE[0] <= c1 <= _HAN_RANGE[1] and _HAN_RANGE[0] <= c2 <= _HAN_RANGE[1]:
            pair = c1 + c2
            if pair in _CJK_STOPWORDS or pair in seen:
                continue
            seen.add(pair)
            out.append(pair)
    return out


# Common 2-char Han particles / temporals / verbs that should never count as names.
# Not exhaustive — false positives are filtered downstream by mention-count threshold.
_CJK_STOPWORDS: frozenset[str] = frozenset(
    {
        "今天",
        "明天",
        "昨天",
        "前天",
        "後天",
        "週一",
        "週二",
        "週三",
        "週四",
        "週五",
        "週六",
        "週日",
        "週末",
        "禮拜",
        "星期",
        "上午",
        "下午",
        "中午",
        "凌晨",
        "今年",
        "明年",
        "去年",
        "今晚",
        "今早",
        "我們",
        "他們",
        "你們",
        "她們",
        "它們",
        "因為",
        "所以",
        "但是",
        "如果",
        "雖然",
        "然後",
        "或者",
        "討論",
        "設計",
        "完成",
        "開始",
        "結束",
        "交付",
        "處理",
    }
)

# Words that are syntactically capitalised (sentence start, days, months, etc.)
# but should NEVER count as standalone proper names. These are stripped from the
# *front* of an extracted phrase so "Yesterday Alice Cooper" → "Alice Cooper".
_STOPWORDS = {
    "The",
    "This",
    "That",
    "These",
    "Those",
    "When",
    "Where",
    "What",
    "Why",
    "How",
    "Yesterday",
    "Today",
    "Tomorrow",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
}


def _extract_names(text: str) -> list[str]:
    if not text:
        return []
    out = []
    seen: set[str] = set()
    for match in _NAME_PATTERN.finditer(text):
        name = match.group(0).strip()
        # Strip leading stopwords (e.g. "Yesterday Alice Cooper" → "Alice Cooper").
        # Loop because more than one stopword can stack in front.
        if " " in name:
            words = name.split()
            while words and words[0] in _STOPWORDS:
                words.pop(0)
            # Require ≥2 words after stripping — single-word leftovers like just
            # "Alice" are too noisy without surname context.
            name = " ".join(words) if len(words) >= 2 else ""
            if not name:
                continue
        if not name or name in _STOPWORDS:
            continue
        # Skip very long matches — likely not names
        if len(name) > 80:
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    # Append CJK 2-char candidates (sliding window). These are intentionally
    # over-generated; downstream min_block_mentions=2 filters out the prose
    # bigrams (「在週」「四討」) while real names that recur across blocks
    # (「李四」、「王五」) cross the threshold.
    for cjk in _extract_cjk_pairs(text):
        if cjk in seen:
            continue
        seen.add(cjk)
        out.append(cjk)
    return out


async def check_missing_entities(
    db: AsyncSession,
    space_id: str,
    *,
    min_block_mentions: int = 2,
    sample_blocks: int = 500,
) -> list:
    from ..lint import LintFinding

    bq = (
        select(MemoryBlock.id, MemoryBlock.content)
        .where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.deleted_at.is_(None),
            MemoryBlock.invalid_at.is_(None),
        )
        .order_by(MemoryBlock.created_at.desc())
        .limit(sample_blocks)
    )
    blocks = (await db.execute(bq)).all()

    name_to_blocks: dict[str, set[str]] = {}
    # Preserve a representative original-case form for each lowercased key so
    # findings can surface the human-readable name (e.g. "Alice Cooper" /
    # "李四") in metadata. Without this, downstream consumers only see the
    # lower-cased Latin form and CJK names round-trip unchanged but are
    # opaque in logs.
    name_original: dict[str, str] = {}
    for bid, content in blocks:
        for name in _extract_names(content or ""):
            key = name.lower()
            name_to_blocks.setdefault(key, set()).add(bid)
            name_original.setdefault(key, name)

    # Existing canonical names (single column → use .scalars().all() so we never
    # fall over a 1-tuple/2-tuple unpack mismatch). Aliases are loaded in a
    # separate best-effort query below; missing aliases must never crash lint.
    eq = select(EntityCanonical.canonical_name).where(
        EntityCanonical.space_id == space_id,
        EntityCanonical.deleted_at.is_(None),
    )
    known: set[str] = set()
    for cname in (await db.execute(eq)).scalars().all():
        if cname:
            known.add(cname.lower())

    aq = select(EntityCanonical.aliases).where(
        EntityCanonical.space_id == space_id,
        EntityCanonical.deleted_at.is_(None),
    )
    try:
        for aliases in (await db.execute(aq)).scalars().all():
            for a in aliases or []:
                if a:
                    known.add(a.lower())
    except Exception:
        # Best-effort enrichment; never crash the lint check on aliases.
        ...

    findings: list = []
    for name_lc, block_ids in name_to_blocks.items():
        if len(block_ids) < min_block_mentions:
            continue
        if name_lc in known:
            continue
        # Sample up to 5 block IDs for the metadata
        sample_ids = sorted(block_ids)[:5]
        original = name_original.get(name_lc, name_lc)
        findings.append(
            LintFinding(
                check="missing_entities",
                severity="info",
                entity_id="",
                entity_type="entity",
                message=(
                    f"Name '{original}' appears in {len(block_ids)} blocks "
                    f"but has no EntityCanonical row"
                ),
                suggested_action=(
                    "Promote this name to an EntityCanonical row, or add it as "
                    "an alias to an existing entity."
                ),
                metadata={
                    "name": original,
                    "name_lower": name_lc,
                    "mention_count": len(block_ids),
                    "sample_block_ids": sample_ids,
                },
            )
        )

    return findings
