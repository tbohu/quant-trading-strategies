from sqlalchemy import Column, Integer, Float, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(10), unique=True, index=True, nullable=False)
    name = Column(String(50), nullable=False)
    market = Column(String(5))          # SH / SZ / BJ
    industry = Column(String(50))
    concepts = Column(Text)             # comma-separated
    is_st = Column(Boolean, default=False)
    is_loss = Column(Boolean, default=False)
    has_major_risk = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    analyses = relationship("StockAnalysis", back_populates="stock", cascade="all, delete-orphan")
    holdings = relationship("Holding", back_populates="stock", cascade="all, delete-orphan")
    trades = relationship("TradeRecord", back_populates="stock", cascade="all, delete-orphan")


class StockAnalysis(Base):
    """One record per stock per analysis session. Contains all factor data + scores."""
    __tablename__ = "stock_analyses"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String(10), ForeignKey("stocks.code"), nullable=False)
    analysis_date = Column(DateTime, default=datetime.utcnow)

    # ── 行情数据 ──────────────────────────────────────────────
    current_price = Column(Float)
    change_pct = Column(Float)
    turnover_amount = Column(Float)
    volume = Column(Float)
    turnover_rate = Column(Float)
    volume_ratio = Column(Float)
    total_market_cap = Column(Float)
    float_market_cap = Column(Float)
    high_price = Column(Float)
    low_price = Column(Float)
    open_price = Column(Float)
    prev_close_price = Column(Float)

    # ── K线技术因子 ───────────────────────────────────────────
    ma5 = Column(Float)
    ma10 = Column(Float)
    ma20 = Column(Float)
    ma60 = Column(Float)
    distance_ma5_pct = Column(Float)
    distance_ma20_pct = Column(Float)
    distance_ma60_pct = Column(Float)
    rise_5d_pct = Column(Float)
    rise_10d_pct = Column(Float)
    rise_20d_pct = Column(Float)
    rise_60d_pct = Column(Float)
    avg_volume_5d = Column(Float)
    avg_volume_20d = Column(Float)
    volume_ratio_5_20 = Column(Float)   # avg_volume_5d / avg_volume_20d

    # ── 财报基本面 ────────────────────────────────────────────
    report_date = Column(String(20))
    revenue = Column(Float)
    revenue_yoy_pct = Column(Float)
    net_profit = Column(Float)
    net_profit_yoy_pct = Column(Float)
    deducted_net_profit = Column(Float)
    deducted_net_profit_yoy_pct = Column(Float)
    roe_pct = Column(Float)
    gross_margin_pct = Column(Float)
    net_margin_pct = Column(Float)
    debt_ratio_pct = Column(Float)
    operating_cashflow = Column(Float)
    eps = Column(Float)
    operating_cashflow_per_share = Column(Float)

    # ── 估值因子 ──────────────────────────────────────────────
    pe_dynamic = Column(Float)
    pe_ttm = Column(Float)
    pb = Column(Float)
    ps = Column(Float)
    peg = Column(Float)
    ev_ebitda = Column(Float)
    industry_pe_median = Column(Float)
    industry_pb_median = Column(Float)

    # ── 公告因子 ──────────────────────────────────────────────
    risk_announcement_count = Column(Integer, default=0)
    catalyst_announcement_count = Column(Integer, default=0)
    announcement_count_30d = Column(Integer, default=0)
    has_reduction_announcement = Column(Boolean, default=False)
    has_major_shareholder_reduction = Column(Boolean, default=False)
    has_unlock_risk = Column(Boolean, default=False)
    has_regulatory_inquiry = Column(Boolean, default=False)
    has_penalty = Column(Boolean, default=False)
    has_investigation = Column(Boolean, default=False)
    has_pledge_risk = Column(Boolean, default=False)
    has_loss_warning = Column(Boolean, default=False)
    has_guidance_cut = Column(Boolean, default=False)
    has_goodwill_impairment = Column(Boolean, default=False)
    has_lawsuit = Column(Boolean, default=False)
    has_delisting_risk = Column(Boolean, default=False)
    has_profit_warning_positive = Column(Boolean, default=False)
    has_major_contract = Column(Boolean, default=False)
    has_winning_bid = Column(Boolean, default=False)
    has_order_catalyst = Column(Boolean, default=False)
    has_buyback = Column(Boolean, default=False)
    has_shareholder_increase = Column(Boolean, default=False)
    has_ma = Column(Boolean, default=False)
    has_restructuring = Column(Boolean, default=False)
    has_dividend = Column(Boolean, default=False)
    has_price_hike = Column(Boolean, default=False)
    has_new_product = Column(Boolean, default=False)
    has_capacity_expansion = Column(Boolean, default=False)

    # ── 主力资金 ──────────────────────────────────────────────
    main_net_inflow = Column(Float, default=0)
    main_net_inflow_pct = Column(Float, default=0)
    super_large_net_inflow = Column(Float, default=0)
    large_net_inflow = Column(Float, default=0)
    medium_net_inflow = Column(Float, default=0)
    small_net_inflow = Column(Float, default=0)
    main_net_inflow_3d = Column(Float, default=0)
    main_net_inflow_5d = Column(Float, default=0)
    main_net_inflow_10d = Column(Float, default=0)

    # ── 板块热度 ──────────────────────────────────────────────
    industry_change_pct = Column(Float, default=0)
    concept_change_pct = Column(Float, default=0)
    industry_main_inflow = Column(Float, default=0)
    concept_main_inflow = Column(Float, default=0)
    industry_rise_count = Column(Integer, default=0)
    industry_fall_count = Column(Integer, default=0)
    concept_rise_count = Column(Integer, default=0)
    concept_fall_count = Column(Integer, default=0)
    industry_rank = Column(Integer, default=99)
    concept_rank = Column(Integer, default=99)

    # ── 龙虎榜 ────────────────────────────────────────────────
    on_lhb = Column(Boolean, default=False)
    lhb_net_buy_amount = Column(Float, default=0)
    institution_net_buy = Column(Float, default=0)
    hot_money_buy_amount = Column(Float, default=0)
    hot_money_sell_amount = Column(Float, default=0)

    # ── 筹码集中度 ────────────────────────────────────────────
    shareholder_count = Column(Integer)
    shareholder_count_change_pct = Column(Float)    # 负数=减少=集中

    # ── 评分结果 ──────────────────────────────────────────────
    v42_score = Column(Float)
    v42_detail = Column(Text)           # JSON breakdown
    v42_conclusion = Column(String(50))

    v60_score = Column(Float)
    v60_detail = Column(Text)
    v60_conclusion = Column(String(50))

    main_force_score = Column(Float)
    main_force_detail = Column(Text)
    main_force_conclusion = Column(String(50))
    main_force_state = Column(String(20))   # 吸筹/拉升/派发/洗盘

    # ── 信号与操作建议 ────────────────────────────────────────
    buy_signal = Column(Boolean, default=False)
    sell_signal = Column(Boolean, default=False)
    matched_strategy = Column(String(20))   # 主线突破/强势回踩/资金异动
    operation_advice = Column(Text)
    risk_alert = Column(Text)
    is_eligible_for_buy = Column(Boolean, default=False)
    needs_sell = Column(Boolean, default=False)
    in_watchlist = Column(Boolean, default=False)
    eliminated = Column(Boolean, default=False)

    stock = relationship("Stock", back_populates="analyses")


class Holding(Base):
    __tablename__ = "holdings"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String(10), ForeignKey("stocks.code"), nullable=False)
    stock_name = Column(String(50))
    holding_cost = Column(Float, nullable=False)
    holding_quantity = Column(Integer, nullable=False)
    buy_date = Column(DateTime, default=datetime.utcnow)
    strategy_type = Column(String(20))  # 主线突破/强势回踩/资金异动
    notes = Column(Text)
    is_active = Column(Boolean, default=True)

    stock = relationship("Stock", back_populates="holdings")


class TradeRecord(Base):
    __tablename__ = "trade_records"

    id = Column(Integer, primary_key=True, index=True)
    trade_id = Column(String(50), unique=True, index=True)
    stock_code = Column(String(10), ForeignKey("stocks.code"), nullable=False)
    stock_name = Column(String(50))
    strategy_type = Column(String(20))
    buy_time = Column(DateTime)
    buy_price = Column(Float)
    buy_quantity = Column(Integer)
    buy_amount = Column(Float)
    buy_score = Column(Float)
    buy_reason = Column(Text)
    sell_time = Column(DateTime)
    sell_price = Column(Float)
    sell_quantity = Column(Integer)
    sell_amount = Column(Float)
    sell_reason = Column(Text)
    holding_days = Column(Integer)
    trade_profit = Column(Float)
    trade_return_pct = Column(Float)
    is_win = Column(Boolean)
    max_floating_profit_pct = Column(Float)
    max_floating_loss_pct = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

    stock = relationship("Stock", back_populates="trades")


class SimAccount(Base):
    __tablename__ = "sim_account"

    id = Column(Integer, primary_key=True, index=True)
    sim_account_total_asset = Column(Float, default=1_000_000)
    sim_cash = Column(Float, default=1_000_000)
    sim_position_market_value = Column(Float, default=0)
    sim_total_profit = Column(Float, default=0)
    sim_total_return_pct = Column(Float, default=0)
    max_drawdown_pct = Column(Float, default=0)
    win_rate = Column(Float, default=0)
    profit_loss_ratio = Column(Float, default=0)
    avg_holding_days = Column(Float, default=0)
    strategy_a_return = Column(Float, default=0)
    strategy_b_return = Column(Float, default=0)
    strategy_c_return = Column(Float, default=0)
    max_single_profit = Column(Float, default=0)
    max_single_loss = Column(Float, default=0)
    continuous_loss_count = Column(Integer, default=0)
    trading_paused = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
