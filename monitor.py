from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

CN_TZ = ZoneInfo("Asia/Shanghai")
TIMEOUT = 12
RSI_PERIOD = 6
RSI_THRESHOLD = 20.0


@dataclass(frozen=True)
class ETF:
    name: str
    code: str
    secid: str
    tx_symbol: str


ETFS = [
    ETF("红利低波ETF", "512890", "1.512890", "sh512890"),
    ETF("中证红利ETF", "515080", "1.515080", "sh515080"),
    ETF("红利低波50ETF", "515450", "1.515450", "sh515450"),
    ETF("红利低波100ETF", "515100", "1.515100", "sh515100"),
]


class MarketDataError(RuntimeError):
    pass


def request_with_retry(
    url: str,
    *,
    params: dict | None = None,
    referer: str | None = None,
    retries: int = 3,
) -> requests.Response:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36",
        "Accept": "*/*",
        "Connection": "close",
    }
    if referer:
        headers["Referer"] = referer

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                # 避免多个 ETF 在同一秒重复撞上上游瞬时 5xx / 连接重置。
                time.sleep((1.3 * (attempt + 1)) + random.uniform(0.15, 0.65))
    raise MarketDataError(f"请求失败: {last_error}")


def get_eastmoney_json(url: str, params: dict) -> dict:
    resp = request_with_retry(url, params=params, referer="https://quote.eastmoney.com/")
    try:
        data = resp.json()
    except ValueError as exc:
        raise MarketDataError(f"东方财富返回非 JSON: {resp.text[:120]!r}") from exc
    if not isinstance(data, dict):
        raise MarketDataError("东方财富返回格式异常")
    return data


def fetch_tencent_realtime(etf: ETF) -> tuple[float, datetime]:
    resp = request_with_retry(
        f"https://qt.gtimg.cn/q={etf.tx_symbol}",
        referer="https://gu.qq.com/",
    )
    # 腾讯实时行情接口为 GBK 文本：v_shxxxxxx="...~现价~...~时间~...";
    resp.encoding = "gbk"
    text = resp.text.strip()
    match = re.search(r'=\"(.*)\";?$', text)
    if not match:
        raise MarketDataError(f"腾讯实时行情格式异常: {text[:100]!r}")
    fields = match.group(1).split("~")
    if len(fields) <= 30:
        raise MarketDataError(f"腾讯实时行情字段不足: {len(fields)}")
    try:
        price = float(fields[3])
        quote_time = datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(tzinfo=CN_TZ)
    except (ValueError, TypeError) as exc:
        raise MarketDataError(f"腾讯实时行情解析失败: {exc}") from exc
    if price <= 0:
        raise MarketDataError(f"腾讯实时行情价格异常: {price}")
    return price, quote_time


def fetch_tencent_daily_closes(etf: ETF, limit: int = 60) -> list[tuple[str, float]]:
    resp = request_with_retry(
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
        params={"param": f"{etf.tx_symbol},day,,,{limit},qfq"},
        referer="https://gu.qq.com/",
    )
    text = resp.text.strip()
    try:
        payload = resp.json()
    except ValueError:
        # 某些节点会返回 JSONP：kline_dayqfq={...}
        json_text = text.split("=", 1)[1] if "=" in text else text
        try:
            payload = json.loads(json_text)
        except ValueError as exc:
            raise MarketDataError(f"腾讯日线返回非 JSON: {text[:120]!r}") from exc

    stock_data = (payload.get("data") or {}).get(etf.tx_symbol) or {}
    klines = stock_data.get("qfqday") or stock_data.get("day") or []
    result: list[tuple[str, float]] = []
    for row in klines:
        if not isinstance(row, list) or len(row) < 3:
            continue
        try:
            result.append((str(row[0]), float(row[2])))
        except (TypeError, ValueError):
            continue
    if len(result) < RSI_PERIOD + 2:
        raise MarketDataError(f"腾讯历史日线不足: {len(result)} 条")
    return result


def fetch_eastmoney_realtime(etf: ETF) -> tuple[float, datetime]:
    payload = get_eastmoney_json(
        "https://push2.eastmoney.com/api/qt/stock/get",
        {"secid": etf.secid, "fields": "f43,f57,f58,f59,f60,f124"},
    )
    data = payload.get("data") or {}
    raw_price = data.get("f43")
    decimals = data.get("f59")
    timestamp = data.get("f124")
    if raw_price in (None, "-") or timestamp in (None, "-"):
        raise MarketDataError("东方财富实时行情缺失")
    try:
        scale = 10 ** int(decimals or 3)
        price = float(raw_price) / scale
        quote_time = datetime.fromtimestamp(int(timestamp), CN_TZ)
    except (TypeError, ValueError, OSError) as exc:
        raise MarketDataError(f"东方财富实时行情解析失败: {exc}") from exc
    return price, quote_time


def fetch_eastmoney_daily_closes(etf: ETF, limit: int = 60) -> list[tuple[str, float]]:
    payload = get_eastmoney_json(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        {
            "secid": etf.secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "beg": "0",
            "end": "20500101",
            "lmt": str(limit),
        },
    )
    data = payload.get("data") or {}
    klines = data.get("klines") or []
    result: list[tuple[str, float]] = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 3:
            continue
        try:
            result.append((parts[0], float(parts[2])))
        except ValueError:
            continue
    if len(result) < RSI_PERIOD + 2:
        raise MarketDataError(f"东方财富历史日线不足: {len(result)} 条")
    return result


def fetch_market_data(etf: ETF) -> tuple[float, datetime, list[tuple[str, float]], str]:
    """腾讯优先，东方财富兜底；实时与历史可分别切换数据源。"""
    realtime_errors: list[str] = []
    daily_errors: list[str] = []

    try:
        price, quote_time = fetch_tencent_realtime(etf)
        realtime_source = "腾讯"
    except Exception as exc:
        realtime_errors.append(f"腾讯实时: {exc}")
        print(f"[WARN] {etf.code} 腾讯实时失败，切换东方财富: {exc}")
        try:
            price, quote_time = fetch_eastmoney_realtime(etf)
            realtime_source = "东财"
        except Exception as fallback_exc:
            realtime_errors.append(f"东财实时: {fallback_exc}")
            raise MarketDataError("；".join(realtime_errors)) from fallback_exc

    try:
        history = fetch_tencent_daily_closes(etf)
        daily_source = "腾讯"
    except Exception as exc:
        daily_errors.append(f"腾讯日线: {exc}")
        print(f"[WARN] {etf.code} 腾讯日线失败，切换东方财富: {exc}")
        try:
            history = fetch_eastmoney_daily_closes(etf)
            daily_source = "东财"
        except Exception as fallback_exc:
            daily_errors.append(f"东财日线: {fallback_exc}")
            raise MarketDataError("；".join(daily_errors)) from fallback_exc

    source = realtime_source if realtime_source == daily_source else f"{realtime_source}实时+{daily_source}日线"
    return price, quote_time, history, source


def cn_sma(values: Iterable[float], n: int, m: int = 1) -> list[float]:
    seq = list(values)
    if not seq:
        return []
    out = [seq[0]]
    for value in seq[1:]:
        out.append((m * value + (n - m) * out[-1]) / n)
    return out


def rsi_cn(closes: list[float], period: int = 6) -> float:
    if len(closes) < period + 2:
        raise ValueError("收盘价样本不足")
    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(x, 0.0) for x in diffs]
    abs_moves = [abs(x) for x in diffs]
    avg_gain = cn_sma(gains, period, 1)[-1]
    avg_abs = cn_sma(abs_moves, period, 1)[-1]
    if avg_abs == 0:
        return 0.0
    return 100.0 * avg_gain / avg_abs


def build_intraday_closes(history: list[tuple[str, float]], realtime_price: float, today: str) -> list[float]:
    rows = list(history)
    if rows and rows[-1][0] == today:
        rows[-1] = (today, realtime_price)
    else:
        rows.append((today, realtime_price))
    return [close for _, close in rows]


def get_feishu_tenant_access_token() -> str:
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise RuntimeError("FEISHU_APP_ID / FEISHU_APP_SECRET 未配置")

    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") not in (0, "0", None):
        raise RuntimeError(f"获取飞书 tenant_access_token 失败: code={result.get('code')}, msg={result.get('msg')}")
    token = result.get("tenant_access_token")
    if not token:
        raise RuntimeError("飞书未返回 tenant_access_token")
    return str(token)


def notify_feishu(title: str, body: str) -> str:
    chat_id = os.getenv("FEISHU_CHAT_ID", "").strip()
    if not chat_id:
        raise RuntimeError("FEISHU_CHAT_ID 未配置")

    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise RuntimeError("FEISHU_APP_ID / FEISHU_APP_SECRET 未配置")

    token = get_feishu_tenant_access_token()
    payload = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": f"{title}\n{body}"}, ensure_ascii=False),
    }
    resp = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") not in (0, "0", None):
        raise RuntimeError(f"飞书通知失败: code={result.get('code')}, msg={result.get('msg')}")
    return "飞书"


def serverchan_channel_name(sendkey: str) -> str:
    if sendkey.startswith("sctp"):
        return "Server酱³ App（sctp，不是微信）"
    if sendkey.startswith("SCT"):
        return "Server酱 Turbo（可推微信）"
    return "Server酱"


def notify_serverchan(title: str, body: str) -> str:
    sendkey = os.getenv("SERVERCHAN_SENDKEY", "").strip()
    if not sendkey:
        raise RuntimeError("SERVERCHAN_SENDKEY 未配置")

    channel_name = serverchan_channel_name(sendkey)
    if sendkey.startswith("sctp"):
        match = re.match(r"^sctp(\d+)t", sendkey)
        if not match:
            raise RuntimeError("Server酱³ SendKey 格式异常：应为 sctp<uid>t<token>")
        uid = match.group(1)
        url = f"https://{uid}.push.ft07.com/send/{quote(sendkey)}.send"
    else:
        url = f"https://sctapi.ftqq.com/{quote(sendkey)}.send"

    # 官方 Python 示例使用 JSON；成功必须明确返回 code=0，不能把缺少 code 的异常响应误判为成功。
    resp = requests.post(
        url,
        json={"title": title.replace("\n", " ")[:32], "desp": body},
        headers={"Content-Type": "application/json;charset=utf-8"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    try:
        result = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"Server酱返回非 JSON: HTTP {resp.status_code}, body={resp.text[:180]!r}") from exc

    code = result.get("code")
    if str(code) != "0":
        message = result.get("message") or result.get("msg") or result
        raise RuntimeError(f"Server酱通知失败: code={code}, message={message}")

    # 不打印 SendKey，只输出上游确认信息。
    push_id = (result.get("data") or {}).get("pushid") if isinstance(result.get("data"), dict) else None
    if push_id:
        print(f"[INFO] {channel_name} pushid={push_id}")
    if sendkey.startswith("sctp"):
        print("[WARN] 当前 SERVERCHAN_SENDKEY 为 sctp 开头，只会推到 Server酱³ App；若要微信请改用 SCT 开头的 Turbo SendKey。")
    return channel_name


def send_notifications(title: str, body: str) -> None:
    errors = []
    for default_name, func in (("飞书", notify_feishu), ("Server酱", notify_serverchan)):
        try:
            actual_name = func(title, body)
            print(f"[OK] {actual_name or default_name} 通知接口返回成功")
        except Exception as exc:
            errors.append(f"{default_name}: {exc}")
            print(f"[ERROR] {default_name} 通知失败: {exc}")
    if len(errors) == 2:
        raise RuntimeError("；".join(errors))


def main() -> int:
    now = datetime.now(CN_TZ)
    today = now.strftime("%Y-%m-%d")
    force_notify = os.getenv("FORCE_NOTIFY", "false").lower() == "true"

    if now.weekday() >= 5 and not force_notify:
        print(f"{today} 为周末，跳过。")
        return 0

    rows: list[tuple[ETF, float, float, datetime, str]] = []
    errors: list[str] = []

    for etf in ETFS:
        try:
            price, quote_time, history, source = fetch_market_data(etf)
            if quote_time.strftime("%Y-%m-%d") != today and not force_notify:
                print(f"{today} 无当日行情（最新 {quote_time:%Y-%m-%d %H:%M}），判定为非交易日，跳过。")
                return 0
            closes = build_intraday_closes(history, price, today)
            rsi = rsi_cn(closes, RSI_PERIOD)
            rows.append((etf, price, rsi, quote_time, source))
            print(
                f"{etf.name} {etf.code}: price={price:.3f}, RSI6={rsi:.2f}, "
                f"quote={quote_time:%F %T}, source={source}"
            )
        except Exception as exc:
            errors.append(f"{etf.name}({etf.code}): {exc}")
            print(f"[ERROR] {errors[-1]}")

    if not rows:
        raise RuntimeError("所有标的行情获取失败")

    triggered = [row for row in rows if row[2] < RSI_THRESHOLD]
    lines = [f"检查时间：{now:%Y-%m-%d %H:%M}（北京时间）", ""]
    for etf, price, rsi, _, source in rows:
        mark = "🚨 RSI<20，进入定投观察区" if rsi < RSI_THRESHOLD else "—"
        lines.append(f"{etf.name} {etf.code}｜现价 {price:.3f}｜RSI(6) {rsi:.2f}｜{mark}｜数据:{source}")
    if errors:
        lines.extend(["", f"⚠️ 行情异常（{len(errors)}/{len(ETFS)}）：", *[f"- {x}" for x in errors]])

    if triggered:
        title = f"🚨 红利ETF定投提醒：{len(triggered)}只 RSI(6)<20"
        lines.extend(["", "提示：RSI 仅是技术指标，请结合仓位、估值和自己的定投纪律执行。"])
        send_notifications(title, "\n".join(lines))
    elif errors:
        # 监控数据不完整时主动告警，避免某只 ETF 恰好进入 RSI<20 却因行情源故障漏报。
        title = f"⚠️ 红利ETF监控异常：{len(errors)}只行情失败"
        lines.extend(["", "本次 RSI 监控数据不完整，请检查 Actions 日志；下次任务仍会自动重试双数据源。"])
        send_notifications(title, "\n".join(lines))
    elif force_notify:
        title = "✅ 红利ETF RSI(6) 监控测试"
        lines.extend(["", "本次为强制测试通知；当前没有标的触发 RSI(6)<20 也会发送。"])
        send_notifications(title, "\n".join(lines))
    else:
        print("当前 4 只 ETF 行情完整，且没有 RSI(6)<20 的标的，不发送通知。")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        sys.exit(1)
