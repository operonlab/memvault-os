"""Source Tracker — Memory provenance and poisoning detection.

Every memory should know where it came from. This enables:
1. Provenance queries ("show me memories from session X")
2. Trust scoring (memories from verified sessions score higher)
3. Anomaly detection (unexpected source patterns flag poisoning)

Based on MemoryGraft research (arXiv:2512.16962): 95% of memory poisoning attacks
succeed because systems don't track or verify memory provenance.

Complements injection_guard.py which covers:
  - XML/HTML role tags: <system>, <user>, etc.
  - Instruction overrides: "ignore previous instructions", "you are now", etc.
  - HTML/URL encoded sequences: &#x..., %xx, \\uXXXX
  - Markdown XSS/JS injection: javascript: links, <script>, <iframe>
  - Separator floods: 20+ repeated chars

This module adds patterns injection_guard does NOT cover:
  - Subtle authority impersonation: "Speaking as your creator..."
  - Base64-encoded payloads (>50 chars)
  - Markdown comment injection: <!-- hidden instructions -->
  - Temporal manipulation: fake future/past dates embedded as facts
  - Role self-declaration: "I am a", "As an AI", "My instructions are"
  - Trust escalation: "As admin", "With elevated permissions"
"""

import base64
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Compiled patterns — only those NOT already in injection_guard.py
# ---------------------------------------------------------------------------

# Authority impersonation — subtle credential claims
_AUTHORITY_IMPERSONATION_PATTERN = re.compile(
    r"(?:"
    r"speaking\s+as\s+(your\s+)?(creator|developer|administrator|owner|maintainer)|"
    r"in\s+my\s+capacity\s+as\s+(admin|administrator|system|owner)|"
    r"as\s+(your\s+)?(creator|developer|god|master|owner)|"
    r"with\s+(elevated|admin|root|system)\s+(permissions?|access|rights?)|"
    r"i\s+have\s+(admin|root|system|elevated)\s+(access|rights?|permissions?)|"
    r"authorized\s+by\s+(system|admin|creator|anthropic|openai)|"
    r"on\s+behalf\s+of\s+(system|admin|anthropic|openai|the\s+creator)"
    r")",
    re.IGNORECASE,
)

# Role self-declaration — claiming to be an AI/system/admin
# Note: injection_guard covers "you are now" and "act as" (external commands TO the model)
# This covers CONTENT claiming to BE the system (first-person declarations in memory)
_ROLE_SELF_DECLARATION_PATTERN = re.compile(
    r"(?:"
    r"\bi\s+am\s+(a\s+)?(language\s+model|llm|ai\s+assistant|gpt|claude|gemini|the\s+system)|"
    r"as\s+an\s+ai\s*(,|language\s+model|assistant|system)|"
    r"my\s+(primary\s+)?(instructions?|directives?|goals?|purpose)\s+(are|is)\s+to|"
    r"i\s+(was\s+)?programmed\s+to|"
    r"my\s+training\s+(data\s+)?(includes?|says?|tells?)"
    r")",
    re.IGNORECASE,
)

# Markdown comment injection — hidden instructions in HTML comments
_MARKDOWN_COMMENT_INJECTION_PATTERN = re.compile(
    r"<!--.*?(?:"
    r"instruct|command|directive|ignore|override|system|you\s+must|always|never|do\s+not"
    r").*?-->",
    re.IGNORECASE | re.DOTALL,
)

# Temporal manipulation — embedding fake authoritative timestamps/dates
# Flags patterns that claim specific future or impossible dates as established facts
_MONTHS = (
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
)
_TEMPORAL_MANIPULATION_PATTERN = re.compile(
    r"(?:"
    rf"as\s+of\s+{_MONTHS}\s+\d{{4}},?\s+(?:the\s+)?(?:new\s+)?(?:instructions?|rules?|guidelines?|policies?)"
    r"|"
    rf"effective\s+(?:from\s+)?{_MONTHS}\s+\d{{4}}[,\s]+(?:you\s+(?:must|should|will|are)|all\s+(?:responses?|outputs?))"
    r"|"
    rf"updated?\s+(?:on\s+)?{_MONTHS}\s+\d{{1,2}},?\s+\d{{4}}[,\s]+(?:new\s+)?(?:instructions?|rules?|guidelines?|behavior)"
    r")",
    re.IGNORECASE,
)

# Base64 payload detection — raw base64 strings > 50 chars embedded in content
# injection_guard covers %xx and \\uXXXX but NOT raw base64
_BASE64_PAYLOAD_RE = re.compile(r"[A-Za-z0-9+/]{50,}={0,2}")

# Minimum entropy threshold for base64 — legitimate text encoded in base64
# has high entropy; short words happen to be valid base64 by chance
_BASE64_CHARSET = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


def _is_likely_base64_payload(candidate: str) -> bool:
    """Return True if candidate looks like an actual base64-encoded payload.

    Heuristics:
    - All chars in base64 alphabet (already guaranteed by regex)
    - Length divisible by 4 OR ends with = padding
    - Decoded content is non-trivial (not plain ASCII words)
    """
    # Pad to multiple of 4 for decoding attempt
    padded = candidate + "=" * (-len(candidate) % 4)
    try:
        decoded = base64.b64decode(padded)
    except Exception:
        return False

    # If decoded content is printable ASCII, it might be legitimate text
    # Flag only if decoded contains non-printable bytes (binary payload)
    # or if it contains suspicious keywords when decoded as utf-8
    try:
        decoded_str = decoded.decode("utf-8", errors="strict")
        # Check if decoded string itself has injection patterns
        suspicious_decoded = re.search(
            r"(ignore|override|system|instruction|you\s+are|act\s+as)",
            decoded_str,
            re.IGNORECASE,
        )
        return suspicious_decoded is not None
    except UnicodeDecodeError:
        # Binary payload — likely not legitimate plain text
        return len(decoded) > 20


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MemoryProvenance:
    """Source metadata for a memory block."""

    source_session_id: str | None = None
    source_agent_id: str | None = None  # which agent extracted this
    extraction_method: str = (
        "manual"  # "manual" | "auto_extract" | "progressive" | "reflection" | "kg_triple"
    )
    extraction_timestamp: datetime | None = None
    trust_score: float = 1.0  # 1.0 = fully trusted, 0.0 = suspicious

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict for storage in memory metadata."""
        return {
            "source_session_id": self.source_session_id,
            "source_agent_id": self.source_agent_id,
            "extraction_method": self.extraction_method,
            "extraction_timestamp": (
                self.extraction_timestamp.isoformat() if self.extraction_timestamp else None
            ),
            "trust_score": self.trust_score,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryProvenance":
        """Deserialize from a stored metadata dict."""
        extraction_timestamp = None
        raw_ts = data.get("extraction_timestamp")
        if raw_ts:
            try:
                extraction_timestamp = datetime.fromisoformat(raw_ts)
            except (ValueError, TypeError):
                logger.warning("source_tracker: invalid extraction_timestamp %r", raw_ts)

        return cls(
            source_session_id=data.get("source_session_id"),
            source_agent_id=data.get("source_agent_id"),
            extraction_method=data.get("extraction_method", "manual"),
            extraction_timestamp=extraction_timestamp,
            trust_score=float(data.get("trust_score", 1.0)),
        )


@dataclass
class AnomalySignal:
    """A detected anomaly in memory access or creation patterns."""

    signal_type: str  # "burst_access" | "foreign_source" | "prompt_like" | "temporal_anomaly"
    severity: float  # 0.0-1.0
    description: str
    memory_id: str
    pattern_matched: str | None = field(default=None)  # the matched text snippet (truncated)


# ---------------------------------------------------------------------------
# Detection functions
# ---------------------------------------------------------------------------


def check_content_for_injection(content: str) -> AnomalySignal | None:
    """Check if memory content contains prompt-injection-like patterns.

    Complements injection_guard.py. Only checks patterns NOT already covered there:
    - Authority impersonation: "Speaking as your creator..."
    - Role self-declaration: "I am a language model", "My instructions are to"
    - Base64-encoded payloads
    - Markdown comment injection: <!-- hidden directives -->
    - Temporal manipulation: fake dates attached to instruction claims

    Returns an AnomalySignal if suspicious, None if clean.
    """
    # Authority impersonation
    m = _AUTHORITY_IMPERSONATION_PATTERN.search(content)
    if m:
        snippet = m.group(0)[:80]
        return AnomalySignal(
            signal_type="prompt_like",
            severity=0.85,
            description=f"Authority impersonation detected: '{snippet}'",
            memory_id="",
            pattern_matched=snippet,
        )

    # Role self-declaration
    m = _ROLE_SELF_DECLARATION_PATTERN.search(content)
    if m:
        snippet = m.group(0)[:80]
        return AnomalySignal(
            signal_type="prompt_like",
            severity=0.75,
            description=f"AI/system role self-declaration: '{snippet}'",
            memory_id="",
            pattern_matched=snippet,
        )

    # Markdown comment injection
    m = _MARKDOWN_COMMENT_INJECTION_PATTERN.search(content)
    if m:
        snippet = m.group(0)[:80]
        return AnomalySignal(
            signal_type="prompt_like",
            severity=0.90,
            description=f"Hidden instruction in HTML comment: '{snippet}'",
            memory_id="",
            pattern_matched=snippet,
        )

    # Temporal manipulation
    m = _TEMPORAL_MANIPULATION_PATTERN.search(content)
    if m:
        snippet = m.group(0)[:80]
        return AnomalySignal(
            signal_type="temporal_anomaly",
            severity=0.70,
            description=f"Temporal instruction injection: '{snippet}'",
            memory_id="",
            pattern_matched=snippet,
        )

    # Base64 payload detection
    for candidate in _BASE64_PAYLOAD_RE.findall(content):
        if _is_likely_base64_payload(candidate):
            snippet = candidate[:40] + "..."
            return AnomalySignal(
                signal_type="prompt_like",
                severity=0.80,
                description=f"Suspicious base64 payload: '{snippet}'",
                memory_id="",
                pattern_matched=snippet,
            )

    return None


def check_access_anomaly(
    memory_id: str,
    recent_access_count: int,
    time_window_minutes: int = 10,
    threshold: int = 20,
) -> AnomalySignal | None:
    """Detect abnormal access patterns (burst access = possible exploitation).

    A memory being accessed far more than the threshold within a short window
    may indicate automated exploitation (e.g., a script repeatedly querying a
    poisoned memory to amplify its effect).

    Args:
        memory_id: The memory block being accessed.
        recent_access_count: How many times it has been accessed in the window.
        time_window_minutes: The observation window length.
        threshold: Accesses above this count trigger the signal.

    Returns:
        AnomalySignal if burst detected, None otherwise.
    """
    if recent_access_count <= threshold:
        return None

    excess_ratio = recent_access_count / threshold
    # Severity scales with excess: 2x → 0.5, 5x → 0.8, 10x → 0.9 (capped)
    severity = min(0.9, 0.3 + 0.12 * excess_ratio)

    return AnomalySignal(
        signal_type="burst_access",
        severity=round(severity, 2),
        description=(
            f"Burst access: {recent_access_count} accesses in {time_window_minutes}min "
            f"(threshold={threshold})"
        ),
        memory_id=memory_id,
    )


def compute_trust_score(provenance: MemoryProvenance) -> float:
    """Compute trust score from provenance metadata.

    Scoring breakdown (additive, max 1.0):
      +0.50  base (no anomaly signals)
      +0.20  known session_id (not None / not empty)
      +0.10  known agent_id
      +0.10  automated method ("auto_extract" | "progressive" | "reflection" | "kg_triple")
      +0.10  recent extraction (within 7 days)

    Returns:
        float in [0.0, 1.0]
    """
    score = 0.50  # base: assume clean unless evidence otherwise

    if provenance.source_session_id:
        score += 0.20

    if provenance.source_agent_id:
        score += 0.10

    automated_methods = {"auto_extract", "progressive", "reflection", "kg_triple"}
    if provenance.extraction_method in automated_methods:
        score += 0.10  # automated pipelines are consistent and auditable

    if provenance.extraction_timestamp:
        now = datetime.now(tz=UTC)
        ts = provenance.extraction_timestamp
        # Ensure tz-aware comparison
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        age_days = (now - ts).total_seconds() / 86400
        if age_days <= 7:
            score += 0.10

    return round(min(score, 1.0), 2)


def detect_anomalies(
    content: str,
    provenance: MemoryProvenance | None = None,
    memory_id: str = "",
) -> list[AnomalySignal]:
    """Run all anomaly detection checks on a memory block.

    Runs content-level injection detection. Burst access detection is NOT
    run here (requires live access counters from the caller).

    Args:
        content: The memory block's text content.
        provenance: Optional provenance metadata to include in trust analysis.
        memory_id: The memory's ID (used to populate AnomalySignal.memory_id).

    Returns:
        List of AnomalySignal instances. Empty list = clean.
    """
    signals: list[AnomalySignal] = []

    # Content-level injection check
    injection_signal = check_content_for_injection(content)
    if injection_signal is not None:
        injection_signal.memory_id = memory_id
        signals.append(injection_signal)

    # Provenance-based signals
    if provenance is not None:
        # Very low trust despite having full provenance = suspicious
        computed = compute_trust_score(provenance)
        if computed < 0.3 and provenance.source_session_id and provenance.source_agent_id:
            signals.append(
                AnomalySignal(
                    signal_type="foreign_source",
                    severity=0.65,
                    description=(
                        f"Low computed trust ({computed}) despite known provenance — "
                        "possible tampered metadata"
                    ),
                    memory_id=memory_id,
                )
            )

    return signals
