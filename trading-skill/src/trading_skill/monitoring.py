from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import IntEnum, StrEnum
from typing import Callable, Iterable, Any

from trading_skill.decision import Action, RiskState
from trading_skill.domain.models import stable_id


class MonitoringLevel(IntEnum):
    M0 = 0
    M1 = 1
    M2 = 2
    M3 = 3


class AlertPriority(IntEnum):
    P1_INFORMATION = 1
    P2_ATTENTION = 2
    P3_NEW_BUY = 3
    P3_POSITION_ACTION = 4
    P4_POSITION_RISK = 5


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"


class HealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class MonitoringState:
    symbol: str
    current_level: MonitoringLevel
    previous_level: MonitoringLevel
    reasons: tuple[str, ...]
    revision: int = 1


@dataclass(frozen=True, slots=True)
class ScanRequest:
    scan_id: str
    trigger_type: str
    scheduled_slot: str | None
    symbols: tuple[str, ...]
    requested_depth: MonitoringLevel
    priority: int
    as_of: datetime


@dataclass(frozen=True, slots=True)
class ScanSlotContext:
    slot: str
    daily_current_bar_provisional: bool
    daily_confirmation_allowed: bool
    intraday_execution_allowed: bool


@dataclass(frozen=True, slots=True)
class Alert:
    alert_id: str
    symbol: str
    priority: AlertPriority
    alert_type: str
    action: Action | None
    old_state: str | None
    new_state: str | None
    reason_codes: tuple[str, ...]
    analysis_id: str
    snapshot_id: str
    event_time: datetime
    dedup_key: str


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    delivery_id: str
    alert_id: str
    channel: str
    status: DeliveryStatus
    attempt: int
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class SystemHealthSnapshot:
    overall_status: HealthStatus
    components: dict[str, HealthStatus]
    new_trade_permission: bool
    position_risk_monitor_permission: bool
    reasons: tuple[str, ...]


SCHEDULE = ("PREMARKET", "10:30", "11:30", "13:30", "14:30", "14:50", "AFTER_CLOSE")


def is_trading_day(day: date, holidays: set[date] | None = None):
    return day.weekday() < 5 and day not in (holidays or set())


def schedule_for_day(day: date, holidays: set[date] | None = None):
    return SCHEDULE if is_trading_day(day, holidays) else ()


def scan_slot_context(slot: str) -> ScanSlotContext:
    if slot not in SCHEDULE:
        raise ValueError("UNKNOWN_SCAN_SLOT")
    if slot == "AFTER_CLOSE":
        return ScanSlotContext(slot, False, True, False)
    if slot == "PREMARKET":
        return ScanSlotContext(slot, False, False, False)
    return ScanSlotContext(slot, True, False, True)


def update_monitoring(
    state: MonitoringState | None,
    *,
    symbol: str,
    open_position: bool = False,
    triggered: bool = False,
    prepare: bool = False,
    risk: RiskState = RiskState.L0,
    watch: bool = False,
    ordinary_demote_confirmed: bool = True,
):
    old = state.current_level if state else MonitoringLevel.M0
    if open_position or triggered or risk >= RiskState.L2:
        target = MonitoringLevel.M3
    elif prepare:
        target = MonitoringLevel.M2
    elif watch:
        target = MonitoringLevel.M1
    else:
        target = MonitoringLevel.M0
    if target < old and not ordinary_demote_confirmed:
        target = old
    reasons: list[str] = []
    if open_position:
        reasons.append("OPEN_POSITION")
    if triggered:
        reasons.append("EXECUTION_TRIGGERED")
    if risk >= RiskState.L2:
        reasons.append("MATERIAL_RISK")
    return MonitoringState(symbol, target, old, tuple(reasons), 1 if state is None else state.revision + 1)


def build_alert(
    *,
    symbol: str,
    analysis_id: str,
    snapshot_id: str,
    action: Action | None,
    risk: RiskState,
    old_state: str | None,
    new_state: str | None,
    event_time: datetime,
    reason_codes: tuple[str, ...] = (),
):
    if risk >= RiskState.L4:
        priority = AlertPriority.P4_POSITION_RISK
        alert_type = "POSITION_RISK"
    elif action in (Action.EXIT, Action.REDUCE_CORE, Action.REDUCE_TACTICAL):
        priority = AlertPriority.P3_POSITION_ACTION
        alert_type = "POSITION_ACTION"
    elif action in (Action.BUY_TRANCHE_1, Action.ADD_TRANCHE_2, Action.ADD_TREND):
        priority = AlertPriority.P3_NEW_BUY
        alert_type = "NEW_BUY"
    elif action in (Action.PREPARE_BUY, Action.WAIT_2B, Action.PAUSE_ADD) or risk >= RiskState.L1:
        priority = AlertPriority.P2_ATTENTION
        alert_type = "ATTENTION"
    else:
        priority = AlertPriority.P1_INFORMATION
        alert_type = "INFORMATION"
    dedup_key = stable_id("dedup", symbol, alert_type, new_state, action, risk)
    alert_id = stable_id("alert", analysis_id, symbol, alert_type, new_state, action, risk)
    return Alert(
        alert_id,
        symbol,
        priority,
        alert_type,
        action,
        old_state,
        new_state,
        reason_codes,
        analysis_id,
        snapshot_id,
        event_time,
        dedup_key,
    )


def build_trade_alert(*, analysis_status: str, **kwargs) -> Alert | None:
    """Production gate: only a READY analysis can create a trade alert."""
    if str(analysis_status) != "READY":
        return None
    return build_alert(**kwargs)


class AlertDeduper:
    def __init__(self, repository: Any | None = None):
        self.repository = repository
        self.sent: set[str] = set()
        self.p2_daily: dict[tuple[date, str], int] = {}

    def _already_sent(self, alert: Alert) -> bool:
        if alert.dedup_key in self.sent:
            return True
        return bool(self.repository and self.repository.has_alert_dedup(alert.dedup_key))

    def _p2_count(self, alert: Alert) -> int:
        if self.repository:
            return int(self.repository.p2_alert_count(alert.symbol, alert.event_time.date().isoformat()))
        return self.p2_daily.get((alert.event_time.date(), alert.symbol), 0)

    def should_send(self, alert: Alert):
        if self._already_sent(alert):
            return False
        # Material Risk/Action/Protection state changes bypass ordinary P2 daily throttling.
        if alert.old_state is not None and alert.new_state is not None and alert.old_state != alert.new_state:
            return True
        if alert.priority is AlertPriority.P2_ATTENTION and self._p2_count(alert) >= 1:
            return False
        return True

    def record(self, alert: Alert):
        self.sent.add(alert.dedup_key)
        if alert.priority is AlertPriority.P2_ATTENTION:
            key = (alert.event_time.date(), alert.symbol)
            self.p2_daily[key] = self.p2_daily.get(key, 0) + 1
        if self.repository:
            fingerprint = f"{alert.old_state}->{alert.new_state}"
            self.repository.record_alert_dedup(
                dedup_key=alert.dedup_key,
                alert_id=alert.alert_id,
                symbol=alert.symbol,
                event_date=alert.event_time.date().isoformat(),
                priority=int(alert.priority),
                state_fingerprint=fingerprint,
            )


class ChannelAdapter:
    name = "BASE"

    def __init__(
        self,
        *,
        enabled: bool,
        sender: Callable[[Alert], None] | None = None,
        secret_loader: Callable[[], str] | None = None,
    ):
        self.enabled = enabled
        self.sender = sender
        self.secret_loader = secret_loader

    def deliver(self, alert: Alert, attempt: int = 1):
        delivery_id = stable_id("delivery", alert.alert_id, self.name)
        if not self.enabled:
            return DeliveryRecord(delivery_id, alert.alert_id, self.name, DeliveryStatus.SKIPPED_DISABLED, attempt)
        try:
            if self.secret_loader:
                self.secret_loader()
            if self.sender:
                self.sender(alert)
            return DeliveryRecord(delivery_id, alert.alert_id, self.name, DeliveryStatus.SUCCESS, attempt)
        except PermissionError:
            return DeliveryRecord(delivery_id, alert.alert_id, self.name, DeliveryStatus.FAILED_FINAL, attempt, "AUTH")
        except Exception:
            return DeliveryRecord(
                delivery_id,
                alert.alert_id,
                self.name,
                DeliveryStatus.FAILED_RETRYABLE,
                attempt,
                "TRANSIENT",
            )


class FeishuAdapter(ChannelAdapter):
    name = "FEISHU"


class GitHubAdapter(ChannelAdapter):
    name = "GITHUB"


class ServerChanAdapter(ChannelAdapter):
    name = "SERVERCHAN"


def dispatch(alert: Alert, adapters: Iterable[ChannelAdapter], *, repository: Any | None = None):
    records: list[DeliveryRecord] = []
    for adapter in adapters:
        record = adapter.deliver(alert)
        records.append(record)
        if repository:
            repository.record_delivery(record)
    return tuple(records)


def dispatch_ready(
    alert: Alert,
    adapters: Iterable[ChannelAdapter],
    *,
    repository: Any,
    artifact_path: str,
):
    from trading_skill.runtime import can_dispatch

    if not can_dispatch(repository, alert.analysis_id, artifact_path):
        return ()
    return dispatch(alert, adapters, repository=repository)


def system_health(components: dict[str, HealthStatus], *, open_position_data_available: bool = True):
    reasons: list[str] = []
    critical = ("PERSISTENCE", "CHAN_ENGINE")
    trade_permission = all(
        components.get(component, HealthStatus.HEALTHY) is HealthStatus.HEALTHY for component in critical
    ) and components.get("MARKET_DATA", HealthStatus.HEALTHY) is not HealthStatus.FAILED
    position_permission = (
        open_position_data_available
        and components.get("PERSISTENCE", HealthStatus.HEALTHY) is not HealthStatus.FAILED
    )
    if not open_position_data_available:
        reasons.append("POSITION_DATA_MISSING")
    values = list(components.values())
    if any(value is HealthStatus.FAILED for value in values):
        overall = HealthStatus.DEGRADED if position_permission or trade_permission else HealthStatus.FAILED
    elif any(value is HealthStatus.DEGRADED for value in values):
        overall = HealthStatus.DEGRADED
    else:
        overall = HealthStatus.HEALTHY
    return SystemHealthSnapshot(overall, components, trade_permission, position_permission, tuple(reasons))


def persist_system_health(repository: Any, health: SystemHealthSnapshot, *, as_of: datetime):
    payload = asdict(health)
    payload["components"] = {key: str(value) for key, value in health.components.items()}
    payload["overall_status"] = str(health.overall_status)
    payload["as_of"] = as_of.isoformat()
    repository.save_current("__SYSTEM_HEALTH__", payload)
    repository.db.commit()
