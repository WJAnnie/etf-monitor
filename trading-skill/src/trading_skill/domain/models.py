from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from typing import Any, Iterable

from .enums import IssueSeverity


@dataclass(frozen=True, slots=True)
class EngineIssue:
    code: str
    severity: IssueSeverity
    engine: str
    object_id: str | None
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime | None = None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    reason_codes: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EngineMetadata:
    schema_version: str = "1.0.0"
    skill_version: str = "0.1.0"
    chan_engine_version: str = "0.1.0"


def to_decimal(value: Decimal | str | int | float) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def price_to_ticks(value: Decimal | str | int | float, tick_size: Decimal) -> int:
    d = to_decimal(value)
    tick = to_decimal(tick_size)
    if tick <= 0:
        raise ValueError("tick_size must be positive")
    return int((d / tick).to_integral_value(rounding=ROUND_HALF_UP))


def ticks_to_price(ticks: int, tick_size: Decimal) -> Decimal:
    return Decimal(ticks) * tick_size


def stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    digest = sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def unique_preserve_order(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return tuple(out)
