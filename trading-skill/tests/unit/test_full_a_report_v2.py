from scripts.send_full_a_report_v2 import build_report


def test_report_exposes_setup_confirmation_execution_and_sector_policy():
    scan = {
        "total_stocks": 5000,
        "industries_screened": 120,
        "deep_scanned": 40,
        "chan_buy_candidates": 3,
        "candidates": [
            {
                "code": "000001",
                "name": "测试股",
                "push": True,
                "industry": "船舶制造",
                "industry_state": "升温",
                "leader_rank": "细分行业第1候选",
                "fundamental_grade": "A",
                "prospect_theme": "造船与海工",
                "setup_timeframe": "120m",
                "setup_signal_types": ["SECOND_BUY"],
                "confirmation_timeframe": "30m",
                "confirmation_signal_types": ["THIRD_BUY"],
                "execution_timeframe": "5m",
                "execution_signal_types": ["FIRST_BUY"],
                "execution_maturity": "TRIGGERED",
                "chan_labels": ["标准二买", "二买级别嵌套：次级别一买执行"],
                "signal_confirmation_time": "2026-09-09T11:30:00+08:00",
                "rise_since_signal_pct": 1.2,
                "recent_signal_note": "母级买点有效，30分钟确认与5分钟执行结构已就绪",
                "parent_structure": "上级结构通过",
                "volume_price": "成交量：正常",
                "technical": "偏支持",
                "opportunity": "高质量机会",
                "risk": "正常",
                "action": "第一笔买入",
                "buy_point": "10.00～10.15元",
                "stop": "9.80元",
                "stop_basis": "5分钟执行结构失效",
                "add_plan": "新的30分钟/120分钟确认后再加仓",
            }
        ],
    }
    universe = {
        "selected_industries": [{"name": "船舶制造", "prospect_theme": "造船与海工"}],
        "leader_candidates": [
            {
                "code": "000001",
                "fundamental_prefilter": {
                    "policy_name": "ORDER_DRIVEN_CAPITAL_GOODS",
                    "focus_metrics": ["营收增速", "净利润增速", "经营现金流"],
                    "external_metrics_required": ["新签订单", "合同负债"],
                },
            }
        ],
        "deduped_leader_candidates": 1,
        "deep_scan_eligible_candidates": 1,
    }
    title, body = build_report(scan, universe, stage="14:45收盘前扫描")
    assert "全A买点扫描" in title
    assert "母级 setup：120分钟 二买" in body
    assert "30分钟确认：30分钟 三买" in body
    assert "5分钟执行：5分钟 一买｜执行状态：已触发" in body
    assert "策略：ORDER_DRIVEN_CAPITAL_GOODS" in body
    assert "待补行业KPI：新签订单、合同负债" in body
    assert "5分钟执行结构失效" in body


def test_report_states_no_5m_trigger_means_no_first_tranche():
    scan = {
        "total_stocks": 5000,
        "candidates": [
            {
                "code": "000002",
                "name": "观察股",
                "push": False,
                "signal": "二买",
                "timeframe": "120分钟",
                "recent_signal_note": "母级买点仍有效，但5分钟执行触发尚未满足",
                "execution_maturity": "PREPARE",
                "execution_signal_types": [],
            }
        ],
    }
    title, body = build_report(scan, {"selected_industries": []}, stage="13:45盘中扫描")
    assert "全A扫描完成" in title
    assert "没有5分钟结构触发就不执行首仓" in body
