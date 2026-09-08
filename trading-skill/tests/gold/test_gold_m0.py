import csv, json
from datetime import datetime
from pathlib import Path
from conftest import make_bar
from trading_skill.chan.fractal import Fractal
from trading_skill.chan.inclusion import process_inclusions
from trading_skill.chan.stroke import build_strokes, validate_stroke_pair
from trading_skill.data.validate import validate_raw_bars
from trading_skill.domain.bar import RawBar
from trading_skill.domain.enums import FractalType, StrokeMode, StructureState, Timeframe

ROOT=Path(__file__).parent

def load_json(case): return json.loads((ROOT/case/"expected.json").read_text())

def test_gs001_complex_inclusion(tick):
    expected=load_json("GS-001-complex-inclusion"); rows=list(csv.DictReader((ROOT/"GS-001-complex-inclusion"/"bars.csv").open())); bars=[]
    for r in rows:
        bars.append(RawBar.make(symbol="TEST",timeframe=Timeframe.M30,timestamp=datetime.fromisoformat(r["timestamp"]),open=r["open"],high=r["high"],low=r["low"],close=r["close"],volume=r["volume"],amount=r["amount"],is_complete=r["is_complete"].lower()=="true"))
    v=validate_raw_bars(bars,tick); p=process_inclusions(v.bars)
    assert v.result.valid and p.result.valid
    assert len(p.bars)==expected["processed_bar_count"]
    assert [[x.low_ticks,x.high_ticks] for x in p.bars]==expected["ranges_ticks"]
    assert p.bars[1].raw_bar_count==expected["middle_raw_bar_count"] and str(p.bars[1].volume)==expected["middle_volume"]

def fixture(tick,n=10):
    bars=[make_bar(i,10+i,11+i,9+i,10+i) for i in range(n)]; v=validate_raw_bars(bars,tick); p=process_inclusions(v.bars); assert v.result.valid and p.result.valid; return v.bars,p.bars

def fr(ftype,idx,p,raw_id,extreme):
    return Fractal(id=f"gold-{ftype}-{idx}",type=ftype,state=StructureState.CONFIRMED,center_processed_bar_id=p[idx].id,source_processed_bar_ids=(p[idx-1].id,p[idx].id,p[idx+1].id),center_index=idx,extreme_ticks=extreme,extreme_source_raw_bar_ids=(raw_id,),center_timestamp=p[idx].end_timestamp,confirmation_timestamp=p[idx+1].end_timestamp)

def test_gs002_strict_vs_relaxed(tick):
    expected=load_json("GS-002-strict-vs-relaxed-stroke"); raw,p=fixture(tick,8); start=fr(FractalType.BOTTOM,1,p,raw[1].id,900); end=fr(FractalType.TOP,4,p,raw[5].id,1600)
    strict=validate_stroke_pair(start,end,mode=StrokeMode.STRICT_OLD,processed_bars=p,raw_bars=raw); relaxed=validate_stroke_pair(start,end,mode=StrokeMode.RELAXED_LATE,processed_bars=p,raw_bars=raw)
    assert strict.valid is expected["STRICT_OLD"]["valid"] and expected["STRICT_OLD"]["reason"] in strict.reason_codes and relaxed.valid is expected["RELAXED_LATE"]["valid"]

def test_gs003_stroke_extension(tick):
    expected=load_json("GS-003-stroke-extension"); raw,p=fixture(tick,12); f1=fr(FractalType.BOTTOM,1,p,raw[1].id,900); f2=fr(FractalType.TOP,5,p,raw[5].id,1600); f3=fr(FractalType.TOP,7,p,raw[7].id,1800)
    a=build_strokes((f1,f2),mode=StrokeMode.RELAXED_LATE,processed_bars=p,raw_bars=raw); b=build_strokes((f1,f2,f3),mode=StrokeMode.RELAXED_LATE,processed_bars=p,raw_bars=raw)
    assert len(b.strokes)==expected["stroke_count"] and a.strokes[0].revision==expected["revision_before"] and b.strokes[0].revision==expected["revision_after"] and (a.strokes[0].id==b.strokes[0].id) is expected["same_id"] and b.strokes[0].state.value==expected["state_after"]
