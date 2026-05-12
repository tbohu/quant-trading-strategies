"""持仓风控模型 — 基于持仓成本生成止盈止损线和操作建议"""
from __future__ import annotations
from typing import Optional
from dataclasses import dataclass


@dataclass
class RiskResult:
    floating_pnl: float
    floating_pnl_pct: float
    normal_stop_loss: float
    hard_stop_loss: float
    midterm_stop_loss: float
    take_profit_alert: float
    take_profit_strong: float
    action: str          # 止损/减仓/持有/止盈/加仓
    urgency: str         # 紧急/警告/正常
    advice: str
    color: str           # red/orange/green/blue


def evaluate_holding_risk(
    holding_cost: float,
    holding_quantity: int,
    current_price: float,
    account_total: float,
    main_continuous_outflow: bool = False,
    below_ma5: bool = False,
    below_ma20: bool = False,
    below_ma60: bool = False,
    sector_fading: bool = False,
    strategy_type: str = "超短线",
) -> RiskResult:
    holding_mv = current_price * holding_quantity
    cost_amount = holding_cost * holding_quantity
    pnl = holding_mv - cost_amount
    pnl_pct = (current_price - holding_cost) / holding_cost * 100

    position_ratio = holding_mv / account_total * 100 if account_total > 0 else 0

    # 止损止盈线
    normal_sl = holding_cost * 0.97
    hard_sl = holding_cost * 0.95
    mid_sl = holding_cost * 0.92
    tp_alert = holding_cost * 1.05
    tp_strong = holding_cost * 1.08

    # 操作建议逻辑（按优先级）
    if pnl_pct <= -5:
        return RiskResult(
            floating_pnl=pnl, floating_pnl_pct=pnl_pct,
            normal_stop_loss=normal_sl, hard_stop_loss=hard_sl,
            midterm_stop_loss=mid_sl, take_profit_alert=tp_alert,
            take_profit_strong=tp_strong,
            action="强制止损", urgency="紧急",
            advice=f"浮亏已达 {pnl_pct:.1f}%，超过硬止损线，立即卖出！",
            color="red",
        )
    if pnl_pct <= -3:
        return RiskResult(
            floating_pnl=pnl, floating_pnl_pct=pnl_pct,
            normal_stop_loss=normal_sl, hard_stop_loss=hard_sl,
            midterm_stop_loss=mid_sl, take_profit_alert=tp_alert,
            take_profit_strong=tp_strong,
            action="止损提醒", urgency="警告",
            advice=f"浮亏 {pnl_pct:.1f}%，触及普通止损线，考虑止损。",
            color="orange",
        )

    advices = []
    urgency = "正常"
    color = "green"
    action = "持有"

    if pnl_pct >= 25:
        action = "分批止盈"
        urgency = "警告"
        color = "orange"
        advices.append(f"盈利 {pnl_pct:.1f}%，建议至少减仓 1/3。")
    elif pnl_pct >= 20:
        action = "利润保护"
        advices.append(f"盈利 {pnl_pct:.1f}%，进入利润保护模式，跌破 {tp_strong:.2f} 减仓。")
    elif pnl_pct >= 8:
        action = "减仓止盈"
        advices.append(f"盈利 {pnl_pct:.1f}%，可模拟减半仓位。")
    elif pnl_pct >= 5:
        action = "止盈提醒"
        advices.append(f"盈利 {pnl_pct:.1f}%，已达止盈提醒线 {tp_alert:.2f}。")

    if position_ratio > 20:
        advices.append(f"仓位占比 {position_ratio:.1f}%，超过单股上限20%，建议减仓。")
        urgency = "警告"
        color = "orange"

    if below_ma60:
        action = "清仓"
        urgency = "警告"
        color = "red"
        advices.append("已跌破60日线，中线清仓或大幅减仓。")
    elif below_ma20:
        action = "波段减仓"
        urgency = "警告"
        color = "orange"
        advices.append("已跌破20日线，建议波段减仓。")
    elif below_ma5:
        if strategy_type == "超短线":
            action = "短线减仓"
            urgency = "警告"
            color = "orange"
            advices.append("跌破5日线，超短线减仓。")

    if main_continuous_outflow:
        advices.append("主力资金连续流出，建议降低仓位。")
        if color == "green":
            color = "orange"

    if sector_fading:
        advices.append("板块退潮，建议降低仓位或停止加仓。")

    if not advices:
        advices.append(f"持仓健康，浮盈 {pnl_pct:.1f}%，继续持有。")

    if action == "持有":
        color = "blue" if pnl_pct > 0 else "green"

    return RiskResult(
        floating_pnl=pnl,
        floating_pnl_pct=pnl_pct,
        normal_stop_loss=normal_sl,
        hard_stop_loss=hard_sl,
        midterm_stop_loss=mid_sl,
        take_profit_alert=tp_alert,
        take_profit_strong=tp_strong,
        action=action,
        urgency=urgency,
        advice=" ".join(advices),
        color=color,
    )
