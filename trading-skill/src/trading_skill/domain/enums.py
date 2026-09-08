from __future__ import annotations

from enum import StrEnum


class Direction(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    UNRESOLVED = "UNRESOLVED"


class StructureState(StrEnum):
    FORMING = "FORMING"
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    CONFIRMED = "CONFIRMED"
    FINALIZED = "FINALIZED"
    INVALIDATED = "INVALIDATED"
    UNRESOLVED = "UNRESOLVED"


class Timeframe(StrEnum):
    WEEKLY = "weekly"
    DAILY = "daily"
    M120 = "120m"
    M30 = "30m"
    M5 = "5m"


class Market(StrEnum):
    CN_A = "CN_A"


class StrokeMode(StrEnum):
    STRICT_OLD = "STRICT_OLD"
    RELAXED_LATE = "RELAXED_LATE"


class DataConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    STALE = "STALE"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"


class FractalType(StrEnum):
    TOP = "TOP"
    BOTTOM = "BOTTOM"


class StrokePhase(StrEnum):
    EXTENDING = "EXTENDING"
    REVERSAL_FRACTAL_FORMING = "REVERSAL_FRACTAL_FORMING"


class IssueSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKER = "BLOCKER"


class SegmentState(StrEnum):
    SEED = "SEED"
    ACTIVE = "ACTIVE"
    STROKE_BREAK_PENDING = "STROKE_BREAK_PENDING"
    CASE1_PENDING = "CASE1_PENDING"
    CASE2_PENDING = "CASE2_PENDING"
    FINALIZED = "FINALIZED"
    INVALIDATED = "INVALIDATED"


class SegmentCase(StrEnum):
    NONE = "NONE"
    CASE1 = "CASE1"
    CASE2 = "CASE2"


class SegmentBreakState(StrEnum):
    NONE = "NONE"
    STROKE_BREAK_PENDING = "STROKE_BREAK_PENDING"
    BREAK_FAILED = "BREAK_FAILED"
    BREAK_CONFIRMED = "BREAK_CONFIRMED"


class FeatureFractalType(StrEnum):
    FEATURE_TOP = "FEATURE_TOP"
    FEATURE_BOTTOM = "FEATURE_BOTTOM"
