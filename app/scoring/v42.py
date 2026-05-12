"""V4.2 中线机构增强评分模型 — 总分100分"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import StockAnalysis


def score_v42(a: "StockAnalysis") -> tuple[float, dict]:
    """Returns (total_score, breakdown_dict)."""
    detail = {}

    # 1. 价格位置 10分
    s1 = _score_price_position(a)
    detail["价格位置"] = {"score": s1, "max": 10}

    # 2. 行业逻辑 15分（用板块热度量化替代主观判断）
    s2 = _score_industry_logic(a)
    detail["行业逻辑"] = {"score": s2, "max": 15}

    # 3. 基本面质量 15分
    s3 = _score_fundamental_quality(a)
    detail["基本面质量"] = {"score": s3, "max": 15}

    # 4. 业绩变化 15分
    s4 = _score_performance(a)
    detail["业绩变化"] = {"score": s4, "max": 15}

    # 5. 催化剂 10分
    s5 = _score_catalyst(a)
    detail["催化剂"] = {"score": s5, "max": 10}

    # 6. 资金面 10分
    s6 = _score_money_flow(a)
    detail["资金面"] = {"score": s6, "max": 10}

    # 7. 技术面 10分
    s7 = _score_technical(a)
    detail["技术面"] = {"score": s7, "max": 10}

    # 8. 估值 10分
    s8 = _score_valuation(a)
    detail["估值"] = {"score": s8, "max": 10}

    # 9. 风险 5分
    s9 = _score_risk_v42(a)
    detail["风险"] = {"score": s9, "max": 5}

    total = s1 + s2 + s3 + s4 + s5 + s6 + s7 + s8 + s9
    return round(total, 1), detail


def v42_conclusion(score: float) -> str:
    if score >= 90:
        return "核心机会"
    if score >= 85:
        return "优质机会"
    if score >= 80:
        return "跟踪机会"
    if score >= 75:
        return "一般机会"
    if score >= 70:
        return "偏弱"
    return "淘汰"


# ── 子项评分 ─────────────────────────────────────────────────────────────────

def _score_price_position(a) -> float:
    """10分：价格相对均线位置"""
    if a.current_price is None:
        return 5.0
    score = 5.0
    above_ma20 = a.ma20 and a.current_price > a.ma20
    above_ma60 = a.ma60 and a.current_price > a.ma60
    rise_20d = a.rise_20d_pct or 0
    rise_60d = a.rise_60d_pct or 0
    dist_ma20 = a.distance_ma20_pct or 0
    dist_ma60 = a.distance_ma60_pct or 0

    if above_ma20 and above_ma60:
        score += 2
    elif above_ma20:
        score += 1

    # 低位企稳加分，高位暴涨减分
    if rise_20d > 30:
        score -= 3
    elif rise_20d > 20:
        score -= 1.5
    elif rise_20d < 5 and above_ma20:
        score += 1.5

    if dist_ma20 > 20:
        score -= 2
    elif dist_ma20 < 5 and above_ma20:
        score += 1

    if rise_60d > 60:
        score -= 2

    return max(0, min(10, round(score, 1)))


def _score_industry_logic(a) -> float:
    """15分：用板块热度量化"""
    score = 7.0
    ind_rank = a.industry_rank or 99
    con_rank = a.concept_rank or 99
    ind_chg = a.industry_change_pct or 0
    con_chg = a.concept_change_pct or 0
    ind_inflow = a.industry_main_inflow or 0

    if ind_rank <= 5 or con_rank <= 5:
        score = 14.0
    elif ind_rank <= 10 or con_rank <= 10:
        score = 11.0
    elif ind_rank <= 20 or con_rank <= 20:
        score = 9.0

    if ind_chg > 3 or con_chg > 3:
        score += 1
    if ind_inflow > 0:
        score += 1

    return max(0, min(15, round(score, 1)))


def _score_fundamental_quality(a) -> float:
    """15分：ROE、现金流、负债率等"""
    score = 7.0
    roe = a.roe_pct
    gm = a.gross_margin_pct
    debt = a.debt_ratio_pct
    ocf = a.operating_cashflow

    if roe is not None:
        if roe >= 20:
            score += 3
        elif roe >= 15:
            score += 2
        elif roe >= 12:
            score += 1
        elif roe < 8:
            score -= 2

    if gm is not None:
        if gm >= 50:
            score += 2
        elif gm >= 40:
            score += 1
        elif gm < 20:
            score -= 1

    if debt is not None:
        if debt > 70:
            score -= 3
        elif debt > 60:
            score -= 1.5
        elif debt < 40:
            score += 1

    if ocf is not None:
        if ocf > 0:
            score += 1
        else:
            score -= 1.5

    if a.has_goodwill_impairment:
        score -= 1

    return max(0, min(15, round(score, 1)))


def _score_performance(a) -> float:
    """15分：营收和利润增速"""
    score = 7.0
    rev_yoy = a.revenue_yoy_pct
    np_yoy = a.net_profit_yoy_pct
    dnp_yoy = a.deducted_net_profit_yoy_pct

    if rev_yoy is None or np_yoy is None:
        return 7.0

    if rev_yoy >= 50 and np_yoy >= 50:
        score = 14.0
    elif rev_yoy >= 30 and np_yoy >= 30:
        score = 12.0
    elif rev_yoy >= 15 and np_yoy >= 15:
        score = 10.0
    elif rev_yoy > 0 and np_yoy > 0:
        score = 8.0
    elif rev_yoy < 0 or np_yoy < 0:
        score = 3.0
    if np_yoy < -20:
        score = 1.0

    # 扣非修正
    if dnp_yoy is not None and dnp_yoy < np_yoy - 20:
        score -= 2

    return max(0, min(15, round(score, 1)))


def _score_catalyst(a) -> float:
    """10分：催化剂数量"""
    count = sum([
        a.has_profit_warning_positive,
        a.has_major_contract,
        a.has_winning_bid,
        a.has_order_catalyst,
        a.has_buyback,
        a.has_shareholder_increase,
        a.has_ma,
        a.has_restructuring,
        a.has_dividend,
        a.has_price_hike,
        a.has_new_product,
        a.has_capacity_expansion,
    ])
    if count >= 3:
        return 9.0
    if count == 2:
        return 7.0
    if count == 1:
        return 5.0
    return 1.0


def _score_money_flow(a) -> float:
    """10分：主力资金"""
    score = 5.0
    mni = a.main_net_inflow or 0
    slni = a.super_large_net_inflow or 0
    lni = a.large_net_inflow or 0
    mni_5d = a.main_net_inflow_5d or 0
    inst_nb = a.institution_net_buy or 0

    if mni > 0:
        score += 1
    else:
        score -= 1.5

    if slni > 0 and lni > 0:
        score += 2
    elif lni > 0:
        score += 1

    if mni_5d > 0:
        score += 1

    if inst_nb > 0:
        score += 1

    return max(0, min(10, round(score, 1)))


def _score_technical(a) -> float:
    """10分：均线趋势 + 量能 + 突破"""
    score = 5.0
    cp = a.current_price
    ma5, ma10, ma20, ma60 = a.ma5, a.ma10, a.ma20, a.ma60
    vr_5_20 = a.volume_ratio_5_20 or 1

    bullish = all(x is not None for x in [ma5, ma10, ma20, ma60]) and ma5 > ma10 > ma20 > ma60
    above_all = all(x is not None for x in [cp, ma5, ma20, ma60]) and cp > ma5 and cp > ma20 and cp > ma60

    if bullish and above_all:
        score += 2.5
    elif above_all:
        score += 1.5

    # 量能
    if vr_5_20 >= 1.5:
        score += 1.5
    elif vr_5_20 <= 0.7:
        score -= 1

    # 高位滞涨惩罚
    rise_20 = a.rise_20d_pct or 0
    vr = a.volume_ratio or 1
    chg = abs(a.change_pct or 0)
    if rise_20 > 20 and vr > 2 and chg < 2:
        score -= 3

    # 跌破均线
    if cp and ma20 and cp < ma20:
        score -= 1.5
    if cp and ma5 and ma10 and ma5 < ma10:
        score -= 1

    return max(0, min(10, round(score, 1)))


def _score_valuation(a) -> float:
    """10分：估值合理性"""
    score = 5.0
    pe = a.pe_dynamic
    pb = a.pb
    np_yoy = a.net_profit_yoy_pct or 0

    if pe is None:
        return 5.0

    if pe <= 20 and np_yoy > 20:
        score = 9.0
    elif pe <= 40 and np_yoy > 20:
        score = 7.5
    elif pe <= 40:
        score = 6.0
    elif pe <= 60:
        score = 5.0
    elif pe <= 100:
        if np_yoy > 50:
            score = 6.0  # 高估值有业绩支撑
        else:
            score = 2.5
    else:
        if np_yoy > 50:
            score = 4.0
        else:
            score = 1.0

    if pb is not None:
        if pb > 12:
            score -= 1.5
        elif pb > 8:
            score -= 0.5

    return max(0, min(10, round(score, 1)))


def _score_risk_v42(a) -> float:
    """5分：风险公告过滤"""
    score = 5.0
    major_risks = [
        a.has_investigation, a.has_penalty, a.has_delisting_risk,
        a.has_major_shareholder_reduction, a.has_guidance_cut,
        a.has_goodwill_impairment,
    ]
    minor_risks = [
        a.has_reduction_announcement, a.has_unlock_risk,
        a.has_regulatory_inquiry, a.has_pledge_risk,
        a.has_loss_warning, a.has_lawsuit,
    ]
    major_count = sum(major_risks)
    minor_count = sum(minor_risks)

    if major_count > 0:
        score -= major_count * 2
    score -= minor_count * 0.5

    return max(0, min(5, round(score, 1)))
