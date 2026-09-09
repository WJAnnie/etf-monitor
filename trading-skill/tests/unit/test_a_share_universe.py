from trading_skill.a_share_universe import rank_industry_leaders, screen_industries


def test_industry_screen_prefers_low_position_warming_over_overheated():
    rows = [
        {"f12": "BK1", "f14": "低位升温", "f3": 2.0, "f184": 6.0, "f24": -8.0, "f25": -4.0, "f104": 18, "f105": 6},
        {"f12": "BK2", "f14": "高位热门", "f3": 8.5, "f184": 10.0, "f24": 50.0, "f25": 60.0, "f104": 25, "f105": 2},
        {"f12": "BK3", "f14": "弱势低位", "f3": -1.5, "f184": -8.0, "f24": -30.0, "f25": -25.0, "f104": 4, "f105": 20},
        {"f12": "BK4", "f14": "温和改善", "f3": 1.2, "f184": 3.0, "f24": -2.0, "f25": 1.0, "f104": 14, "f105": 8},
    ]
    selected = screen_industries(rows, limit=4)
    names = [item.name for item in selected]
    assert "低位升温" in names
    assert names.index("低位升温") < names.index("高位热门")
    assert "弱势低位" not in names


def test_leader_ranking_excludes_st_and_uses_market_cap_and_liquidity():
    industry = screen_industries(
        [{"f12": "BK1", "f14": "细分行业", "f3": 2, "f184": 5, "f24": -5, "f25": 0, "f104": 10, "f105": 3}],
        limit=1,
    )[0]
    rows = [
        {"f12": "600001", "f13": 1, "f14": "龙头甲", "f2": 20, "f3": 1, "f6": 8e8, "f8": 3, "f9": 20, "f20": 1000e8, "f21": 800e8, "f23": 2, "f24": 10},
        {"f12": "600002", "f13": 1, "f14": "龙头乙", "f2": 15, "f3": 2, "f6": 6e8, "f8": 4, "f9": 25, "f20": 700e8, "f21": 500e8, "f23": 3, "f24": 8},
        {"f12": "600003", "f13": 1, "f14": "*ST问题", "f2": 4, "f3": 5, "f6": 20e8, "f8": 10, "f9": 5, "f20": 2000e8, "f21": 1500e8, "f23": 1, "f24": 5},
    ]
    leaders = rank_industry_leaders(rows, industry=industry, limit=3)
    assert [x.name for x in leaders] == ["龙头甲", "龙头乙"]
    assert leaders[0].leader_rank == 1


def test_leader_ranking_uses_market_cap_fallback_when_premarket_liquidity_is_blank():
    industry = screen_industries(
        [{"f12": "BK1", "f14": "细分行业", "f3": 0, "f184": 0, "f24": -5, "f25": 0, "f104": 0, "f105": 0}],
        limit=1,
    )[0]
    rows = [
        {"f12": "600010", "f13": 1, "f14": "盘前龙头甲", "f2": 12, "f3": "-", "f6": "-", "f8": "-", "f20": 900e8, "f21": 700e8, "f24": 5},
        {"f12": "600011", "f13": 1, "f14": "盘前龙头乙", "f2": 8, "f3": "-", "f6": 0, "f8": 0, "f20": 500e8, "f21": 400e8, "f24": 4},
    ]
    leaders = rank_industry_leaders(rows, industry=industry, limit=3)
    assert [x.name for x in leaders] == ["盘前龙头甲", "盘前龙头乙"]
    assert leaders[0].amount == 0
