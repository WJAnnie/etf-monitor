from __future__ import annotations

from datetime import date

# 上海证券交易所发布的 2026 年休市安排中的工作日休市日期。
# 未列入已知安排的未来工作日按“预期交易日”处理；如果行情源缺失，
# 质量门会将其判为 NOT_READY，而不会冒充节假日。
CN_EXCHANGE_CLOSED_WEEKDAYS_2026 = frozenset(
    {
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 2, 16),
        date(2026, 2, 17),
        date(2026, 2, 18),
        date(2026, 2, 19),
        date(2026, 2, 20),
        date(2026, 2, 23),
        date(2026, 4, 6),
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 6, 19),
        date(2026, 9, 25),
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 5),
        date(2026, 10, 6),
        date(2026, 10, 7),
    }
)


def calendar_status(day: date) -> dict[str, object]:
    if day.weekday() >= 5:
        return {
            "expected_trading_day": False,
            "reason": "weekend",
        }
    if day in CN_EXCHANGE_CLOSED_WEEKDAYS_2026:
        return {
            "expected_trading_day": False,
            "reason": "scheduled_exchange_closure",
        }
    return {
        "expected_trading_day": True,
        "reason": "weekday_not_listed_as_scheduled_closure",
    }


def is_expected_trading_day(day: date) -> bool:
    return bool(calendar_status(day)["expected_trading_day"])
