from trading_skill.industry_priority import IndustryState, rank_industries, select_priority_industries


def row(code, name, pct, flow, ch60, ytd, up, down):
    return {
        "f12": code,
        "f14": name,
        "f3": pct,
        "f184": flow,
        "f24": ch60,
        "f25": ytd,
        "f104": up,
        "f105": down,
    }


def test_static_theme_is_prior_not_whitelist():
    rows = [
        row("A", "半导体设备", -2, -8, -12, -10, 20, 80),
        row("B", "航运港口", 3, 12, 14, 18, 80, 20),
        row("C", "专业服务", 0, 0, 2, 5, 50, 50),
    ]
    selected, _ = select_priority_industries(rows, limit=1)
    assert selected[0].name == "航运港口"
    assert selected[0].prospect_theme is None


def test_rotation_replaces_industry_when_market_confirmation_changes():
    first_rows = [
        row("A", "半导体设备", 3, 10, 12, 15, 80, 20),
        row("B", "航运港口", -2, -8, -12, -10, 20, 80),
        row("C", "专业服务", 0, 0, 2, 5, 50, 50),
    ]
    second_rows = [
        row("A", "半导体设备", -3, -10, -15, -12, 15, 85),
        row("B", "航运港口", 4, 14, 16, 20, 85, 15),
        row("C", "专业服务", 0, 0, 2, 5, 50, 50),
    ]
    selected1, _ = select_priority_industries(first_rows, limit=1)
    selected2, _ = select_priority_industries(second_rows, limit=1)
    assert selected1[0].name == "半导体设备"
    assert selected2[0].name == "航运港口"


def test_overheated_industry_is_penalized_but_not_permanently_deleted():
    rows = [
        row("A", "半导体设备", 6, 15, 60, 90, 90, 10),
        row("B", "航运港口", 1, 3, 10, 12, 60, 40),
        row("C", "专业服务", 0, 0, 2, 5, 50, 50),
    ]
    ranked = {x.name: x for x in rank_industries(rows)}
    assert ranked["半导体设备"].state is IndustryState.OVERHEATED
    assert ranked["半导体设备"].priority_score > 0
