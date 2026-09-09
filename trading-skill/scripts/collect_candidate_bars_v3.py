from __future__ import annotations

from datetime import datetime, timedelta

import scripts.collect_candidate_bars_v2 as v2


V3_METADATA_KEYS = (
    "industry_rotation_state",
    "industry_analysis_profile",
    "candidate_route",
    "recent_report",
    "sector_financial_metrics",
    "sector_observation_override",
    "sector_observation_reason",
    "pe",
    "pb",
    "industry_events",
    "prospect_theme",
    "industry_name",
    "industry_selection_reason",
    "industry_context_complete",
    "industry_context_note",
)


_fetch_daily_v2 = v2.fetch_daily_v2
_collect_one_v2 = v2.collect_one


def tencent_daily_long(code: str, *, limit: int = v2.DAILY_LIMIT, page_size: int = 500) -> tuple[list[dict], list[str]]:
    """按结束日期向前分段抓取腾讯前复权日线，突破单次返回条数限制。

    腾讯日线单次请求经常只返回略少于请求数量的历史K线，因此“短页”不能视为上市初期。
    V3只在空页、没有新增交易日、达到目标长度或达到最大翻页次数时停止；新股翻到上市日前会
    自然得到空页/重复页，不补造K线。
    """
    symbol = v2.tx_symbol(code)
    collected: dict[str, dict] = {}
    warnings: list[str] = []
    end_date = ""
    max_pages = max(2, (max(1, limit) + max(1, page_size) - 1) // max(1, page_size) + 2)

    for page_no in range(1, max_pages + 1):
        remaining = max(1, limit - len(collected))
        count = min(max(1, page_size), remaining)
        try:
            response = v2._request(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                params={"param": f"{symbol},day,,{end_date},{count},qfq"},
                referer="https://gu.qq.com/",
            )
            payload = v2._decode_json_or_jsonp(response.text)
            stock = (payload.get("data") or {}).get(symbol) or {}
            page_rows = v2._parse_array(stock.get("qfqday") or stock.get("day") or [])
        except Exception as exc:
            warnings.append(f"腾讯前复权分段第{page_no}页:{exc}")
            break

        if not page_rows:
            break

        before = len(collected)
        for row in page_rows:
            day = str(row.get("time") or "")[:10]
            if day:
                collected[day] = row
        if len(collected) == before:
            break
        if len(collected) >= limit:
            break

        dated_rows = [str(row.get("time") or "")[:10] for row in page_rows if row.get("time")]
        if not dated_rows:
            warnings.append(f"腾讯前复权分段第{page_no}页缺少日期")
            break
        earliest_text = min(dated_rows)
        try:
            earliest = datetime.fromisoformat(earliest_text)
        except ValueError:
            warnings.append(f"腾讯前复权分段日期异常:{earliest_text}")
            break
        end_date = (earliest - timedelta(days=1)).strftime("%Y-%m-%d")

    rows = [collected[key] for key in sorted(collected)]
    return rows[-limit:], warnings


def fetch_daily_v3(code: str, market: int) -> tuple[list[dict], str, list[str]]:
    """V3日线：优先使用腾讯前复权分段长历史，失败时回退V2供应链。"""
    warnings: list[str] = []
    rows, tx_warnings = tencent_daily_long(code, limit=v2.DAILY_LIMIT)
    warnings.extend(tx_warnings)
    if len(rows) >= v2.MIN_DAILY:
        return rows[-v2.DAILY_LIMIT :], "腾讯前复权分段", warnings

    try:
        fallback_rows, fallback_source, fallback_warnings = _fetch_daily_v2(code, market)
        warnings.extend(fallback_warnings)
        return fallback_rows, fallback_source, warnings
    except Exception as exc:
        warnings.append(f"V2日线回退:{exc}")
        raise RuntimeError("；".join(warnings)) from exc


def merge_v3_metadata(result: dict, source: dict) -> dict:
    """把V3预筛阶段的行业/财报/估值上下文完整带到多周期行情结果。

    K线采集只负责行情，不得把上游已经确定的行业画像、轮动状态、近期财报、事件上下文，
    以及跨行业真实行业解析状态丢失。后者直接参与“未解析真实行业时禁止新开仓”的安全门。
    """
    merged = dict(result)
    for key in V3_METADATA_KEYS:
        if key in source:
            merged[key] = source.get(key)
    return merged


def collect_one_v3(item: dict, now):
    return merge_v3_metadata(_collect_one_v2(item, now), item)


# V2的collect_one在运行时读取模块全局函数，因此在V3入口统一替换数据供应链即可。
v2.fetch_daily_v2 = fetch_daily_v3
v2.collect_one = collect_one_v3


if __name__ == "__main__":
    raise SystemExit(v2.main())
