from trading_skill.deep_scan_handoff import DeepScanTier, build_deep_scan_queue


def stock(code, *, status="PASS", priority="HIGH", risk="LOW", evidence="FULL", industry_complete=True):
    return {
        "code": code,
        "name": code,
        "security_type": "STOCK",
        "source_routes": ["MARKET_STRENGTH"],
        "research_priority": priority,
        "industry_name": "半导体材料",
        "industry_context_complete": industry_complete,
        "phase_a_assessment": {
            "risk_level": risk,
            "evidence_coverage": evidence,
        },
        "specialized_evidence": None,
        "final_decision": {
            "status": status,
            "deep_analysis_eligible": status != "REJECT",
        },
    }


def fund(code, *, status="PASS", priority="HIGH", risk="LOW", product="ADEQUATE", trading="STRONG", deep=True, category="EQUITY_BROAD"):
    return {
        "code": code,
        "name": code,
        "security_type": "ETF",
        "source_routes": ["FUND_RELATIVE"],
        "research_priority": priority,
        "fund_category": category,
        "assessment": {
            "status": status,
            "risk_level": risk,
            "product_quality": product,
            "trading_quality": trading,
            "evidence_coverage": "PARTIAL",
            "deep_analysis_eligible": deep,
        },
    }


def test_reject_and_high_risk_never_enter_step4_queue():
    rows = [
        stock("000001", status="REJECT"),
        stock("000002", status="WATCH", risk="HIGH"),
        stock("000003", status="PASS"),
    ]
    queue = build_deep_scan_queue(rows, [], capacity_max=20, soft_target_min=5)
    assert [x.code for x in queue] == ["000003"]


def test_pass_is_prioritized_over_watch():
    queue = build_deep_scan_queue(
        [stock("000001", status="WATCH", priority="HIGH", evidence="PARTIAL"), stock("000002", status="PASS", priority="MEDIUM")],
        [],
        capacity_max=20,
        soft_target_min=5,
    )
    assert queue[0].code == "000002"
    assert queue[0].tier is DeepScanTier.PRIMARY


def test_low_priority_watch_is_not_used_to_fill_quota():
    rows = [stock(f"{i:06d}", status="WATCH", priority="LOW", evidence="PARTIAL") for i in range(30)]
    queue = build_deep_scan_queue(rows, [], capacity_max=20, soft_target_min=10)
    assert queue == ()


def test_observe_only_fills_until_soft_target_not_capacity_max():
    primary = [stock(f"1{i:05d}", status="PASS", priority="HIGH") for i in range(3)]
    observe = [stock(f"2{i:05d}", status="WATCH", priority="MEDIUM", evidence="LIMITED") for i in range(20)]
    queue = build_deep_scan_queue(primary + observe, [], capacity_max=15, soft_target_min=8)
    assert len(queue) == 8
    assert sum(x.tier is DeepScanTier.OBSERVE for x in queue) == 5


def test_fund_watch_with_adequate_product_can_be_observed_but_high_risk_cannot():
    queue = build_deep_scan_queue(
        [],
        [
            fund("510300", status="PASS", priority="HIGH"),
            fund("513100", status="WATCH", priority="HIGH"),
            fund("161130", status="WATCH", priority="HIGH", risk="HIGH"),
        ],
        capacity_max=20,
        soft_target_min=5,
    )
    codes = [x.code for x in queue]
    assert "510300" in codes
    assert "513100" in codes
    assert "161130" not in codes


def test_cash_or_non_deep_fund_is_excluded_even_if_product_passes():
    queue = build_deep_scan_queue([], [fund("511990", deep=False)], capacity_max=20, soft_target_min=5)
    assert queue == ()


def test_unresolved_other_fund_never_enters_step4_even_if_other_fields_look_good():
    queue = build_deep_scan_queue(
        [],
        [fund("599999", status="PASS", priority="HIGH", category="OTHER")],
        capacity_max=20,
        soft_target_min=5,
    )
    assert queue == ()
