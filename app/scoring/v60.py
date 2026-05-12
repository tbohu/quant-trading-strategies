"""V6.0 超短线量化评分模型 — 总分100分"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import StockAnalysis


def score_v60(a: "StockAnalysis") -> tuple[float, dict]:
    detail = {}

    s1 = _score_sector_heat(a)
    detail["板块热度"] = {"score": s1, "max": 20}

    s2 = _score_stock_position(a)
    detail["个股位置"] = {"score": s2, "max": 15}

    s3 = _score_volume(a)
    detail["成交量量比"] = {"score": s3, "max": 15}

    s4 = _score_money_flow_v60(a)
    detail["主力资金"] = {"score": s4, "max": 20}

    s5 = _score_technical_breakout(a)
    detail["技术突破"] = {"score": s5, "max": 15}

    s6 = _score_risk_filter(a)
    detail["风险过滤"] = {"score": s6, "max": 10}

    s7 = _score_catalyst_v60(a)
    detail["催化剂"] = {"score": s7, "max": 5}

    total = s1 + s2 + s3 + s4 + s5 + s6 + s7
    return round(total, 1), detail


def v60_conclusion(score: float) -> str:
    if score >= 90:
        return "强信号"
    if score >= 85:
        return "可买入"
    if score >= 80:
        return "候选观察"
    if score >= 70:
        return "弱信号"
    return "淘汰"


# ── 子项评分 ─────────────────────────────────────────────────────────────────

def _score_sector_heat(a) -> float:
    """20分：板块热度"""
    ind_rank = a.industry_rank or 99
    con_rank = a.concept_rank or 99
    ind_inflow = a.industry_main_inflow or 0
    con_inflow = a.concept_main_inflow or 0
    ind_chg = a.industry_change_pct or 0
    con_chg = a.concept_change_pct or 0

    best_rank = min(ind_rank, con_rank)
    best_chg = max(ind_chg, con_chg)
    has_inflow = ind_inflow > 0 or con_inflow > 0

    if best_rank <= 5 and has_inflow:
        score = 19.0
    elif best_rank <= 5:
        score = 17.0
    elif best_rank <= 10:
        score = 15.0
    elif best_rank <= 20:
        score = 11.0
    elif best_rank <= 30:
        score = 9.0
    else:
        score = 5.0

    if best_chg > 5:
        score += 1
    if has_inflow:
        score += 0.5

    return max(0, min(20, round(score, 1)))


def _score_stock_position(a) -> float:
    """15分：个股涨幅位置"""
    chg = a.change_pct or 0
    rise_5d = a.rise_5d_pct or 0
    rise_20d = a.rise_20d_pct or 0
    cp = a.current_price
    ma5 = a.ma5

    # 今日涨幅判断
    if 3 <= chg <= 7:
        score = 13.0
    elif 0 <= chg < 3:
        score = 10.0
    elif 7 < chg <= 9:
        score = 7.0
    elif chg > 9:
        score = 3.0      # 涨停追高风险
    elif chg < 0:
        score = 5.0      # 回调位置

    # 近期累计涨幅惩罚
    if rise_20d > 40:
        score -= 4
    elif rise_20d > 25:
        score -= 2

    if rise_5d > 20:
        score -= 2

    # 站上5日线加分
    if cp and ma5 and cp > ma5:
        score += 1

    return max(0, min(15, round(score, 1)))


def _score_volume(a) -> float:
    """15分：量比 + 放量情况"""
    vr = a.volume_ratio or 1
    vr_5_20 = a.volume_ratio_5_20 or 1
    ta = a.turnover_amount or 0

    # 量比评分
    if 1.5 <= vr <= 3:
        score = 13.0
    elif 1.0 <= vr < 1.5:
        score = 10.0
    elif 3 < vr <= 5:
        score = 9.0     # 放量过大，风险增加
    elif vr > 5:
        score = 5.0     # 高度异动，防冲高回落
    elif vr < 0.8:
        score = 4.0     # 缩量
    else:
        score = 8.0

    # 5/20日量能比补充
    if vr_5_20 >= 1.5:
        score += 1.5
    elif vr_5_20 <= 0.7:
        score -= 2

    # 成交额过小扣分
    if ta < 50_000_000:  # 5000万
        score -= 2

    return max(0, min(15, round(score, 1)))


def _score_money_flow_v60(a) -> float:
    """20分：主力资金强弱"""
    mni = a.main_net_inflow or 0
    slni = a.super_large_net_inflow or 0
    lni = a.large_net_inflow or 0
    sni = a.small_net_inflow or 0
    mni_5d = a.main_net_inflow_5d or 0

    if mni > 0 and slni > 0 and lni > 0:
        score = 18.0
    elif mni > 0 and lni > 0:
        score = 14.0
    elif mni > 0:
        score = 11.0
    elif mni < 0 and slni < 0:
        score = 4.0
    else:
        score = 7.0

    # 5日趋势
    if mni_5d > 0:
        score += 1
    elif mni_5d < 0:
        score -= 1

    # 散户接盘扣分
    if mni < 0 and sni > 0:
        score -= 2

    return max(0, min(20, round(score, 1)))


def _score_technical_breakout(a) -> float:
    """15分：技术突破形态"""
    cp = a.current_price
    ma5, ma10, ma20 = a.ma5, a.ma10, a.ma20
    vr_5_20 = a.volume_ratio_5_20 or 1

    score = 5.0

    if cp is None:
        return score

    above_ma5 = ma5 and cp > ma5
    above_ma10 = ma10 and cp > ma10
    above_ma20 = ma20 and cp > ma20

    # 均线多头排列
    bullish = all(x is not None for x in [ma5, ma10, ma20]) and ma5 > ma10 > ma20
    if bullish and above_ma5:
        score += 4
    elif above_ma5 and above_ma10:
        score += 2.5
    elif above_ma5:
        score += 1

    # 量价配合的突破额外加分
    if above_ma20 and vr_5_20 >= 1.5:
        score += 3

    # 破位惩罚
    if cp and ma20 and cp < ma20:
        score -= 2
    if cp and ma5 and cp < ma5:
        score -= 1

    return max(0, min(15, round(score, 1)))


def _score_risk_filter(a) -> float:
    """10分：风险过滤"""
    score = 9.0

    hard_stop = [
        getattr(a, "stock", None) and getattr(a.stock, "is_st", False),
        getattr(a, "stock", None) and getattr(a.stock, "is_loss", False),
        a.has_investigation,
        a.has_penalty,
        a.has_delisting_risk,
    ]
    medium_risk = [
        a.has_major_shareholder_reduction,
        a.has_regulatory_inquiry,
        a.has_guidance_cut,
        a.has_unlock_risk,
    ]
    soft_risk = [
        a.has_reduction_announcement,
        a.has_pledge_risk,
        a.has_loss_warning,
    ]

    if any(hard_stop):
        return 0.0

    score -= sum(medium_risk) * 2
    score -= sum(soft_risk) * 0.5

    # 高位放量滞涨
    rise_20 = a.rise_20d_pct or 0
    vr = a.volume_ratio or 1
    chg = abs(a.change_pct or 0)
    if rise_20 > 20 and vr > 2 and chg < 2:
        score -= 3

    # 成交额极小流动性差
    ta = a.turnover_amount or 0
    if ta < 20_000_000:
        score -= 2

    return max(0, min(10, round(score, 1)))


def _score_catalyst_v60(a) -> float:
    """5分：短线催化剂"""
    count = sum([
        a.has_profit_warning_positive,
        a.has_major_contract,
        a.has_winning_bid,
        a.has_order_catalyst,
        a.has_price_hike,
        a.has_new_product,
    ])
    if count >= 2:
        return 5.0
    if count == 1:
        return 3.0
    return 1.0
