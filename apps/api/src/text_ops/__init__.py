"""text-ops — shared text operations: noise detection, normalization, similarity."""

from .merge import merge_content
from .noise import QUARANTINE_TAG, NoiseVerdict, check_noise, filter_results
from .normalize import (
    CurrencyNormalizer,
    DateNormalizer,
    DurationNormalizer,
    NormalizerOp,
    NormChange,
    NormContext,
    NormResult,
    ProportionNormalizer,
    preprocess_chinese,
)
from .overlap import jaccard_word_overlap
from .temporal import (
    TemporalIntent,
    TemporalNormalizer,
    normalize_temporal,
    normalize_temporal_range,
    resolve_temporal_intent,
)

__all__ = [
    "QUARANTINE_TAG",
    "CurrencyNormalizer",
    "DateNormalizer",
    "DurationNormalizer",
    "NoiseVerdict",
    "NormChange",
    "NormContext",
    "NormResult",
    "NormalizerOp",
    "ProportionNormalizer",
    "TemporalIntent",
    "TemporalNormalizer",
    "check_noise",
    "filter_results",
    "jaccard_word_overlap",
    "merge_content",
    "normalize_temporal",
    "normalize_temporal_range",
    "preprocess_chinese",
    "resolve_temporal_intent",
]
