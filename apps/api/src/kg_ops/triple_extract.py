"""Triple extraction from text via LLM.

Extracted from memvault/kg_auto_evolve.py. Generalized to accept
LLM endpoint and model as parameters (no hardcoded URLs).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx

from .predicates import PREDICATE_VOCABULARY, VALID_PREDICATES, normalize_predicate

logger = logging.getLogger(__name__)


def _build_predicate_list(vocabulary: dict[str, list[str]] | None = None) -> str:
    """Build a human-readable predicate list for the LLM prompt."""
    vocab = vocabulary or PREDICATE_VOCABULARY
    lines = []
    for category, predicates in vocab.items():
        lines.append(f"  [{category}]: {', '.join(predicates)}")
    return "\n".join(lines)


def build_extraction_prompt(
    content: str,
    *,
    predicate_vocabulary: dict[str, list[str]] | None = None,
    predicate_hint: str = "Use all available predicates as appropriate for the content.",
    max_triples: int = 5,
) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for triple extraction. Pure function.

    Args:
        content: Text to extract triples from.
        predicate_vocabulary: Custom vocabulary (defaults to shared PREDICATE_VOCABULARY).
        predicate_hint: Domain-specific guidance for extraction.
        max_triples: Maximum number of triples to request.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    predicate_list = _build_predicate_list(predicate_vocabulary)

    system_prompt = (
        "You are a knowledge graph triple extractor. "
        "Extract factual subject-predicate-object triples from the given text. "
        "ONLY use predicates from the approved vocabulary below — do not invent new ones.\n\n"
        f"APPROVED PREDICATES:\n{predicate_list}\n\n"
        f"EXTRACTION GUIDANCE: {predicate_hint}\n\n"
        "Return ONLY a JSON array. Each element must have exactly these keys: "
        '"subject", "predicate", "object", "topic". '
        "topic should be a short phrase (≤5 words) categorising the triple. "
        f"Extract 1-{max_triples} high-quality triples. "
        "Do not hallucinate — only extract facts clearly stated or strongly implied."
    )
    user_prompt = f"TEXT:\n{content}\n\nExtract triples as JSON array:"
    return system_prompt, user_prompt


def validate_extracted_triples(
    raw_triples: list[Any],
    valid_predicates: set[str] | None = None,
) -> list[dict[str, str]]:
    """Filter and normalize raw LLM output to valid triples. Pure function.

    Args:
        raw_triples: Raw list of dicts from LLM JSON output.
        valid_predicates: Set of allowed predicates (defaults to shared VALID_PREDICATES).

    Returns:
        List of validated triple dicts with normalized predicates.
    """
    allowed = valid_predicates or VALID_PREDICATES
    valid: list[dict[str, str]] = []
    for t in raw_triples:
        if not isinstance(t, dict):
            continue
        subj = str(t.get("subject", "")).strip()
        pred = str(t.get("predicate", "")).strip()
        obj = str(t.get("object", "")).strip()
        topic = str(t.get("topic", "")).strip() or None

        if not (subj and pred and obj):
            continue

        canonical = normalize_predicate(pred)
        if canonical not in allowed:
            logger.debug("Skipping unknown predicate %r (normalized: %r)", pred, canonical)
            continue

        valid.append(
            {"subject": subj, "predicate": canonical, "object": obj, "topic": topic}
        )
    return valid


def _parse_json_fallback(text: str) -> Any:
    """Minimal JSON parser for LLM output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find array or object in text
        for start_char, end_char in [("[", "]"), ("{", "}")]:
            start = text.find(start_char)
            end = text.rfind(end_char) + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    continue
    return []


async def extract_triples(
    content: str,
    *,
    llm_base_url: str = "http://localhost:4000/v1",
    llm_api_key: str = "sk-litellm-local-dev",
    model: str = "deepseek-v3",
    predicate_vocabulary: dict[str, list[str]] | None = None,
    predicate_hint: str = "Use all available predicates as appropriate.",
    max_triples: int = 5,
    timeout: float = 20.0,  # noqa: ASYNC109
    parse_json_fn: Callable[[str], Any] | None = None,
) -> list[dict[str, str]]:
    """Extract triples from text via LLM call.

    Args:
        content: Text to extract from.
        llm_base_url: LiteLLM or compatible API base URL.
        llm_api_key: API key for authentication.
        model: Model name to use.
        predicate_vocabulary: Custom vocabulary (defaults to shared).
        predicate_hint: Domain-specific extraction guidance.
        max_triples: Maximum triples per call.
        timeout: HTTP timeout in seconds.
        parse_json_fn: Custom JSON parser (defaults to built-in).

    Returns:
        List of validated triple dicts. Empty on failure.
    """
    system_prompt, user_prompt = build_extraction_prompt(
        content,
        predicate_vocabulary=predicate_vocabulary,
        predicate_hint=predicate_hint,
        max_triples=max_triples,
    )

    try:
        headers = {"Authorization": f"Bearer {llm_api_key}"} if llm_api_key else {}
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{llm_base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 512,
                },
                headers=headers,
            )
            resp.raise_for_status()

        raw_text = resp.json()["choices"][0]["message"]["content"].strip()

        parser = parse_json_fn or _parse_json_fallback
        triples_raw = parser(raw_text)
        if not isinstance(triples_raw, list):
            triples_raw = []

        valid = validate_extracted_triples(triples_raw, predicate_vocabulary and
            {p for preds in predicate_vocabulary.values() for p in preds})

        logger.debug("extract_triples: %d valid / %d raw", len(valid), len(triples_raw))
        return valid

    except httpx.TimeoutException:
        logger.warning("Triple extraction timed out after %.1fs", timeout)
        return []
    except Exception:
        logger.warning("Triple extraction failed", exc_info=True)
        return []
