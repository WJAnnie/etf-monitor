from __future__ import annotations

from trading_skill.a_share_fundamentals import FundamentalPrefilter


SPECIALIZED_OBSERVATION_PROFILES = {
    "创新药/生物医药",
    "造船与海工",
    "半导体设备与材料",
    "机器人与高端自动化",
    "商业航天与军工电子",
    "先进能源装备",
    "新材料/周期制造",
    "工业软件/网络安全",
    "煤炭/油气/资源品",
}


def sector_observation_override(profile_name: str, prefilter: FundamentalPrefilter) -> tuple[bool, str | None]:
    """只决定是否允许进入缠论深扫，不把通用财务不合格改写成“可买”。

    原则：
    - 缺年报/中报永不覆盖；未来数据不可用。
    - 行业专属模型允许某些“通用会计指标暂差”的公司进入观察性结构扫描，例如研发期创新药、
      景气反转早期重资产制造。
    - 该覆盖不会修改 prefilter.eligible；正式买入仍会被 FUNDAMENTAL_VETO 阻断，直到行业专属证据充足。
    """
    if prefilter.eligible:
        return True, None
    if profile_name not in SPECIALIZED_OBSERVATION_PROFILES:
        return False, None
    if prefilter.annual is None or prefilter.interim is None:
        return False, None

    reasons = set(prefilter.reasons)
    interim = prefilter.interim

    # 明显营收塌陷或毛利率异常仍不允许覆盖；这些通常不是“行业口径差异”能解释的。
    if "营收同比明显下滑" in reasons or "毛利率异常" in reasons:
        return False, None

    if profile_name == "创新药/生物医药":
        # 研发期生物科技不能用EPS/ROE一票否决，但仍要求收入/毛利没有明显恶化。
        if interim.gross_margin is not None and interim.gross_margin <= 0:
            return False, None
        if interim.revenue_growth is not None and interim.revenue_growth < -10:
            return False, None
        return True, "行业专属观察：研发期创新药允许EPS/利润暂为负，需进一步看管线、BD授权、现金储备与研发效率；不能据此直接买入。"

    if profile_name == "工业软件/网络安全":
        if interim.revenue_growth is not None and interim.revenue_growth < 0:
            return False, None
        return True, "行业专属观察：高研发软件公司允许利润阶段性承压，需进一步看合同负债、续费/订阅、经营现金流与研发资本化。"

    # 周期制造/装备：利润在景气拐点前后可能滞后，允许结构观察，但正式执行仍需订单/价格/合同负债/现金流证据。
    allowed_reasons = {"净利润同比明显下滑", "每股收益非正", "净资产收益率偏低", "财务质量暂未达到候选阈值"}
    if reasons and not reasons.issubset(allowed_reasons):
        return False, None
    return True, "行业专属观察：周期/装备盈利可能滞后于订单和景气拐点，需结合订单、合同负债、固定资产/在建工程、价格与现金流确认；不能据此直接买入。"
