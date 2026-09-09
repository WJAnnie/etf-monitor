from __future__ import annotations

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
)


_collect_one_v2 = v2.collect_one


def merge_v3_metadata(result: dict, source: dict) -> dict:
    """把V3预筛阶段的行业/财报/估值上下文完整带到多周期行情结果。

    K线采集只负责行情，不得把上游已经确定的行业画像、轮动状态、近期财报或事件上下文丢失。
    """
    merged = dict(result)
    for key in V3_METADATA_KEYS:
        if key in source:
            merged[key] = source.get(key)
    return merged


def collect_one_v3(item: dict, now):
    return merge_v3_metadata(_collect_one_v2(item, now), item)


v2.collect_one = collect_one_v3


if __name__ == "__main__":
    raise SystemExit(v2.main())
