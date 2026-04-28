"""Shared G-R-C (Generate-Reflect-Curate) Framework.

Provides composable dataclasses, protocols, and pure functions for modules
that need self-improvement loops. Follows tier_manager.py design:
  - Zero ORM imports — caller handles persistence
  - Dataclass IO — no Pydantic at this layer
  - Protocols for optional capabilities — modules adopt incrementally

Usage:
    from src.shared.grc import (
        GRCConfig, GenerateItem, ReflectResult, CurateAction, CurateResult,
        SupportsReflect, SupportsCurate,
        classify_content, three_guard_filter, calculate_quality_score,
    )
    from src.shared.capabilities import has_capability

    if has_capability(adapter, SupportsReflect):
        items = adapter.gather_items(scope_id)
        result = adapter.reflect(items, scope_id)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

# ─── Dataclasses ──────────────────────────────────────────────────────


@dataclass
class GenerateItem:
    """A single item to reflect upon. Module converts ORM → this."""

    id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    # metadata: confidence, access_count, created_at, tags, block_type, etc.


@dataclass
class ReflectResult:
    """Output of a reflect pass — insights extracted from generated items."""

    module: str
    scope_id: str  # space_id, session_id, or any scoping key
    items_analyzed: int = 0
    insights: list[str] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    reflected_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class CurateAction:
    """A single proposed curation action."""

    item_id: str
    action: str  # "soft_delete" | "demote" | "flag" | "merge" | "archive"
    reason: str
    confidence: float = 0.0  # 0.0-1.0


@dataclass
class CurateResult:
    """Output of a curate pass."""

    module: str
    scope_id: str
    actions: list[CurateAction] = field(default_factory=list)
    dry_run: bool = False
    applied_count: int = 0
    skipped_count: int = 0
    curated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class GRCConfig:
    """Safety constants for curation and reflection. Modules can override defaults."""

    max_actions_per_run: int = 50
    min_item_age_days: int = 30
    min_access_for_protection: int = 1
    cooldown_hours: int = 24
    confidence_threshold: float = 0.15
    max_insights: int = 20
    max_corrections: int = 10
    max_anomalies: int = 10


# ─── Protocols ────────────────────────────────────────────────────────


@runtime_checkable
class SupportsReflect(Protocol):
    """Module can reflect on its generated data."""

    def gather_items(self, scope_id: str, **kwargs: Any) -> list[GenerateItem]:
        """Collect items to reflect upon."""
        ...

    def reflect(self, items: list[GenerateItem], scope_id: str) -> ReflectResult:
        """Analyze items and produce insights. Pure computation."""
        ...


@runtime_checkable
class SupportsCurate(Protocol):
    """Module can curate (clean up) its data based on quality signals."""

    def identify_candidates(
        self,
        scope_id: str,
        config: GRCConfig | None = None,
        **kwargs: Any,
    ) -> list[CurateAction]:
        """Identify items that need curation. Pure computation."""
        ...

    async def apply_actions(
        self,
        actions: list[CurateAction],
        dry_run: bool = False,
        **kwargs: Any,
    ) -> CurateResult:
        """Execute curation actions. Caller commits transaction."""
        ...


@runtime_checkable
class SupportsGenerate(Protocol):
    """Module can generate derived content from reflection results."""

    def generate_derived(
        self,
        reflect_result: ReflectResult,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Produce derived artifacts (KG triples, reports, etc.)."""
        ...


@runtime_checkable
class SupportsRLMReflect(Protocol):
    """Module can use RLM (Recursive Language Model) for deep reflection.

    Extends the standard reflect pattern with multi-step LLM reasoning.
    Modules implement gather_items() and optionally override rlm_reflect().
    """

    def gather_items(self, scope_id: str, **kwargs: Any) -> list[GenerateItem]:
        """Collect items to reflect upon."""
        ...

    def rlm_reflect(
        self,
        items: list[GenerateItem],
        scope_id: str,
        **kwargs: Any,
    ) -> ReflectResult:
        """Reflect on items using RLM engine. Default via rlm_reflect_default()."""
        ...


# ─── RLM Reflect Default Implementation ─────────────────────────────


def rlm_reflect_default(
    items: list[GenerateItem],
    module: str,
    scope_id: str,
    *,
    model: str = "grok-4-fast",
    max_iterations: int = 10,
    api_base: str = "http://localhost:4000/v1",
    api_key: str = "sk-litellm-local-dev",
) -> ReflectResult:
    """Default RLM-powered reflect implementation that modules can reuse.

    Takes gather_items output and uses RLM to:
      1. Summarize items
      2. Detect patterns
      3. Identify anomalies
      4. Suggest curation actions

    Returns a ReflectResult compatible with existing G-R-C pipelines.
    """
    from src.shared.rlm_engine import RLMConfig, RLMEngine

    if not items:
        return ReflectResult(module=module, scope_id=scope_id)

    # Build context from items
    context_lines = []
    for item in items:
        meta = ", ".join(f"{k}={v}" for k, v in item.metadata.items()) if item.metadata else ""
        context_lines.append(f"[{item.id}] {item.content[:500]}" + (f" ({meta})" if meta else ""))

    context = "\n".join(context_lines)

    prompt = (
        f"You are analyzing {len(items)} items from the '{module}' module (scope: {scope_id}).\n\n"
        "Tasks:\n"
        "1. SUMMARIZE: What are the main themes across these items?\n"
        "2. PATTERNS: What recurring patterns do you see?\n"
        "3. ANOMALIES: Any outliers, contradictions, or quality issues?\n"
        "4. CURATION: Which items should be flagged, merged, demoted, or archived? Why?\n\n"
        "Return a structured analysis with clear sections for each task.\n"
        "Use FINAL() when done."
    )

    config = RLMConfig(
        model=model,
        max_iterations=max_iterations,
        api_base=api_base,
        api_key=api_key,
    )
    engine = RLMEngine(config)
    result = engine.completion(prompt=prompt, context=context)

    # Parse RLM response into ReflectResult fields
    response = result.response
    insights, anomalies, corrections = _parse_rlm_reflect_response(response)

    return ReflectResult(
        module=module,
        scope_id=scope_id,
        items_analyzed=len(items),
        insights=insights,
        anomalies=anomalies,
        corrections=corrections,
        metrics={
            "rlm_iterations": float(result.iterations),
            "rlm_time_secs": round(result.execution_time_secs, 2),
            "rlm_llm_calls": float(result.usage.total_calls),
            "rlm_status": 1.0 if result.status == "ok" else 0.0,
        },
    )


def _parse_rlm_reflect_response(
    response: str,
) -> tuple[list[str], list[str], list[str]]:
    """Extract insights, anomalies, and corrections from RLM response text."""
    insights: list[str] = []
    anomalies: list[str] = []
    corrections: list[str] = []

    current: list[str] | None = None
    for line in response.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if any(kw in lower for kw in ("summar", "pattern", "theme", "insight")):
            current = insights
        elif any(kw in lower for kw in ("anomal", "outlier", "issue", "quality")):
            current = anomalies
        elif any(kw in lower for kw in ("curat", "flag", "merge", "demot", "archiv", "action")):
            current = corrections
        elif current is not None and (
            stripped.startswith("-") or stripped.startswith("•") or stripped[0].isdigit()
        ):
            # Strip bullet markers
            text = stripped.lstrip("-•*0123456789.) ").strip()
            if text:
                current.append(text)

    return insights, anomalies, corrections


# ─── Content Classification (extracted from memvault/reflection.py) ──


# Pattern library — bilingual (zh-TW + English)
_PATTERNS: dict[str, re.Pattern[str]] = {
    "preference": re.compile(
        r"(偏好|喜歡|我比較喜歡|我覺得.*比較好|prefer|rather|instead of|always|never"
        r"|一律|禁止|必須|鐵律|must|should|shouldn'?t)",
        re.IGNORECASE,
    ),
    "rule": re.compile(
        r"(規則|慣例|以後|鐵律|convention|pattern|原則|principle|策略|strategy"
        r"|must\s+always|never\s+again|禁止|一定要)",
        re.IGNORECASE,
    ),
    "correction": re.compile(
        r"(不[是對]|搞錯|更正|其實是|correction|actually|wait.*wrong"
        r"|之前說錯|should\s+be|not.*but\s+rather)",
        re.IGNORECASE,
    ),
    "workflow": re.compile(
        r"(流程|步驟|流程改成|改用|從.*改成|新做法|pipeline|workflow"
        r"|switch\s+to|migrate\s+to|先.*再.*然後|step\s*\d|phase\s*\d)",
        re.IGNORECASE,
    ),
    "decision": re.compile(
        r"(決定|決策|決定了|拍板|定案|最後選|decided|chose|confirmed|we'?ll\s+go\s+with"
        r"|選擇|採用|rejected|不採用|棄用)",
        re.IGNORECASE,
    ),
    "lesson": re.compile(
        r"(學到|教訓|踩坑|才知道|原來|注意|小心"
        r"|lesson|learned|gotcha|turns?\s+out|caveat|pitfall)",
        re.IGNORECASE,
    ),
}

# Map pattern names → insight categories
_CATEGORY_MAP: dict[str, str] = {
    "preference": "invariant",
    "rule": "invariant",
    "correction": "correction",
    "workflow": "derived",
    "decision": "derived",
    "lesson": "derived",
}


def classify_content(
    content: str,
    patterns: dict[str, re.Pattern[str]] | None = None,
    category_map: dict[str, str] | None = None,
) -> str | None:
    """Classify content into invariant/derived/correction categories.

    Modules can pass custom patterns + category_map to extend or replace
    the default bilingual patterns.
    """
    p = patterns or _PATTERNS
    cm = category_map or _CATEGORY_MAP
    for name, pattern in p.items():
        if pattern.search(content):
            return cm.get(name)
    return None


def extract_key_sentence(
    content: str,
    marker_patterns: list[re.Pattern[str]] | None = None,
    max_length: int = 200,
) -> str:
    """Extract the most informative sentence from content.

    Prefers sentences matching marker_patterns, falls back to longest sentence.
    """
    sentences = re.split(r"[。.!\uff01?\n]", content)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
    if not sentences:
        return content[:max_length]

    markers = marker_patterns or [
        _PATTERNS["preference"],
        _PATTERNS["rule"],
        _PATTERNS["decision"],
        _PATTERNS["lesson"],
    ]
    for s in sentences:
        for marker in markers:
            if marker.search(s):
                return s

    return max(sentences, key=len)


# ─── Quality Scoring ─────────────────────────────────────────────────

_DEFAULT_WEIGHTS = {
    "success_rate": 0.4,
    "error_free": 0.3,
    "efficiency": 0.2,
    "completion": 0.1,
}


def calculate_quality_score(
    total_items: int,
    error_count: int,
    success_count: int,
    efficiency: float = 0.0,
    completion_signal: float = 0.0,
    weights: dict[str, float] | None = None,
) -> tuple[str, float]:
    """Calculate quality outcome + score from generic metrics.

    Returns:
        (outcome, score) where outcome is "success" | "partial" | "failure"
        and score is 0.0-1.0.
    """
    w = weights or _DEFAULT_WEIGHTS

    if total_items == 0:
        return ("failure", 0.0)

    success_rate = success_count / total_items if total_items > 0 else 0.0
    error_rate = error_count / total_items if total_items > 0 else 0.0

    score = (
        w.get("success_rate", 0.4) * success_rate
        + w.get("error_free", 0.3) * (1 - error_rate)
        + w.get("efficiency", 0.2) * efficiency
        + w.get("completion", 0.1) * completion_signal
    )
    score = max(0.0, min(1.0, score))

    # Outcome classification
    if error_rate > 0.5 or total_items == 0:
        outcome = "failure"
    elif error_rate > 0.2 or success_rate < 0.7:
        outcome = "partial"
    else:
        outcome = "success"

    return (outcome, round(score, 3))


# ─── Three-Guard Filter (curation safety) ────────────────────────────


def three_guard_filter(
    items: list[GenerateItem],
    config: GRCConfig,
    now: datetime | None = None,
) -> list[GenerateItem]:
    """Filter items that pass all three safety guards for curation.

    Three guards must ALL be true for an item to be a candidate:
    1. confidence < config.confidence_threshold
    2. access_count < config.min_access_for_protection
    3. age > config.min_item_age_days

    Returns at most config.max_actions_per_run items.
    """
    now = now or datetime.now(UTC)
    candidates = []

    for item in items:
        confidence = item.metadata.get("confidence", 1.0)
        access_count = item.metadata.get("access_count", 0)
        created_at = item.metadata.get("created_at")

        if created_at is None:
            continue

        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                continue

        # Ensure timezone-aware comparison
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        age_days = (now - created_at).total_seconds() / 86400

        if (
            confidence < config.confidence_threshold
            and access_count < config.min_access_for_protection
            and age_days > config.min_item_age_days
        ):
            candidates.append(item)

    return candidates[: config.max_actions_per_run]
