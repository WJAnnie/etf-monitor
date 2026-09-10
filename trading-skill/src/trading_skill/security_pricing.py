from __future__ import annotations

from decimal import Decimal

from trading_skill.market_universe import SecurityType


STOCK_TICK_SIZE = Decimal("0.01")
FUND_TICK_SIZE = Decimal("0.001")


def tick_size_for_security_type(security_type: SecurityType | str) -> Decimal:
    try:
        kind = security_type if isinstance(security_type, SecurityType) else SecurityType(str(security_type))
    except ValueError as exc:
        raise ValueError(f"UNSUPPORTED_SECURITY_TYPE:{security_type}") from exc
    if kind is SecurityType.STOCK:
        return STOCK_TICK_SIZE
    if kind in {SecurityType.ETF, SecurityType.LOF, SecurityType.FUND}:
        return FUND_TICK_SIZE
    raise ValueError(f"UNSUPPORTED_SECURITY_TYPE:{kind.value}")
