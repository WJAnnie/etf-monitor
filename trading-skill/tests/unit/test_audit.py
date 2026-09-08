from conftest import make_bar
from trading_skill.chan.audit import compare_stroke_modes
from trading_skill.chan.fractal import Fractal
from trading_skill.chan.inclusion import process_inclusions
from trading_skill.data.validate import validate_raw_bars
from trading_skill.domain.enums import FractalType, StructureState


def test_shadow_audit_exposes_mode_difference(tick):
    bars = [make_bar(i, 10+i, 11+i, 9+i, 10+i) for i in range(8)]
    v = validate_raw_bars(bars, tick); p = process_inclusions(v.bars)
    f1 = Fractal(id="f1", type=FractalType.BOTTOM, state=StructureState.CONFIRMED, center_processed_bar_id=p.bars[1].id, source_processed_bar_ids=(p.bars[0].id,p.bars[1].id,p.bars[2].id), center_index=1, extreme_ticks=900, extreme_source_raw_bar_ids=(v.bars[1].id,), center_timestamp=p.bars[1].end_timestamp, confirmation_timestamp=p.bars[2].end_timestamp)
    f2 = Fractal(id="f2", type=FractalType.TOP, state=StructureState.CONFIRMED, center_processed_bar_id=p.bars[4].id, source_processed_bar_ids=(p.bars[3].id,p.bars[4].id,p.bars[5].id), center_index=4, extreme_ticks=1600, extreme_source_raw_bar_ids=(v.bars[5].id,), center_timestamp=p.bars[4].end_timestamp, confirmation_timestamp=p.bars[5].end_timestamp)
    audit = compare_stroke_modes((f1,f2), processed_bars=p.bars, raw_bars=v.bars)
    assert audit.relaxed_count == 1 and audit.strict_count == 0 and audit.differs
