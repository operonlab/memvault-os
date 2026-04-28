"""Content Normalizer Pipeline — composable normalization for memory blocks.

Orchestrates normalization ops from text_ops with optional LLM refinement.

Usage:
    pipeline = ContentNormalizerPipeline()  # regex-only
    pipeline = ContentNormalizerPipeline(llm_refinement=True)  # hybrid

    result = await pipeline.normalize(content, NormContext(created_at=...))
"""

from __future__ import annotations

import logging

import httpx

from text_ops import (
    TemporalNormalizer,
    CurrencyNormalizer,
    DurationNormalizer,
    NormChange,
    NormContext,
    NormResult,
    NormalizerOp,
    ProportionNormalizer,
    preprocess_chinese,
)
from text_ops.normalize import _FUZZY_INDICATORS

logger = logging.getLogger(__name__)

# Re-export for backward compatibility (dream.py imports from here)
__all__ = ["ContentNormalizerPipeline", "NormContext", "NormResult"]

# --- LLM Refinement Config ---
_LLM_URL = "http://localhost:4000/v1/chat/completions"
_LLM_API_KEY = "sk-litellm-local-dev"  # nosec — local dev proxy key
_LLM_MODEL = "gemini-2.5-flash"
_LLM_TIMEOUT = 15


async def _llm_refine(content: str, ctx: NormContext) -> tuple[str, bool]:
    """Use LLM to normalize residual fuzzy expressions.

    Returns (normalized_content, was_refined).
    Only called when fuzzy indicators are detected after regex ops.
    """
    system_prompt = (
        "You are a content normalizer for a personal knowledge management system. "
        "Normalize relative/vague expressions to precise values where possible.\n\n"
        "Rules:\n"
        "- Relative dates → YYYY-MM-DD (reference: {ref_date})\n"
        "- Vague quantities (幾個, 一些) → keep as-is if truly unknown\n"
        "- Vague time (最近, 當時) → keep as-is (insufficient context)\n"
        "- 大約/大概 + number → add ~ prefix to the number\n"
        "- Do NOT change anything you're unsure about\n"
        "- Return ONLY the normalized text, nothing else\n"
        "- If nothing needs changing, return the original text exactly"
    ).format(ref_date=ctx.created_at.strftime("%Y-%m-%d"))

    payload = {
        "model": _LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "temperature": 0.0,
        "max_tokens": len(content) + 200,
    }

    try:
        headers = {
            "Authorization": f"Bearer {_LLM_API_KEY}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
            resp = await client.post(_LLM_URL, json=payload, headers=headers)
            resp.raise_for_status()

        data = resp.json()
        normalized: str = data["choices"][0]["message"]["content"].strip()

        # Safety: reject if LLM output is drastically different (>30% length change)
        len_ratio = len(normalized) / max(len(content), 1)
        if len_ratio < 0.7 or len_ratio > 1.3:
            logger.warning("normalizer.llm: output length ratio %.2f, rejecting", len_ratio)
            return content, False

        return normalized, normalized != content

    except Exception as e:
        logger.debug("normalizer.llm: refinement failed: %s", e)
        return content, False


# ======================== Pipeline ========================

# Default op order
DEFAULT_OPS: list[NormalizerOp] = [
    TemporalNormalizer(),
    CurrencyNormalizer(),
    ProportionNormalizer(),
    DurationNormalizer(),
]


class ContentNormalizerPipeline:
    """Composable content normalization pipeline.

    Args:
        ops: List of NormalizerOps to run. Defaults to all built-in ops.
        llm_refinement: If True, run LLM refinement on residual fuzzy expressions.
        preprocess_chinese: If True, run Chinese term normalization before ops.
    """

    def __init__(
        self,
        ops: list[NormalizerOp] | None = None,
        llm_refinement: bool = False,
        enable_chinese_preprocess: bool = True,
    ):
        self.ops = ops or list(DEFAULT_OPS)
        self.llm_refinement = llm_refinement
        self.enable_chinese_preprocess = enable_chinese_preprocess

    async def normalize(self, content: str, ctx: NormContext) -> NormResult:
        """Run the full normalization pipeline."""
        if not content or not content.strip():
            return NormResult(original=content, normalized=content, changed=False)

        working = content
        all_changes: list[NormChange] = []

        # Stage 0: Chinese pre-processing
        if self.enable_chinese_preprocess:
            working = preprocess_chinese(working)

        # Stage 1: Regex ops (Direction A)
        for op in self.ops:
            try:
                working, changes = op.normalize(working, ctx)
                all_changes.extend(changes)
            except Exception as e:
                logger.warning("normalizer.%s failed: %s", op.name, e)

        # Stage 2: Check for residual fuzzy expressions
        llm_refined = False
        if self.llm_refinement and _FUZZY_INDICATORS.search(working):
            # Stage 3: LLM refinement
            try:
                working, llm_refined = await _llm_refine(working, ctx)
                if llm_refined:
                    all_changes.append(
                        NormChange("llm_refine", "(residual fuzzy)", "(llm normalized)")
                    )
            except Exception as e:
                logger.warning("normalizer.llm_refine failed: %s", e)

        has_changes = len(all_changes) > 0
        return NormResult(
            original=content,
            normalized=working if has_changes else content,
            changed=has_changes,
            changes=all_changes,
            llm_refined=llm_refined,
        )
