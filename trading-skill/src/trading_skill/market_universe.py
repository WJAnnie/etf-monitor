from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from math import isfinite
from typing import Iterable, Mapping


class SecurityType(StrEnum):
    STOCK = "STOCK"
    ETF = "ETF"
    LOF = "LOF"
    FUND = "FUND"


class StockBoard(StrEnum):
    SH_MAIN = "SH_MAIN"
    SZ_MAIN = "SZ_MAIN"
    STAR = "STAR"
    CHINEXT = "CHINEXT"
    BSE = "BSE"
    OTHER = "OTHER"
    NOT_APPLICABLE = "N/A"


class DataQuality(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True, slots=True)
class TradePermissions:
    sh_main: bool = True
    sz_main: bool = True
    star: bool = False
    chinext: bool = False
    bse: bool = False
    etf: bool = True
    lof: bool = True
    fund: bool = True

    def board_allowed(self, board: StockBoard) -> bool:
        return {
            StockBoard.SH_MAIN: self.sh_main,
            StockBoard.SZ_MAIN: self.sz_main,
            StockBoard.STAR: self.star,
            StockBoard.CHINEXT: self.chinext,
            StockBoard.BSE: self.bse,
        }.get(board, False)

    def security_type_allowed(self, security_type: SecurityType) -> bool:
        return {
            SecurityType.STOCK: True,
            SecurityType.ETF: self.etf,
            SecurityType.LOF: self.lof,
            SecurityType.FUND: self.fund,
        }[security_type]


@dataclass(frozen=True, slots=True)
class MarketSecurity:
    code: str
    name: str
    market: int
    security_type: SecurityType
    board: StockBoard
    price: float | None
    change_pct: float | None
    amount: float | None
    turnover_rate: float | None
    total_market_cap: float | None
    float_market_cap: float | None
    change_60d: float | None
    change_ytd: float | None
    tradable: bool
    exclusion_reasons: tuple[str, ...]
    data_quality: DataQuality
    missing_fields: tuple[str, ...]
    source: str

    def as_dict(self) -> dict:
        data = asdict(self)
        data["security_type"] = self.security_type.value
        data["board"] = self.board.value
        data["data_quality"] = self.data_quality.value
        return data


def optional_float(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def classify_stock_board(code: str, market: int) -> StockBoard:
    text = str(code or "").strip()
    if market == 0 and text.startswith(("4", "8", "920")):
        return StockBoard.BSE
    if text.startswith(("688", "689")):
        return StockBoard.STAR
    if text.startswith(("300", "301")):
        return StockBoard.CHINEXT
    if market == 1 and text.startswith("6"):
        return StockBoard.SH_MAIN
    if market == 0 and text.startswith("0"):
        return StockBoard.SZ_MAIN
    return StockBoard.OTHER


def classify_fund_security_type(name: str, fallback: SecurityType) -> SecurityType:
    """数据源板块只能当兜底，证券名称中的明确 ETF/LOF 标识优先。"""
    text = "".join(str(name or "").upper().split())
    if "ETF" in text:
        return SecurityType.ETF
    if "LOF" in text:
        return SecurityType.LOF
    return fallback


def _name_exclusion_reason(name: str) -> str | None:
    text = str(name or "").strip().upper()
    if not text:
        return "EMPTY_NAME"
    if text.startswith("*ST") or text.startswith("ST"):
        return "ST"
    if "退市" in text or text.endswith("退"):
        return "DELISTING"
    return None


def _quality(
    *,
    code: str,
    name: str,
    price: float | None,
    amount: float | None,
    change_60d: float | None,
    change_ytd: float | None,
) -> tuple[DataQuality, tuple[str, ...]]:
    missing = []
    for field, value in (("code", code), ("name", name), ("price", price), ("amount", amount)):
        if value in (None, ""):
            missing.append(field)
    if change_60d is None:
        missing.append("change_60d")
    if change_ytd is None:
        missing.append("change_ytd")
    hard_missing = any(field in {"code", "name", "price"} for field in missing)
    quality = DataQuality.DEGRADED if hard_missing else DataQuality.PARTIAL if missing else DataQuality.COMPLETE
    return quality, tuple(missing)


def normalize_stock_row(
    row: Mapping[str, object], *, source: str, permissions: TradePermissions | None = None
) -> MarketSecurity:
    permissions = permissions or TradePermissions()
    code = str(row.get("f12") or "").strip()
    name = str(row.get("f14") or "").strip()
    market = int(optional_float(row.get("f13")) or 0)
    board = classify_stock_board(code, market)
    price = optional_float(row.get("f2"))
    amount = optional_float(row.get("f6"))
    change_60d = optional_float(row.get("f24"))
    change_ytd = optional_float(row.get("f25"))
    quality, missing = _quality(
        code=code, name=name, price=price, amount=amount, change_60d=change_60d, change_ytd=change_ytd
    )

    reasons: list[str] = []
    name_reason = _name_exclusion_reason(name)
    if name_reason:
        reasons.append(name_reason)
    if not permissions.board_allowed(board):
        reasons.append(f"BOARD_NOT_ALLOWED:{board.value}")
    if not code:
        reasons.append("EMPTY_CODE")
    if price is None or price <= 0:
        reasons.append("NO_VALID_PRICE")

    return MarketSecurity(
        code=code,
        name=name,
        market=market,
        security_type=SecurityType.STOCK,
        board=board,
        price=price,
        change_pct=optional_float(row.get("f3")),
        amount=amount,
        turnover_rate=optional_float(row.get("f8")),
        total_market_cap=optional_float(row.get("f20")),
        float_market_cap=optional_float(row.get("f21")),
        change_60d=change_60d,
        change_ytd=change_ytd,
        tradable=not reasons,
        exclusion_reasons=tuple(reasons),
        data_quality=quality,
        missing_fields=missing,
        source=source,
    )


def normalize_fund_row(
    row: Mapping[str, object],
    *,
    security_type: SecurityType,
    source: str,
    permissions: TradePermissions | None = None,
) -> MarketSecurity:
    if security_type is SecurityType.STOCK:
        raise ValueError("normalize_fund_row cannot normalize STOCK")
    permissions = permissions or TradePermissions()
    code = str(row.get("f12") or "").strip()
    name = str(row.get("f14") or "").strip()
    actual_type = classify_fund_security_type(name, security_type)
    market = int(optional_float(row.get("f13")) or 0)
    price = optional_float(row.get("f2"))
    amount = optional_float(row.get("f6"))
    change_60d = optional_float(row.get("f24"))
    change_ytd = optional_float(row.get("f25"))
    quality, missing = _quality(
        code=code, name=name, price=price, amount=amount, change_60d=change_60d, change_ytd=change_ytd
    )

    reasons: list[str] = []
    if not permissions.security_type_allowed(actual_type):
        reasons.append(f"SECURITY_TYPE_NOT_ALLOWED:{actual_type.value}")
    if not code:
        reasons.append("EMPTY_CODE")
    if not name:
        reasons.append("EMPTY_NAME")
    if price is None or price <= 0:
        reasons.append("NO_VALID_PRICE")
    if any(token in name for token in ("退市", "终止上市", "终止运作")):
        reasons.append("DELISTING")

    return MarketSecurity(
        code=code,
        name=name,
        market=market,
        security_type=actual_type,
        board=StockBoard.NOT_APPLICABLE,
        price=price,
        change_pct=optional_float(row.get("f3")),
        amount=amount,
        turnover_rate=optional_float(row.get("f8")),
        total_market_cap=optional_float(row.get("f20")),
        float_market_cap=optional_float(row.get("f21")),
        change_60d=change_60d,
        change_ytd=change_ytd,
        tradable=not reasons,
        exclusion_reasons=tuple(reasons),
        data_quality=quality,
        missing_fields=missing,
        source=source,
    )


def build_tradeable_universe(
    stock_rows: Iterable[Mapping[str, object]],
    etf_rows: Iterable[Mapping[str, object]] = (),
    lof_rows: Iterable[Mapping[str, object]] = (),
    fund_rows: Iterable[Mapping[str, object]] = (),
    *,
    stock_source: str = "UNKNOWN",
    fund_source: str = "UNKNOWN",
    permissions: TradePermissions | None = None,
) -> tuple[tuple[MarketSecurity, ...], tuple[MarketSecurity, ...]]:
    permissions = permissions or TradePermissions()
    securities = [
        *(normalize_stock_row(row, source=stock_source, permissions=permissions) for row in stock_rows),
        *(
            normalize_fund_row(row, security_type=SecurityType.ETF, source=fund_source, permissions=permissions)
            for row in etf_rows
        ),
        *(
            normalize_fund_row(row, security_type=SecurityType.LOF, source=fund_source, permissions=permissions)
            for row in lof_rows
        ),
        *(
            normalize_fund_row(row, security_type=SecurityType.FUND, source=fund_source, permissions=permissions)
            for row in fund_rows
        ),
    ]
    dedup: dict[tuple[str, SecurityType], MarketSecurity] = {}
    for item in securities:
        key = (item.code, item.security_type)
        previous = dedup.get(key)
        if previous is None or (not previous.tradable and item.tradable):
            dedup[key] = item
    all_items = tuple(dedup.values())
    tradeable = tuple(item for item in all_items if item.tradable)
    return all_items, tradeable
