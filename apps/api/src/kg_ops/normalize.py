"""Entity text normalization — deterministic, zero cost.

Extracted from memvault/entity_resolution.py. CJK detection inlined
to avoid dependency on core/src/shared/text_utils.
"""

from __future__ import annotations

import re
import unicodedata

# CJK Unicode ranges (CJK Unified + Extension A + Punctuation + Kana + Fullwidth + Hangul)
_CJK_PATTERN = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f"
    r"\u30a0-\u30ff\uff00-\uffef\uac00-\ud7af\uf900-\ufaff]"
)

_TRAILING_PUNCT = ".,;:!?\u3002\u3001\uff1b\uff1a\uff01\uff1f"


def is_cjk(text: str) -> bool:
    """Check if text contains any CJK characters."""
    return bool(_CJK_PATTERN.search(text))


def normalize_entity_text(text: str) -> str:
    """Deterministic normalization — zero cost, run on every ingest.

    1. Unicode NFC (handles full/half-width CJK)
    2. Strip + collapse whitespace
    3. Case-fold for non-CJK
    4. Strip trailing punctuation
    """
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\s+", " ", text.strip())
    if not is_cjk(text):
        text = text.lower()
    text = text.rstrip(_TRAILING_PUNCT)
    return text
