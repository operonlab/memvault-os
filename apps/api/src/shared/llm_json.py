"""Parse JSON from LLM responses, handling markdown fences and malformed output."""

from __future__ import annotations

import json
from typing import Any


def parse_llm_json(raw: str) -> dict[str, Any] | list[Any] | None:
    """Parse JSON from an LLM response string.

    Handles common LLM output artifacts:
    1. Markdown code fences (```json ... ```)
    2. Leading/trailing non-JSON text
    3. Fallback: extract first {...} or [...] block

    Returns parsed JSON (dict or list), or None if unparseable.
    """
    text = raw.strip()
    if not text:
        return None

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: find first JSON object or array
    for open_char, close_char in [("{", "}"), ("[", "]")]:
        start = text.find(open_char)
        end = text.rfind(close_char)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    return None
