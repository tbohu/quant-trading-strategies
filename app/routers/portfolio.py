import json
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Holding, Stock, StockAnalysis, SimAccount
from ..schemas import HoldingCreate
from ..scoring.risk import evaluate_holding_risk

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


@router.get("/portfolio", response_class=HTMLResponse)
def portfolio_page(request: Request, db: Session = Depends(get_db)):
    holdings = db.query(Holding).filter(Holding.is_active == True).all()
    account = _get_account(db)

    enriched = []
    for h in holdings:
        latest = (
            db.query(StockAnalysis)
            .filter(StockAnalysis.stock_code == h.stock_code)
            .order_by(StockAnalysis.analysis_date.desc())
            .first()
        )
        cp = latest.current_price if latest else h.holding_cost
        below_ma5 = latest and latest.ma5 and cp < latest.ma5
        below_ma20 = latest and latest.ma20 and cp < latest.ma20
        below_ma60 = latest and latest.ma60 and cp < latest.ma60
        main_out = latest and (latest.main_net_inflow_3d or 0) < 0

        risk = evaluate_holding_risk(
            holding_cost=h.holding_cost,
            holding_quantity=h.holding_quantity,
            current_price=cp,
            account_total=account.sim_account_total_asset,
            main_continuous_outflow=main_out,
            below_ma5=below_ma5,
            below_ma20=below_ma20,
            below_ma60=below_ma60,
            strategy_type=h.strategy_type or "超短线",
        )
        enriched.append({
            "holding": h,
            "current_price": cp,
            "risk": risk,
            "analysis": latest,
        })

    total_mv = sum(e["holding"].holding_quantity * e["current_price"] for e in enriched)
    return templates.TemplateResponse(request, "portfolio.html", {
        "enriched": enriched,
        "account": account,
        "total_mv": total_mv,
    })


@router.post("/portfolio/add")
def add_holding(h: HoldingCreate, db: Session = Depends(get_db)):
    stock = db.query(Stock).filter(Stock.code == h.stock_code).first()
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在，请先添加股票")
    buy_amount = h.holding_cost * h.holding_quantity
    account = _get_account(db)
    if account.sim_cash < buy_amount:
        raise HTTPException(status_code=400, detail="模拟账户资金不足")

    obj = Holding(**h.model_dump())
    db.add(obj)
    account.sim_cash -= buy_amount
    account.sim_position_market_value += buy_amount
    db.commit()
    return {"ok": True, "id": obj.id}


@router.post("/portfolio/{holding_id}/close")
def close_holding(holding_id: int, db: Session = Depends(get_db)):
    h = db.query(Holding).filter(Holding.id == holding_id).first()
    if not h:
        raise HTTPException(status_code=404, detail="持仓不存在")
    latest = (
        db.query(StockAnalysis)
        .filter(StockAnalysis.stock_code == h.stock_code)
        .order_by(StockAnalysis.analysis_date.desc())
        .first()
    )
    cp = latest.current_price if latest else h.holding_cost
    sell_amount = cp * h.holding_quantity
    account = _get_account(db)
    account.sim_cash += sell_amount
    account.sim_position_market_value = max(0, account.sim_position_market_value - h.holding_cost * h.holding_quantity)
    account.sim_account_total_asset = account.sim_cash + account.sim_position_market_value
    pnl = (cp - h.holding_cost) * h.holding_quantity
    account.sim_total_profit = (account.sim_total_profit or 0) + pnl
    h.is_active = False
    db.commit()
    return {"ok": True, "sell_price": cp, "pnl": pnl}


@router.get("/portfolio/account", response_class=HTMLResponse)
def account_page(request: Request, db: Session = Depends(get_db)):
    account = _get_account(db)
    return templates.TemplateResponse(request, "account.html", {
        "account": account,
    })


@router.post("/portfolio/account/init")
def init_account(total: float = 1_000_000, db: Session = Depends(get_db)):
    account = _get_account(db)
    account.sim_account_total_asset = total
    account.sim_cash = total
    account.sim_position_market_value = 0
    account.sim_total_profit = 0
    account.sim_total_return_pct = 0
    account.continuous_loss_count = 0
    account.trading_paused = False
    db.commit()
    return {"ok": True, "total": total}
