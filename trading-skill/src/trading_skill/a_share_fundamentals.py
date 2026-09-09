from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class FinancialPeriod:
    report_date: str
    notice_date: str
    revenue_growth: float | None
    profit_growth: float | None
    roe: float | None
    gross_margin: float | None
    eps: float | None
    operating_cash_per_share: float | None


@dataclass(frozen=True, slots=True)
class FundamentalPrefilter:
    eligible: bool
    grade: str
    annual: FinancialPeriod | None
    interim: FinancialPeriod | None
    reasons: tuple[str, ...]


def _num(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value: object) -> str:
    text = str(value or "")
    return text[:10]


def parse_period(row: Mapping[str, object]) -> FinancialPeriod:
    return FinancialPeriod(
        report_date=_date(row.get("REPORTDATE")),
        notice_date=_date(row.get("NOTICE_DATE") or row.get("UPDATE_DATE")),
        revenue_growth=_num(row.get("YSTZ")),
        profit_growth=_num(row.get("SJLTZ")),
        roe=_num(row.get("WEIGHTAVG_ROE")),
        gross_margin=_num(row.get("XSMLL")),
        eps=_num(row.get("BASIC_EPS")),
        operating_cash_per_share=_num(row.get("MGJYXJJE")),
    )


def _visible(period: FinancialPeriod, as_of: datetime) -> bool:
    if not period.notice_date:
        return True
    try:
        published = datetime.fromisoformat(period.notice_date)
    except ValueError:
        return True
    if as_of.tzinfo is not None:
        published = published.replace(tzinfo=as_of.tzinfo)
    return published <= as_of


def select_annual_and_interim(
    rows: Iterable[Mapping[str, object]], *, as_of: datetime
) -> tuple[FinancialPeriod | None, FinancialPeriod | None]:
    periods = [parse_period(row) for row in rows]
    periods = [period for period in periods if period.report_date and _visible(period, as_of)]
    periods.sort(key=lambda p: p.report_date, reverse=True)
    annual = next((p for p in periods if p.report_date.endswith("12-31")), None)
    interim = next((p for p in periods if p.report_date.endswith("06-30")), None)
    return annual, interim


def evaluate_prefilter(
    rows: Iterable[Mapping[str, object]], *, as_of: datetime, pe: float | None, pb: float | None
) -> FundamentalPrefilter:
    annual, interim = select_annual_and_interim(rows, as_of=as_of)
    reasons: list[str] = []
    if annual is None:
        reasons.append("缺少已披露年报")
    if interim is None:
        reasons.append("缺少已披露中报")
    if annual is None or interim is None:
        return FundamentalPrefilter(False, "E", annual, interim, tuple(reasons))

    metrics = interim
    if metrics.revenue_growth is not None and metrics.revenue_growth < -20:
        reasons.append("营收同比明显下滑")
    if metrics.profit_growth is not None and metrics.profit_growth < -25:
        reasons.append("净利润同比明显下滑")
    if metrics.roe is not None and metrics.roe < 3:
        reasons.append("净资产收益率偏低")
    if metrics.eps is not None and metrics.eps <= 0:
        reasons.append("每股收益非正")
    if metrics.gross_margin is not None and metrics.gross_margin <= 0:
        reasons.append("毛利率异常")

    # 这里只做财务预筛，不把估值高直接当作缠论买点否决；极端估值只降低等级。
    score = 0
    score += 2 if (metrics.revenue_growth or 0) >= 15 else 1 if (metrics.revenue_growth or 0) >= 5 else 0
    score += 2 if (metrics.profit_growth or 0) >= 15 else 1 if (metrics.profit_growth or 0) >= 5 else 0
    score += 2 if (metrics.roe or 0) >= 15 else 1 if (metrics.roe or 0) >= 8 else 0
    score += 1 if (metrics.gross_margin or 0) >= 20 else 0
    if metrics.eps and metrics.operating_cash_per_share is not None and metrics.eps > 0:
        score += 1 if metrics.operating_cash_per_share / metrics.eps >= 0.7 else 0
    if pe is not None and pe > 100:
        score -= 1
    if pb is not None and pb > 12:
        score -= 1

    hard_fail = any(
        reason in reasons
        for reason in ("营收同比明显下滑", "净利润同比明显下滑", "每股收益非正", "毛利率异常")
    )
    if score >= 7:
        grade = "A"
    elif score >= 5:
        grade = "B"
    elif score >= 3:
        grade = "C"
    elif score >= 1:
        grade = "D"
    else:
        grade = "E"
    eligible = not hard_fail and grade in {"A", "B", "C"}
    if not eligible and not reasons:
        reasons.append("财务质量暂未达到候选阈值")
    return FundamentalPrefilter(eligible, grade, annual, interim, tuple(reasons))
