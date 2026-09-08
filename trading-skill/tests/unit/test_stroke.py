from dataclasses import replace
from conftest import make_bar
from trading_skill.chan.fractal import Fractal
from trading_skill.chan.inclusion import process_inclusions
from trading_skill.chan.stroke import build_strokes, validate_stroke_pair
from trading_skill.data.validate import validate_raw_bars
from trading_skill.domain.enums import FractalType, StrokeMode, StructureState


def synthetic_fractal(ftype, idx, pb, raw_id, extreme):
    return Fractal(id=f"f{idx}{ftype}", type=ftype, state=StructureState.CONFIRMED, center_processed_bar_id=pb[idx].id, source_processed_bar_ids=(pb[idx-1].id,pb[idx].id,pb[idx+1].id), center_index=idx, extreme_ticks=extreme, extreme_source_raw_bar_ids=(raw_id,), center_timestamp=pb[idx].end_timestamp, confirmation_timestamp=pb[idx+1].end_timestamp)


def fixture(n, tick):
    bars=[make_bar(i,10+i,11+i,9+i,10+i) for i in range(n)]
    v=validate_raw_bars(bars,tick); assert v.result.valid
    p=process_inclusions(v.bars); assert p.result.valid
    return v.bars,p.bars


def test_relaxed_requires_three_raw_between(tick):
    v,p=fixture(8,tick); start=synthetic_fractal(FractalType.BOTTOM,1,p,v[1].id,900); end=synthetic_fractal(FractalType.TOP,4,p,v[4].id,1500)
    val=validate_stroke_pair(start,end,mode=StrokeMode.RELAXED_LATE,processed_bars=p,raw_bars=v)
    assert not val.valid and "INSUFFICIENT_RAW_BARS" in val.reason_codes


def test_relaxed_accepts_three_raw_between(tick):
    v,p=fixture(9,tick); start=synthetic_fractal(FractalType.BOTTOM,1,p,v[1].id,900); end=synthetic_fractal(FractalType.TOP,5,p,v[5].id,1600)
    assert validate_stroke_pair(start,end,mode=StrokeMode.RELAXED_LATE,processed_bars=p,raw_bars=v).valid


def test_shared_processed_bar_rejected(tick):
    v,p=fixture(7,tick); start=synthetic_fractal(FractalType.BOTTOM,1,p,v[1].id,900); end=synthetic_fractal(FractalType.TOP,3,p,v[3].id,1400)
    val=validate_stroke_pair(start,end,mode=StrokeMode.RELAXED_LATE,processed_bars=p,raw_bars=v)
    assert not val.valid and "SHARED_PROCESSED_BAR" in val.reason_codes


def test_active_stroke_extends_same_id(tick):
    v,p=fixture(12,tick); f1=synthetic_fractal(FractalType.BOTTOM,1,p,v[1].id,900); f2=synthetic_fractal(FractalType.TOP,5,p,v[5].id,1600); f3=synthetic_fractal(FractalType.TOP,7,p,v[7].id,1800)
    a=build_strokes((f1,f2),mode=StrokeMode.RELAXED_LATE,processed_bars=p,raw_bars=v); b=build_strokes((f1,f2,f3),mode=StrokeMode.RELAXED_LATE,processed_bars=p,raw_bars=v)
    assert a.strokes[0].id==b.strokes[0].id and b.strokes[0].revision==2 and b.strokes[0].end_ticks==1800


def test_reverse_validates_and_finalizes_previous(tick):
    v,p=fixture(15,tick); f1=synthetic_fractal(FractalType.BOTTOM,1,p,v[1].id,900); f2=synthetic_fractal(FractalType.TOP,5,p,v[5].id,1600); f3=synthetic_fractal(FractalType.BOTTOM,9,p,v[9].id,1200)
    out=build_strokes((f1,f2,f3),mode=StrokeMode.RELAXED_LATE,processed_bars=p,raw_bars=v)
    assert len(out.strokes)==2 and out.strokes[0].state==StructureState.FINALIZED and out.strokes[1].state==StructureState.ACTIVE


def test_strict_old_rejects_where_relaxed_accepts(tick):
    v,p=fixture(8,tick); start=synthetic_fractal(FractalType.BOTTOM,1,p,v[1].id,900); end=synthetic_fractal(FractalType.TOP,4,p,v[5].id,1600)
    strict=validate_stroke_pair(start,end,mode=StrokeMode.STRICT_OLD,processed_bars=p,raw_bars=v); relaxed=validate_stroke_pair(start,end,mode=StrokeMode.RELAXED_LATE,processed_bars=p,raw_bars=v)
    assert not strict.valid and strict.reason_codes==("NO_INDEPENDENT_PROCESSED_BAR",) and relaxed.valid


def test_relaxed_tie_extremes_use_conservative_min_spacing(tick):
    v,p=fixture(10,tick); start=synthetic_fractal(FractalType.BOTTOM,1,p,v[1].id,900); end=synthetic_fractal(FractalType.TOP,6,p,v[6].id,1800); end=replace(end,extreme_source_raw_bar_ids=(v[4].id,v[6].id))
    val=validate_stroke_pair(start,end,mode=StrokeMode.RELAXED_LATE,processed_bars=p,raw_bars=v)
    assert not val.valid and val.reason_codes==("INSUFFICIENT_RAW_BARS",)


def test_finalized_stroke_not_rewritten_by_later_active_extension(tick):
    v,p=fixture(18,tick); f1=synthetic_fractal(FractalType.BOTTOM,1,p,v[1].id,900); f2=synthetic_fractal(FractalType.TOP,5,p,v[5].id,1600); f3=synthetic_fractal(FractalType.BOTTOM,9,p,v[9].id,1200); f4=synthetic_fractal(FractalType.BOTTOM,11,p,v[11].id,1000)
    before=build_strokes((f1,f2,f3),mode=StrokeMode.RELAXED_LATE,processed_bars=p,raw_bars=v); after=build_strokes((f1,f2,f3,f4),mode=StrokeMode.RELAXED_LATE,processed_bars=p,raw_bars=v)
    assert before.strokes[0]==after.strokes[0] and after.strokes[1].revision==2


def test_stroke_confirmation_uses_end_fractal_confirmation_time(tick):
    v,p=fixture(10,tick); f1=synthetic_fractal(FractalType.BOTTOM,1,p,v[1].id,900); f2=synthetic_fractal(FractalType.TOP,5,p,v[5].id,1600)
    out=build_strokes((f1,f2),mode=StrokeMode.RELAXED_LATE,processed_bars=p,raw_bars=v)
    assert out.strokes[0].structural_end_timestamp==f2.center_timestamp and out.strokes[0].confirmation_timestamp==f2.confirmation_timestamp and out.strokes[0].confirmation_timestamp>out.strokes[0].structural_end_timestamp
