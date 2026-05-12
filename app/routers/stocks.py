import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Stock, StockAnalysis
from ..schemas import StockCreate, AnalysisInput
from ..scoring.analyzer import run_full_analysis

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    stocks = db.query(Stock).all()
    analyses = []
    for s in stocks:
        latest = (
            db.query(StockAnalysis)
            .filter(StockAnalysis.stock_code == s.code)
            .order_by(StockAnalysis.analysis_date.desc())
            .first()
        )
        if latest:
            analyses.append((s, latest))

    buy_signals = [(s, a) for s, a in analyses if a.buy_signal]
    watchlist = [(s, a) for s, a in analyses if a.in_watchlist and not a.buy_signal]
    sell_signals = [(s, a) for s, a in analyses if a.sell_signal]

    return templates.TemplateResponse(request, "index.html", {
        "analyses": analyses,
        "buy_signals": buy_signals,
        "watchlist": watchlist,
        "sell_signals": sell_signals,
        "total_stocks": len(stocks),
    })


@router.get("/stocks", response_class=HTMLResponse)
def stock_list(request: Request, db: Session = Depends(get_db)):
    stocks = db.query(Stock).all()
    rows = []
    for s in stocks:
        latest = (
            db.query(StockAnalysis)
            .filter(StockAnalysis.stock_code == s.code)
            .order_by(StockAnalysis.analysis_date.desc())
            .first()
        )
        rows.append({"stock": s, "analysis": latest})
    return templates.TemplateResponse(request, "stock_list.html", {
        "rows": rows,
    })


@router.get("/stocks/add", response_class=HTMLResponse)
def add_stock_page(request: Request):
    return templates.TemplateResponse(request, "stock_add.html", {})


@router.post("/stocks/add")
def add_stock(stock_in: StockCreate, db: Session = Depends(get_db)):
    existing = db.query(Stock).filter(Stock.code == stock_in.code).first()
    if existing:
        raise HTTPException(status_code=400, detail="股票代码已存在")
    obj = Stock(**stock_in.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return {"ok": True, "id": obj.id, "code": obj.code}


@router.get("/stocks/{code}", response_class=HTMLResponse)
def stock_detail(code: str, request: Request, db: Session = Depends(get_db)):
    stock = db.query(Stock).filter(Stock.code == code).first()
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")

    analyses = (
        db.query(StockAnalysis)
        .filter(StockAnalysis.stock_code == code)
        .order_by(StockAnalysis.analysis_date.desc())
        .limit(30)
        .all()
    )
    latest = analyses[0] if analyses else None
    v42_detail = json.loads(latest.v42_detail) if latest and latest.v42_detail else {}
    v60_detail = json.loads(latest.v60_detail) if latest and latest.v60_detail else {}
    mf_detail = json.loads(latest.main_force_detail) if latest and latest.main_force_detail else {}

    return templates.TemplateResponse(request, "stock_detail.html", {
        "stock": stock,
        "latest": latest,
        "analyses": analyses,
        "v42_detail": v42_detail,
        "v60_detail": v60_detail,
        "mf_detail": mf_detail,
        "concepts": stock.concepts.split(",") if stock.concepts else [],
    })


@router.get("/stocks/{code}/analyze", response_class=HTMLResponse)
def analyze_page(code: str, request: Request, db: Session = Depends(get_db)):
    stock = db.query(Stock).filter(Stock.code == code).first()
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    return templates.TemplateResponse(request, "analyze_form.html", {
        "stock": stock,
    })


@router.post("/stocks/{code}/analyze")
def run_analysis(code: str, data: AnalysisInput, db: Session = Depends(get_db)):
    stock = db.query(Stock).filter(Stock.code == code).first()
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")

    a = StockAnalysis(stock_code=code, analysis_date=datetime.utcnow(), **data.model_dump())
    a = run_full_analysis(a, stock)
    db.add(a)
    db.commit()
    db.refresh(a)
    return {
        "ok": True,
        "analysis_id": a.id,
        "v42_score": a.v42_score,
        "v42_conclusion": a.v42_conclusion,
        "v60_score": a.v60_score,
        "v60_conclusion": a.v60_conclusion,
        "main_force_score": a.main_force_score,
        "main_force_state": a.main_force_state,
        "buy_signal": a.buy_signal,
        "sell_signal": a.sell_signal,
        "matched_strategy": a.matched_strategy,
        "operation_advice": a.operation_advice,
        "risk_alert": a.risk_alert,
        "is_eligible_for_buy": a.is_eligible_for_buy,
    }
