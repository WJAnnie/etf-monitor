from __future__ import annotations

from trading_skill.a_share_fundamentals import FinancialPeriod, FundamentalPrefilter
from trading_skill.sector_fundamental_gate import sector_observation_override


def _period(*, revenue=10.0, profit=-50.0, roe=-5.0, gross=70.0, eps=-0.5):
    return FinancialPeriod(
        report_date="2026-06-30",
        notice_date="2026-08-30",
        revenue_growth=revenue,
        profit_growth=profit,
        roe=roe,
        gross_margin=gross,
        eps=eps,
        operating_cash_per_share=-0.2,
    )


def test_innovation_drug_can_be_deep_observed_without_becoming_fundamental_buy_eligible():
    annual = _period()
    interim = _period()
    prefilter = FundamentalPrefilter(
        eligible=False,
        grade="D",
        annual=annual,
        interim=interim,
        reasons=("净利润同比明显下滑", "每股收益非正", "净资产收益率偏低"),
    )
    allowed, reason = sector_observation_override("创新药/生物医药", prefilter)
    assert allowed is True
    assert reason and "不能据此直接买入" in reason
    assert prefilter.eligible is False


def test_missing_reports_and_revenue_collapse_are_never_overridden():
    missing = FundamentalPrefilter(False, "E", None, None, ("缺少已披露年报", "缺少已披露中报"))
    assert sector_observation_override("创新药/生物医药", missing)[0] is False

    annual = _period(revenue=-30)
    interim = _period(revenue=-30)
    collapsed = FundamentalPrefilter(
        False,
        "E",
        annual,
        interim,
        ("营收同比明显下滑", "净利润同比明显下滑", "每股收益非正"),
    )
    assert sector_observation_override("造船与海工", collapsed)[0] is False
