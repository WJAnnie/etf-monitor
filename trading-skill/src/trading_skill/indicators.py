from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
from math import sqrt
from statistics import fmean

class VolumeState(StrEnum): SHRINK="SHRINK"; NORMAL="NORMAL"; MILD_EXPAND="MILD_EXPAND"; SIGNIFICANT="SIGNIFICANT"; EXTREME="EXTREME"
class MacdState(StrEnum): BEARISH="BEARISH"; GREEN_SHRINKING="GREEN_SHRINKING"; TURNING_UP="TURNING_UP"; GOLDEN_CROSS="GOLDEN_CROSS"; GOLDEN_CROSS_FAILED="GOLDEN_CROSS_FAILED"; BULLISH="BULLISH"; RED_SHRINKING="RED_SHRINKING"; TURNING_DOWN="TURNING_DOWN"; DEATH_CROSS="DEATH_CROSS"
class BollState(StrEnum): BELOW_MID="BELOW_MID"; ABOVE_MID="ABOVE_MID"; UPPER_RAIL_WALK="UPPER_RAIL_WALK"; BREAKOUT="BREAKOUT"; RETEST="RETEST"; FAILED_RECLAIM="FAILED_RECLAIM"
class KdjState(StrEnum): LOW_ZONE="LOW_ZONE"; GOLDEN_CROSS="GOLDEN_CROSS"; MID_SECOND_CROSS="MID_SECOND_CROSS"; HIGH_SATURATION="HIGH_SATURATION"; DEATH_CROSS="DEATH_CROSS"; NEUTRAL="NEUTRAL"
class TechnicalConfirmation(StrEnum): SUPPORT="SUPPORT"; NEUTRAL="NEUTRAL"; CAUTION="CAUTION"; PAUSE="PAUSE"

@dataclass(frozen=True, slots=True)
class MacdPoint: dif: float; dea: float; hist: float
@dataclass(frozen=True, slots=True)
class BollPoint: mid: float; upper: float; lower: float; bandwidth: float
@dataclass(frozen=True, slots=True)
class KdjPoint: k: float; d: float; j: float
@dataclass(frozen=True, slots=True)
class TechnicalBundle:
    volume_state: VolumeState
    macd_state: MacdState
    boll_state: BollState
    kdj_state: KdjState
    confirmation: TechnicalConfirmation
    provisional: bool
    reason_codes: tuple[str, ...] = ()

def ema(values, period):
    if not values: return []
    alpha = 2 / (period + 1); out = [float(values[0])]
    for x in values[1:]: out.append(alpha * float(x) + (1 - alpha) * out[-1])
    return out

def macd(closes, fast=6, slow=13, signal=4):
    if not closes: return []
    ef=ema(closes,fast); es=ema(closes,slow); dif=[a-b for a,b in zip(ef,es)]; dea=ema(dif,signal)
    return [MacdPoint(d,e,d-e) for d,e in zip(dif,dea)]

def macd_state(points):
    if len(points)<2: return MacdState.BEARISH
    a,b=points[-2],points[-1]
    if a.dif<=a.dea and b.dif>b.dea: return MacdState.GOLDEN_CROSS
    if a.dif>=a.dea and b.dif<b.dea: return MacdState.DEATH_CROSS
    if b.dif>b.dea: return MacdState.RED_SHRINKING if b.hist<a.hist else MacdState.BULLISH
    if b.hist>a.hist: return MacdState.GREEN_SHRINKING
    return MacdState.BEARISH

def volume_state(current, ma20):
    if ma20<=0: return VolumeState.NORMAL
    r=current/ma20
    if r<0.8: return VolumeState.SHRINK
    if r<1.2: return VolumeState.NORMAL
    if r<1.5: return VolumeState.MILD_EXPAND
    if r<2: return VolumeState.SIGNIFICANT
    return VolumeState.EXTREME

def boll(closes, period=20, std_mult=2):
    if len(closes)<period: return None
    w=[float(x) for x in closes[-period:]]; mid=fmean(w); var=fmean([(x-mid)**2 for x in w]); sd=sqrt(var)
    up=mid+std_mult*sd; lo=mid-std_mult*sd; bw=(up-lo)/mid if mid else 0
    return BollPoint(mid,up,lo,bw)

def boll_state(closes,p):
    if p is None: return BollState.BELOW_MID
    c=float(closes[-1]); prev=float(closes[-2]) if len(closes)>1 else c
    if c>p.upper and prev>p.mid: return BollState.BREAKOUT
    if c>=p.upper*0.995: return BollState.UPPER_RAIL_WALK
    return BollState.ABOVE_MID if c>=p.mid else BollState.BELOW_MID

def kdj(highs,lows,closes,n=9,k_period=3,d_period=3):
    if not closes: return []
    k=d=50.0; out=[]
    for i,c in enumerate(closes):
        s=max(0,i-n+1); hh=max(float(x) for x in highs[s:i+1]); ll=min(float(x) for x in lows[s:i+1])
        rsv=50.0 if hh==ll else (float(c)-ll)/(hh-ll)*100
        k=((k_period-1)*k+rsv)/k_period; d=((d_period-1)*d+k)/d_period; j=3*k-2*d
        out.append(KdjPoint(k,d,j))
    return out

def kdj_state(points):
    if not points: return KdjState.NEUTRAL
    p=points[-1]
    if len(points)>=2:
        a=points[-2]
        if a.k<=a.d and p.k>p.d: return KdjState.GOLDEN_CROSS if p.k<50 else KdjState.MID_SECOND_CROSS
        if a.k>=a.d and p.k<p.d: return KdjState.DEATH_CROSS
    if p.k<20: return KdjState.LOW_ZONE
    if p.k>80: return KdjState.HIGH_SATURATION
    return KdjState.NEUTRAL

def build_bundle(*, volumes, closes, highs, lows, bar_complete=True):
    if not closes or not volumes:
        return TechnicalBundle(VolumeState.NORMAL,MacdState.BEARISH,BollState.BELOW_MID,KdjState.NEUTRAL,TechnicalConfirmation.PAUSE,not bar_complete,("DATA_INCOMPLETE",))
    ma20=fmean(float(x) for x in volumes[-20:]) if volumes else 0
    vs=volume_state(float(volumes[-1]),ma20); mp=macd(closes); ms=macd_state(mp); bp=boll(closes); bs=boll_state(closes,bp); kp=kdj(highs,lows,closes); ks=kdj_state(kp)
    reasons=[]
    if ms in (MacdState.DEATH_CROSS,MacdState.BEARISH): reasons.append("MOMENTUM_WEAK")
    if vs is VolumeState.EXTREME and ms in (MacdState.DEATH_CROSS,MacdState.BEARISH): reasons.append("EXTREME_VOLUME_WITH_WEAK_MOMENTUM")
    if "EXTREME_VOLUME_WITH_WEAK_MOMENTUM" in reasons: conf=TechnicalConfirmation.PAUSE
    elif reasons: conf=TechnicalConfirmation.CAUTION
    elif ms in (MacdState.GOLDEN_CROSS,MacdState.BULLISH) and bs in (BollState.ABOVE_MID,BollState.BREAKOUT,BollState.UPPER_RAIL_WALK): conf=TechnicalConfirmation.SUPPORT
    else: conf=TechnicalConfirmation.NEUTRAL
    return TechnicalBundle(vs,ms,bs,ks,conf,not bar_complete,tuple(reasons))
