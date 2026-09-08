from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from trading_skill.domain.bar import RawBar
from trading_skill.domain.enums import Timeframe

TZ = ZoneInfo("Asia/Shanghai")
BASE = datetime(2026, 1, 5, 9, 30, tzinfo=TZ)


def make_bar(i: int, o, h, l, c, v=100, *, complete=True, symbol="TEST", timeframe=Timeframe.M30):
    return RawBar.make(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=BASE + timedelta(minutes=30 * i),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
        amount=Decimal(str(v)) * Decimal(str(c)),
        is_complete=complete,
    )


@pytest.fixture
def tick():
    return Decimal("0.01")
