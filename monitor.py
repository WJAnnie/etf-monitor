from __future__ import annotations

import json
import os
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


ETFS = [
    ETF("红利低波ETF", "512890", "1.512890"),
    ETF("中证红利ETF", "515080", "1.515080"),
    ETF("红利低波50ETF", "515450", "1.515450"),
    ETF("红利低波100ETF", "515100", "1.515100"),
]


class MarketDataError(RuntimeError):
    pass


def get_json(url: str, params: dict, retries: int = 3) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (GitHub Actions RSI monitor)",
        "Referer": "https://quote.eastmoney.com/",
    }
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise MarketDataError("行情接口返回格式异常")
            return data
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    raise MarketDataError(f"行情请求失败: {last_error}")


def fetch_realtime(etf: ETF) -> tuple[float, datetime]:
    payload = get_json(
        "https://push2.eastmoney.com/api/qt/stock/get",
        {"secid": etf.secid, "fields": "f43,f57,f58,f59,f60,f124"},
    )
    data = payload.get("data") or {}
    raw_price = data.get("f43")
    decimals = data.get("f59")
    timestamp = data.get("f124")
    if raw_price in (None, "-") or timestamp in (None, "-"):
        raise MarketDataError(f"{etf.code} 实时行情缺失")
    try:
        scale = 10 ** int(decimals or 3)
        price = float(raw_price) / scale
        quote_time = datetime.fromtimestamp(int(timestamp), CN_TZ)
    except (TypeError, ValueError, OSError) as exc:
        raise MarketDataError(f"{etf.code} 实时行情解析失败: {exc}") from exc
    return price, quote_time


def fetch_daily_closes(etf: ETF, limit: int = 60) -> list[tuple[str, float]]:
    payload = get_json(
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
        raise MarketDataError(f"{etf.code} 历史日线不足")
    return result


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


def notify_feishu(title: str, body: str) -> None:
    chat_id = os.getenv("FEISHU_CHAT_ID", "").strip()
    if not chat_id:
        print("[WARN] FEISHU_CHAT_ID 未配置，跳过飞书通知")
        return

    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        print("[WARN] FEISHU_APP_ID / FEISHU_APP_SECRET 未配置，跳过飞书通知")
        return

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


def notify_serverchan(title: str, body: str) -> None:
    sendkey = os.getenv("SERVERCHAN_SENDKEY", "").strip()
    if not sendkey:
        print("[WARN] SERVERCHAN_SENDKEY 未配置，跳过 Server酱通知")
        return

    if sendkey.startswith("sctp"):
        match = re.match(r"^sctp(\d+)t", sendkey)
        if not match:
            raise RuntimeError("Server酱³ SendKey 格式异常：应为 sctp<uid>t<token>")
        uid = match.group(1)
        url = f"https://{uid}.push.ft07.com/send/{quote(sendkey)}.send"
    else:
        url = f"https://sctapi.ftqq.com/{quote(sendkey)}.send"

    resp = requests.post(url, data={"title": title.replace("\n", " "), "desp": body}, timeout=TIMEOUT)
    resp.raise_for_status()
    try:
        result = resp.json()
    except ValueError:
        result = {"raw": resp.text[:200]}
    code = result.get("code")
    if code not in (0, "0", None):
        raise RuntimeError(f"Server酱通知失败: {result}")


def send_notifications(title: str, body: str) -> None:
    errors = []
    for name, func in (("飞书", notify_feishu), ("Server酱", notify_serverchan)):
        try:
            func(title, body)
            print(f"[OK] {name} 通知完成")
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            print(f"[ERROR] {name} 通知失败: {exc}")
    if len(errors) == 2:
        raise RuntimeError("；".join(errors))


def main() -> int:
    now = datetime.now(CN_TZ)
    today = now.strftime("%Y-%m-%d")
    force_notify = os.getenv("FORCE_NOTIFY", "false").lower() == "true"

    if now.weekday() >= 5 and not force_notify:
        print(f"{today} 为周末，跳过。")
        return 0

    rows: list[tuple[ETF, float, float, datetime]] = []
    errors: list[str] = []

    for etf in ETFS:
        try:
            price, quote_time = fetch_realtime(etf)
            if quote_time.strftime("%Y-%m-%d") != today and not force_notify:
                print(f"{today} 无当日行情（最新 {quote_time:%Y-%m-%d %H:%M}），判定为非交易日，跳过。")
                return 0
            history = fetch_daily_closes(etf)
            closes = build_intraday_closes(history, price, today)
            rsi = rsi_cn(closes, RSI_PERIOD)
            rows.append((etf, price, rsi, quote_time))
            print(f"{etf.name} {etf.code}: price={price:.3f}, RSI6={rsi:.2f}, quote={quote_time:%F %T}")
        except Exception as exc:
            errors.append(f"{etf.name}({etf.code}): {exc}")
            print(f"[ERROR] {errors[-1]}")

    if not rows:
        raise RuntimeError("所有标的行情获取失败")

    triggered = [row for row in rows if row[2] < RSI_THRESHOLD]
    lines = [f"检查时间：{now:%Y-%m-%d %H:%M}（北京时间）", ""]
    for etf, price, rsi, _ in rows:
        mark = "🚨 RSI<20，进入定投观察区" if rsi < RSI_THRESHOLD else "—"
        lines.append(f"{etf.name} {etf.code}｜现价 {price:.3f}｜RSI(6) {rsi:.2f}｜{mark}")
    if errors:
        lines.extend(["", "行情异常：", *[f"- {x}" for x in errors]])

    if triggered:
        title = f"🚨 红利ETF定投提醒：{len(triggered)}只 RSI(6)<20"
        lines.extend(["", "提示：RSI 仅是技术指标，请结合仓位、估值和自己的定投纪律执行。"])
        send_notifications(title, "\n".join(lines))
    elif force_notify:
        title = "✅ 红利ETF RSI(6) 监控测试"
        lines.extend(["", "本次为强制测试通知；当前没有标的触发 RSI(6)<20 也会发送。"])
        send_notifications(title, "\n".join(lines))
    else:
        print("当前没有 RSI(6)<20 的标的，不发送通知。")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        sys.exit(1)
