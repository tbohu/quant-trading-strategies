"""综合分析器：汇聚四套模型，生成最终信号和操作建议"""
import json
from ..models import StockAnalysis
from .v42 import score_v42, v42_conclusion
from .v60 import score_v60, v60_conclusion
from .main_force import score_main_force, main_force_conclusion


HARD_STOP_RULES = [
    ("is_st", "ST股票，硬性排除。"),
    ("is_loss", "最近一年亏损，硬性排除。"),
    ("has_major_risk", "存在重大风险公告，硬性排除。"),
]


def run_full_analysis(a: StockAnalysis, stock) -> StockAnalysis:
    """计算所有评分并写回 a，返回更新后的对象。"""

    # 派生计算字段
    _compute_derived(a)

    # 硬性规则检查
    for attr, msg in HARD_STOP_RULES:
        if getattr(stock, attr, False) or getattr(a, attr, False):
            a.eliminated = True
            a.risk_alert = msg
            a.is_eligible_for_buy = False
            a.v42_score = 0
            a.v60_score = 0
            a.main_force_score = 0
            a.v42_conclusion = "淘汰"
            a.v60_conclusion = "淘汰"
            a.main_force_conclusion = "淘汰"
            a.buy_signal = False
            a.operation_advice = f"硬性规则：{msg}"
            return a

    # V4.2 中线评分
    v42, v42_detail = score_v42(a)
    a.v42_score = v42
    a.v42_detail = json.dumps(v42_detail, ensure_ascii=False)
    a.v42_conclusion = v42_conclusion(v42)

    # V6.0 短线评分
    v60, v60_detail = score_v60(a)
    a.v60_score = v60
    a.v60_detail = json.dumps(v60_detail, ensure_ascii=False)
    a.v60_conclusion = v60_conclusion(v60)

    # 主力控盘
    mf, mf_detail, mf_state = score_main_force(a)
    a.main_force_score = mf
    a.main_force_detail = json.dumps(mf_detail, ensure_ascii=False)
    a.main_force_conclusion = main_force_conclusion(mf)
    a.main_force_state = mf_state

    # 额外硬规则：高估值无业绩支撑
    pe = a.pe_dynamic or 0
    np_yoy = a.net_profit_yoy_pct or 0
    if pe > 60 and np_yoy < 20:
        a.eliminated = True
        a.risk_alert = "高估值无业绩支撑，排除。"
        a.is_eligible_for_buy = False
        a.buy_signal = False
        a.operation_advice = "高估值无业绩支撑，不买。"
        return a

    # 高位放量滞涨
    if _is_high_volume_stagnation(a):
        a.eliminated = True
        a.risk_alert = "高位放量滞涨，不追。"
        a.is_eligible_for_buy = False
        a.buy_signal = False
        a.operation_advice = "高位放量滞涨信号，等待回调。"
        return a

    # 主力连续流出+散户接盘
    if (a.main_net_inflow or 0) < 0 and (a.small_net_inflow or 0) > 0:
        a.risk_alert = (a.risk_alert or "") + " 主力流出+散户接盘，不追。"
        a.is_eligible_for_buy = False

    # ── 买入信号判断 ──────────────────────────────────────────
    risks = _collect_risks(a, stock)
    catalysts = _collect_catalysts(a)
    a.risk_alert = "；".join(risks) if risks else "无明显风险"

    strategy, buy_sig = _judge_buy_signal(a, v60, mf)
    a.matched_strategy = strategy
    a.buy_signal = buy_sig
    a.is_eligible_for_buy = buy_sig and not a.eliminated

    # ── 卖出信号 ──────────────────────────────────────────────
    a.sell_signal = _judge_sell_signal(a)
    a.needs_sell = a.sell_signal

    # ── 观察池 ────────────────────────────────────────────────
    a.in_watchlist = (v60 >= 75 or v42 >= 75) and not a.eliminated

    # ── 淘汰 ─────────────────────────────────────────────────
    a.eliminated = v60 < 70 and v42 < 70

    # ── 操作建议 ──────────────────────────────────────────────
    a.operation_advice = _build_advice(a, catalysts)

    return a


def _compute_derived(a: StockAnalysis):
    cp = a.current_price or 0
    if cp and a.ma5:
        a.distance_ma5_pct = round((cp - a.ma5) / a.ma5 * 100, 2)
    if cp and a.ma20:
        a.distance_ma20_pct = round((cp - a.ma20) / a.ma20 * 100, 2)
    if cp and a.ma60:
        a.distance_ma60_pct = round((cp - a.ma60) / a.ma60 * 100, 2)

    if a.avg_volume_5d and a.avg_volume_20d and a.avg_volume_20d > 0:
        a.volume_ratio_5_20 = round(a.avg_volume_5d / a.avg_volume_20d, 2)

    if a.pe_dynamic and a.net_profit_yoy_pct and a.net_profit_yoy_pct > 0:
        a.peg = round(a.pe_dynamic / a.net_profit_yoy_pct, 2)


def _is_high_volume_stagnation(a: StockAnalysis) -> bool:
    return (
        (a.rise_20d_pct or 0) > 20
        and (a.volume_ratio or 1) > 2
        and abs(a.change_pct or 0) < 2
    )


def _judge_buy_signal(a: StockAnalysis, v60: float, mf: float) -> tuple[str, bool]:
    cp = a.current_price or 0
    chg = a.change_pct or 0
    mni = a.main_net_inflow or 0
    vr_5_20 = a.volume_ratio_5_20 or 1
    ind_rank = a.industry_rank or 99
    con_rank = a.concept_rank or 99
    above_ma5 = a.ma5 and cp > a.ma5
    above_ma10 = a.ma10 and cp > a.ma10
    above_ma20 = a.ma20 and cp > a.ma20
    sector_strong = ind_rank <= 10 or con_rank <= 10

    # 策略A：主线突破
    strategy_a = (
        sector_strong
        and (a.industry_main_inflow or 0) > 0
        and 3 <= chg <= 7
        and above_ma5 and above_ma10 and above_ma20
        and vr_5_20 >= 1.5
        and mni > 0
        and not a.has_major_shareholder_reduction
    )
    if strategy_a:
        return "主线突破", True

    # 策略B：强势回踩
    vr = a.volume_ratio or 1
    strategy_b = (
        sector_strong
        and above_ma5
        and vr_5_20 <= 0.9   # 缩量回踩
        and chg >= -3
        and mni >= 0
        and (a.main_net_inflow_5d or 0) > 0
    )
    if strategy_b:
        return "强势回踩", True

    # 策略C：资金异动
    strategy_c = (
        mni > 0
        and (a.super_large_net_inflow or 0) > 0
        and (a.large_net_inflow or 0) > 0
        and (a.volume_ratio or 1) > 1.5
        and chg > 0
        and sector_strong
    )
    if strategy_c:
        return "资金异动", True

    return "", False


def _judge_sell_signal(a: StockAnalysis) -> bool:
    cp = a.current_price or 0
    mni = a.main_net_inflow or 0
    chg = a.change_pct or 0
    vr_5_20 = a.volume_ratio_5_20 or 1

    # 主力持续流出
    if (a.main_net_inflow_3d or 0) < 0 and mni < 0:
        return True
    # 高位放量滞涨
    if _is_high_volume_stagnation(a):
        return True
    # 跌破5日线且缩量
    if a.ma5 and cp < a.ma5 and vr_5_20 < 0.8:
        return True
    return False


def _collect_risks(a: StockAnalysis, stock) -> list[str]:
    risks = []
    risk_map = {
        "has_investigation": "立案调查",
        "has_penalty": "监管处罚",
        "has_delisting_risk": "退市风险",
        "has_major_shareholder_reduction": "大股东减持",
        "has_unlock_risk": "解禁压力",
        "has_guidance_cut": "业绩下修",
        "has_goodwill_impairment": "商誉减值",
        "has_regulatory_inquiry": "监管问询",
        "has_pledge_risk": "质押风险",
        "has_reduction_announcement": "减持公告",
        "has_loss_warning": "亏损预警",
        "has_lawsuit": "诉讼风险",
    }
    for field, label in risk_map.items():
        if getattr(a, field, False):
            risks.append(label)
    if getattr(stock, "is_st", False):
        risks.insert(0, "ST股")
    return risks


def _collect_catalysts(a: StockAnalysis) -> list[str]:
    cats = []
    cat_map = {
        "has_profit_warning_positive": "业绩预增",
        "has_major_contract": "重大合同",
        "has_winning_bid": "中标",
        "has_order_catalyst": "新订单",
        "has_buyback": "回购",
        "has_shareholder_increase": "增持",
        "has_dividend": "分红",
        "has_price_hike": "涨价",
        "has_new_product": "新产品",
        "has_ma": "并购",
        "has_capacity_expansion": "产能扩张",
    }
    for field, label in cat_map.items():
        if getattr(a, field, False):
            cats.append(label)
    return cats


def _build_advice(a: StockAnalysis, catalysts: list[str]) -> str:
    parts = []
    if a.buy_signal:
        parts.append(f"【买入信号-{a.matched_strategy}】")
        parts.append(f"V6.0={a.v60_score}分({a.v60_conclusion})，"
                     f"V4.2={a.v42_score}分({a.v42_conclusion})，"
                     f"主力控盘={a.main_force_score}分({a.main_force_state})。")
        if catalysts:
            parts.append(f"催化剂：{'、'.join(catalysts)}。")
    elif a.sell_signal:
        parts.append("【卖出信号】主力资金转出或技术破位，建议减仓/止损。")
    elif a.in_watchlist:
        parts.append("【观察池】评分接近买点，继续跟踪板块热度和资金动向。")
    elif a.eliminated:
        parts.append("【淘汰】评分不达标，移出候选池。")
    else:
        parts.append("【持续观察】未触发明确信号。")
    return " ".join(parts)
