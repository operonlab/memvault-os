"""G2: Injection sanitization — filter prompt injection patterns before memory injection.

Adapted from memory-lancedb-pro's reflection injection sanitization.
Memories (wisdom, attitude, knowledge) injected into system prompts must
be sanitized to prevent prompt injection attacks.
"""

import re

# --- Dangerous patterns that should never appear in injected memory ---

_ROLE_TAG_PATTERN = re.compile(
    r"<\s*/?\s*(system|user|assistant|human|ai)\s*>",
    re.IGNORECASE,
)

_INSTRUCTION_OVERRIDE_PATTERN = re.compile(
    r"(ignore\s+(all\s+)?previous\s+instructions|"
    r"disregard\s+(all\s+)?(above|prior)|"
    r"forget\s+(everything|all)\s+(above|before)|"
    r"new\s+instructions?\s*:|"
    r"override\s+(system|previous)|"
    r"you\s+are\s+now\s+|"
    r"act\s+as\s+(if\s+you\s+are|a)\s+|"
    r"pretend\s+(you\s+are|to\s+be)\s+|"
    r"from\s+now\s+on\s*,?\s*(you|your|ignore)|"
    r"忽略(所有|之前的)?指令|"
    r"無視(之前|上面)的|"
    r"新的指令\s*[:：]|"  # noqa: RUF001
    r"你現在是)",
    re.IGNORECASE,
)

_ENCODED_INJECTION_PATTERN = re.compile(
    r"(&#x[0-9a-f]+;|&#\d+;|\\u[0-9a-f]{4}|%[0-9a-f]{2}){3,}",
    re.IGNORECASE,
)

_MARKDOWN_INJECTION_PATTERN = re.compile(
    r"!\[.*?\]\(javascript:|"
    r"\[.*?\]\(data:|"
    r"<script\b|"
    r"<iframe\b|"
    r"on\w+\s*=\s*[\"']",
    re.IGNORECASE,
)

_SEPARATOR_FLOOD_PATTERN = re.compile(
    r"[-=_*#]{20,}",
)


def is_unsafe_for_injection(content: str) -> tuple[bool, str | None]:
    """Check if memory content contains prompt injection patterns.

    Returns (is_unsafe, reason).
    """
    if _ROLE_TAG_PATTERN.search(content):
        return True, "role_tag"

    if _INSTRUCTION_OVERRIDE_PATTERN.search(content):
        return True, "instruction_override"

    if _ENCODED_INJECTION_PATTERN.search(content):
        return True, "encoded_injection"

    if _MARKDOWN_INJECTION_PATTERN.search(content):
        return True, "markdown_injection"

    if _SEPARATOR_FLOOD_PATTERN.search(content):
        return True, "separator_flood"

    return False, None


def sanitize_for_injection(content: str) -> str:
    """Remove or neutralize injection patterns from memory content.

    Non-destructive: only modifies dangerous patterns, preserves meaning.
    """
    # Neutralize role tags by adding zero-width spaces
    result = _ROLE_TAG_PATTERN.sub(
        lambda m: m.group(0).replace("<", "\uff1c").replace(">", "\uff1e"),
        content,
    )

    # Neutralize instruction overrides by prefixing with [memory:]
    result = _INSTRUCTION_OVERRIDE_PATTERN.sub(
        lambda m: f"[memory: {m.group(0)}]",
        result,
    )

    # Remove encoded injection sequences
    result = _ENCODED_INJECTION_PATTERN.sub("[encoded-removed]", result)

    # Neutralize markdown injection
    result = _MARKDOWN_INJECTION_PATTERN.sub("[markup-removed]", result)

    # Truncate separator floods
    result = _SEPARATOR_FLOOD_PATTERN.sub("---", result)

    return result


def sanitize_results_for_injection(
    results: list[dict],
    content_key: str = "content",
) -> list[dict]:
    """Sanitize a list of result dicts before injection into prompts.

    Modifies results in-place and returns them.
    """
    for r in results:
        content = r.get(content_key, "")
        if content:
            unsafe, _ = is_unsafe_for_injection(content)
            if unsafe:
                r[content_key] = sanitize_for_injection(content)
    return results
