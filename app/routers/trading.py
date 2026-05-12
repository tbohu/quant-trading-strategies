import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import TradeRecord, Stock, SimAccount
from ..schemas import TradeCreate, TradeSell, TradeOut

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _get_account(db: Session) -> SimAccount:
    acc = db.query(SimAccount).first()
    if not acc:
        acc = SimAccount()
        db.add(acc)
        db.commit()
        db.refresh(acc)
    return acc


def _refresh_account_stats(db: Session, account: SimAccount):
    """重新计算账户统计数据"""
    trades = db.query(TradeRecord).filter(TradeRecord.is_win.isnot(None)).all()
    if not trades:
        return
    wins = [t for t in trades if t.is_win]
    account.win_rate = round(len(wins) / len(trades) * 100, 1)

    profits = [t.trade_profit for t in wins if t.trade_profit]
    losses = [abs(t.trade_profit) for t in trades if not t.is_win and t.trade_profit]
    avg_profit = sum(profits) / len(profits) if profits else 0
    avg_loss = sum(losses) / len(losses) if losses else 1
    account.profit_loss_ratio = round(avg_profit / avg_loss, 2) if avg_loss else 0

    days = [t.holding_days for t in trades if t.holding_days]
    account.avg_holding_days = round(sum(days) / len(days), 1) if days else 0

    all_profits = [t.trade_profit for t in trades if t.trade_profit]
    account.sim_total_profit = round(sum(all_profits), 2)

    if account.sim_account_total_asset > 0:
        account.sim_total_return_pct = round(
            account.sim_total_profit / (account.sim_account_total_asset - account.sim_total_profit) * 100, 2
        )

    max_p = max(all_profits) if all_profits else 0
    min_p = min(all_profits) if all_profits else 0
    account.max_single_profit = max_p
    account.max_single_loss = min_p

    # 连续亏损
    recent = sorted(trades, key=lambda t: t.created_at or datetime.min, reverse=True)[:10]
    count = 0
    for t in recent:
        if not t.is_win:
            count += 1
        else:
            break
    account.continuous_loss_count = count
    account.trading_paused = count >= 3


@router.get("/trading", response_class=HTMLResponse)
def trading_page(request: Request, db: Session = Depends(get_db)):
    trades = db.query(TradeRecord).order_by(TradeRecord.created_at.desc()).all()
    account = _get_account(db)
    open_trades = [t for t in trades if t.sell_time is None]
    closed_trades = [t for t in trades if t.sell_time is not None]
    return templates.TemplateResponse(request, "trading.html", {
        "trades": trades,
        "open_trades": open_trades,
        "closed_trades": closed_trades,
        "account": account,
    })


@router.post("/trading/buy")
def buy(data: TradeCreate, db: Session = Depends(get_db)):
    account = _get_account(db)
    if account.trading_paused:
        raise HTTPException(status_code=400, detail="账户已暂停交易（连续3笔亏损），请复盘后手动恢复。")
    buy_amount = data.buy_price * data.buy_quantity
    if account.sim_cash < buy_amount:
        raise HTTPException(status_code=400, detail="模拟账户资金不足")

    # 单股仓位不超过20%
    position_ratio = buy_amount / account.sim_account_total_asset * 100
    if position_ratio > 20:
        raise HTTPException(status_code=400, detail=f"单股仓位 {position_ratio:.1f}% 超过20%上限，请减少数量。")

    trade = TradeRecord(
        trade_id=str(uuid.uuid4())[:8].upper(),
        stock_code=data.stock_code,
        stock_name=data.stock_name,
        strategy_type=data.strategy_type,
        buy_time=data.buy_time or datetime.utcnow(),
        buy_price=data.buy_price,
        buy_quantity=data.buy_quantity,
        buy_amount=buy_amount,
        buy_score=data.buy_score,
        buy_reason=data.buy_reason,
    )
    db.add(trade)
    account.sim_cash -= buy_amount
    account.sim_position_market_value += buy_amount
    db.commit()
    return {"ok": True, "trade_id": trade.trade_id}


@router.post("/trading/{trade_id}/sell")
def sell(trade_id: str, data: TradeSell, db: Session = Depends(get_db)):
    trade = db.query(TradeRecord).filter(TradeRecord.trade_id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="交易记录不存在")
    if trade.sell_time:
        raise HTTPException(status_code=400, detail="该交易已平仓")

    sell_amount = data.sell_price * data.sell_quantity
    trade.sell_time = datetime.utcnow()
    trade.sell_price = data.sell_price
    trade.sell_quantity = data.sell_quantity
    trade.sell_amount = sell_amount
    trade.sell_reason = data.sell_reason
    trade.max_floating_profit_pct = data.max_floating_profit_pct
    trade.max_floating_loss_pct = data.max_floating_loss_pct

    if trade.buy_time:
        delta = trade.sell_time - trade.buy_time
        trade.holding_days = delta.days

    trade.trade_profit = round(sell_amount - (trade.buy_amount or 0), 2)
    if trade.buy_amount and trade.buy_amount > 0:
        trade.trade_return_pct = round(trade.trade_profit / trade.buy_amount * 100, 2)
    trade.is_win = (trade.trade_profit or 0) > 0

    account = _get_account(db)
    account.sim_cash += sell_amount
    account.sim_position_market_value = max(0, (account.sim_position_market_value or 0) - (trade.buy_amount or 0))
    account.sim_account_total_asset = account.sim_cash + account.sim_position_market_value
    db.commit()
    _refresh_account_stats(db, account)
    db.commit()
    return {"ok": True, "profit": trade.trade_profit, "return_pct": trade.trade_return_pct}


@router.get("/trading/records")
def get_records(db: Session = Depends(get_db)):
    trades = db.query(TradeRecord).order_by(TradeRecord.created_at.desc()).all()
    return [TradeOut.model_validate(t) for t in trades]
