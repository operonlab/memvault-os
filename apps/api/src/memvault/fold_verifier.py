"""Verifier-Backed Extractive Fold + Dual-Key Idempotency (Worker 2).

Cannibalize Phase 1 — closes the real idempotency gap in Dream Loop's Phase 3
consolidate stage: dual-gate dedup is *coarse* (content-similarity at write time),
this module adds **dual-key fold identity** + **post-hoc extractive verifier**.

Design:

* ``fold_id``       = sha256(sorted(children_block_ids))[:16]
                       — stable across re-runs over the same child-set
* ``content_hash``  = sha256(consolidate_output_text)[:16]
                       — detects child-content drift

Idempotency rules (consumer enforces, this module computes):

1. fold_id same + content_hash same → skip (no row written)
2. fold_id same + content_hash diff → overwrite the existing fold (child drift)
3. fold_id new                       → insert new fold

Verifier (post-hoc, replaces prompt-only "extractive" promises):

* Each sentence in fold output must be grounded in some child by either
  (a) substring match (case-insensitive, length-aware) or
  (b) embedding cosine ≥ threshold (default 0.85).
* Sentences failing both checks are dropped + logged in ``rejected``.
* Embedding goes through the existing ``src.shared.embedding.get_embedding``
  shim — same path used by memvault elsewhere.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Sentence splitter — bilingual (CJK full stop + Western). CJK punctuation
# is built from unicode escapes so ruff RUF001/003 stays quiet.
# U+3002 IDEOGRAPHIC FULL STOP, U+FF01 FULLWIDTH EXCLAM, U+FF1F FULLWIDTH QUESTION
_CJK_TERMINATORS = "\u3002\uff01\uff1f"
_SENT_SPLIT_RE = re.compile(
    rf"(?<=[{_CJK_TERMINATORS}\.!\?])\s+|(?<=[{_CJK_TERMINATORS}])(?=[^\s])"
)

# Substring fuzz: short sentences need a stricter floor to avoid trivial matches
_SHORT_SENT_LEN = 8
_DEFAULT_EMBEDDING_THRESHOLD = 0.85

# Common abbreviations that look like sentence terminators but aren't.
# When split_sentences splits on ". " after one of these, glue the
# fragments back together so "Mr. Smith said hi." stays one sentence.
_ABBREVIATIONS = frozenset(
    {
        "Mr.",
        "Mrs.",
        "Ms.",
        "Dr.",
        "Prof.",
        "Sr.",
        "Jr.",
        "St.",
        "Mt.",
        "Inc.",
        "Ltd.",
        "Co.",
        "Corp.",
        "vs.",
        "etc.",
        "e.g.",
        "i.e.",
        "Ph.D.",
        "M.D.",
        "B.A.",
        "U.S.",
    }
)


# ============================================================================
# Hash helpers (dual-key)
# ============================================================================


def compute_fold_id(children_block_ids: list[str]) -> str:
    """Stable across child-set permutations; collisions practically impossible at 16 hex.

    Sorting normalizes ordering. Empty list yields a deterministic but distinct id —
    callers should treat empty fold as "do nothing" rather than relying on this.
    """
    if not children_block_ids:
        # Distinct sentinel so empty-input hashes never collide with content-bearing folds.
        return hashlib.sha256(b"__empty__").hexdigest()[:16]
    payload = ",".join(sorted(str(cid) for cid in children_block_ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compute_content_hash(consolidate_output_text: str) -> str:
    """Hash normalized output. Whitespace-collapsed so trivial reformatting is idempotent."""
    normalized = " ".join((consolidate_output_text or "").split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


# ============================================================================
# Verifier
# ============================================================================


@dataclass
class VerifierResult:
    filtered_text: str
    accepted: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    # Per-sentence reason: "substring" | "embedding" | "rejected"
    reasons: dict[str, str] = field(default_factory=dict)


def split_sentences(text: str) -> list[str]:
    """Best-effort bilingual sentence split. Empty input → []. Strips whitespace.

    Post-split merge: if a fragment ends in a known abbreviation (e.g. "Mr.",
    "Ph.D.", "etc."), glue it to the following fragment. The regex splits on
    ". " after any "." which over-cuts on titles and Latin abbreviations —
    this pass repairs those without complicating the regex itself.
    """
    if not text:
        return []
    parts = _SENT_SPLIT_RE.split(text)
    raw = [p.strip() for p in parts if p and p.strip()]
    if not raw:
        return []

    merged: list[str] = []
    i = 0
    while i < len(raw):
        cur = raw[i]
        # Keep gluing forward while the current fragment ends in an
        # abbreviation. Handles chains like "We use e.g. Python and i.e. uv."
        # where the first merge still leaves an abbrev at the tail.
        while i + 1 < len(raw):
            tokens = cur.split()
            last_token = tokens[-1] if tokens else ""
            if last_token in _ABBREVIATIONS:
                cur = cur + " " + raw[i + 1]
                i += 1
            else:
                break
        merged.append(cur)
        i += 1
    return merged


def _normalize(s: str) -> str:
    return " ".join(s.split()).lower()


def substring_match(sentence: str, children_texts: list[str]) -> bool:
    """Case-insensitive substring grounding.

    For very short sentences (<8 chars) we require exact-word overlap with at
    least one child to avoid noise-matching ("是" / "the" / etc.).
    """
    s = _normalize(sentence)
    if not s:
        return False

    if len(s) < _SHORT_SENT_LEN:
        s_tokens = set(s.split())
        for c in children_texts:
            c_norm = _normalize(c)
            if not s_tokens:
                continue
            c_tokens = set(c_norm.split())
            if s_tokens and s_tokens.issubset(c_tokens):
                return True
        return False

    for c in children_texts:
        c_norm = _normalize(c)
        if s in c_norm:
            return True
    return False


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


async def _embedding_match(
    sentence: str,
    children_texts: list[str],
    *,
    threshold: float,
    embedding_fn=None,
) -> bool:
    """Cosine-similarity grounding check.

    ``embedding_fn`` is injectable for tests. Defaults to memvault's shared helper
    (``src.shared.embedding.get_embedding``); on any error returns False (fail closed).
    """
    if not children_texts:
        return False

    if embedding_fn is None:
        try:
            from src.shared.embedding import get_embedding as embedding_fn  # type: ignore
        except Exception as exc:  # pragma: no cover — import-time fallback
            logger.debug("fold_verifier: embedding import failed: %s", exc)
            return False

    try:
        s_emb = await embedding_fn(sentence)
        if s_emb is None:
            return False
        for c in children_texts:
            c_emb = await embedding_fn(c)
            if c_emb is None:
                continue
            if _cosine(s_emb, c_emb) >= threshold:
                return True
    except Exception as exc:
        logger.debug("fold_verifier: embedding match failed: %s", exc)
        return False
    return False


async def verify_fold_extractiveness(
    fold_text: str,
    children_texts: list[str],
    *,
    embedding_threshold: float = _DEFAULT_EMBEDDING_THRESHOLD,
    embedding_fn=None,
    use_embedding: bool = True,
) -> VerifierResult:
    """Drop sentences that have no grounding in any child block.

    Args:
        fold_text: consolidate output text.
        children_texts: raw text content from each child block.
        embedding_threshold: cosine floor for embedding match (default 0.85).
        embedding_fn: optional injection point for tests / pipelines that already
            cached embeddings. Signature: ``async (text) -> list[float] | None``.
        use_embedding: when False, skip embedding step entirely (substring only).
            Useful when offline or in fast unit tests.

    Returns:
        ``VerifierResult`` with the cleaned text + accepted/rejected lists.
    """
    sentences = split_sentences(fold_text)
    accepted: list[str] = []
    rejected: list[str] = []
    reasons: dict[str, str] = {}

    for sentence in sentences:
        if substring_match(sentence, children_texts):
            accepted.append(sentence)
            reasons[sentence] = "substring"
            continue

        if use_embedding and await _embedding_match(
            sentence,
            children_texts,
            threshold=embedding_threshold,
            embedding_fn=embedding_fn,
        ):
            accepted.append(sentence)
            reasons[sentence] = "embedding"
            continue

        rejected.append(sentence)
        reasons[sentence] = "rejected"

    filtered_text = " ".join(accepted)
    return VerifierResult(
        filtered_text=filtered_text,
        accepted=accepted,
        rejected=rejected,
        reasons=reasons,
    )


# ============================================================================
# Pre-write conflict check (Mem0-style)
# ============================================================================


@dataclass
class ConflictCheckResult:
    """Outcome of pre-write KG contradiction check.

    ``has_conflict`` drives the consumer's status assignment:
    True → write fold with status='conflict_pending' + skip downstream KG/entity updates.

    ``scoped_to_children`` records whether the caller asked for a per-fold scope
    (children_ids was provided). The current implementation still runs a global
    contradiction check — narrowing the scope to triples sourced from the given
    children blocks is a TODO (requires Triple↔block back-reference, which is
    not stored on the Triple model today; only ``source_session`` is available).
    """

    has_conflict: bool
    findings: list = field(default_factory=list)
    error: str | None = None
    scoped_to_children: bool = False


async def pre_write_conflict_check(
    db,
    space_id: str,
    *,
    sample_size: int = 100,
    similarity_threshold: float = 0.80,
    children_ids: list[str] | None = None,
) -> ConflictCheckResult:
    """Run a contradiction check before consolidate writes.

    Delegates to ``lint.check_contradictions`` (Worker 3's territory — already
    exists at module load time). On any error we return ``has_conflict=False``
    with an error message: degrade-gracefully, never block the dream loop.

    When ``children_ids`` is provided the caller wants a per-fold scope (only
    contradictions touching those children should gate this fold). Today the
    Triple model has no block_id back-reference, so we still run the global
    check and just flag ``scoped_to_children=True`` on the result for
    observability — narrowing the actual filter is a TODO once the schema
    grows that link.
    """
    try:
        from .lint import check_contradictions

        findings = await check_contradictions(
            db,
            space_id,
            sample_size=sample_size,
            similarity_threshold=similarity_threshold,
        )
        # Only count actionable findings (warning+ severity, with a real triple_id)
        active = [
            f
            for f in (findings or [])
            if getattr(f, "severity", None) in {"warning", "critical"}
            and getattr(f, "entity_type", None) == "triple"
        ]
        return ConflictCheckResult(
            has_conflict=bool(active),
            findings=active,
            scoped_to_children=bool(children_ids),
        )
    except Exception as exc:
        logger.warning("fold_verifier.pre_write_conflict_check failed: %s", exc)
        return ConflictCheckResult(
            has_conflict=False,
            findings=[],
            error=str(exc),
            scoped_to_children=bool(children_ids),
        )


__all__ = [
    "ConflictCheckResult",
    "VerifierResult",
    "compute_content_hash",
    "compute_fold_id",
    "pre_write_conflict_check",
    "split_sentences",
    "substring_match",
    "verify_fold_extractiveness",
]
