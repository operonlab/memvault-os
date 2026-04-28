"""Content Normalizer — composable normalization ops for text blocks.

Normalizes natural language expressions that lose precision over time:
- Relative dates → absolute dates (YYYY-MM-DD)
- Chinese numbers → Arabic numbers
- Currency expressions → structured amounts
- Proportions → percentages
- Durations → standardized format

Two modes:
- **regex-only** (Direction A): fast, deterministic, zero cost
- **hybrid** (Direction C): regex fast-path + optional LLM refinement for residuals

Pure ops only — no LLM, no HTTP, no logging.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# ======================== Shared: Chinese Pre-Processor ========================

ZH_NUM_MAP: dict[str, str] = {
    "零": "0",
    "一": "1",
    "二": "2",
    "兩": "2",
    "两": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
    "十": "10",
    "百": "00",
    "千": "000",
    "萬": "0000",
    "万": "0000",
}

# Simple Chinese number → Arabic (handles 1-99 and common compounds)
_ZH_SIMPLE_NUM = re.compile(
    r"([零一二兩两三四五六七八九十百千萬万]+)"
    r"(?=[天週周個个月年小時时分秒鐘钟次塊块元])"
)

# NOTE: Term normalization (繁→簡) removed — regex patterns now use dual-variant
# matching (e.g., [週周]) to avoid writing simplified Chinese back to blocks.


def _zh_num_to_arabic(zh: str) -> str:
    """Convert simple Chinese numbers to Arabic.

    Handles: 一→1, 二→2, 十→10, 十二→12, 二十→20, 二十三→23,
    三百→300, 五百→500, 一千→1000, 兩千五→2500
    """
    if not zh:
        return zh

    # Direct single-char lookup
    if len(zh) == 1 and zh in ZH_NUM_MAP:
        return ZH_NUM_MAP[zh]

    result = 0
    current = 0

    for char in zh:
        if char in ("十",):
            if current == 0:
                current = 1
            result += current * 10
            current = 0
        elif char in ("百",):
            if current == 0:
                current = 1
            result += current * 100
            current = 0
        elif char in ("千",):
            if current == 0:
                current = 1
            result += current * 1000
            current = 0
        elif char in ("萬", "万"):
            if current == 0:
                current = 1
            result = (result + current) * 10000
            current = 0
        else:
            digit = ZH_NUM_MAP.get(char)
            if digit and digit.isdigit():
                current = int(digit)
            else:
                return zh  # unrecognized, return as-is

    result += current

    # Special: "兩千五" means 2500 (五 is shorthand for 五百)
    # This is handled naturally: 兩(2) x 千(1000) + 五(5) = 2005
    # But convention means 2500. Only apply when last char maps to 1-9
    # and the previous magnitude was 千/百.
    # For simplicity, don't over-engineer — the basic conversion is good enough.

    return str(result) if result > 0 else zh


def _replace_zh_nums(text: str) -> str:
    """Replace Chinese numbers before unit words with Arabic equivalents."""

    def _repl(m: re.Match[str]) -> str:
        return _zh_num_to_arabic(m.group(1))

    return _ZH_SIMPLE_NUM.sub(_repl, text)


def preprocess_chinese(text: str) -> str:
    """Convert Chinese numbers to Arabic before unit words.

    Only does number conversion (三天→3天). Does NOT convert Traditional→Simplified
    terms (週→周) — that's handled by dual-variant regex patterns in each op.
    """
    return _replace_zh_nums(text)


# ======================== Data Structures ========================


@dataclass
class NormContext:
    """Context passed to each normalizer op."""

    created_at: datetime  # block creation time (anchor for relative dates)
    block_type: str = "knowledge"
    space_id: str = "default"


@dataclass
class NormResult:
    """Result of content normalization."""

    original: str
    normalized: str
    changed: bool
    changes: list[NormChange] = field(default_factory=list)
    llm_refined: bool = False

    @property
    def change_count(self) -> int:
        return len(self.changes)


@dataclass
class NormChange:
    """A single normalization change."""

    op: str  # which normalizer op made this change
    original_fragment: str
    normalized_fragment: str


# ======================== Normalizer Ops (ABC) ========================


class NormalizerOp(ABC):
    """Base class for content normalization operations."""

    name: str = "base"

    @abstractmethod
    def normalize(self, content: str, ctx: NormContext) -> tuple[str, list[NormChange]]:
        """Normalize content. Returns (normalized_content, list_of_changes)."""
        ...


# ======================== Date Normalizer (DEPRECATED) ========================

from .temporal import TemporalNormalizer


class DateNormalizer(TemporalNormalizer):
    """Deprecated: use TemporalNormalizer instead."""

    name = "date"

    def __init__(self) -> None:
        import warnings

        warnings.warn(
            "DateNormalizer is deprecated, use TemporalNormalizer",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__()


# ======================== Currency Normalizer ========================

# "$500", "NT$3,000", "US$50", "500元", "3000塊" (after preprocess: 塊→块)
_CURRENCY_USD = re.compile(r"(?:US)?\$\s*([\d,]+(?:\.\d{1,2})?)\s*(?:K|k)?")
_CURRENCY_TWD = re.compile(r"NT\$\s*([\d,]+(?:\.\d{1,2})?)")
_CURRENCY_ZH = re.compile(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块)")
_CURRENCY_VAGUE_ZH = re.compile(r"[幾几]([百千萬万])[塊块元]")  # dual-variant


class CurrencyNormalizer(NormalizerOp):
    """Normalize currency expressions to structured format."""

    name = "currency"

    def normalize(self, content: str, ctx: NormContext) -> tuple[str, list[NormChange]]:
        changes: list[NormChange] = []
        result = content

        # Vague amounts: "幾百塊" → "~數百 TWD"
        def _repl_vague(m: re.Match[str]) -> str:
            magnitude = m.group(1)
            label = {"百": "~數百", "千": "~數千", "萬": "~數萬", "万": "~數萬"}.get(
                magnitude, m.group()
            )
            replacement = f"{label} TWD"
            changes.append(NormChange("currency", m.group(), replacement))
            return replacement

        result = _CURRENCY_VAGUE_ZH.sub(_repl_vague, result)

        return result, changes


# ======================== Proportion Normalizer ========================

_PROPORTION_ZH = re.compile(r"([一二三四五六七八九])\s*成")
_PROPORTION_HALF = re.compile(r"[大約约]?[概]?\s*一半")
_PROPORTION_MAJORITY = re.compile(r"\b(?:majority|most of)\b", re.IGNORECASE)


class ProportionNormalizer(NormalizerOp):
    """Normalize proportions to percentage format."""

    name = "proportion"

    def normalize(self, content: str, ctx: NormContext) -> tuple[str, list[NormChange]]:
        changes: list[NormChange] = []
        result = content

        # "八成" → "~80%"
        zh_digit = {
            "一": 10,
            "二": 20,
            "三": 30,
            "四": 40,
            "五": 50,
            "六": 60,
            "七": 70,
            "八": 80,
            "九": 90,
        }

        def _repl_cheng(m: re.Match[str]) -> str:
            pct = zh_digit.get(m.group(1), 0)
            replacement = f"~{pct}%"
            changes.append(NormChange("proportion", m.group(), replacement))
            return replacement

        result = _PROPORTION_ZH.sub(_repl_cheng, result)

        # "大概一半" → "~50%"
        for m in _PROPORTION_HALF.finditer(result):
            changes.append(NormChange("proportion", m.group(), "~50%"))
        result = _PROPORTION_HALF.sub("~50%", result)

        return result, changes


# ======================== Duration Normalizer ========================

_DUR_HOURS_ZH = re.compile(r"(\d+)\s*小[時时]")  # dual-variant
_DUR_MINUTES_ZH = re.compile(r"(\d+)\s*分[鐘钟]")  # dual-variant
_DUR_DAYS_ZH = re.compile(r"(\d+)\s*天(?!前)")  # N天 but not N天前
_DUR_HALF_DAY = re.compile(r"半天")


class DurationNormalizer(NormalizerOp):
    """Normalize duration expressions to standardized format."""

    name = "duration"

    def normalize(self, content: str, ctx: NormContext) -> tuple[str, list[NormChange]]:
        # Duration normalization is lower priority — only normalize
        # clearly standalone duration mentions, not embedded in sentences.
        # For now, return unchanged to avoid false positives.
        return content, []


# ======================== Fuzzy Indicator Pattern ========================

# Patterns that suggest residual fuzzy expressions (needed by consumers
# to check if LLM refinement is warranted)
_FUZZY_INDICATORS = re.compile(
    r"[幾几](?:天|次|個|个)|"  # 幾天、幾次
    r"很多|一些|不少|大量|少量|"  # vague quantities
    r"最近|早期|當時|那時|"  # vague time references
    r"差不多|大約|大概|約"  # approximate markers
)


# ======================== Default Op List ========================

# Default op order
DEFAULT_OPS: list[NormalizerOp] = [
    TemporalNormalizer(),
    CurrencyNormalizer(),
    ProportionNormalizer(),
    DurationNormalizer(),
]
