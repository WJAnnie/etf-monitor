from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")

TIMEFRAME_CN = {
    "weekly": "周线",
    "daily": "日线",
    "120m": "120分钟",
    "30m": "30分钟",
    "5m": "5分钟",
}

SIGNAL_CN = {
    "FIRST_BUY": "一买",
    "SECOND_BUY": "二买",
    "THIRD_BUY": "三买",
    "FIRST_SELL": "一卖",
    "SECOND_SELL": "二卖",
    "THIRD_SELL": "三卖",
}

ACTION_CN = {
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

TECHNICAL_CN = {
    "SUPPORT": "偏支持",
    "NEUTRAL": "中性",
    "CAUTION": "谨慎",
    "PAUSE": "暂停执行",
}

VOLUME_CN = {
    "SHRINK": "缩量",
    "NORMAL": "正常",
    "MILD_EXPAND": "温和放量",
    "SIGNIFICANT": "明显放量",
    "EXTREME": "异常巨量",
}

OPPORTUNITY_CN = {
    "S": "顶级机会",
    "A": "高质量机会",
    "B": "可跟踪机会",
    "C": "暂不参与",
}

RISK_CN = {
    "L0": "正常",
    "L1": "预警",
    "L2": "暂停加仓",
    "L3": "结构恶化，考虑减仓",
    "L4": "结构失效，退出",
}


class DeliveryStatus(StrEnum):
    READY = "READY"
    HOLIDAY_SKIP = "HOLIDAY_SKIP"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"


@dataclass(frozen=True, slots=True)
class DeliveryGate:
    status: DeliveryStatus
    message: str
    current_count: int
    total_count: int
    coverage: float


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=CN_TZ)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=CN_TZ) if parsed.tzinfo is None else parsed.astimezone(CN_TZ)


def _expected_time(stage: str) -> time:
    if "14:30" in stage:
        return time(14, 30)
    if "14:50" in stage:
        return time(14, 50)
    return time(15, 0)


def evaluate_delivery_gate(bars: dict, *, stage: str, minimum_coverage: float = 0.80) -> DeliveryGate:
    generated = _parse_time(bars.get("generated_at"))
    if generated is None:
        return DeliveryGate(DeliveryStatus.DATA_INCOMPLETE, "无法确认本次行情生成时间。", 0, 0, 0.0)

    symbols = list(bars.get("symbols") or [])
    if not symbols:
        # 没有财务通过候选时，仍应允许发送“无候选”摘要；不能把零候选误判成休市。
        return DeliveryGate(DeliveryStatus.READY, "本轮没有进入五周期深度分析的股票。", 0, 0, 1.0)

    expected = _expected_time(stage)
    current = 0
    close_daily_complete = 0
    for symbol in symbols:
        m5_rows = list(symbol.get("5m") or [])
        latest = _parse_time((m5_rows[-1] if m5_rows else {}).get("time"))
        if latest and latest.date() == generated.date() and latest.time() >= expected:
            current += 1

        daily_rows = list(symbol.get("daily") or [])
        if daily_rows:
            last_daily = daily_rows[-1]
            last_date = _parse_time(last_daily.get("time"))
            if last_date and last_date.date() == generated.date() and bool(last_daily.get("_complete")):
                close_daily_complete += 1

    total = len(symbols)
    coverage = current / total if total else 1.0
    if current == 0:
        return DeliveryGate(
            DeliveryStatus.HOLIDAY_SKIP,
            "没有发现当日对应时点的5分钟行情，按休市或非交易时段处理，不使用旧数据推送。",
            current,
            total,
            coverage,
        )
    if coverage < minimum_coverage:
        return DeliveryGate(
            DeliveryStatus.DATA_INCOMPLETE,
            f"当日行情覆盖仅{current}/{total}，低于{minimum_coverage:.0%}质量门槛。",
            current,
            total,
            coverage,
        )
    if expected == time(15, 0) and close_daily_complete / total < minimum_coverage:
        return DeliveryGate(
            DeliveryStatus.DATA_INCOMPLETE,
            f"收盘日线确认仅{close_daily_complete}/{total}，未达到收盘确认质量门槛。",
            close_daily_complete,
            total,
            close_daily_complete / total,
        )
    return DeliveryGate(
        DeliveryStatus.READY,
        f"行情覆盖{current}/{total}，达到生产推送标准。",
        current,
        total,
        coverage,
    )


def _replace_embedded(text: str) -> str:
    replacements: dict[str, str] = {}
    replacements.update(TIMEFRAME_CN)
    replacements.update(SIGNAL_CN)
    replacements.update(ACTION_CN)
    replacements.update(TECHNICAL_CN)
    replacements.update(VOLUME_CN)
    replacements.update(OPPORTUNITY_CN)
    replacements.update(RISK_CN)
    # 长词优先，避免短词替换影响长词。
    for source in sorted(replacements, key=len, reverse=True):
        text = text.replace(source, replacements[source])
    return text


def translate_candidate(candidate: dict) -> dict:
    item = deepcopy(candidate)
    timeframe = str(item.get("timeframe") or "")
    if timeframe:
        item["timeframe"] = TIMEFRAME_CN.get(timeframe, timeframe)

    signal = str(item.get("signal") or "")
    if signal:
        item["signal"] = SIGNAL_CN.get(signal, signal)

    action = str(item.get("action") or "")
    if action:
        item["action"] = ACTION_CN.get(action, action)

    technical = str(item.get("technical") or "")
    if technical:
        item["technical"] = TECHNICAL_CN.get(technical, technical)

    opportunity = str(item.get("opportunity") or "")
    if opportunity:
        item["opportunity"] = OPPORTUNITY_CN.get(opportunity, opportunity)

    risk = str(item.get("risk") or "")
    if risk:
        item["risk"] = RISK_CN.get(risk, risk)

    volume_price = str(item.get("volume_price") or "")
    if volume_price:
        for source, target in VOLUME_CN.items():
            volume_price = volume_price.replace(source, target)
        item["volume_price"] = volume_price.replace("成交量:", "成交量：")

    reason = str(item.get("reason") or "")
    if reason:
        item["reason"] = _replace_embedded(reason)
    return item


def translate_scan_for_user(scan: dict) -> dict:
    translated = deepcopy(scan)
    translated["candidates"] = [translate_candidate(item) for item in scan.get("candidates") or []]
    return translated
