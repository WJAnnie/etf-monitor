from trading_skill.industry_priority import IndustryPool, IndustryState, rank_industries, select_priority_industries


def row(code, name, pct, flow, ch60, ytd, up, down):
    return {"f12": code, "f14": name, "f3": pct, "f184": flow, "f24": ch60, "f25": ytd, "f104": up, "f105": down}


def test_short_term_hot_industry_does_not_automatically_become_quality_pool():
    rows = [
        row("A", "半导体设备", 1, 2, 10, 15, 60, 40),
        row("B", "航运港口", 5, 18, 18, 25, 90, 10),
        row("C", "专业服务", 0, 0, 2, 5, 50, 50),
    ]
    ranked = {x.name: x for x in rank_industries(rows)}
    assert ranked["半导体设备"].quality_score > ranked["航运港口"].quality_score
    assert ranked["航运港口"].pool in {IndustryPool.EMERGING, IndustryPool.TACTICAL}
    assert ranked["航运港口"].pool is not IndustryPool.QUALITY


def test_extremely_hot_resource_industry_still_cannot_become_quality():
    rows = [
        row("A", "白银", 6, 20, 18, 35, 95, 5),
        row("B", "半导体设备", 1, 2, 8, 12, 55, 45),
        row("C", "专业服务", 0, 0, 1, 3, 50, 50),
    ]
    ranked = {x.name: x for x in rank_industries(rows)}
    assert ranked["白银"].market_confirmation_score > ranked["半导体设备"].market_confirmation_score
    assert ranked["白银"].quality_score < 80
    assert ranked["白银"].pool is not IndustryPool.QUALITY
    assert ranked["半导体设备"].quality_score >= 80


def test_quality_industry_can_defer_when_overheated_and_reenter_after_cooling():
    hot_rows = [
        row("A", "半导体设备", 8, 18, 65, 95, 95, 5),
        row("B", "航运港口", 1, 2, 10, 15, 60, 40),
        row("C", "专业服务", 0, 0, 2, 5, 50, 50),
    ]
    normal_rows = [
        row("A", "半导体设备", 2, 8, 12, 20, 75, 25),
        row("B", "航运港口", 0, 0, 5, 10, 50, 50),
        row("C", "专业服务", -1, -2, 0, 3, 40, 60),
    ]
    hot = {x.name: x for x in rank_industries(hot_rows)}["半导体设备"]
    normal = {x.name: x for x in rank_industries(normal_rows)}["半导体设备"]
    assert hot.state is IndustryState.OVERHEATED
    assert hot.pool is IndustryPool.DEFERRED
    assert normal.pool is IndustryPool.QUALITY


def test_priority_selection_limits_same_industry_family_concentration():
    rows = [
        row("A", "动力煤", 4, 15, 15, 25, 85, 15),
        row("B", "焦煤", 3, 14, 14, 24, 82, 18),
        row("C", "煤炭开采", 3, 13, 13, 23, 80, 20),
        row("D", "半导体设备", 2, 10, 12, 20, 75, 25),
        row("E", "电网设备", 2, 9, 11, 18, 72, 28),
    ]
    selected, _ = select_priority_industries(rows, limit=4, max_per_family=1)
    coal = [x for x in selected if x.industry_family == "煤炭焦化"]
    assert len(coal) <= 1


def test_priority_selection_limits_same_long_term_theme_concentration():
    rows = [
        row("A", "半导体设备", 2, 10, 12, 20, 75, 25),
        row("B", "半导体材料", 2, 9, 11, 18, 72, 28),
        row("C", "电子特气", 2, 8, 10, 17, 70, 30),
        row("D", "机器人", 2, 7, 9, 16, 68, 32),
        row("E", "电网设备", 2, 6, 8, 15, 66, 34),
    ]
    selected, _ = select_priority_industries(rows, limit=5, max_per_theme=2)
    semiconductor = [x for x in selected if x.prospect_theme == "半导体设备与材料"]
    assert len(semiconductor) <= 2


def test_event_only_adjusts_timing_not_structural_quality():
    rows = [
        row("A", "半导体设备", 1, 3, 10, 15, 60, 40),
        row("B", "航运港口", 1, 3, 10, 15, 60, 40),
        row("C", "专业服务", 0, 0, 2, 5, 50, 50),
    ]
    events = {"航运港口": [{"impact": "利好", "importance": "重大"}]}
    plain = {x.name: x for x in rank_industries(rows)}["航运港口"]
    adjusted = {x.name: x for x in rank_industries(rows, event_map=events)}["航运港口"]
    assert adjusted.quality_score == plain.quality_score
    assert adjusted.timing_score > plain.timing_score


def test_non_static_theme_can_still_enter_active_pool():
    rows = [
        row("A", "半导体设备", -3, -10, -15, -12, 20, 80),
        row("B", "航运港口", 5, 18, 18, 25, 90, 10),
        row("C", "专业服务", 0, 0, 2, 5, 50, 50),
    ]
    selected, _ = select_priority_industries(rows, limit=2)
    assert any(x.name == "航运港口" for x in selected)
