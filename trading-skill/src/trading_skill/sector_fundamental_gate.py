from __future__ import annotations

from trading_skill.a_share_fundamentals import FundamentalPrefilter


SPECIALIZED_OBSERVATION_PROFILES = {
    "创新药/生物医药",
    "造船与海工",
    "半导体设备与材料",
    "AI基础设施/通信硬件",
    "机器人与高端自动化",
    "电网设备与储能",
    "商业航天与军工电子",
    "智能驾驶与汽车电子",
    "医疗器械",
    "先进能源装备",
    "光伏与新能源制造",
    "新材料/周期制造",
    "化工/橡胶",
    "工业软件/网络安全",
    "机械/工程机械",
    "公用事业/电力",
    "港口/航运",
    "农业/养殖",
    "煤炭/油气/资源品",
}


def sector_observation_override(profile_name: str, prefilter: FundamentalPrefilter) -> tuple[bool, str | None]:
    """只决定是否允许进入缠论深扫，不把通用财务不合格改写成“可买”。

    原则：
    - 缺年报/中报永不覆盖；未来数据不可用。
    - 行业专属模型允许某些“通用会计指标暂差”的公司进入观察性结构扫描，例如研发期创新药、
      景气反转早期重资产制造、周期底部资源/航运/养殖。
    - 该覆盖不会修改 prefilter.eligible；正式买入仍会被 FUNDAMENTAL_VETO 阻断，直到正式基本面资格满足。
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
        return True, "行业专属观察：高研发软件公司允许利润阶段性承压，需进一步看合同负债、续费/订阅、经营现金流与研发资本化；不能据此直接买入。"

    # 周期/重资产/装备行业：利润通常滞后于订单、价格、利用率或产能周期，允许进入结构观察。
    # 但只容忍“盈利/ROE暂弱”这类差异；其他财务硬伤仍不覆盖。
    allowed_reasons = {"净利润同比明显下滑", "每股收益非正", "净资产收益率偏低", "财务质量暂未达到候选阈值"}
    if reasons and not reasons.issubset(allowed_reasons):
        return False, None

    profile_notes = {
        "造船与海工": "手持/新接订单、船价、合同负债、交付、在建工程和产能利用",
        "半导体设备与材料": "客户验证、订单、国产化率、在建工程、资本开支和存货",
        "AI基础设施/通信硬件": "大客户资本开支、订单、产品迭代、库存和应收",
        "机器人与高端自动化": "订单、出货、国产化率、在建工程和库存",
        "电网设备与储能": "国网/南网招标、手持订单、合同负债、应收和现金流",
        "商业航天与军工电子": "批产/定型、订单、合同负债、存货和交付节奏",
        "智能驾驶与汽车电子": "定点订单、单车价值量、客户结构、库存和现金流",
        "医疗器械": "招标/装机、产品获批、应收、库存和经营现金流",
        "先进能源装备": "核准/招标、订单、装机、在建工程、资本开支和现金流",
        "光伏与新能源制造": "产品价格、库存、产能利用、在建工程、资本开支与去产能",
        "新材料/周期制造": "产品价格/价差、客户认证、产能利用、库存和在建工程",
        "化工/橡胶": "产品价差、开工率、原料成本、库存、检修和新增产能",
        "机械/工程机械": "订单、销量、开工小时、出口、应收和库存",
        "公用事业/电力": "利用小时、电价、燃料成本、装机、资本开支和分红",
        "港口/航运": "运价、吞吐量、运力供给、利用率、资本开支和长协",
        "农业/养殖": "产品价格、产能去化、出栏、单位成本、生物资产和现金流",
        "煤炭/油气/资源品": "商品价格、产量、成本曲线、库存、储量和资本开支",
    }
    focus = profile_notes.get(profile_name, "行业订单/价格/产能/现金流等专属景气证据")
    return True, f"行业专属观察：通用利润指标可能滞后于行业景气变化，需重点结合{focus}确认；不能据此直接买入。"
