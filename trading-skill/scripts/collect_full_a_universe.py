from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from trading_skill.a_share_fundamentals import evaluate_prefilter
from trading_skill.a_share_universe import rank_industry_leaders, screen_industries, valid_stock_name

CN_TZ = ZoneInfo("Asia/Shanghai")
TIMEOUT = 12
RETRIES = 3
PAGE_SIZE = 100
MAX_WORKERS = 8
UT = "bd1d9ddb04089700cf9c27f6f7426281"
PUSH2_HOSTS = (
    "https://push2.eastmoney.com/webguest/api/qt/clist/get",
    "https://82.push2.eastmoney.com/webguest/api/qt/clist/get",
    "https://73.push2.eastmoney.com/webguest/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
)
DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SINA_MARKET_CENTER = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
A_SHARE_MARKETS = (
    "m:1 t:2,m:1 t:23",
    "m:0 t:6,m:0 t:80",
    "m:0 t:81 s:2048",
)
INDUSTRY_FS = "m:90 t:2 f:!50"
STOCK_FIELDS = "f2,f3,f5,f6,f8,f9,f12,f13,f14,f20,f21,f23,f24,f25"
INDUSTRY_FIELDS = "f2,f3,f6,f8,f12,f14,f24,f25,f62,f104,f105,f184"


def _headers(*, sina: bool = False) -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://vip.stock.finance.sina.com.cn/" if sina else "https://quote.eastmoney.com/center/",
        "Connection": "close",
    }


def _safe_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "-"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_json(url: str, params: dict[str, object], *, sina: bool = False) -> dict:
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            response = requests.get(url, params=params, headers=_headers(sina=sina), timeout=TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("返回内容不是JSON对象")
            return payload
        except Exception as exc:
            last = exc
            if attempt + 1 < RETRIES:
                time.sleep(0.7 * (attempt + 1) + random.uniform(0.1, 0.4))
    raise RuntimeError(f"行情请求失败: {last}")


def _push2(params: dict[str, object]) -> dict:
    errors: list[str] = []
    for host in PUSH2_HOSTS:
        try:
            payload = _get_json(host, params)
            if payload.get("data") is not None:
                return payload
        except Exception as exc:
            errors.append(f"{host}: {exc}")
    raise RuntimeError("；".join(errors))


def _page(params: dict[str, object], page: int) -> tuple[int, list[dict]]:
    query = dict(params)
    query["pn"] = page
    payload = _push2(query)
    diff = ((payload.get("data") or {}).get("diff") or [])
    return page, [row for row in diff if isinstance(row, dict)]


def fetch_paginated(fs: str, fields: str, *, fid: str, max_pages: int | None = None) -> list[dict]:
    params: dict[str, object] = {
        "pn": 1,
        "pz": PAGE_SIZE,
        "po": 1,
        "np": 1,
        "ut": UT,
        "fltt": 2,
        "invt": 2,
        "fid": fid,
        "fs": fs,
        "fields": fields,
    }
    first_payload = _push2(params)
    data = first_payload.get("data") or {}
    first = [row for row in (data.get("diff") or []) if isinstance(row, dict)]
    total = int(data.get("total") or len(first))
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if max_pages is not None:
        pages = min(pages, max_pages)
    if pages == 1:
        return first

    rows_by_page: dict[int, list[dict]] = {1: first}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_page, params, page): page for page in range(2, pages + 1)}
        for future in as_completed(futures):
            page, rows = future.result()
            rows_by_page[page] = rows
    out: list[dict] = []
    for page in range(1, pages + 1):
        out.extend(rows_by_page.get(page, []))
    return out


def _decode_sina_js(text: str) -> list[dict]:
    raw = text.strip()
    if raw in ("", "null", "[]"):
        return []
    normalized = re.sub(r'([,{])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', raw)
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"新浪列表解析失败: {exc}: {raw[:160]}") from exc
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def _sina_page(page: int) -> tuple[int, list[dict]]:
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            response = requests.get(
                SINA_MARKET_CENTER,
                params={"page": page, "num": 100, "sort": "symbol", "asc": 1, "node": "hs_a", "symbol": "", "_s_r_a": "page"},
                headers=_headers(sina=True),
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            response.encoding = "utf-8"
            return page, _decode_sina_js(response.text)
        except Exception as exc:
            last = exc
            if attempt + 1 < RETRIES:
                time.sleep(0.7 * (attempt + 1) + random.uniform(0.1, 0.4))
    raise RuntimeError(f"新浪全A第{page}页失败: {last}")


def fetch_all_a_sina() -> list[dict]:
    rows: list[dict] = []
    for page in range(1, 81):
        _, items = _sina_page(page)
        if not items:
            break
        for item in items:
            code = str(item.get("code") or "")
            symbol = str(item.get("symbol") or "")
            market = 1 if symbol.startswith("sh") else 0
            name = str(item.get("name") or "")
            if not code or not valid_stock_name(name):
                continue
            rows.append(
                {
                    "f12": code,
                    "f13": market,
                    "f14": name,
                    "f2": item.get("trade"),
                    "f3": item.get("changepercent"),
                    "f6": item.get("amount"),
                    "f8": item.get("turnoverratio"),
                    "f9": item.get("per"),
                    "f20": (_safe_float(item.get("mktcap")) * 10_000),
                    "f21": (_safe_float(item.get("nmc")) * 10_000),
                    "f23": item.get("pb"),
                    "f24": 0,
                    "f25": 0,
                }
            )
    return rows


def fetch_all_a_shares() -> tuple[list[dict], str]:
    rows: list[dict] = []
    eastmoney_errors: list[str] = []
    for fs in A_SHARE_MARKETS:
        try:
            rows.extend(fetch_paginated(fs, STOCK_FIELDS, fid="f3"))
        except Exception as exc:
            eastmoney_errors.append(f"{fs}: {exc}")
            rows = []
            break
    clean = [row for row in rows if valid_stock_name(str(row.get("f14") or "")) and str(row.get("f12") or "")]
    if len(clean) >= 4000:
        return clean, "东方财富分市场"
    sina = fetch_all_a_sina()
    if len(sina) >= 4000:
        return sina, "新浪全A兜底"
    raise RuntimeError(
        f"全A多源均不足：东财={len(clean)} 新浪={len(sina)} 东财错误={'；'.join(eastmoney_errors)[:500]}"
    )


def fetch_industries() -> list[dict]:
    return fetch_paginated(INDUSTRY_FS, INDUSTRY_FIELDS, fid="f3")


def fetch_industry_members(board_code: str) -> list[dict]:
    return fetch_paginated(f"b:{board_code} f:!50", STOCK_FIELDS, fid="f20", max_pages=1)


def fetch_financial_reports(code: str) -> list[dict]:
    payload = _get_json(
        DATACENTER,
        {
            "sortColumns": "REPORTDATE",
            "sortTypes": "-1",
            "pageSize": 12,
            "pageNumber": 1,
            "reportName": "RPT_LICO_FN_CPD",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")',
        },
    )
    result = payload.get("result") or {}
    return [row for row in (result.get("data") or []) if isinstance(row, dict)]


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("full-a-results/universe_latest.json"))
    parser.add_argument("--industry-limit", type=int, default=8)
    parser.add_argument("--leaders-per-industry", type=int, default=3)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    now = datetime.now(CN_TZ)
    all_stocks, all_a_source = fetch_all_a_shares()
    industry_rows = fetch_industries()
    selected = screen_industries(industry_rows, limit=args.industry_limit)

    leaders = []
    member_errors: list[dict] = []
    for industry in selected:
        try:
            members = fetch_industry_members(industry.code)
            leaders.extend(rank_industry_leaders(members, industry=industry, limit=args.leaders_per_industry))
        except Exception as exc:
            member_errors.append({"industry": industry.name, "code": industry.code, "error": str(exc)})

    finance_rows: dict[str, list[dict]] = {}
    finance_errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(leaders)))) as pool:
        futures = {pool.submit(fetch_financial_reports, item.code): item for item in leaders}
        for future in as_completed(futures):
            item = futures[future]
            try:
                finance_rows[item.code] = future.result()
            except Exception as exc:
                finance_errors.append({"code": item.code, "name": item.name, "error": str(exc)})
                finance_rows[item.code] = []

    candidates = []
    for leader in leaders:
        prefilter = evaluate_prefilter(
            finance_rows.get(leader.code, []), as_of=now, pe=leader.pe, pb=leader.pb
        )
        candidates.append(
            {
                **asdict(leader),
                "fundamental_prefilter": {
                    "eligible": prefilter.eligible,
                    "grade": prefilter.grade,
                    "reasons": list(prefilter.reasons),
                    "annual": asdict(prefilter.annual) if prefilter.annual else None,
                    "interim": asdict(prefilter.interim) if prefilter.interim else None,
                },
            }
        )

    eligible = [item for item in candidates if item["fundamental_prefilter"]["eligible"]]
    advancers = sum(1 for row in all_stocks if _safe_float(row.get("f3")) > 0)
    decliners = sum(1 for row in all_stocks if _safe_float(row.get("f3")) < 0)
    payload = {
        "mode": "FULL_A_PHASE1_SCREEN",
        "generated_at": now.isoformat(),
        "all_a_stocks_loaded": len(all_stocks),
        "all_a_source": all_a_source,
        "market_breadth": {"advancers": advancers, "decliners": decliners},
        "industry_rows_loaded": len(industry_rows),
        "selected_industries": [asdict(item) for item in selected],
        "leader_candidates": candidates,
        "fundamental_eligible_candidates": len(eligible),
        "member_errors": member_errors,
        "finance_errors": finance_errors,
        "guardrails": {
            "full_market_first": True,
            "deep_chan_only_after_industry_and_leader_screen": True,
            "industry_weights": {"prospects": 0.40, "low_position": 0.35, "heat": 0.25},
            "st_stocks_excluded": True,
            "this_phase_emits_trade_signals": False,
        },
    }
    atomic_json(args.output, payload)

    print(f"全A加载: {len(all_stocks)}（{all_a_source}）")
    print(f"行业加载: {len(industry_rows)}")
    print("入选行业:", ", ".join(item.name for item in selected))
    print(f"龙头候选: {len(candidates)}，财务预筛通过: {len(eligible)}")
    print(f"行业成员异常: {len(member_errors)}，财报异常: {len(finance_errors)}")

    if args.strict:
        problems = []
        if len(all_stocks) < 4000:
            problems.append(f"全A股票数量异常:{len(all_stocks)}")
        if len(industry_rows) < 100:
            problems.append(f"行业数量异常:{len(industry_rows)}")
        if len(selected) < min(4, args.industry_limit):
            problems.append(f"入选行业过少:{len(selected)}")
        if len(candidates) < min(8, args.industry_limit * args.leaders_per_industry):
            problems.append(f"龙头候选过少:{len(candidates)}")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
