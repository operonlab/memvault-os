"""Dual-direction noise filter for memvault.

Capture-side: prevents noisy content from being stored.
Retrieval-side: filters noise from search results.
Shared logic for both directions.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# --- Greeting patterns ---

_GREETING_PATTERNS_EN = re.compile(
    r"^(hi|hello|hey|howdy|yo|sup|greetings|good\s*(morning|afternoon|evening|night))"
    r"[\s!.,]*$",
    re.IGNORECASE,
)
_GREETING_PATTERNS_CJK = re.compile(
    r"^(你好|嗨|哈囉|早安|午安|晚安|哈嘍|嘿)[\s!.,、。\uff01]*$",
)

# --- Agent refusal patterns ---

_REFUSAL_PATTERNS = re.compile(
    r"(I cannot|I can't|I'm unable|I am unable|As an AI|As a language model|"
    r"I don't have the ability|I'm not able|"
    r"我無法|我不能|作為AI|作為一個語言模型|身為AI)",
    re.IGNORECASE,
)

# --- Heartbeat / meta patterns ---

_HEARTBEAT_PATTERNS = re.compile(
    r"^(HEARTBEAT|ping|pong|test|ok|ack|noop)$",
    re.IGNORECASE,
)

# --- Status-only acknowledgement patterns (single-turn noise with no info value) ---
# e.g. "OK", "done", "好", "收到", "了解" — useful as chat response, not worth storing
_STATUS_ONLY_PATTERNS = re.compile(
    r"^(ok|okay|done|got\s+it|sure|roger|affirmative|copy\s+that|understood"
    r"|好|收到|了解|知道了|明白|好的|沒問題|遵命)[\s!.,、。\uff01]*$",
    re.IGNORECASE,
)

# --- Pure error trace / raw JSON dump patterns ---
# Long stack traces or raw JSON blobs (>500 chars) without surrounding context are noise
_ERROR_TRACE_PATTERNS = re.compile(
    r"^\s*(Traceback \(most recent call last\)|  File \"|    raise |Error:|Exception:)",
    re.IGNORECASE | re.MULTILINE,
)
_RAW_JSON_PATTERN = re.compile(r"^\s*[\[{]")  # starts with [ or { (JSON-like)

# --- Memory keywords that override greeting detection ---

_MEMORY_KEYWORDS = re.compile(
    r"(記得|記住|忘了|想起|提過|討論過|說過|之前|上次|以前|前面|先前|剛才"
    r"|我們聊過|有聊過|講過|聊到|提到|想想|回想"
    r"|remember|recall|forgot|mentioned|discussed|previously|earlier|last\s*time"
    r"|memory|memorize|noted|recorded|talked\s+about)",
    re.IGNORECASE,
)

# --- Memory-worthy override patterns ---
# Content matching these MUST bypass ALL noise checks — high-signal directives
_MEMORY_WORTHY_PATTERNS = re.compile(
    r"(記住|remember|note\s+to\s+self|重要|鐵律|iron\s+rule"
    r"|以後|from\s+now\s+on|always|never|禁止|must)",
    re.IGNORECASE,
)

QUARANTINE_TAG = "__quarantined__"


@dataclass
class NoiseVerdict:
    is_noise: bool
    reason: str | None = None
    confidence: float = 1.0


def check_noise(content: str) -> NoiseVerdict:
    """Shared noise detection -- used by both capture and retrieval paths."""
    stripped = content.strip()

    # Memory-worthy override: skip ALL noise checks if content contains high-signal directives
    if _MEMORY_WORTHY_PATTERNS.search(stripped):
        return NoiseVerdict(is_noise=False)

    # Too short
    if len(stripped) < 10:
        # Allow heartbeat check to provide more specific reason
        if _HEARTBEAT_PATTERNS.match(stripped):
            return NoiseVerdict(is_noise=True, reason="heartbeat", confidence=1.0)
        return NoiseVerdict(is_noise=True, reason="too_short", confidence=1.0)

    # Status-only acknowledgements (no informational value)
    if _STATUS_ONLY_PATTERNS.match(stripped):
        return NoiseVerdict(is_noise=True, reason="status_only", confidence=0.95)

    # Pure error traces without surrounding context
    if _ERROR_TRACE_PATTERNS.search(stripped):
        # Allow if it's short (likely a quoted snippet); block large raw traces
        if len(stripped) > 500:
            return NoiseVerdict(is_noise=True, reason="raw_error_trace", confidence=0.8)

    # Raw JSON dumps (>500 chars) with no surrounding context
    if _RAW_JSON_PATTERN.match(stripped) and len(stripped) > 500:
        # Allow if it contains memory keywords (JSON may hold structured memory)
        if not _MEMORY_KEYWORDS.search(stripped):
            return NoiseVerdict(is_noise=True, reason="raw_json_dump", confidence=0.75)

    # Too repetitive: >80% same character
    if stripped:
        most_common_count = max(stripped.count(c) for c in set(stripped))
        if most_common_count / len(stripped) > 0.8:
            return NoiseVerdict(is_noise=True, reason="repetitive", confidence=0.9)

    # Check for memory keywords — if present, never classify as noise
    if _MEMORY_KEYWORDS.search(stripped):
        return NoiseVerdict(is_noise=False)

    # Greetings (but not if contains question mark)
    if "?" not in stripped and "\uff1f" not in stripped:
        if _GREETING_PATTERNS_EN.match(stripped) or _GREETING_PATTERNS_CJK.match(stripped):
            return NoiseVerdict(is_noise=True, reason="greeting", confidence=0.9)

    # Agent refusal
    if _REFUSAL_PATTERNS.search(stripped):
        return NoiseVerdict(is_noise=True, reason="agent_refusal", confidence=0.85)

    return NoiseVerdict(is_noise=False)


def filter_results(
    results: list[Any],
    key_fn: Callable[[Any], str] | None = None,
) -> tuple[list[Any], int]:
    """Filter noise from search results.

    Args:
        results: List of result objects.
        key_fn: Function to extract content string from a result.
                Defaults to accessing result.block.content for SemanticSearchResult.

    Returns:
        (clean_results, filtered_count)
    """
    if key_fn is None:

        def key_fn(r: Any) -> str:
            return r.block.content

    clean = []
    filtered = 0
    for r in results:
        content = key_fn(r)
        verdict = check_noise(content)
        if verdict.is_noise:
            filtered += 1
        else:
            clean.append(r)
    return clean, filtered
