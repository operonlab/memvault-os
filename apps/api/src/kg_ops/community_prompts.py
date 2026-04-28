"""Community summary prompt templates.

Extracted from mcp/memvault/pipelines/community_summary_pipeline.py.
Language-parameterized for cross-module use.
"""

from __future__ import annotations

# Max triples to include in the LLM prompt per community
MAX_TRIPLES_IN_PROMPT = 40

# Default summary prompt (Traditional Chinese)
SUMMARY_PROMPT_ZH = (
    "以下是屬於同一知識社群的三元組。"
    "請用繁體中文總結這個社群的主題(50-100字),"
    "並列出 2-4 個核心發現。"
    '輸出 JSON: {"summary": "...", "key_findings": ["..."]}'
)

SUMMARY_PROMPT_EN = (
    "The following triples belong to the same knowledge community. "
    "Summarize the community's theme in 50-100 words and list 2-4 key findings. "
    'Output JSON: {"summary": "...", "key_findings": ["..."]}'
)


def build_triple_text(
    triples: list[dict],
    max_triples: int = MAX_TRIPLES_IN_PROMPT,
    subject_key: str = "subject",
    predicate_key: str = "predicate",
    object_key: str = "object",
) -> str:
    """Format triples as human-readable lines for LLM prompt.

    Handles both naming conventions:
      memvault: {"s": ..., "p": ..., "o": ...}
      docvault: {"subject": ..., "predicate": ..., "object": ...}
    """
    lines = []
    for t in triples[:max_triples]:
        s = t.get(subject_key, t.get("s", ""))
        p = t.get(predicate_key, t.get("p", ""))
        o = t.get(object_key, t.get("o", ""))
        if s and p and o:
            lines.append(f"- {s} → {p} → {o}")
    return "\n".join(lines)


def build_community_summary_messages(
    triples_text: str,
    language: str = "zh-TW",
) -> list[dict[str, str]]:
    """Build LLM messages array for community summary generation.

    Args:
        triples_text: Output of build_triple_text().
        language: "zh-TW" for Traditional Chinese, "en" for English.

    Returns:
        List of message dicts ready for chat completions API.
    """
    prompt = SUMMARY_PROMPT_ZH if language.startswith("zh") else SUMMARY_PROMPT_EN
    return [
        {"role": "user", "content": f"{triples_text}\n\n{prompt}"},
    ]
