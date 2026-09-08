from trading_skill.notifications import build_stock_scan_report, build_system_test_report


def test_system_test_report_is_chinese_and_summary_first():
    payload = {
        "generated_from_market_snapshot": "2026-09-08T18:27:18+08:00",
        "m5_snapshot": "2026-09-08T21:58:06+08:00",
        "total_symbols": 9,
        "analyzed_symbols": 9,
        "failures": [],
        "m5_failures": [],
        "guardrails": {"real_5m_loaded_symbols": 9},
    }
    title, body = build_system_test_report(
        payload, core_ok=True, shadow_ok=True, core_test_count=154
    )
    assert title == "✅ 系统测试通过"
    assert body.startswith("【结论】")
    assert "核心回归测试：通过（154项）" in body
    assert "真实行情链测试：通过" in body
    assert "真实5分钟数据：9/9" in body
    assert "Shadow" not in body
    assert "SUPPORT" not in body
    assert "NO_TREND" not in body


def test_system_test_report_failure_is_explicit():
    title, body = build_system_test_report(
        {"failures": [{"symbol": "600000", "reason": "示例异常"}]},
        core_ok=False,
        shadow_ok=False,
    )
    assert title == "⚠️ 系统测试异常"
    assert "暂不把本次结果作为正式选股依据" in body
    assert "核心回归测试：失败" in body
    assert "【异常明细】" in body


def test_stock_scan_report_contract():
    scan = {
        "generated_at": "2026-09-08 18:30",
        "total_stocks": 5300,
        "industries_screened": 31,
        "selected_industries": 6,
        "leader_candidates": 28,
        "fundamental_passed": 21,
        "deep_scanned": 21,
        "chan_buy_candidates": 4,
        "candidates": [
            {
                "name": "示例股份",
                "code": "600000",
                "industry": "示例行业",
                "industry_state": "低位升温",
                "leader_rank": "细分前三",
                "signal": "SECOND_BUY",
                "timeframe": "30分钟",
                "parent_structure": "120分钟回调末端",
                "volume_price": "缩量回踩",
                "technical": "SUPPORT",
                "opportunity": "A",
                "risk": "L1",
                "action": "PREPARE_BUY",
                "stop": "跌破结构低点",
                "reason": "行业、龙头、缠论与量价共振",
                "push": True,
            }
        ],
    }
    title, body = build_stock_scan_report(scan)
    assert title == "🎯 全A买点扫描"
    assert "A股股票：5300只" in body
    assert "缠论买点：二买" in body
    assert "技术确认：偏支持" in body
    assert "操作：准备买入" in body
    assert "SECOND_BUY" not in body
    assert "SUPPORT" not in body
    assert "PREPARE_BUY" not in body
