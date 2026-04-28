"""Temporal Normalizer — replaces relative time expressions with absolute dates.

10-pass architecture (zero external dependencies, stdlib only):
  Pass 0: Simplified Chinese → Traditional Chinese (temporal keywords only)
  Pass 0.5: Chinese number → Arabic (八天→8天)
  Pass 1: Special day keywords (今天, 昨天, 大後天, …)
  Pass 2: Prefix + weekday (上週一, 下禮拜五, last Monday, …)
  Pass 3: N units ago/later (3天前, 2週後, 3個星期前, 5 days ago, …)
  Pass 4: Relative period (上個月, 下週, 去年, …)
  Pass 5: Month + day combo (上個月3號, 下個月15日, …)
  Pass 5.5: Year + month combo (去年三月, 明年十二月, …)
  Pass 6: Boundary keywords (月底, 年底, 上半年, 下半年, 上一季, …)
  Pass 7: Double relative (上上週, 上上星期, 上上禮拜, 前年, 後年)

IMPORTANT: Pass 7 and longer patterns run BEFORE shorter ones to avoid
partial matches ("上上週" must not be consumed by "上週").
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .normalize import NormalizerOp, NormChange, NormContext

# ======================== Simplified→Traditional mapping (temporal only) ========================

# str.maketrans for pass-0 conversion: maps simplified chars to traditional
_S2T = str.maketrans(
    {
        # NOTE: "周" intentionally excluded from full-text translate — it's a
        # very common surname (周建宏/周杰倫). Pass 0.6 below handles the
        # context-bound 周→週 conversion (週末/週年/週一..週日/上下這本周).
        "个": "個",
        "后": "後",
        "点": "點",
        "时": "時",
        "钟": "鐘",
        "礼": "禮",
        "这": "這",
    }
)

# Context-aware 周→週 conversion. Only fires when 周 sits in an unambiguously
# temporal position. Avoids destroying surnames like 周建宏/周杰倫.
_PASS06_ZHOU_PATTERNS = [
    # Weekday: 周一/周二/.../周日/周天
    (re.compile(r"周(?=[一二三四五六日天])"), "週"),
    # Period prefixes: 上周/下周/這周/本周/每周/前周/後周/这周
    (re.compile(r"(?<=[上下這本每前後这])周"), "週"),
    # Compound nouns: 周末/周年
    (re.compile(r"周(?=[末年])"), "週"),
    # N 周前/N 周後 (after Pass 0.5 turned ZH numbers to Arabic)
    (re.compile(r"(?<=\d)\s*周(?=[前後])"), "週"),
    # 個周 (e.g., "兩個周末" → after pass 0.5, "2個周末")
    (re.compile(r"(?<=個)周(?=[末年])"), "週"),
]


def _apply_pass06_zhou(text: str) -> str:
    for pat, repl in _PASS06_ZHOU_PATTERNS:
        text = pat.sub(repl, text)
    return text

# ======================== Core lookup tables ========================

WEEKDAY_MAP: dict[str, int] = {
    "週一": 1,
    "周一": 1,
    "星期一": 1,
    "禮拜一": 1,
    "礼拜一": 1,
    "週二": 2,
    "周二": 2,
    "星期二": 2,
    "禮拜二": 2,
    "礼拜二": 2,
    "週三": 3,
    "周三": 3,
    "星期三": 3,
    "禮拜三": 3,
    "礼拜三": 3,
    "週四": 4,
    "周四": 4,
    "星期四": 4,
    "禮拜四": 4,
    "礼拜四": 4,
    "週五": 5,
    "周五": 5,
    "星期五": 5,
    "禮拜五": 5,
    "礼拜五": 5,
    "週六": 6,
    "周六": 6,
    "星期六": 6,
    "禮拜六": 6,
    "礼拜六": 6,
    "週日": 7,
    "周日": 7,
    "星期日": 7,
    "星期天": 7,
    "週天": 7,
    "周天": 7,
    "禮拜日": 7,
    "禮拜天": 7,
    "礼拜日": 7,
    "礼拜天": 7,
}

# Ordered so longer keys appear before shorter keys (大後天 before 後天, etc.)
SPECIAL_DAY_SWIFT: dict[str, int] = {
    # English (longer first)
    "the day before yesterday": -2,
    "the day after tomorrow": 2,
    "yesterday": -1,
    "tomorrow": 1,
    "today": 0,
    # Chinese (longer first)
    "大後天": 3,
    "大前天": -3,
    "大后天": 3,
    "後天": 2,
    "前天": -2,
    "后天": 2,
    "明天": 1,
    "明日": 1,
    "今天": 0,
    "今日": 0,
    "昨天": -1,
    "昨日": -1,
}

# ======================== Weekday helpers ========================


def _this_weekday(ref: datetime, wd: int) -> datetime:
    """Same-week date for the given isoweekday (1=Mon … 7=Sun)."""
    return ref + timedelta(days=wd - ref.isoweekday())


def _next_weekday(ref: datetime, wd: int) -> datetime:
    return _this_weekday(ref, wd) + timedelta(weeks=1)


def _last_weekday(ref: datetime, wd: int) -> datetime:
    return _this_weekday(ref, wd) - timedelta(weeks=1)


# ======================== TemporalIntent (LLM interface schema) ========================


@dataclass
class TemporalIntent:
    """Structured representation of a temporal expression for LLM handoff."""

    type: str  # "relative", "absolute", "recurring", "vague"
    direction: str | None = None  # "past", "future"
    unit: str | None = None  # "day", "week", "month", "year", "hour", "minute", "second"
    quantity: int | None = None
    weekday: int | None = None  # 1-7 (isoweekday)
    day_of_month: int | None = None
    time_of_day: str | None = None  # e.g. "15:00"
    period: str | None = None  # "morning", "afternoon", "evening"
    confidence: float = 0.0


def resolve_temporal_intent(intent: TemporalIntent, ref: datetime) -> datetime | None:
    """Pure function: resolve a TemporalIntent to an absolute datetime.

    Returns None when the intent is too vague to produce a deterministic result.
    """
    if intent.type == "absolute":
        return None  # already absolute — caller should parse directly

    if intent.type == "vague":
        return None

    if intent.type == "relative":
        if intent.unit is None or intent.quantity is None:
            return None
        qty = intent.quantity
        sign = -1 if intent.direction == "past" else 1
        if intent.unit == "second":
            return ref + timedelta(seconds=sign * qty)
        if intent.unit == "minute":
            return ref + timedelta(minutes=sign * qty)
        if intent.unit == "hour":
            return ref + timedelta(hours=sign * qty)
        if intent.unit == "day":
            return ref + timedelta(days=sign * qty)
        if intent.unit == "week":
            return ref + timedelta(weeks=sign * qty)
        if intent.unit == "month":
            return ref + timedelta(days=sign * qty * 30)
        if intent.unit == "year":
            return ref + timedelta(days=sign * qty * 365)
        if intent.unit == "weekday" and intent.weekday is not None:
            if intent.direction == "past":
                return _last_weekday(ref, intent.weekday)
            return _next_weekday(ref, intent.weekday)
        return None

    if intent.type == "recurring":
        # Recurring: resolve to next occurrence
        if intent.weekday is not None:
            return _next_weekday(ref, intent.weekday)
        if intent.day_of_month is not None:
            candidate = ref.replace(day=intent.day_of_month)
            if candidate <= ref:
                # Push to next month
                year = ref.year + (ref.month // 12)
                month = (ref.month % 12) + 1
                max_day = calendar.monthrange(year, month)[1]
                day = min(intent.day_of_month, max_day)
                return ref.replace(year=year, month=month, day=day)
            return candidate
        return None

    return None


# ======================== TemporalNormalizer ========================

# Build weekday alternation for regex (longest keys first to avoid partial match)
_WD_ALTS = sorted(WEEKDAY_MAP.keys(), key=len, reverse=True)
_WD_PATTERN = "(?:" + "|".join(re.escape(k) for k in _WD_ALTS) + ")"


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _fmt_datetime(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


class TemporalNormalizer(NormalizerOp):
    """Replace relative temporal expressions with absolute dates (YYYY-MM-DD).

    All 7 passes are applied in order. Pass 0 normalises Simplified→Traditional
    in-memory only; the result is used for matching but the *original* fragments
    are tracked in NormChange so callers can correlate back to the source.
    """

    name = "temporal"

    # ---- compiled patterns (class-level, built once) ----

    # Pass 2: prefix + weekday (Chinese)
    _P2_LAST = re.compile(r"(上一?[個个]?|上)(的)?" + _WD_PATTERN)
    _P2_NEXT = re.compile(r"(下一?[個个]?|下)(的)?" + _WD_PATTERN)
    _P2_THIS = re.compile(r"(這一?[個个]?|這|本)(的)?" + _WD_PATTERN)
    # Pass 2: prefix + weekday (English)
    _EN_WEEKDAYS = r"(?P<ewd>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
    _P2_EN_LAST = re.compile(r"\blast\s+" + _EN_WEEKDAYS + r"\b", re.IGNORECASE)
    _P2_EN_NEXT = re.compile(r"\bnext\s+" + _EN_WEEKDAYS + r"\b", re.IGNORECASE)
    _P2_EN_THIS = re.compile(r"\bthis\s+" + _EN_WEEKDAYS + r"\b", re.IGNORECASE)

    # Pass 3: N units ago/later — Chinese
    # Direction suffixes: 前/之前/以前 = past, 後/之後/以後 = future
    _DIR_SUFFIX = r"(?:之?[前後后]|以[前後后])"
    _P3_DAYS_ZH = re.compile(r"(\d+)\s*天(" + _DIR_SUFFIX + r")")
    _P3_WEEKS_ZH = re.compile(r"(\d+)\s*(?:[週周]|個?(?:星期|禮拜))(" + _DIR_SUFFIX + r")")
    _P3_MONTHS_ZH = re.compile(r"(\d+)\s*[個个]月(" + _DIR_SUFFIX + r")")
    _P3_YEARS_ZH = re.compile(r"(\d+)\s*年(" + _DIR_SUFFIX + r")")
    _P3_HOURS_ZH = re.compile(r"(\d+)\s*小[時时](" + _DIR_SUFFIX + r")")
    _P3_MINUTES_ZH = re.compile(r"(\d+)\s*分[鐘钟](" + _DIR_SUFFIX + r")")
    _P3_SECONDS_ZH = re.compile(r"(\d+)\s*秒(" + _DIR_SUFFIX + r")")
    # Pass 3: half-year (半年前/後)
    _P3_HALF_YEAR = re.compile(r"半年(" + _DIR_SUFFIX + r")")
    # Pass 3: N年半 (一年半前)
    _P3_YEAR_HALF = re.compile(r"(\d+)年半(" + _DIR_SUFFIX + r")")
    # Pass 3: English
    _P3_DAYS_EN_AGO = re.compile(r"\b(\d+)\s*days?\s*ago\b", re.IGNORECASE)
    _P3_WEEKS_EN_AGO = re.compile(r"\b(\d+)\s*weeks?\s*ago\b", re.IGNORECASE)
    _P3_MONTHS_EN_AGO = re.compile(r"\b(\d+)\s*months?\s*ago\b", re.IGNORECASE)
    _P3_HOURS_EN_AGO = re.compile(r"\b(\d+)\s*hours?\s*ago\b", re.IGNORECASE)
    _P3_IN_DAYS_EN = re.compile(r"\bin\s+(\d+)\s*days?\b", re.IGNORECASE)

    # Pass 4: relative period (LONGER matches before shorter, handled by order)
    # Ordered dict ensures上上週/下下月 are caught in Pass 7 first
    _P4_PATTERNS: list[tuple[re.Pattern[str], int, str]] = [
        (re.compile(r"上[個个]?月"), -30, "date"),
        (re.compile(r"下[個个]?月"), 30, "date"),
        (re.compile(r"上[週周]"), -7, "date"),
        (re.compile(r"下[週周]"), 7, "date"),
        (re.compile(r"去年"), -365, "date"),
        (re.compile(r"明年"), 365, "date"),
        (re.compile(r"今年"), 0, "year_start"),
        (re.compile(r"本[月]"), 0, "month_start"),
        (re.compile(r"本[週周]"), 0, "date"),
        (re.compile(r"這[週周]"), 0, "date"),
        (re.compile(r"\blast\s+week\b", re.IGNORECASE), -7, "date"),
        (re.compile(r"\bnext\s+week\b", re.IGNORECASE), 7, "date"),
        (re.compile(r"\blast\s+month\b", re.IGNORECASE), -30, "date"),
        (re.compile(r"\bnext\s+month\b", re.IGNORECASE), 30, "date"),
        (re.compile(r"\blast\s+year\b", re.IGNORECASE), -365, "date"),
        (re.compile(r"\bnext\s+year\b", re.IGNORECASE), 365, "date"),
    ]

    # Pass 5: month + specific day combo
    _P5_PATTERN = re.compile(r"(上|下|這|本)[個个]?月(\d{1,2})[號号日]?")

    # Pass 6: boundary keywords (optional prefix consumed to avoid leftovers)
    _P6_PATTERNS: list[tuple[re.Pattern[str], str]] = [
        (re.compile(r"(?:[本這这])?月底"), "month_end"),
        (re.compile(r"(?:[本這这今])?年底"), "year_end"),
        (re.compile(r"(?:[本這这])?月初"), "month_start"),
        (re.compile(r"(?:[本這这今])?年初"), "year_start"),
        (re.compile(r"上半年"), "first_half"),
        (re.compile(r"下半年"), "second_half"),
        (re.compile(r"上一?季"), "last_quarter"),
        (re.compile(r"下一?季"), "next_quarter"),
        (re.compile(r"這一?季|本季"), "this_quarter"),
    ]
    # Pass 6 extended: year-scoped boundary (去年年底, 今年年初, 明年年底)
    _P6_YEAR_BOUNDARY = re.compile(r"(去年|今年|明年|前年)(年底|年初)")

    # Pass 7: double relative (run BEFORE pass 4)
    # 週/周/星期/禮拜 all mean "week"
    _WEEK_UNIT = r"(?:[週周]|星期|禮拜)"
    _P7_PATTERNS: list[tuple[re.Pattern[str], int, str]] = [
        (re.compile(r"上上[個个]?" + _WEEK_UNIT), -14, "date"),
        (re.compile(r"下下[個个]?" + _WEEK_UNIT), 14, "date"),
        (re.compile(r"上上[個个]?月"), -60, "date"),
        (re.compile(r"下下[個个]?月"), 60, "date"),
        (re.compile(r"前年"), -730, "date"),
        (re.compile(r"[後后]年"), 730, "date"),
    ]

    # Pass 5.5: year + month combo (去年三月, 明年十二月)
    _MONTH_ZH_MAP: dict[str, int] = {
        "一月": 1,
        "二月": 2,
        "三月": 3,
        "四月": 4,
        "五月": 5,
        "六月": 6,
        "七月": 7,
        "八月": 8,
        "九月": 9,
        "十月": 10,
        "十一月": 11,
        "十二月": 12,
        "1月": 1,
        "2月": 2,
        "3月": 3,
        "4月": 4,
        "5月": 5,
        "6月": 6,
        "7月": 7,
        "8月": 8,
        "9月": 9,
        "10月": 10,
        "11月": 11,
        "12月": 12,
    }
    _MONTH_ALTS = "|".join(sorted(_MONTH_ZH_MAP.keys(), key=len, reverse=True))
    _P55_YEAR_MONTH = re.compile(r"(前年|去年|今年|明年|[後后]年)(?:的)?(" + _MONTH_ALTS + r")")

    def normalize(self, content: str, ctx: NormContext) -> tuple[str, list[NormChange]]:
        changes: list[NormChange] = []
        ref = ctx.created_at

        # ---- Pass 0: Simplified→Traditional (in-memory only) ----
        normalised = content.translate(_S2T)

        # ---- Pass 0.5: Chinese number → Arabic (八天→8天) ----
        from .normalize import preprocess_chinese

        normalised = preprocess_chinese(normalised)

        # ---- Pass 0.6: context-bound 周→週 (preserves surnames like 周建宏) ----
        normalised = _apply_pass06_zhou(normalised)

        # ---- Pass 0.7: Week synonym normalization ----
        # "上禮拜"/"下禮拜"/"這禮拜"/"本禮拜" (standalone) → 上週/下週/這週/本週
        # Skip when followed by weekday char (一二三四五六日天) so Pass 2 can still
        # match patterns like "上禮拜三".
        normalised = re.sub(
            r"([上下這本])禮拜(?![一二三四五六日天])",
            r"\1週",
            normalised,
        )

        # ---- Pass 7: double relative (before pass 4 to prevent partial match) ----
        normalised = self._pass7(normalised, ref, changes, content)

        # ---- Pass 1: special day keywords ----
        normalised = self._pass1(normalised, ref, changes)

        # ---- Pass 2: prefix + weekday ----
        normalised = self._pass2(normalised, ref, changes)

        # ---- Pass 3: N units ago/later ----
        normalised = self._pass3(normalised, ref, changes)

        # ---- Pass 6: boundary keywords (before pass 4 to avoid 月底 partial) ----
        normalised = self._pass6(normalised, ref, changes)

        # ---- Pass 5.5: year + month combo (before pass 5) ----
        normalised = self._pass55(normalised, ref, changes)

        # ---- Pass 5: month + specific day (before pass 4, more specific) ----
        normalised = self._pass5(normalised, ref, changes)

        # ---- Pass 4: relative period ----
        normalised = self._pass4(normalised, ref, changes)

        return normalised, changes

    # ---- pass implementations ----

    def _pass1(self, text: str, ref: datetime, changes: list[NormChange]) -> str:
        # Longer keys first (大後天 before 後天, "the day before yesterday" before "yesterday")
        for key, swift in SPECIAL_DAY_SWIFT.items():
            if key not in text.lower() if key.isascii() else key not in text:
                continue
            target = _fmt_date(ref + timedelta(days=swift))

            def _repl(m: re.Match[str], t: str = target, k: str = key) -> str:
                changes.append(NormChange("temporal", k, t))
                return t

            # English keys need word boundaries + case-insensitive
            if key.isascii():
                pattern = r"\b" + re.escape(key) + r"\b"
                text = re.sub(pattern, _repl, text, flags=re.IGNORECASE)
            else:
                text = re.sub(re.escape(key), _repl, text)
        return text

    def _pass2(self, text: str, ref: datetime, changes: list[NormChange]) -> str:
        def _make_repl(calc_fn: object) -> object:
            def _repl(m: re.Match[str]) -> str:
                wd_str = _extract_weekday(m.group(0))
                if wd_str is None:
                    return m.group(0)
                wd = WEEKDAY_MAP[wd_str]
                # calc_fn is one of _last_weekday / _next_weekday / _this_weekday
                dt = calc_fn(ref, wd)  # type: ignore[call-arg]
                target = _fmt_date(dt)
                changes.append(NormChange("temporal", m.group(0), target))
                return target

            return _repl

        text = self._P2_LAST.sub(_make_repl(_last_weekday), text)
        text = self._P2_NEXT.sub(_make_repl(_next_weekday), text)
        text = self._P2_THIS.sub(_make_repl(_this_weekday), text)

        # English prefix + weekday
        _EN_WD = {
            "monday": 1,
            "tuesday": 2,
            "wednesday": 3,
            "thursday": 4,
            "friday": 5,
            "saturday": 6,
            "sunday": 7,
        }

        def _en_wd_repl(calc_fn: object):
            def _repl(m: re.Match[str]) -> str:
                wd = _EN_WD.get(m.group("ewd").lower())
                if wd is None:
                    return m.group(0)
                dt = calc_fn(ref, wd)  # type: ignore[call-arg]
                target = _fmt_date(dt)
                changes.append(NormChange("temporal", m.group(0), target))
                return target

            return _repl

        text = self._P2_EN_LAST.sub(_en_wd_repl(_last_weekday), text)
        text = self._P2_EN_NEXT.sub(_en_wd_repl(_next_weekday), text)
        text = self._P2_EN_THIS.sub(_en_wd_repl(_this_weekday), text)
        return text

    def _pass3(self, text: str, ref: datetime, changes: list[NormChange]) -> str:
        def _zh_dir(d: str) -> int:
            """Parse direction from suffix: 前/之前/以前 → -1, 後/之後/以後 → +1."""
            return -1 if "前" in d else 1

        def _repl_factory(unit: str, is_dt: bool = False):
            def _repl(m: re.Match[str]) -> str:
                n = int(m.group(1))
                sign = _zh_dir(m.group(2))
                if unit == "day":
                    dt = ref + timedelta(days=sign * n)
                elif unit == "week":
                    dt = ref + timedelta(weeks=sign * n)
                elif unit == "month":
                    dt = ref + timedelta(days=sign * n * 30)
                elif unit == "year":
                    dt = ref + timedelta(days=sign * n * 365)
                elif unit == "hour":
                    dt = ref + timedelta(hours=sign * n)
                    is_dt_ = True
                    target = _fmt_datetime(dt)
                    changes.append(NormChange("temporal", m.group(0), target))
                    return target
                elif unit == "minute":
                    dt = ref + timedelta(minutes=sign * n)
                    target = _fmt_datetime(dt)
                    changes.append(NormChange("temporal", m.group(0), target))
                    return target
                elif unit == "second":
                    dt = ref + timedelta(seconds=sign * n)
                    target = _fmt_datetime(dt)
                    changes.append(NormChange("temporal", m.group(0), target))
                    return target
                else:
                    return m.group(0)
                target = _fmt_datetime(dt) if is_dt else _fmt_date(dt)
                changes.append(NormChange("temporal", m.group(0), target))
                return target

            return _repl

        # N年半前/後 (must run before _P3_YEARS to avoid partial match)
        def _year_half_repl(m: re.Match[str]) -> str:
            n = int(m.group(1))
            sign = _zh_dir(m.group(2))
            dt = ref + timedelta(days=sign * (n * 365 + 182))
            target = _fmt_date(dt)
            changes.append(NormChange("temporal", m.group(0), target))
            return target

        text = self._P3_YEAR_HALF.sub(_year_half_repl, text)

        # 半年前/後
        def _half_year_repl(m: re.Match[str]) -> str:
            sign = _zh_dir(m.group(1))
            dt = ref + timedelta(days=sign * 182)
            target = _fmt_date(dt)
            changes.append(NormChange("temporal", m.group(0), target))
            return target

        text = self._P3_HALF_YEAR.sub(_half_year_repl, text)

        text = self._P3_DAYS_ZH.sub(_repl_factory("day"), text)
        text = self._P3_WEEKS_ZH.sub(_repl_factory("week"), text)
        text = self._P3_MONTHS_ZH.sub(_repl_factory("month"), text)
        text = self._P3_YEARS_ZH.sub(_repl_factory("year"), text)
        text = self._P3_HOURS_ZH.sub(_repl_factory("hour"), text)
        text = self._P3_MINUTES_ZH.sub(_repl_factory("minute"), text)
        text = self._P3_SECONDS_ZH.sub(_repl_factory("second"), text)

        # English
        def _en_ago_factory(unit: str):
            def _repl(m: re.Match[str]) -> str:
                n = int(m.group(1))
                if unit == "day":
                    dt = ref - timedelta(days=n)
                elif unit == "week":
                    dt = ref - timedelta(weeks=n)
                elif unit == "month":
                    dt = ref - timedelta(days=n * 30)
                elif unit == "hour":
                    dt = ref - timedelta(hours=n)
                    target = _fmt_datetime(dt)
                    changes.append(NormChange("temporal", m.group(0), target))
                    return target
                else:
                    return m.group(0)
                target = _fmt_date(dt)
                changes.append(NormChange("temporal", m.group(0), target))
                return target

            return _repl

        text = self._P3_DAYS_EN_AGO.sub(_en_ago_factory("day"), text)
        text = self._P3_WEEKS_EN_AGO.sub(_en_ago_factory("week"), text)
        text = self._P3_MONTHS_EN_AGO.sub(_en_ago_factory("month"), text)
        text = self._P3_HOURS_EN_AGO.sub(_en_ago_factory("hour"), text)

        def _in_days_repl(m: re.Match[str]) -> str:
            n = int(m.group(1))
            dt = ref + timedelta(days=n)
            target = _fmt_date(dt)
            changes.append(NormChange("temporal", m.group(0), target))
            return target

        text = self._P3_IN_DAYS_EN.sub(_in_days_repl, text)
        return text

    def _pass4(self, text: str, ref: datetime, changes: list[NormChange]) -> str:
        for pat, offset, kind in self._P4_PATTERNS:
            if kind == "year_start":
                target = ref.replace(month=1, day=1).strftime("%Y-%m-%d")
            elif kind == "month_start":
                target = ref.replace(day=1).strftime("%Y-%m-%d")
            else:
                target = _fmt_date(ref + timedelta(days=offset))

            def _repl(m: re.Match[str], t: str = target) -> str:
                changes.append(NormChange("temporal", m.group(0), t))
                return t

            text = pat.sub(_repl, text)
        return text

    def _pass5(self, text: str, ref: datetime, changes: list[NormChange]) -> str:
        def _repl(m: re.Match[str]) -> str:
            prefix = m.group(1)
            day = int(m.group(2))
            if prefix in ("上",):
                # last month
                month = ref.month - 1 if ref.month > 1 else 12
                year = ref.year if ref.month > 1 else ref.year - 1
            elif prefix in ("下",):
                month = ref.month + 1 if ref.month < 12 else 1
                year = ref.year if ref.month < 12 else ref.year + 1
            else:  # 這/本
                month = ref.month
                year = ref.year
            max_day = calendar.monthrange(year, month)[1]
            day = min(day, max_day)
            try:
                target = datetime(year, month, day).strftime("%Y-%m-%d")
            except ValueError:
                return m.group(0)
            changes.append(NormChange("temporal", m.group(0), target))
            return target

        return self._P5_PATTERN.sub(_repl, text)

    def _pass6(self, text: str, ref: datetime, changes: list[NormChange]) -> str:
        # Year-scoped boundary first (去年年底, 今年年初) — longer match
        def _year_boundary_repl(m: re.Match[str]) -> str:
            year_word, boundary = m.group(1), m.group(2)
            year_offsets = {"前年": -2, "去年": -1, "今年": 0, "明年": 1}
            year = ref.year + year_offsets.get(year_word, 0)
            if boundary == "年底":
                target = f"{year}-12-31"
            else:  # 年初
                target = f"{year}-01-01"
            changes.append(NormChange("temporal", m.group(0), target))
            return target

        text = self._P6_YEAR_BOUNDARY.sub(_year_boundary_repl, text)

        # Quarter calculation helper
        def _quarter_start(year: int, q: int) -> datetime:
            return datetime(year, (q - 1) * 3 + 1, 1)

        cur_quarter = (ref.month - 1) // 3 + 1  # 1-4

        for pat, kind in self._P6_PATTERNS:
            if kind == "month_end":
                last_day = calendar.monthrange(ref.year, ref.month)[1]
                target = ref.replace(day=last_day).strftime("%Y-%m-%d")
            elif kind == "year_end":
                target = ref.replace(month=12, day=31).strftime("%Y-%m-%d")
            elif kind == "month_start":
                target = ref.replace(day=1).strftime("%Y-%m-%d")
            elif kind == "year_start":
                target = ref.replace(month=1, day=1).strftime("%Y-%m-%d")
            elif kind == "first_half":
                target = ref.replace(month=1, day=1).strftime("%Y-%m-%d")
            elif kind == "second_half":
                target = ref.replace(month=7, day=1).strftime("%Y-%m-%d")
            elif kind == "last_quarter":
                q = cur_quarter - 1 if cur_quarter > 1 else 4
                y = ref.year if cur_quarter > 1 else ref.year - 1
                target = _fmt_date(_quarter_start(y, q))
            elif kind == "next_quarter":
                q = cur_quarter + 1 if cur_quarter < 4 else 1
                y = ref.year if cur_quarter < 4 else ref.year + 1
                target = _fmt_date(_quarter_start(y, q))
            elif kind == "this_quarter":
                target = _fmt_date(_quarter_start(ref.year, cur_quarter))
            else:
                target = _fmt_date(ref)

            def _repl(m: re.Match[str], t: str = target) -> str:
                changes.append(NormChange("temporal", m.group(0), t))
                return t

            text = pat.sub(_repl, text)
        return text

    def _pass55(self, text: str, ref: datetime, changes: list[NormChange]) -> str:
        """Year + month combo: 去年三月, 明年十二月, 今年1月."""
        year_offsets = {"前年": -2, "去年": -1, "今年": 0, "明年": 1, "後年": 2}

        def _repl(m: re.Match[str]) -> str:
            year_word, month_str = m.group(1), m.group(2)
            year = ref.year + year_offsets.get(year_word, 0)
            month = self._MONTH_ZH_MAP.get(month_str)
            if month is None:
                return m.group(0)
            target = f"{year}-{month:02d}-01"
            changes.append(NormChange("temporal", m.group(0), target))
            return target

        return self._P55_YEAR_MONTH.sub(_repl, text)

    def _pass7(
        self,
        text: str,
        ref: datetime,
        changes: list[NormChange],
        _original: str,
    ) -> str:
        for pat, offset, _kind in self._P7_PATTERNS:
            target = _fmt_date(ref + timedelta(days=offset))

            def _repl(m: re.Match[str], t: str = target) -> str:
                changes.append(NormChange("temporal", m.group(0), t))
                return t

            text = pat.sub(_repl, text)
        return text


# ======================== Helper ========================


def _extract_weekday(matched: str) -> str | None:
    """Extract the weekday key from a full prefix+weekday match string."""
    for key in sorted(WEEKDAY_MAP.keys(), key=len, reverse=True):
        if key in matched:
            return key
    return None


# ======================== Range helpers (for normalize_temporal_range) ========================


def _week_range(ref: datetime, offset_weeks: int) -> tuple[datetime, datetime]:
    """Return (Monday, Sunday) of the week that is offset_weeks from ref's week."""
    this_monday = ref - timedelta(days=ref.isoweekday() - 1)
    start = this_monday + timedelta(weeks=offset_weeks)
    end = start + timedelta(days=6)
    return start, end


def _month_range(ref: datetime, offset_months: int) -> tuple[datetime, datetime]:
    """Return (1st, last day) of month that is offset_months from ref's month."""
    year = ref.year
    month = ref.month + offset_months
    while month < 1:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    last_day = calendar.monthrange(year, month)[1]
    return datetime(year, month, 1), datetime(year, month, last_day)


def _year_range(ref: datetime, offset_years: int) -> tuple[datetime, datetime]:
    year = ref.year + offset_years
    return datetime(year, 1, 1), datetime(year, 12, 31)


def _quarter_range_pair(year: int, q: int) -> tuple[datetime, datetime]:
    """q is 1..4. Returns (1st of q's first month, last day of q's last month)."""
    start_month = (q - 1) * 3 + 1
    end_month = q * 3
    last_day = calendar.monthrange(year, end_month)[1]
    return datetime(year, start_month, 1), datetime(year, end_month, last_day)


def _fmt_range(start: datetime, end: datetime) -> str:
    return f"{start.strftime('%Y-%m-%d')} 到 {end.strftime('%Y-%m-%d')}"


# Negative-lookahead fragments used by the range pre-pass
_NOT_WEEKDAY = r"(?![一二三四五六日天])"
_NOT_DAY_NUM = r"(?!\d+[號号日])"


def _range_prepass(text: str, ref: datetime) -> str:
    """Replace range-valued period expressions with 'YYYY-MM-DD 到 YYYY-MM-DD'.

    Order matters: longer / more-specific patterns run first so leftover
    single-date expressions can fall through to TemporalNormalizer.normalize().
    """
    cur_q = (ref.month - 1) // 3 + 1
    year_off_map = {
        "前年": -2,
        "去年": -1,
        "今年": 0,
        "明年": 1,
        "後年": 2,
        "后年": 2,
    }

    # ---- Year + month combo FIRST (前年三月, 去年12月) — must beat plain 去年 ----
    month_alts = TemporalNormalizer._MONTH_ALTS

    def _year_month_repl(m: re.Match[str]) -> str:
        year_word = m.group(1)
        month_str = m.group(2)
        y = ref.year + year_off_map.get(year_word, 0)
        mn = TemporalNormalizer._MONTH_ZH_MAP.get(month_str)
        if mn is None:
            return m.group(0)
        last_day = calendar.monthrange(y, mn)[1]
        return _fmt_range(datetime(y, mn, 1), datetime(y, mn, last_day))

    text = re.sub(
        r"(前年|去年|今年|明年|[後后]年)(?:的)?(" + month_alts + r")",
        _year_month_repl,
        text,
    )

    # ---- Double-relative week (上上週, 下下禮拜) — before single week ----
    _WEEK = r"(?:[週周]|星期|禮拜)"
    text = re.sub(
        r"上上[個个]?" + _WEEK + _NOT_WEEKDAY,
        lambda m: _fmt_range(*_week_range(ref, -2)),
        text,
    )
    text = re.sub(
        r"下下[個个]?" + _WEEK + _NOT_WEEKDAY,
        lambda m: _fmt_range(*_week_range(ref, 2)),
        text,
    )

    # ---- Double-relative month (上上月, 下下個月) — before single month ----
    text = re.sub(
        r"上上[個个]?月" + _NOT_DAY_NUM,
        lambda m: _fmt_range(*_month_range(ref, -2)),
        text,
    )
    text = re.sub(
        r"下下[個个]?月" + _NOT_DAY_NUM,
        lambda m: _fmt_range(*_month_range(ref, 2)),
        text,
    )

    # ---- 最近 N units (last N <unit>, count-based range ending today) ----
    def _recent_days(m: re.Match[str]) -> str:
        n = max(int(m.group(1)), 1)
        start = ref - timedelta(days=n - 1)
        return _fmt_range(start, ref)

    text = re.sub(r"最近(\d+)\s*天", _recent_days, text)

    def _recent_weeks(m: re.Match[str]) -> str:
        n = max(int(m.group(1)), 1)
        start = ref - timedelta(weeks=n)
        return _fmt_range(start, ref)

    text = re.sub(r"最近(\d+)\s*(?:" + _WEEK + r")", _recent_weeks, text)

    def _recent_months(m: re.Match[str]) -> str:
        n = max(int(m.group(1)), 1)
        start = ref - timedelta(days=n * 30)
        return _fmt_range(start, ref)

    text = re.sub(r"最近(\d+)\s*[個个]?月", _recent_months, text)

    def _recent_years(m: re.Match[str]) -> str:
        n = max(int(m.group(1)), 1)
        start = ref - timedelta(days=n * 365)
        return _fmt_range(start, ref)

    text = re.sub(r"最近(\d+)\s*年", _recent_years, text)

    # "最近一週" / "最近一個月" / "最近一年" (explicit 1 unit, no digit)
    text = re.sub(
        r"最近一[個个]?(?:" + _WEEK + r")",
        lambda m: _fmt_range(ref - timedelta(weeks=1), ref),
        text,
    )
    text = re.sub(
        r"最近一[個个]?月",
        lambda m: _fmt_range(ref - timedelta(days=30), ref),
        text,
    )
    text = re.sub(
        r"最近一年",
        lambda m: _fmt_range(ref - timedelta(days=365), ref),
        text,
    )

    # ---- Week synonym: 上禮拜 → 上週 (Pass 2 weekday safe via lookahead) ----
    text = re.sub(r"([上下這本])禮拜" + _NOT_WEEKDAY, r"\1週", text)

    # ---- Single-word week (上週/下週/本週/這週) with neg lookahead weekday ----
    text = re.sub(
        r"上[週周]" + _NOT_WEEKDAY,
        lambda m: _fmt_range(*_week_range(ref, -1)),
        text,
    )
    text = re.sub(
        r"下[週周]" + _NOT_WEEKDAY,
        lambda m: _fmt_range(*_week_range(ref, 1)),
        text,
    )
    text = re.sub(
        r"(?:本|這)[週周]" + _NOT_WEEKDAY,
        lambda m: _fmt_range(*_week_range(ref, 0)),
        text,
    )

    # ---- Single-word month (上月/下月/本月/這月) with neg lookahead day ----
    text = re.sub(
        r"上[個个]?月" + _NOT_DAY_NUM,
        lambda m: _fmt_range(*_month_range(ref, -1)),
        text,
    )
    text = re.sub(
        r"下[個个]?月" + _NOT_DAY_NUM,
        lambda m: _fmt_range(*_month_range(ref, 1)),
        text,
    )
    text = re.sub(
        r"(?:本|這)[個个]?月" + _NOT_DAY_NUM,
        lambda m: _fmt_range(*_month_range(ref, 0)),
        text,
    )

    # ---- Year (去年/今年/明年/前年/後年) — safe since year+month already consumed ----
    text = re.sub(r"前年", lambda m: _fmt_range(*_year_range(ref, -2)), text)
    text = re.sub(r"去年", lambda m: _fmt_range(*_year_range(ref, -1)), text)
    text = re.sub(r"今年", lambda m: _fmt_range(*_year_range(ref, 0)), text)
    text = re.sub(r"明年", lambda m: _fmt_range(*_year_range(ref, 1)), text)
    text = re.sub(r"[後后]年", lambda m: _fmt_range(*_year_range(ref, 2)), text)

    # ---- Half year / quarter ----
    text = re.sub(
        r"上半年",
        lambda m: _fmt_range(datetime(ref.year, 1, 1), datetime(ref.year, 6, 30)),
        text,
    )
    text = re.sub(
        r"下半年",
        lambda m: _fmt_range(datetime(ref.year, 7, 1), datetime(ref.year, 12, 31)),
        text,
    )

    def _last_q_repl(m: re.Match[str]) -> str:
        q = cur_q - 1 if cur_q > 1 else 4
        y = ref.year if cur_q > 1 else ref.year - 1
        return _fmt_range(*_quarter_range_pair(y, q))

    def _next_q_repl(m: re.Match[str]) -> str:
        q = cur_q + 1 if cur_q < 4 else 1
        y = ref.year if cur_q < 4 else ref.year + 1
        return _fmt_range(*_quarter_range_pair(y, q))

    def _this_q_repl(m: re.Match[str]) -> str:
        return _fmt_range(*_quarter_range_pair(ref.year, cur_q))

    text = re.sub(r"上一?季", _last_q_repl, text)
    text = re.sub(r"下一?季", _next_q_repl, text)
    text = re.sub(r"(?:這一?季|本季)", _this_q_repl, text)

    return text


# ======================== ISO date spacing for downstream regex ========================

_ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

# Collapse a chain of 3+ ISO dates joined by 到/至 into a single (earliest, latest)
# range. Rationale: "去年一月到今年三月" expands to
#   "2025-01-01 到 2025-01-31 到 2026-03-01 到 2026-03-31"
# after range_prepass (each period → its own range). Downstream parsers that
# take only the first two dates would wrongly return just 2025-01, so we
# collapse the chain into a single outer-bound range before emitting.
_ISO_CHAIN_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:\s*(?:到|至)\s*\d{4}-\d{2}-\d{2}){2,}")


def _collapse_iso_chains(text: str) -> str:
    """Collapse ``A 到 B 到 C 到 D`` → ``A 到 D`` (time-sorted first/last)."""

    def _repl(m: re.Match[str]) -> str:
        chain = m.group(0)
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", chain)
        if not dates:
            return chain
        sorted_dates = sorted(dates)  # ISO YYYY-MM-DD lexicographic = chronological
        return f"{sorted_dates[0]} 到 {sorted_dates[-1]}"

    return _ISO_CHAIN_RE.sub(_repl, text)


def _pad_iso_dates(text: str) -> str:
    """Ensure each YYYY-MM-DD is surrounded by whitespace.

    Rationale: Python 3 regex ``\\b`` treats CJK characters as word chars
    (Unicode ``\\w``), so downstream patterns like ``\\b20\\d{2}-\\d{2}-\\d{2}\\b``
    fail to match ISO dates adjacent to Chinese text (e.g. ``查2026-04-06``).
    Padding with spaces restores ASCII word-boundary semantics.
    """

    def _repl(m: re.Match[str]) -> str:
        start, end = m.span()
        lead = "" if start == 0 or text[start - 1] == " " else " "
        trail = "" if end == len(text) or text[end] == " " else " "
        return f"{lead}{m.group(0)}{trail}"

    return _ISO_DATE_RE.sub(_repl, text)


# ======================== Public pure-function API ========================

_SINGLETON = TemporalNormalizer()


def normalize_temporal(text: str, ref: datetime | None = None) -> str:
    """Rewrite relative temporal expressions to absolute ISO single dates.

    Thin pure-function wrapper over :class:`TemporalNormalizer` that drops the
    :class:`NormContext` requirement, accepting a plain ``datetime`` instead.
    Period expressions ("上週", "上個月") are treated as anchor points (single
    dates). For range-aware rewriting, use :func:`normalize_temporal_range`.

    Args:
        text: input text (Chinese or English).
        ref:  reference datetime for "today". Defaults to ``datetime.now()``.

    Returns:
        Rewritten text. Never raises; falls back to the original on any error.

    Examples:
        >>> from datetime import datetime
        >>> ref = datetime(2026, 4, 13)  # Monday
        >>> normalize_temporal("三天前開的刀", ref)
        ' 2026-04-10 開的刀'
    """
    if not text:
        return text
    if ref is None:
        ref = datetime.now()
    try:
        from .normalize import NormContext

        normalised, _ = _SINGLETON.normalize(text, NormContext(created_at=ref))
        return _pad_iso_dates(normalised)
    except Exception:
        return text


def normalize_temporal_range(text: str, ref: datetime | None = None) -> str:
    """Rewrite relative temporal expressions, expanding periods to full date ranges.

    Behaviour:

    - Period expressions (``上週``, ``上個月``, ``去年``, ``最近3天``) become
      ``"YYYY-MM-DD 到 YYYY-MM-DD"`` (Traditional Chinese "to").
    - Single-date expressions (``今天``, ``昨天``, ``3天前``, ``上週一``) remain
      as single absolute ISO dates, identical to :func:`normalize_temporal`.
    - Output dates are space-padded so downstream regex using ``\\b`` can match
      them when adjacent to CJK characters (see :func:`_pad_iso_dates`).

    This is the preferred form when feeding queries to LLM tool-callers that
    take separate ``start`` / ``end`` parameters — the LLM (or a deterministic
    regex parser) can copy both dates verbatim instead of inferring a range.

    Args:
        text: input text (Chinese or English).
        ref:  reference datetime for "today". Defaults to ``datetime.now()``.

    Returns:
        Rewritten text. Never raises.

    Examples:
        >>> from datetime import datetime
        >>> ref = datetime(2026, 4, 13)  # Monday
        >>> normalize_temporal_range("查上週的手術", ref)
        '查 2026-04-06 到 2026-04-12 的手術'
        >>> normalize_temporal_range("去年三月有多少", ref)
        ' 2025-03-01 到 2025-03-31 有多少'
        >>> normalize_temporal_range("最近3天開幾刀", ref)
        ' 2026-04-11 到 2026-04-13 開幾刀'
        >>> normalize_temporal_range("3天前那台手術", ref)
        ' 2026-04-10 那台手術'
    """
    if not text:
        return text
    if ref is None:
        ref = datetime.now()
    try:
        from .normalize import NormContext

        # Pass 0 + 0.5 + 0.6: S2T + ZH number → Arabic + context-bound 周→週
        t = text.translate(_S2T)
        from .normalize import preprocess_chinese

        t = preprocess_chinese(t)
        t = _apply_pass06_zhou(t)

        # Range pre-pass: consume period expressions as ranges
        t = _range_prepass(t, ref)

        # Fall through to single-date normalizer for leftovers
        t, _ = _SINGLETON.normalize(t, NormContext(created_at=ref))

        # Collapse "A 到 B 到 C 到 D" chains into single (earliest, latest) range
        # so "去年一月到今年三月" → "2025-01-01 到 2026-03-31" (not split 4 ways).
        t = _collapse_iso_chains(t)

        return _pad_iso_dates(t)
    except Exception:
        return text
