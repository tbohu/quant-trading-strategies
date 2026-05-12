from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# ── Stock ───────────────────────────────────────────────────────────────────
class StockBase(BaseModel):
    code: str
    name: str
    market: Optional[str] = None
    industry: Optional[str] = None
    concepts: Optional[str] = None
    is_st: bool = False
    is_loss: bool = False
    has_major_risk: bool = False


class StockCreate(StockBase):
    pass


class StockOut(StockBase):
    id: int
    created_at: datetime
    model_config = {"from_attributes": True}


# ── StockAnalysis Input ──────────────────────────────────────────────────────
class AnalysisInput(BaseModel):
    # 行情
    current_price: Optional[float] = None
    change_pct: Optional[float] = None
    turnover_amount: Optional[float] = None
    volume: Optional[float] = None
    turnover_rate: Optional[float] = None
    volume_ratio: Optional[float] = None
    total_market_cap: Optional[float] = None
    float_market_cap: Optional[float] = None
    high_price: Optional[float] = None
    low_price: Optional[float] = None
    open_price: Optional[float] = None
    prev_close_price: Optional[float] = None

    # 技术
    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    rise_5d_pct: Optional[float] = None
    rise_10d_pct: Optional[float] = None
    rise_20d_pct: Optional[float] = None
    rise_60d_pct: Optional[float] = None
    avg_volume_5d: Optional[float] = None
    avg_volume_20d: Optional[float] = None

    # 基本面
    report_date: Optional[str] = None
    revenue: Optional[float] = None
    revenue_yoy_pct: Optional[float] = None
    net_profit: Optional[float] = None
    net_profit_yoy_pct: Optional[float] = None
    deducted_net_profit: Optional[float] = None
    deducted_net_profit_yoy_pct: Optional[float] = None
    roe_pct: Optional[float] = None
    gross_margin_pct: Optional[float] = None
    net_margin_pct: Optional[float] = None
    debt_ratio_pct: Optional[float] = None
    operating_cashflow: Optional[float] = None
    eps: Optional[float] = None
    operating_cashflow_per_share: Optional[float] = None

    # 估值
    pe_dynamic: Optional[float] = None
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    ps: Optional[float] = None
    ev_ebitda: Optional[float] = None
    industry_pe_median: Optional[float] = None
    industry_pb_median: Optional[float] = None

    # 公告风险
    risk_announcement_count: int = 0
    catalyst_announcement_count: int = 0
    announcement_count_30d: int = 0
    has_reduction_announcement: bool = False
    has_major_shareholder_reduction: bool = False
    has_unlock_risk: bool = False
    has_regulatory_inquiry: bool = False
    has_penalty: bool = False
    has_investigation: bool = False
    has_pledge_risk: bool = False
    has_loss_warning: bool = False
    has_guidance_cut: bool = False
    has_goodwill_impairment: bool = False
    has_lawsuit: bool = False
    has_delisting_risk: bool = False
    has_profit_warning_positive: bool = False
    has_major_contract: bool = False
    has_winning_bid: bool = False
    has_order_catalyst: bool = False
    has_buyback: bool = False
    has_shareholder_increase: bool = False
    has_ma: bool = False
    has_restructuring: bool = False
    has_dividend: bool = False
    has_price_hike: bool = False
    has_new_product: bool = False
    has_capacity_expansion: bool = False

    # 主力资金
    main_net_inflow: float = 0
    main_net_inflow_pct: float = 0
    super_large_net_inflow: float = 0
    large_net_inflow: float = 0
    medium_net_inflow: float = 0
    small_net_inflow: float = 0
    main_net_inflow_3d: float = 0
    main_net_inflow_5d: float = 0
    main_net_inflow_10d: float = 0

    # 板块
    industry_change_pct: float = 0
    concept_change_pct: float = 0
    industry_main_inflow: float = 0
    concept_main_inflow: float = 0
    industry_rise_count: int = 0
    industry_fall_count: int = 0
    concept_rise_count: int = 0
    concept_fall_count: int = 0
    industry_rank: int = 99
    concept_rank: int = 99

    # 龙虎榜
    on_lhb: bool = False
    lhb_net_buy_amount: float = 0
    institution_net_buy: float = 0
    hot_money_buy_amount: float = 0
    hot_money_sell_amount: float = 0

    # 筹码
    shareholder_count: Optional[int] = None
    shareholder_count_change_pct: Optional[float] = None


# ── Holding ──────────────────────────────────────────────────────────────────
class HoldingCreate(BaseModel):
    stock_code: str
    stock_name: str
    holding_cost: float
    holding_quantity: int
    strategy_type: Optional[str] = None
    notes: Optional[str] = None


class HoldingOut(HoldingCreate):
    id: int
    buy_date: datetime
    is_active: bool
    model_config = {"from_attributes": True}


# ── Trade ─────────────────────────────────────────────────────────────────────
class TradeCreate(BaseModel):
    stock_code: str
    stock_name: str
    strategy_type: Optional[str] = None
    buy_time: Optional[datetime] = None
    buy_price: float
    buy_quantity: int
    buy_score: Optional[float] = None
    buy_reason: Optional[str] = None


class TradeSell(BaseModel):
    sell_price: float
    sell_quantity: int
    sell_reason: Optional[str] = None
    max_floating_profit_pct: Optional[float] = None
    max_floating_loss_pct: Optional[float] = None


class TradeOut(BaseModel):
    id: int
    trade_id: str
    stock_code: str
    stock_name: str
    strategy_type: Optional[str]
    buy_time: Optional[datetime]
    buy_price: Optional[float]
    buy_quantity: Optional[int]
    buy_amount: Optional[float]
    buy_score: Optional[float]
    buy_reason: Optional[str]
    sell_time: Optional[datetime]
    sell_price: Optional[float]
    sell_quantity: Optional[int]
    sell_amount: Optional[float]
    sell_reason: Optional[str]
    holding_days: Optional[int]
    trade_profit: Optional[float]
    trade_return_pct: Optional[float]
    is_win: Optional[bool]
    model_config = {"from_attributes": True}
