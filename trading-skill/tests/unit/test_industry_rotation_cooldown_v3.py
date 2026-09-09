from trading_skill.a_share_universe import IndustryCandidate
from trading_skill.industry_event_rotation import _too_high
from trading_skill.industry_prospects import industry_rotation_state


def _industry(*, ch60: float, ytd: float, pct: float = 0.0, heat_state: str = "温和"):
    return IndustryCandidate(
        code="x", name="测试行业", heat_score=50, low_position_score=50, prospects_score=90,
        rank_score=80, heat_state=heat_state, change_pct=pct, change_60d=ch60, change_ytd=ytd,
        main_flow_ratio=0, breadth=0.5,
    )


def test_high_ytd_but_recently_cooled_industry_can_reenter_watch_pool():
    item = _industry(ch60=8, ytd=90, pct=-1)
    assert industry_rotation_state(item) != "高位暂退"
    assert _too_high(-1, 8, 90) is False


def test_high_ytd_with_recent_strength_remains_paused():
    item = _industry(ch60=25, ytd=90, pct=1)
    assert industry_rotation_state(item) == "高位暂退"
    assert _too_high(1, 25, 90) is True


def test_extreme_recent_run_remains_high_regardless_of_ytd():
    item = _industry(ch60=52, ytd=40, pct=2, heat_state="过热")
    assert industry_rotation_state(item) == "高位暂退"
    assert _too_high(2, 52, 40) is True
