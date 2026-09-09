from trading_skill.industry_prospects import match_theme, prospect_industry_candidates, select_industries_v2


def row(code, name, pct=0.5, ch60=5.0, ytd=8.0, flow=1.0, up=6, down=4):
    return {
        "f12": code,
        "f14": name,
        "f3": pct,
        "f24": ch60,
        "f25": ytd,
        "f184": flow,
        "f104": up,
        "f105": down,
    }


def test_innovation_drug_and_shipbuilding_are_long_term_prospect_themes():
    assert match_theme("创新药") is not None
    assert match_theme("创新药").name == "创新药"
    assert match_theme("船舶制造") is not None
    assert match_theme("船舶制造").name == "造船与海工"


def test_prospect_pool_does_not_require_current_heat():
    rows = [
        row("BK001", "创新药", pct=-1.2, ch60=-8.0, flow=-0.5, up=3, down=7),
        row("BK002", "船舶制造", pct=-0.8, ch60=2.0, flow=-0.2, up=4, down=6),
    ]
    selected = prospect_industry_candidates(rows)
    assert {item.name for item in selected} == {"创新药", "船舶制造"}
    assert all(item.selection_reason.startswith("长期前景池") for item in selected)


def test_select_v2_keeps_prospect_pool_primary_and_can_add_dynamic_supplement():
    rows = [
        row("BK001", "创新药", pct=-0.3, ch60=1.0),
        row("BK002", "船舶制造", pct=0.1, ch60=3.0),
        row("BK003", "普通热门细分", pct=6.0, ch60=18.0, flow=10.0, up=9, down=1),
        row("BK004", "另一个热门细分", pct=5.0, ch60=15.0, flow=8.0, up=8, down=2),
    ]
    selected = select_industries_v2(rows, prospect_limit=2, dynamic_supplement=1)
    names = [item.name for item in selected]
    assert names[:2] == ["创新药", "船舶制造"]
    assert len(selected) == 3


def test_prospect_selection_diversifies_themes_before_taking_duplicate_subindustries():
    rows = [
        row("D1", "创新药", ch60=1),
        row("D2", "化学制剂", ch60=2),
        row("D3", "生物制品", ch60=3),
        row("S1", "船舶制造", ch60=4),
        row("A1", "其他通信设备", ch60=5),
    ]
    selected = select_industries_v2(rows, prospect_limit=3, dynamic_supplement=0)
    themes = [item.prospect_theme for item in selected]
    assert themes == ["创新药", "造船与海工", "人工智能基础设施"]
