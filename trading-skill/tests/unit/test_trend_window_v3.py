from datetime import datetime, timedelta, timezone

from trading_skill.chan.center import Center
from trading_skill.chan.trend import classify_trend
from trading_skill.domain.enums import CenterState, Timeframe, TrendClassification
from trading_skill.production_chan_v3 import directional_center_runs, latest_directional_center_run


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
    old = classify_trend(centers, symbol="X").trend
    assert old.current_classification is TrendClassification.UNRESOLVED

    current = latest_directional_center_run(centers)
    assert [center.id for center in current] == ["c3", "c4"]
    new = classify_trend(current, symbol="X").trend
    assert new.current_classification is TrendClassification.DOWNTREND


def test_all_directional_runs_keep_recent_history_instead_of_only_latest_run():
    centers = (
        _center(1, dd=90, zd=92, zg=98, gg=100),
        _center(2, dd=110, zd=112, zg=118, gg=120),
        _center(3, dd=130, zd=132, zg=138, gg=140),
        _center(4, dd=115, zd=117, zg=123, gg=125),
        _center(5, dd=95, zd=97, zg=103, gg=105),
    )
    runs = directional_center_runs(centers)
    assert [[center.id for center in run] for run in runs] == [
        ["c1", "c2", "c3"],
        ["c3", "c4", "c5"],
    ]
    assert classify_trend(runs[0], symbol="X").trend.current_classification is TrendClassification.UPTREND
    assert classify_trend(runs[1], symbol="X").trend.current_classification is TrendClassification.DOWNTREND


def test_latest_directional_run_keeps_complete_same_direction_sequence():
    centers = (
        _center(1, dd=90, zd=92, zg=98, gg=100),
        _center(2, dd=110, zd=112, zg=118, gg=120),
        _center(3, dd=130, zd=132, zg=138, gg=140),
    )
    current = latest_directional_center_run(centers)
    assert [center.id for center in current] == ["c1", "c2", "c3"]
    assert classify_trend(current, symbol="X").trend.current_classification is TrendClassification.UPTREND


def test_latest_expansion_is_not_forced_into_a_same_level_trend_but_old_run_is_kept():
    centers = (
        _center(1, dd=90, zd=92, zg=98, gg=100),
        _center(2, dd=110, zd=112, zg=118, gg=120),
        # c2 -> c3 核心分离但外围重叠，属于扩展待定。
        _center(3, dd=116, zd=121, zg=127, gg=130),
    )
    assert latest_directional_center_run(centers) == ()
    runs = directional_center_runs(centers)
    assert [[center.id for center in run] for run in runs] == [["c1", "c2"]]


def test_pure_expansion_is_not_forced_into_a_same_level_trend():
    centers = (
        _center(1, dd=90, zd=95, zg=105, gg=110),
        _center(2, dd=106, zd=108, zg=115, gg=120),
    )
    assert latest_directional_center_run(centers) == ()
    assert directional_center_runs(centers) == ()
