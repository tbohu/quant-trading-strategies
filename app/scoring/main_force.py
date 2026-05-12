"""主力控盘评分模型 — 总分100分，并判断主力状态"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import StockAnalysis


def score_main_force(a: "StockAnalysis") -> tuple[float, dict, str]:
    """Returns (total_score, breakdown_dict, main_force_state)."""
    detail = {}

    s1 = _score_5d_trend(a)
    detail["近5日主力趋势"] = {"score": s1, "max": 20}

    s2 = _score_10d_trend(a)
    detail["近10日主力趋势"] = {"score": s2, "max": 15}

    s3 = _score_large_order(a)
    detail["大单超大单"] = {"score": s3, "max": 15}

    s4 = _score_small_order(a)
    detail["小单方向"] = {"score": s4, "max": 10}

    s5 = _score_price_money_match(a)
    detail["价格资金配合"] = {"score": s5, "max": 10}

    s6 = _score_chip_concentration(a)
    detail["筹码集中度"] = {"score": s6, "max": 15}

    s7 = _score_lhb_institution(a)
    detail["龙虎榜机构"] = {"score": s7, "max": 10}

    s8 = _score_announcement_risk(a)
    detail["公告风险过滤"] = {"score": s8, "max": 5}

    total = s1 + s2 + s3 + s4 + s5 + s6 + s7 + s8
    state = _judge_state(a)
    return round(total, 1), detail, state


def main_force_conclusion(score: float) -> str:
    if score >= 80:
        return "强控盘"
    if score >= 65:
        return "资金集中"
    if score >= 50:
        return "一般控盘"
    return "控盘弱"


def _score_5d_trend(a) -> float:
    mni_5d = a.main_net_inflow_5d or 0
    mni_3d = a.main_net_inflow_3d or 0
    mni = a.main_net_inflow or 0

    if mni_5d > 0 and mni_3d > 0 and mni > 0:
        return 19.0
    if mni_5d > 0 and (mni_3d > 0 or mni > 0):
        return 15.0
    if mni_5d > 0:
        return 12.0
    if mni_5d < 0:
        return 4.0
    return 8.0


def _score_10d_trend(a) -> float:
    mni_10d = a.main_net_inflow_10d or 0
    mni_5d = a.main_net_inflow_5d or 0

    if mni_10d > 0 and mni_5d > 0:
        return 14.0
    if mni_10d > 0:
        return 10.0
    if mni_10d < 0 and mni_5d < 0:
        return 2.0
    return 6.0


def _score_large_order(a) -> float:
    slni = a.super_large_net_inflow or 0
    lni = a.large_net_inflow or 0

    if slni > 0 and lni > 0:
        return 14.0
    if lni > 0:
        return 10.0
    if slni < 0 and lni < 0:
        return 2.0
    return 6.0


def _score_small_order(a) -> float:
    mni = a.main_net_inflow or 0
    sni = a.small_net_inflow or 0

    # 小单流出且主力流入 = 机构吸筹，最优
    if mni > 0 and sni < 0:
        return 10.0
    # 小单流入且主力流出 = 散户接盘，最差
    if mni < 0 and sni > 0:
        return 0.0
    return 5.0


def _score_price_money_match(a) -> float:
    chg = a.change_pct or 0
    mni = a.main_net_inflow or 0

    if chg > 0 and mni > 0:
        return 10.0
    if chg >= -1 and mni > 0:  # 横盘+主力流入
        return 8.0
    if chg > 0 and mni < 0:    # 价格上涨但主力流出（背离）
        return 3.0
    if chg < 0 and mni < 0:
        return 0.0
    return 5.0


def _score_chip_concentration(a) -> float:
    """筹码集中度：股东人数变化"""
    sc_chg = a.shareholder_count_change_pct
    if sc_chg is None:
        return 8.0  # 无数据给中间分

    if sc_chg <= -10:    # 股东人数大幅下降，高度集中
        return 14.0
    if sc_chg <= -5:
        return 12.0
    if sc_chg <= 0:
        return 10.0
    if sc_chg <= 5:      # 轻微分散
        score = 7.0
    elif sc_chg <= 10:
        score = 4.0
    else:
        score = 1.0
    return score


def _score_lhb_institution(a) -> float:
    if not a.on_lhb:
        return 5.0   # 未上龙虎榜给中间分

    inst_nb = a.institution_net_buy or 0
    hm_buy = a.hot_money_buy_amount or 0
    hm_sell = a.hot_money_sell_amount or 0

    if inst_nb > 0:
        return 9.0
    if inst_nb < 0:
        return 1.0
    if hm_buy > hm_sell:
        return 5.0
    return 3.0


def _score_announcement_risk(a) -> float:
    major = [a.has_investigation, a.has_penalty, a.has_delisting_risk, a.has_major_shareholder_reduction]
    minor = [a.has_reduction_announcement, a.has_regulatory_inquiry, a.has_guidance_cut]
    if any(major):
        return 0.0
    if any(minor):
        return max(0, 5 - sum(minor) * 1.5)
    return 5.0


def _judge_state(a) -> str:
    """判断主力当前行为阶段"""
    mni = a.main_net_inflow or 0
    mni_5d = a.main_net_inflow_5d or 0
    slni = a.super_large_net_inflow or 0
    lni = a.large_net_inflow or 0
    sni = a.small_net_inflow or 0
    chg = a.change_pct or 0
    vr_5_20 = a.volume_ratio_5_20 or 1
    cp = a.current_price
    ma20 = a.ma20
    rise_20 = a.rise_20d_pct or 0
    vr = a.volume_ratio or 1

    # 派发：高位放量滞涨 + 主力流出 + 小单流入
    if rise_20 > 20 and vr > 2 and abs(chg) < 2 and mni < 0 and sni > 0:
        return "疑似派发"

    # 拉升：主力流入 + 大单超大单流入 + 股价突破 + 板块同步
    ind_chg = a.industry_change_pct or 0
    if mni > 0 and slni > 0 and lni > 0 and chg > 3 and ind_chg > 1:
        return "疑似拉升"

    # 洗盘：趋势未破 + 缩量回调 + 主力未明显流出 + 回踩20日线
    at_ma20 = cp is not None and ma20 is not None and abs(cp - ma20) / ma20 < 0.05
    if vr_5_20 < 0.8 and mni >= 0 and at_ma20:
        return "疑似洗盘"

    # 吸筹：主力连续流入 + 小单流出 + 股价横盘小涨 + 成交量温和放大
    if mni_5d > 0 and mni > 0 and sni < 0 and abs(chg) < 3 and 0.8 <= vr_5_20 <= 1.5:
        return "疑似吸筹"

    return "无明显特征"
