from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any


_STATE_CN = {
    "SUPPORT": "偏支持",
    "NEUTRAL": "中性",
    "CAUTION": "谨慎",
    "PAUSE": "暂停执行",
    "UPTREND": "上涨趋势",
    "DOWNTREND": "下跌趋势",
    "CONSOLIDATION": "盘整",
    "UNRESOLVED": "结构待确认",
    "NO_TREND": "尚未形成明确趋势",
    "OK": "正常",
    "DATA_INCOMPLETE": "数据不完整",
}

_SIGNAL_CN = {
    "FIRST_BUY": "一买",
    "SECOND_BUY": "二买",
    "THIRD_BUY": "三买",
    "FIRST_SELL": "一卖",
    "SECOND_SELL": "二卖",
    "THIRD_SELL": "三卖",
}

_ACTION_CN = {
    "OBSERVE": "观察",
    "WAIT_2B": "等待二买",
    "PREPARE_BUY": "准备买入",
    "BUY_TRANCHE_1": "第一笔买入",
    "ADD_TRANCHE_2": "第二笔加仓",
    "ADD_TREND": "趋势加仓",
    "HOLD": "持有",
    "PAUSE_ADD": "暂停加仓",
    "REDUCE_TACTICAL": "减战术仓",
    "REDUCE_CORE": "减核心仓",
    "EXIT": "退出",
}


def cn(value: Any, default: str = "暂无") -> str:
    if value is None or value == "":
        return default
    text = str(value)
    return _STATE_CN.get(text, _SIGNAL_CN.get(text, _ACTION_CN.get(text, text)))


def _requests():
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("飞书发送需要 requests 依赖") from exc
    return requests


def _notify_webhook(title: str, body: str) -> str:
    requests = _requests()
    webhook = os.getenv("FEISHU_WEBHOOK", "").strip()
    if not webhook:
        raise RuntimeError("未配置飞书机器人地址")
    payload: dict[str, object] = {"msg_type": "text", "content": {"text": f"{title}\n{body}"}}
    secret = os.getenv("FEISHU_SECRET", "").strip()
    if secret:
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        payload["timestamp"] = timestamp
        payload["sign"] = base64.b64encode(digest).decode("utf-8")
    response = requests.post(webhook, json=payload, timeout=12)
    response.raise_for_status()
    result = response.json()
    code = result.get("code", result.get("StatusCode", 0))
    if str(code) not in ("0", "None"):
        raise RuntimeError(f"飞书机器人发送失败：{result}")
    return "飞书机器人"


def _tenant_token() -> str:
    requests = _requests()
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise RuntimeError("未配置飞书应用凭证")
    response = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=12,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("code") not in (0, "0", None) or not result.get("tenant_access_token"):
        raise RuntimeError(f"飞书应用认证失败：{result}")
    return str(result["tenant_access_token"])


def _notify_app(title: str, body: str) -> str:
    requests = _requests()
    chat_id = os.getenv("FEISHU_CHAT_ID", "").strip()
    if not chat_id:
        raise RuntimeError("未配置飞书群聊编号")
    token = _tenant_token()
    response = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        headers={"Authorization": f"Bearer {token}"},
        json={
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": f"{title}\n{body}"}, ensure_ascii=False),
        },
        timeout=12,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("code") not in (0, "0", None):
        raise RuntimeError(f"飞书应用发送失败：{result}")
    return "飞书应用机器人"


def notify_feishu(title: str, body: str) -> str:
    # 仅飞书；这里没有 Server 酱路径。
    if os.getenv("FEISHU_WEBHOOK", "").strip():
        return _notify_webhook(title, body)
    return _notify_app(title, body)


def build_system_test_report(
    payload: dict | None,
    *,
    core_ok: bool,
    shadow_ok: bool,
    core_test_count: int | None = None,
    note: str | None = None,
) -> tuple[str, str]:
    payload = payload or {}
    failures = payload.get("failures") or []
    m5_failures = payload.get("m5_failures") or []
    total = int(payload.get("total_symbols") or 0)
    analyzed = int(payload.get("analyzed_symbols") or 0)
    m5_loaded = int((payload.get("guardrails") or {}).get("real_5m_loaded_symbols") or 0)
    all_ok = core_ok and shadow_ok and not failures and not m5_failures
    title = "✅ 系统测试通过" if all_ok else "⚠️ 系统测试异常"

    test_text = "通过" if core_ok else "失败"
    if core_test_count is not None:
        test_text += f"（{core_test_count}项）"
    shadow_text = "通过" if shadow_ok else "失败"

    lines = [
        f"【结论】{'当前系统运行正常，可以继续影子测试。' if all_ok else '发现异常，暂不把本次结果作为正式选股依据。'}",
        "",
        "【程序测试】",
        f"核心回归测试：{test_text}",
        f"真实行情链测试：{shadow_text}",
        "",
        "【行情数据】",
        f"基础行情快照：{payload.get('generated_from_market_snapshot') or '未生成'}",
        f"5分钟行情快照：{payload.get('m5_snapshot') or '未生成'}",
        f"分析标的：{analyzed}/{total}" if total else "分析标的：未完成",
        f"真实5分钟数据：{m5_loaded}/{total}" if total else "真实5分钟数据：未完成",
        f"基础行情异常：{len(failures)}",
        f"5分钟行情异常：{len(m5_failures)}",
        "",
        "【关键约束】",
        "5分钟只使用真实5分钟K线，不用15分钟反推。",
        "当前仍为影子测试，不连接券商、不自动下单。",
    ]
    if note:
        lines.extend(["", f"【补充说明】{note}"])
    if failures or m5_failures:
        lines.extend(["", "【异常明细】"])
        lines.extend(f"基础行情：{x}" for x in failures[:10])
        lines.extend(f"5分钟行情：{x}" for x in m5_failures[:10])
    return title, "\n".join(lines)


def build_stock_scan_report(scan: dict) -> tuple[str, str]:
    candidates = list(scan.get("candidates") or [])
    confirmed = [x for x in candidates if x.get("push") is not False]
    title = "🎯 全A买点扫描" if confirmed else "📊 全A扫描完成"
    lines = [
        f"【扫描时间】{scan.get('generated_at') or '暂无'}",
        "",
        "【市场筛选】",
        f"A股股票：{scan.get('total_stocks', 0)}只",
        f"行业初筛：{scan.get('industries_screened', 0)}个",
        f"重点细分行业：{scan.get('selected_industries', 0)}个",
        f"龙头/前三候选：{scan.get('leader_candidates', 0)}只",
        f"基本面通过：{scan.get('fundamental_passed', 0)}只",
        f"深度缠论分析：{scan.get('deep_scanned', 0)}只",
        f"出现缠论买点：{scan.get('chan_buy_candidates', 0)}只",
        f"最终确认：{len(confirmed)}只",
    ]
    if not confirmed:
        lines.extend(["", "【结论】今天没有达到推送标准的股票，不为了凑数量而降低条件。"])
        return title, "\n".join(lines)

    lines.extend(["", "【今日候选】"])
    for idx, item in enumerate(confirmed[:10], 1):
        signal = item.get("signal") or item.get("chan_signal")
        lines.extend([
            "",
            f"{idx}. {item.get('name', '未知')}（{item.get('code', '')}）",
            f"行业：{item.get('industry', '暂无')}｜行业状态：{item.get('industry_state', '暂无')}",
            f"行业地位：{item.get('leader_rank', '暂无')}",
            f"缠论买点：{cn(signal)}｜级别：{item.get('timeframe', '暂无')}",
            f"上级结构：{item.get('parent_structure', '暂无')}",
            f"量价：{item.get('volume_price', '暂无')}｜技术确认：{cn(item.get('technical'))}",
            f"机会等级：{item.get('opportunity', '暂无')}｜风险：{item.get('risk', '暂无')}",
            f"操作：{cn(item.get('action'))}",
            f"结构止损：{item.get('stop', '暂无')}",
            f"入选理由：{item.get('reason', '暂无')}",
        ])
    lines.extend(["", "说明：缠论决定是否具备买点，量价和其他指标只做确认或暂停执行。"])
    return title, "\n".join(lines)
