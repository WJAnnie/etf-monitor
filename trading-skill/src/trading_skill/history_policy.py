from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True, slots=True)
class HistoryQuality:
    daily_count: int
    weekly_count: int
    m120_count: int
    m30_count: int
    m5_count: int
    long_term_tier: str
    long_term_complete: bool
    daily_primary_ok: bool
    m120_primary_ok: bool
    m30_primary_ok: bool
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


def classify_history_counts(*, daily: int, weekly: int, m120: int, m30: int, m5: int) -> HistoryQuality:
    """统一定义不同交易周期最低需要多少历史证据。

    目标不是强迫所有股票都必须有1200根日线，而是区分：完整长期证据、充足、有限、以及不足。
    新股可以参与30分钟结构扫描，但不能在长期历史不足时伪装成完整日线/周线机会。
    """
    daily = max(0, int(daily))
    weekly = max(0, int(weekly))
    m120 = max(0, int(m120))
    m30 = max(0, int(m30))
    m5 = max(0, int(m5))

    if daily >= 1000 and weekly >= 200:
        tier = "完整长期证据"
    elif daily >= 500 and weekly >= 100:
        tier = "长期证据充足"
    elif daily >= 250 and weekly >= 60:
        tier = "长期证据有限"
    else:
        tier = "长期证据不足"

    daily_primary_ok = daily >= 250 and weekly >= 60
    m120_primary_ok = m120 >= 200 and daily >= 120 and weekly >= 40
    m30_primary_ok = m30 >= 500 and m120 >= 200 and daily >= 120
    long_term_complete = daily >= 1000 and weekly >= 200

    note = (
        f"日线{daily}根、周线{weekly}根、120分钟{m120}根、30分钟{m30}根、5分钟{m5}根；{tier}。"
        "日线/120分钟/30分钟分别按各自最低历史门槛判断，5分钟只负责执行确认。"
    )
    return HistoryQuality(
        daily_count=daily,
        weekly_count=weekly,
        m120_count=m120,
        m30_count=m30,
        m5_count=m5,
        long_term_tier=tier,
        long_term_complete=long_term_complete,
        daily_primary_ok=daily_primary_ok,
        m120_primary_ok=m120_primary_ok,
        m30_primary_ok=m30_primary_ok,
        note=note,
    )


def primary_history_gate(timeframe: str, quality: dict | HistoryQuality) -> tuple[bool, str]:
    value = quality.to_dict() if isinstance(quality, HistoryQuality) else dict(quality or {})
    if timeframe == "daily":
        ok = bool(value.get("daily_primary_ok"))
        return ok, "日线主买点历史证据满足" if ok else "日线主买点历史不足：至少需要约250根日线和60根周线背景"
    if timeframe == "120m":
        ok = bool(value.get("m120_primary_ok"))
        return ok, "120分钟主买点历史证据满足" if ok else "120分钟主买点历史不足：至少需要200根120分钟、120根日线和40根周线背景"
    if timeframe == "30m":
        ok = bool(value.get("m30_primary_ok"))
        return ok, "30分钟主买点历史证据满足" if ok else "30分钟主买点历史不足：至少需要500根30分钟、200根120分钟和120根日线背景"
    return False, "该周期不是正式主买点周期"
