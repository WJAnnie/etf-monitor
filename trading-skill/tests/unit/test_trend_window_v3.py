from datetime import datetime, timedelta, timezone

from trading_skill.chan.center import Center
from trading_skill.chan.trend import classify_trend
from trading_skill.domain.enums import CenterState, Timeframe, TrendClassification
from trading_skill.production_chan_v3 import latest_directional_center_run


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _center(index: int, *, dd: int, zd: int, zg: int, gg: int) -> Center:
    start = T0 + timedelta(days=index * 10)
    end = start + timedelta(days=5)
    return Center(
        id=f"c{index}", symbol="X", source_timeframe=Timeframe.M30, level_rank=1,
        state=CenterState.CONFIRMED,
        seed_motion_ids=(f"a{index}", f"b{index}", f"d{index}"),
        motion_ids=(f"a{index}", f"b{index}", f"d{index}"),
        zd_ticks=zd, zg_ticks=zg, dd_ticks=dd, gg_ticks=gg,
        d_ticks=dd, g_ticks=gg,
        structural_start_timestamp=start, structural_end_timestamp=end,
        confirmation_timestamp=end,
    )


def test_latest_directional_run_resets_when_old_trend_direction_changes():
    centers = (
        _center(1, dd=90, zd=92, zg=98, gg=100),
        _center(2, dd=110, zd=112, zg=118, gg=120),
        _center(3, dd=130, zd=132, zg=138, gg=140),
        _center(4, dd=115, zd=117, zg=123, gg=125),
    )
    # 全历史同时含上涨和下跌关系，冻结分类器对“整段历史”只能判无法统一。
    old = classify_trend(centers, symbol="X").trend
    assert old.current_classification is TrendClassification.UNRESOLVED

    # V3只取最新连续同向独立中枢段，因此当前段应是 c3 -> c4 的下跌趋势候选。
    current = latest_directional_center_run(centers)
    assert [center.id for center in current] == ["c3", "c4"]
    new = classify_trend(current, symbol="X").trend
    assert new.current_classification is TrendClassification.DOWNTREND


def test_latest_directional_run_keeps_complete_same_direction_sequence():
    centers = (
        _center(1, dd=90, zd=92, zg=98, gg=100),
        _center(2, dd=110, zd=112, zg=118, gg=120),
        _center(3, dd=130, zd=132, zg=138, gg=140),
    )
    current = latest_directional_center_run(centers)
    assert [center.id for center in current] == ["c1", "c2", "c3"]
    assert classify_trend(current, symbol="X").trend.current_classification is TrendClassification.UPTREND


def test_latest_expansion_is_not_forced_into_a_same_level_trend():
    centers = (
        _center(1, dd=90, zd=95, zg=105, gg=110),
        _center(2, dd=106, zd=108, zg=115, gg=120),
    )
    # 核心区已分开，但外围区间仍重叠，应保留“扩展/更高级别结构待定”，不能强造趋势。
    assert latest_directional_center_run(centers) == ()
