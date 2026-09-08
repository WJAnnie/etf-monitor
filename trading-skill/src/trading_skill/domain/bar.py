from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .enums import Direction, Timeframe
from .models import price_to_ticks, stable_id, to_decimal


@dataclass(frozen=True, slots=True)
class RawBar:
    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    amount: Decimal = Decimal("0")
    is_complete: bool = True
    source: str = "fixture"
    adjustment: str = "forward"

    @classmethod
    def make(
        cls,
        *,
        symbol: str,
        timeframe: Timeframe,
        timestamp: datetime,
        open: Decimal | str | int | float,
        high: Decimal | str | int | float,
        low: Decimal | str | int | float,
        close: Decimal | str | int | float,
        volume: Decimal | str | int | float,
        amount: Decimal | str | int | float = 0,
        is_complete: bool = True,
        source: str = "fixture",
        adjustment: str = "forward",
    ) -> "RawBar":
        return cls(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=timestamp,
            open=to_decimal(open),
            high=to_decimal(high),
            low=to_decimal(low),
            close=to_decimal(close),
            volume=to_decimal(volume),
            amount=to_decimal(amount),
            is_complete=is_complete,
            source=source,
            adjustment=adjustment,
        )

    @property
    def id(self) -> str:
        return stable_id("bar", self.symbol, self.timeframe, self.timestamp.isoformat(), self.source)


@dataclass(frozen=True, slots=True)
class ValidatedBar:
    id: str
    raw: RawBar
    tick_size: Decimal
    open_ticks: int
    high_ticks: int
    low_ticks: int
    close_ticks: int

    @classmethod
    def from_raw(cls, bar: RawBar, tick_size: Decimal) -> "ValidatedBar":
        return cls(
            id=bar.id,
            raw=bar,
            tick_size=tick_size,
            open_ticks=price_to_ticks(bar.open, tick_size),
            high_ticks=price_to_ticks(bar.high, tick_size),
            low_ticks=price_to_ticks(bar.low, tick_size),
            close_ticks=price_to_ticks(bar.close, tick_size),
        )


@dataclass(frozen=True, slots=True)
class ProcessedBar:
    id: str
    symbol: str
    timeframe: Timeframe
    start_timestamp: datetime
    end_timestamp: datetime
    open_ticks: int
    high_ticks: int
    low_ticks: int
    close_ticks: int
    volume: Decimal
    amount: Decimal
    raw_bar_count: int
    source_raw_bar_ids: tuple[str, ...]
    extreme_high_raw_bar_ids: tuple[str, ...]
    extreme_low_raw_bar_ids: tuple[str, ...]
    direction_context: Direction
    is_complete: bool

    @property
    def timestamp(self) -> datetime:
        return self.end_timestamp
