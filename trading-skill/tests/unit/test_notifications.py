from trading_skill.notifications import (
    build_holding_monitor_report,
    build_stock_scan_report,
    build_system_test_report,
)


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
                "buy_point": "23.40—23.70元，30分钟二买回踩确认",
                "buy_amount": 12000,
                "buy_quantity": "500股",
                "buy_fraction": "约总资金的6%",
                "add_plan": "新的30分钟或120分钟确认结构出现后再加仓",
                "stop": "跌破22.80元的30分钟结构低点",
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
    assert "建议买入点：23.40—23.70元" in body
    assert "建议首笔资金：约12,000元" in body
    assert "建议首笔仓位：约总资金的6%" in body
    assert "SECOND_BUY" not in body
    assert "SUPPORT" not in body
    assert "PREPARE_BUY" not in body


def test_holding_monitor_report_has_add_and_sell_advice():
    report = {
        "generated_at": "2026-09-08 14:30",
        "summary": "一只持有，一只触发减仓。",
        "positions": [
            {
                "name": "示例股份",
                "code": "600000",
                "cost_price": 23.50,
                "current_price": 24.10,
                "position_value": 12000,
                "pnl_pct": "+2.6%",
                "signal": "SECOND_BUY",
                "timeframe": "30分钟",
                "parent_structure": "120分钟上升结构未破坏",
                "volume_price": "缩量回踩后企稳",
                "technical": "SUPPORT",
                "risk": "L1",
                "action": "HOLD",
                "add_point": "23.80元附近出现新的30分钟二买确认",
                "add_amount": 6000,
                "add_quantity": "200股",
                "sell_point": "30分钟结构失效或日线二卖确认",
                "stop": "22.80元",
                "reduce_amount": 0,
                "reason": "原买入逻辑仍成立",
            },
            {
                "name": "风险样例",
                "code": "600001",
                "cost_price": 10.00,
                "current_price": 9.60,
                "position_value": 9600,
                "pnl_pct": "-4.0%",
                "signal": "SECOND_SELL",
                "timeframe": "30分钟",
                "parent_structure": "120分钟转弱",
                "volume_price": "放量下跌",
                "technical": "CAUTION",
                "risk": "L3",
                "action": "REDUCE_TACTICAL",
                "add_point": "暂停补仓",
                "add_amount": 0,
                "add_quantity": "0股",
                "sell_point": "当前已触发减仓",
                "stop": "9.45元",
                "reduce_amount": 4800,
                "reason": "结构转弱且量价不利",
            },
        ],
    }
    title, body = build_holding_monitor_report(report)
    assert title == "🚨 持仓风险提醒"
    assert "今日建议：持有" in body
    assert "建议补仓资金：约6,000元" in body
    assert "减仓/卖出触发点：当前已触发减仓" in body
    assert "建议减仓金额：约4,800元" in body
    assert "不因浮亏机械摊低成本" in body
    assert "SECOND_BUY" not in body
    assert "REDUCE_TACTICAL" not in body
