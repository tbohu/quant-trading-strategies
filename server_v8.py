"""
旺财A股AI量化分析系统 V8.0
FastAPI backend - single file implementation
"""

import os, sqlite3, hmac, hashlib, time, uuid, json, threading, logging
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from collections import defaultdict

import requests
from fastapi import FastAPI, Request, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

# ── optional deps (graceful fallback) ──────────────────────────────────────
try:
    import schedule
    HAS_SCHEDULE = True
except ImportError:
    HAS_SCHEDULE = False

# ── Config ─────────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=False)

API_TOKEN        = os.getenv("WANGCAI_TOKEN", "wangcai-secret-2024")
DB_PATH          = os.getenv("DB_PATH", "wangcai_v8.db")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE    = os.getenv("DEEPSEEK_BASE", "https://api.deepseek.com/v1")
WECOM_KEY        = os.getenv("WECOM_KEY", "")
CORS_ORIGINS     = os.getenv("CORS_ORIGINS", "*").split(",")
RATE_LIMIT_MAX   = int(os.getenv("RATE_LIMIT_MAX", "120"))   # requests per window
RATE_LIMIT_WIN   = int(os.getenv("RATE_LIMIT_WIN", "60"))    # seconds

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wangcai")

# ── FastAPI app ─────────────────────────────────────────────────────────────
app = FastAPI(title="旺财A股AI量化分析系统", version="8.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS if CORS_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate limiter ────────────────────────────────────────────────────────────
_rate_store: Dict[str, List[float]] = defaultdict(list)
_rate_lock = threading.Lock()

def check_rate_limit(ip: str) -> bool:
    now = time.time()
    with _rate_lock:
        hits = _rate_store[ip]
        hits = [t for t in hits if now - t < RATE_LIMIT_WIN]
        if len(hits) >= RATE_LIMIT_MAX:
            _rate_store[ip] = hits
            return False
        hits.append(now)
        _rate_store[ip] = hits
        return True

# ── Auth ────────────────────────────────────────────────────────────────────
def require_auth(request: Request):
    token = request.headers.get("X-Wangcai-Token", "")
    if not hmac.compare_digest(token.encode(), API_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")
    ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

# ── Database ────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS sim_account (
        id TEXT PRIMARY KEY, cash REAL DEFAULT 440000,
        peak_value REAL DEFAULT 440000, updated_at INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS sim_positions (
        id TEXT PRIMARY KEY, stock_code TEXT, stock_name TEXT, industry TEXT DEFAULT '',
        qty INTEGER, cost_price REAL, normal_stop_loss REAL, hard_stop_loss REAL,
        take_profit_alert REAL, take_profit_strong REAL,
        score_v42 REAL DEFAULT 0, score_v60 REAL DEFAULT 0,
        strategy TEXT DEFAULT '', buy_reason TEXT DEFAULT '',
        bought_at INTEGER DEFAULT (strftime('%s','now'))
    );
    CREATE TABLE IF NOT EXISTS sim_trades (
        id TEXT PRIMARY KEY, stock_code TEXT, stock_name TEXT,
        direction TEXT, price REAL, qty INTEGER, amount REAL,
        strategy TEXT DEFAULT '', score_v60 REAL DEFAULT 0, score_v42 REAL DEFAULT 0,
        buy_reason TEXT DEFAULT '', sell_reason TEXT DEFAULT '',
        pnl REAL DEFAULT 0, pnl_pct REAL DEFAULT 0, hold_days INTEGER DEFAULT 0,
        cash_after REAL DEFAULT 0, is_win INTEGER DEFAULT 0,
        traded_at INTEGER DEFAULT (strftime('%s','now'))
    );
    CREATE TABLE IF NOT EXISTS signals (
        id TEXT PRIMARY KEY, stock_code TEXT, stock_name TEXT,
        signal_type TEXT, strategy TEXT DEFAULT '', score_v60 REAL DEFAULT 0,
        price REAL, reason TEXT DEFAULT '', executed INTEGER DEFAULT 0,
        created_at INTEGER DEFAULT (strftime('%s','now'))
    );
    CREATE TABLE IF NOT EXISTS analysis_records (
        id TEXT PRIMARY KEY, stock_code TEXT, stock_name TEXT,
        score_v42 REAL, score_v60 REAL, score_zl REAL,
        grade_v42 TEXT, grade_v60 TEXT, grade_zl TEXT,
        strategy TEXT, buy_signal TEXT DEFAULT '', sell_signal TEXT DEFAULT '',
        stop_loss REAL, take_profit REAL, operation_advice TEXT,
        risk_warning TEXT DEFAULT '[]', can_sim_buy INTEGER DEFAULT 0,
        need_sim_sell INTEGER DEFAULT 0, factors TEXT DEFAULT '{}',
        created_at INTEGER DEFAULT (strftime('%s','now'))
    );
    CREATE TABLE IF NOT EXISTS watchlist (
        id TEXT PRIMARY KEY, stock_code TEXT UNIQUE, stock_name TEXT,
        industry TEXT DEFAULT '', concepts TEXT DEFAULT '',
        note TEXT DEFAULT '', added_at INTEGER DEFAULT (strftime('%s','now'))
    );
    CREATE TABLE IF NOT EXISTS daily_stats (
        id TEXT PRIMARY KEY, stat_date TEXT UNIQUE, total_value REAL,
        daily_pnl REAL, daily_pnl_pct REAL, total_pnl REAL, total_pnl_pct REAL,
        max_drawdown REAL DEFAULT 0, win_trades INTEGER DEFAULT 0, lose_trades INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS review_records (
        id TEXT PRIMARY KEY, review_date TEXT UNIQUE,
        market_summary TEXT DEFAULT '', hot_sectors TEXT DEFAULT '',
        reflections TEXT DEFAULT '', tomorrow_plan TEXT DEFAULT '',
        created_at INTEGER DEFAULT (strftime('%s','now'))
    );
    CREATE TABLE IF NOT EXISTS scan_results (
        id TEXT PRIMARY KEY, scan_time INTEGER,
        results TEXT DEFAULT '[]'
    );
    """)
    # Ensure default account row
    c.execute("INSERT OR IGNORE INTO sim_account(id,cash,peak_value) VALUES('main',440000,440000)")
    conn.commit()
    conn.close()

# ── EastMoney helpers ───────────────────────────────────────────────────────
EM_TIMEOUT = 8

def _secid(code: str) -> str:
    if code.startswith("6") or code.startswith("9"):
        return f"1.{code}"
    return f"0.{code}"

def fetch_quotes(codes: List[str]) -> List[Dict]:
    if not codes:
        return []
    secids = ",".join(_secid(c) for c in codes)
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fltt": 2,
        "invt": 2,
        "fields": "f12,f14,f2,f3,f4,f5,f6,f7,f8,f10,f15,f16,f17,f18,f20,f21,f23,f9",
        "secids": secids,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "pn": 1,
        "pz": 200,
    }
    try:
        r = requests.get(url, params=params, timeout=EM_TIMEOUT)
        data = r.json()
        items = data.get("data", {}).get("diff", []) or []
        result = []
        for it in items:
            result.append({
                "code":             str(it.get("f12", "")),
                "name":             it.get("f14", ""),
                "current_price":    _safe_float(it.get("f2")),
                "change_pct":       _safe_float(it.get("f3")),
                "chg_amount":       _safe_float(it.get("f4")),
                "volume":           _safe_float(it.get("f5")),
                "turnover_amount":  _safe_float(it.get("f6")),
                "amplitude":        _safe_float(it.get("f7")),
                "turnover_rate":    _safe_float(it.get("f8")),   # f8=换手率
                "pe_dynamic":       _safe_float(it.get("f9")),
                "volume_ratio":     _safe_float(it.get("f10")),  # f10=量比
                "high_price":       _safe_float(it.get("f15")),
                "low_price":        _safe_float(it.get("f16")),
                "open_price":       _safe_float(it.get("f17")),
                "prev_close_price": _safe_float(it.get("f18")),
                "total_market_cap": _safe_float(it.get("f20")),
                "float_market_cap": _safe_float(it.get("f21")),
                "pb":               _safe_float(it.get("f23")),
            })
        return result
    except Exception as e:
        log.warning(f"fetch_quotes error: {e}")
        return []

def _safe_float(v, default=0.0) -> float:
    try:
        if v is None or v == "-":
            return default
        return float(v)
    except Exception:
        return default

def fetch_kline(code: str, period: int = 101, limit: int = 120) -> List[Dict]:
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": _secid(code),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": period,
        "fqt": 1,
        "lmt": limit,
        "end": "20500101",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    try:
        r = requests.get(url, params=params, timeout=EM_TIMEOUT)
        data = r.json()
        klines_raw = data.get("data", {}).get("klines", []) or []
        result = []
        for k in klines_raw:
            parts = k.split(",")
            if len(parts) < 7:
                continue
            result.append({
                "date":        parts[0],
                "open":        _safe_float(parts[1]),
                "close":       _safe_float(parts[2]),
                "high":        _safe_float(parts[3]),
                "low":         _safe_float(parts[4]),
                "volume":      _safe_float(parts[5]),
                "turnover":    _safe_float(parts[6]),
                "change_pct":  _safe_float(parts[8]) if len(parts) > 8 else 0.0,
                "amplitude":   _safe_float(parts[9]) if len(parts) > 9 else 0.0,
                "turnover_rate": _safe_float(parts[10]) if len(parts) > 10 else 0.0,
            })
        return result
    except Exception as e:
        log.warning(f"fetch_kline {code} error: {e}")
        return []

def fetch_moneyflow_detail(code: str, days: int = 10) -> Dict:
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
    params = {
        "lmt": days,
        "klt": 101,
        "secid": _secid(code),
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
    }
    base = {
        "main_net_inflow": 0.0,
        "main_net_inflow_pct": 0.0,
        "super_large_net_inflow": 0.0,
        "large_net_inflow": 0.0,
        "medium_net_inflow": 0.0,
        "small_net_inflow": 0.0,
        "main_net_inflow_3d": 0.0,
        "main_net_inflow_5d": 0.0,
        "main_net_inflow_10d": 0.0,
        "main_inflow_positive": False,
        "main_continuous_inflow": False,
        "main_continuous_outflow": False,
        "super_large_inflow_positive": False,
        "large_inflow_positive": False,
        "small_inflow_positive": False,
        "retail_taking_over": False,
        "institution_accumulation": False,
        "strong_money_flow": False,
        "money_price_divergence": False,
    }
    try:
        r = requests.get(url, params=params, timeout=EM_TIMEOUT)
        data = r.json()
        klines = data.get("data", {}).get("klines", []) or []
        if not klines:
            return base

        parsed = []
        for k in klines:
            parts = k.split(",")
            if len(parts) < 9:
                continue
            parsed.append({
                "date":          parts[0],
                "main_net":      _safe_float(parts[1]),
                "super_net":     _safe_float(parts[2]),
                "large_net":     _safe_float(parts[3]),
                "medium_net":    _safe_float(parts[4]),
                "small_net":     _safe_float(parts[5]),
                "main_pct":      _safe_float(parts[6]),
                "close":         _safe_float(parts[7]),
            })

        if not parsed:
            return base

        latest = parsed[-1]
        main_vals = [p["main_net"] for p in parsed]

        base["main_net_inflow"]           = latest["main_net"]
        base["main_net_inflow_pct"]       = latest["main_pct"]
        base["super_large_net_inflow"]    = latest["super_net"]
        base["large_net_inflow"]          = latest["large_net"]
        base["medium_net_inflow"]         = latest["medium_net"]
        base["small_net_inflow"]          = latest["small_net"]

        base["main_net_inflow_3d"]  = sum(v for v in main_vals[-3:])
        base["main_net_inflow_5d"]  = sum(v for v in main_vals[-5:])
        base["main_net_inflow_10d"] = sum(v for v in main_vals[-10:])

        base["main_inflow_positive"]        = latest["main_net"] > 0
        base["super_large_inflow_positive"] = latest["super_net"] > 0
        base["large_inflow_positive"]       = latest["large_net"] > 0
        base["small_inflow_positive"]       = latest["small_net"] > 0

        # Continuous inflow/outflow (last 3 days)
        last3 = main_vals[-3:]
        if len(last3) >= 3:
            base["main_continuous_inflow"]  = all(v > 0 for v in last3)
            base["main_continuous_outflow"] = all(v < 0 for v in last3)

        base["retail_taking_over"] = (
            latest["main_net"] < 0 and latest["small_net"] > 0
        )
        base["institution_accumulation"] = (
            latest["main_net"] > 0 and
            latest["super_net"] > 0 and
            latest["large_net"] > 0
        )
        base["strong_money_flow"] = (
            base["institution_accumulation"] and
            base["main_net_inflow_5d"] > 0
        )
        # Price-money divergence: price rising but main outflow 3d
        if len(parsed) >= 3:
            price_up = parsed[-1]["close"] > parsed[-3]["close"]
            money_out = base["main_net_inflow_3d"] < 0
            base["money_price_divergence"] = price_up and money_out

        return base
    except Exception as e:
        log.warning(f"fetch_moneyflow_detail {code} error: {e}")
        return base

def fetch_sector_hot() -> List[Dict]:
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1, "pz": 40, "po": 1, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2,
        "fid": "f3",
        "fs": "m:90+t:2+f:!50",
        "fields": "f2,f3,f4,f12,f14,f20,f8,f6",
        "cb": "",
    }
    try:
        r = requests.get(url, params=params, timeout=EM_TIMEOUT)
        data = r.json()
        items = data.get("data", {}).get("diff", []) or []
        result = []
        for i, it in enumerate(items):
            result.append({
                "rank":       i + 1,
                "code":       str(it.get("f12", "")),
                "name":       it.get("f14", ""),
                "change_pct": _safe_float(it.get("f3")),
                "leader_change_pct": _safe_float(it.get("f8")),
                "turnover":   _safe_float(it.get("f6")),
                "market_cap": _safe_float(it.get("f20")),
            })
        return result
    except Exception as e:
        log.warning(f"fetch_sector_hot error: {e}")
        return []

def search_stocks(q: str) -> List[Dict]:
    url = "https://searchapi.eastmoney.com/api/suggest/get"
    params = {
        "input": q,
        "type": "14",
        "token": "D43BF722C8E33BDC906FB84D85E326EC",
        "count": 10,
    }
    try:
        r = requests.get(url, params=params, timeout=EM_TIMEOUT)
        data = r.json()
        items = data.get("QuotationCodeTable", {}).get("Data", []) or []
        result = []
        for it in items:
            code = it.get("Code", "")
            mktnum = it.get("MktNum", "")
            result.append({
                "code":   code,
                "name":   it.get("Name", ""),
                "market": mktnum,
                "type":   it.get("SecurityType", ""),
            })
        return result
    except Exception as e:
        log.warning(f"search_stocks error: {e}")
        return []

# ── K-line indicators ───────────────────────────────────────────────────────
def _ma(closes: List[float], n: int) -> float:
    if len(closes) < n:
        return closes[-1] if closes else 0.0
    return sum(closes[-n:]) / n

def build_k_factors(klines: List[Dict], current_price: float) -> Dict:
    if not klines:
        return {}
    closes  = [k["close"]  for k in klines]
    volumes = [k["volume"] for k in klines]
    cp      = current_price if current_price else (closes[-1] if closes else 0)

    ma5  = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)

    def pct_diff(a, b):
        return round((a - b) / b * 100, 2) if b else 0.0

    avg_vol_5  = sum(volumes[-5:])  / min(5,  len(volumes)) if volumes else 0
    avg_vol_20 = sum(volumes[-20:]) / min(20, len(volumes)) if volumes else 0
    vr_5_20    = round(avg_vol_5 / avg_vol_20, 2) if avg_vol_20 else 1.0

    last20_closes = closes[-20:] if len(closes) >= 20 else closes
    last20_max    = max(last20_closes) if last20_closes else cp
    last20_min    = min(last20_closes) if last20_closes else cp

    rise_5d  = pct_diff(cp, closes[-6])  if len(closes) > 5  else 0.0
    rise_10d = pct_diff(cp, closes[-11]) if len(closes) > 10 else 0.0
    rise_20d = pct_diff(cp, closes[-21]) if len(closes) > 20 else 0.0
    rise_60d = pct_diff(cp, closes[-61]) if len(closes) > 60 else 0.0

    breakout  = cp > last20_max and avg_vol_5 >= avg_vol_20 * 1.2
    breakdown = cp < last20_min

    # High-volume stagnation: up 20% in 20d, vol ratio >2, today's change <2%
    today_change = klines[-1].get("change_pct", 0)
    high_volume_stagnation = (
        rise_20d > 20 and vr_5_20 > 2 and abs(today_change) < 2
    )

    bullish_ma = ma5 > ma10 > ma20 > ma60 and ma5 > 0 and ma60 > 0

    # Trend weakening: consecutive decline in ma5
    trend_weakening = False
    if len(closes) >= 6:
        ma5_vals = [_ma(closes[:i+1], 5) for i in range(len(closes)-5, len(closes))]
        if len(ma5_vals) >= 3:
            trend_weakening = all(ma5_vals[i] > ma5_vals[i+1] for i in range(len(ma5_vals)-1))

    return {
        "ma5": round(ma5, 3), "ma10": round(ma10, 3),
        "ma20": round(ma20, 3), "ma60": round(ma60, 3),
        "above_ma5":  cp > ma5,
        "above_ma10": cp > ma10,
        "above_ma20": cp > ma20,
        "above_ma60": cp > ma60,
        "distance_ma5_pct":  pct_diff(cp, ma5),
        "distance_ma20_pct": pct_diff(cp, ma20),
        "distance_ma60_pct": pct_diff(cp, ma60),
        "rise_5d_pct":  rise_5d,
        "rise_10d_pct": rise_10d,
        "rise_20d_pct": rise_20d,
        "rise_60d_pct": rise_60d,
        "avg_volume_5d":  round(avg_vol_5, 0),
        "avg_volume_20d": round(avg_vol_20, 0),
        "volume_ratio_5_20": vr_5_20,
        "is_volume_expanding": vr_5_20 >= 1.5,
        "is_volume_shrinking": vr_5_20 <= 0.7,
        "breakout_platform":        breakout,
        "breakdown_platform":       breakdown,
        "high_volume_stagnation":   high_volume_stagnation,
        "bullish_ma_alignment":     bullish_ma,
        "trend_weakening":          trend_weakening,
    }

# ── Scoring models ──────────────────────────────────────────────────────────

def score_v60(factors: Dict, sectors: List[Dict]) -> Dict:
    f = factors
    reasons  = []
    warnings = []
    details  = {}
    exclude_reasons = []

    # ── Hard excludes ──────────────────────────────────────────────────────
    hard_exclude = False
    if f.get("is_st"):
        hard_exclude = True; exclude_reasons.append("ST股票")
    if f.get("is_loss"):
        hard_exclude = True; exclude_reasons.append("亏损企业")
    if f.get("has_major_risk"):
        hard_exclude = True; exclude_reasons.append("重大风险")
    if f.get("has_investigation"):
        hard_exclude = True; exclude_reasons.append("被立案调查")
    if f.get("has_penalty"):
        hard_exclude = True; exclude_reasons.append("受到重大处罚")
    if f.get("has_delisting_risk"):
        hard_exclude = True; exclude_reasons.append("退市风险")

    pe = f.get("pe_dynamic", 0) or 0
    net_profit_yoy = f.get("net_profit_yoy", 0) or 0
    if pe > 60 and net_profit_yoy < 20:
        hard_exclude = True; exclude_reasons.append("高估值无业绩支撑(PE>60且净利润增速<20%)")

    main_cont_out = f.get("main_continuous_outflow", False)
    retail_over   = f.get("retail_taking_over", False)
    if main_cont_out and retail_over:
        hard_exclude = True; exclude_reasons.append("主力持续出货散户接盘")

    if f.get("high_volume_stagnation"):
        hard_exclude = True; exclude_reasons.append("高位放量滞涨(涨幅>20%且量比>2但涨幅<2%)")

    if hard_exclude:
        return {
            "score": 0, "grade": "D淘汰", "strategy": "",
            "hard_exclude": True, "exclude_reasons": exclude_reasons,
            "reasons": exclude_reasons, "warnings": warnings, "details": {},
        }

    total = 0

    # ── 1. 板块热度 20pts ─────────────────────────────────────────────────
    sector_rank    = f.get("sector_rank", 99)
    main_inflow    = f.get("main_net_inflow", 0) or 0
    if sector_rank <= 5 and main_inflow > 0:
        s1 = 19
        reasons.append("板块热度TOP5且主力资金流入")
    elif sector_rank <= 10:
        s1 = 15
        reasons.append("板块热度TOP10")
    elif sector_rank <= 20:
        s1 = 10
    else:
        s1 = 4
        warnings.append("板块热度较弱")
    details["板块热度"] = s1; total += s1

    # ── 2. 个股位置 15pts ─────────────────────────────────────────────────
    change_pct  = f.get("change_pct", 0) or 0
    above_ma5   = f.get("above_ma5", False)
    rise_20d    = f.get("rise_20d_pct", 0) or 0
    if 3 <= change_pct <= 7 and rise_20d < 30:
        s2 = 13
        reasons.append("股价涨幅适中未追高")
    elif 0 <= change_pct < 3 and above_ma5:
        s2 = 9
        reasons.append("股价启动初期")
    elif change_pct > 9:
        s2 = 3
        warnings.append("涨幅过大追高风险")
    elif change_pct < -3:
        s2 = 4
        warnings.append("跌幅较大")
    else:
        s2 = 7
    details["个股位置"] = s2; total += s2

    # ── 3. 成交量/量比 15pts ──────────────────────────────────────────────
    vr_5_20  = f.get("volume_ratio_5_20", 1.0) or 1.0
    breakout = f.get("breakout_platform", False)
    vol_ratio = f.get("volume_ratio", 1.0) or 1.0
    if vr_5_20 >= 1.5 and breakout:
        s3 = 13
        reasons.append("量比放大突破平台")
    elif vr_5_20 >= 1.2:
        s3 = 10
        reasons.append("成交量适度放大")
    elif vr_5_20 <= 0.5 or vol_ratio > 5:
        s3 = 4
        warnings.append("成交量异常(极度萎缩或极度放大)")
    else:
        s3 = 8
    details["成交量量比"] = s3; total += s3

    # ── 4. 主力资金 20pts ─────────────────────────────────────────────────
    super_in  = f.get("super_large_inflow_positive", False)
    large_in  = f.get("large_inflow_positive", False)
    main_pos  = f.get("main_inflow_positive", False)
    if main_pos and super_in and large_in:
        s4 = 18
        reasons.append("主力超大单大单全面流入")
    elif main_pos:
        s4 = 13
        reasons.append("主力资金净流入")
    elif main_cont_out:
        s4 = 4
        warnings.append("主力连续流出")
    else:
        s4 = 8
    details["主力资金"] = s4; total += s4

    # ── 5. 技术突破 15pts ─────────────────────────────────────────────────
    vol_exp  = f.get("is_volume_expanding", False)
    above20  = f.get("above_ma20", False)
    bullish  = f.get("bullish_ma_alignment", False)
    breakdown= f.get("breakdown_platform", False)
    if breakout and vol_exp:
        s5 = 13
        reasons.append("放量突破平台")
    elif above20 and bullish:
        s5 = 9
        reasons.append("均线多头趋势回踩")
    elif breakdown:
        s5 = 2
        warnings.append("跌破平台支撑")
    else:
        s5 = 6
    details["技术突破"] = s5; total += s5

    # ── 6. 风险过滤 10pts ─────────────────────────────────────────────────
    minor_risk = f.get("minor_risk", False)
    if not minor_risk and not hard_exclude:
        s6 = 9
    elif minor_risk:
        s6 = 5
        warnings.append("存在轻微风险")
    else:
        s6 = 2
    details["风险过滤"] = s6; total += s6

    # ── 7. 催化剂 5pts ────────────────────────────────────────────────────
    has_catalyst = f.get("has_catalyst", False)
    catalyst_cnt = f.get("catalyst_count", 0) or 0
    if has_catalyst and catalyst_cnt >= 2:
        s7 = 5
        reasons.append("明确催化剂驱动")
    elif has_catalyst:
        s7 = 3
    else:
        s7 = 1
    details["催化剂"] = s7; total += s7

    # ── Grade ─────────────────────────────────────────────────────────────
    if total >= 90:   grade = "S强信号"
    elif total >= 85: grade = "A可买"
    elif total >= 80: grade = "B候选"
    elif total >= 70: grade = "C弱"
    else:             grade = "D淘汰"

    # ── Strategy ──────────────────────────────────────────────────────────
    is_hot = f.get("is_hot_industry", False) or f.get("is_hot_concept", False)
    vol_shrink = f.get("is_volume_shrinking", False)
    strong_mf  = f.get("strong_money_flow", False)
    inst_acc   = f.get("institution_accumulation", False)

    if is_hot and breakout and vol_exp:
        strategy = "A"
    elif above_ma5 and not breakout and vol_shrink and bullish:
        strategy = "B"
    elif strong_mf or inst_acc:
        strategy = "C"
    else:
        strategy = "B"

    return {
        "score": total, "grade": grade, "strategy": strategy,
        "hard_exclude": False, "exclude_reasons": [],
        "reasons": reasons, "warnings": warnings, "details": details,
    }


def score_v42(factors: Dict) -> Dict:
    f = factors
    reasons  = []
    warnings = []
    details  = {}
    total    = 0

    # ── 1. 价格与位置 10pts ───────────────────────────────────────────────
    above_ma20 = f.get("above_ma20", False)
    above_ma60 = f.get("above_ma60", False)
    dist_ma60  = f.get("distance_ma60_pct", 0) or 0
    rise_20d   = f.get("rise_20d_pct", 0) or 0
    if above_ma20 and above_ma60 and rise_20d < 20:
        s1 = 9
        reasons.append("低位站上MA20和MA60")
    elif above_ma20 and rise_20d < 30:
        s1 = 6
    elif dist_ma60 > 30 or rise_20d > 40:
        s1 = 2
        warnings.append("股价远离均线高位风险")
    else:
        s1 = 4
    details["价格与位置"] = s1; total += s1

    # ── 2. 行业逻辑 15pts ─────────────────────────────────────────────────
    is_hot_ind  = f.get("is_hot_industry", False)
    is_hot_con  = f.get("is_hot_concept", False)
    sector_rank = f.get("sector_rank", 99)
    if is_hot_ind or is_hot_con:
        s2 = 14
        reasons.append("处于热门行业/概念")
    elif sector_rank <= 20:
        s2 = 10
    elif sector_rank <= 40:
        s2 = 6
    else:
        s2 = 3
        warnings.append("行业热度偏低")
    details["行业逻辑"] = s2; total += s2

    # ── 3. 基本面质量 15pts ───────────────────────────────────────────────
    roe            = f.get("roe", 0) or 0
    healthy_cash   = f.get("healthy_cashflow", False)
    high_debt      = f.get("high_debt", False)
    if roe >= 12 and healthy_cash and not high_debt:
        s3 = 14
        reasons.append("ROE优秀现金流健康低负债")
    elif roe >= 8:
        s3 = 10
    elif roe >= 5:
        s3 = 6
    else:
        s3 = 3
        warnings.append("基本面质量偏差")
    details["基本面质量"] = s3; total += s3

    # ── 4. 业绩变化 15pts ─────────────────────────────────────────────────
    net_yoy     = f.get("net_profit_yoy", 0) or 0
    rev_yoy     = f.get("revenue_yoy", 0) or 0
    is_loss     = f.get("is_loss", False)
    if is_loss:
        s4 = 1
        warnings.append("亏损企业")
    elif net_yoy >= 80 and rev_yoy >= 30:
        s4 = 14
        reasons.append("业绩爆炸性增长")
    elif net_yoy >= 30:
        s4 = 12
        reasons.append("业绩高速增长")
    elif net_yoy >= 10:
        s4 = 8
    elif net_yoy >= 0:
        s4 = 5
    else:
        s4 = 2
        warnings.append("业绩下滑")
    details["业绩变化"] = s4; total += s4

    # ── 5. 催化剂 10pts ───────────────────────────────────────────────────
    catalyst_cnt = f.get("catalyst_count", 0) or 0
    has_cat      = f.get("has_catalyst", False)
    if has_cat and catalyst_cnt >= 2:
        s5 = 9
        reasons.append("多重催化剂共振")
    elif has_cat:
        s5 = 6
    elif catalyst_cnt == 0:
        s5 = 1
        warnings.append("缺乏明确催化剂")
    else:
        s5 = 3
    details["催化剂"] = s5; total += s5

    # ── 6. 资金面 10pts ───────────────────────────────────────────────────
    strong_mf  = f.get("strong_money_flow", False)
    main_pos   = f.get("main_inflow_positive", False)
    main_5d    = f.get("main_net_inflow_5d", 0) or 0
    if strong_mf:
        s6 = 9
        reasons.append("主力资金强势流入")
    elif main_pos and main_5d > 0:
        s6 = 6
    elif main_5d < 0:
        s6 = 2
        warnings.append("主力资金持续流出")
    else:
        s6 = 4
    details["资金面"] = s6; total += s6

    # ── 7. 技术面 10pts ───────────────────────────────────────────────────
    bullish = f.get("bullish_ma_alignment", False)
    if bullish and above_ma20:
        s7 = 9
        reasons.append("均线多头排列")
    elif above_ma20:
        s7 = 6
    elif f.get("breakdown_platform", False):
        s7 = 2
        warnings.append("跌破平台技术破位")
    else:
        s7 = 4
    details["技术面"] = s7; total += s7

    # ── 8. 估值 10pts ─────────────────────────────────────────────────────
    pe = f.get("pe_dynamic", 0) or 0
    pb = f.get("pb", 0) or 0
    net_yoy2 = f.get("net_profit_yoy", 0) or 0
    if 0 < pe <= 30 or (pe == 0 and pb < 2):
        s8 = 9
        reasons.append("估值合理")
    elif pe <= 50 and net_yoy2 >= 30:
        s8 = 6
        reasons.append("高估值有业绩支撑")
    elif pe > 60 and net_yoy2 < 20:
        s8 = 2
        warnings.append("高估值无业绩支撑")
    else:
        s8 = 5
    details["估值"] = s8; total += s8

    # ── 9. 风险 5pts ──────────────────────────────────────────────────────
    has_major = f.get("has_major_risk", False)
    minor     = f.get("minor_risk", False)
    is_st     = f.get("is_st", False)
    if has_major or is_st:
        s9 = 0
        warnings.append("存在重大风险")
    elif minor:
        s9 = 3
    else:
        s9 = 5
    details["风险"] = s9; total += s9

    # ── Grade ─────────────────────────────────────────────────────────────
    if total >= 90:   grade = "核心机会"
    elif total >= 85: grade = "优质机会"
    elif total >= 80: grade = "跟踪机会"
    elif total >= 75: grade = "观察池"
    elif total >= 70: grade = "偏弱"
    else:             grade = "淘汰"

    return {"score": total, "grade": grade, "reasons": reasons, "warnings": warnings, "details": details}


def score_zhuangli(factors: Dict) -> Dict:
    f = factors
    reasons = []
    details = {}
    total   = 0

    main_5d  = f.get("main_net_inflow_5d",  0) or 0
    main_10d = f.get("main_net_inflow_10d", 0) or 0
    super_in = f.get("super_large_inflow_positive", False)
    large_in = f.get("large_inflow_positive", False)
    small_in = f.get("small_inflow_positive", False)
    main_now = f.get("main_net_inflow", 0) or 0
    main_pos = f.get("main_inflow_positive", False)
    above_20 = f.get("above_ma20", False)
    vr_5_20  = f.get("volume_ratio_5_20", 1.0) or 1.0

    # ── 1. 近5日主力趋势 20pts ────────────────────────────────────────────
    if main_5d > 5e7:
        s1 = 18; reasons.append("近5日主力大幅净流入")
    elif main_5d > 0:
        s1 = 13; reasons.append("近5日主力净流入")
    elif main_5d > -1e7:
        s1 = 8
    else:
        s1 = 3
    details["近5日主力趋势"] = s1; total += s1

    # ── 2. 近10日主力趋势 15pts ───────────────────────────────────────────
    if main_10d > 1e8:
        s2 = 14; reasons.append("近10日主力持续大幅流入")
    elif main_10d > 0:
        s2 = 10
    elif main_10d > -5e7:
        s2 = 6
    else:
        s2 = 2
    details["近10日主力趋势"] = s2; total += s2

    # ── 3. 大单/超大单占比 15pts ──────────────────────────────────────────
    if super_in and large_in:
        s3 = 14; reasons.append("超大单大单同步净流入")
    elif super_in or large_in:
        s3 = 9
    else:
        s3 = 3
    details["大单超大单占比"] = s3; total += s3

    # ── 4. 小单方向 10pts ─────────────────────────────────────────────────
    small_out = not small_in
    if small_out and main_pos:
        s4 = 10; reasons.append("散户流出主力接筹")
    elif small_in and not main_pos:
        s4 = 0
    else:
        s4 = 5
    details["小单方向"] = s4; total += s4

    # ── 5. 价格资金配合 10pts ─────────────────────────────────────────────
    div = f.get("money_price_divergence", False)
    if main_pos and above_20 and not div:
        s5 = 9; reasons.append("价格资金同向良好")
    elif div:
        s5 = 2
    else:
        s5 = 5
    details["价格资金配合"] = s5; total += s5

    # ── 6. 筹码集中度 15pts ───────────────────────────────────────────────
    # Proxy: low vol ratio + above ma20 = accumulation
    if vr_5_20 < 1.0 and above_20:
        s6 = 13; reasons.append("缩量在均线上方筹码集中")
    elif vr_5_20 < 1.3 and above_20:
        s6 = 9
    elif vr_5_20 > 2.5:
        s6 = 3
    else:
        s6 = 6
    details["筹码集中度"] = s6; total += s6

    # ── 7. 龙虎榜机构 10pts ───────────────────────────────────────────────
    inst_buy  = f.get("has_institution_buy", False)
    inst_net  = f.get("institution_net_buy_positive", False)
    hot_money = f.get("has_hot_money", False)
    inst_sell = f.get("institution_selling", False)
    if inst_buy and inst_net:
        s7 = 9; reasons.append("机构龙虎榜净买入")
    elif hot_money:
        s7 = 5
    elif inst_sell:
        s7 = 1
    else:
        s7 = 4
    details["龙虎榜机构"] = s7; total += s7

    # ── 8. 公告风险 5pts ──────────────────────────────────────────────────
    has_risk = f.get("has_major_risk", False) or f.get("has_investigation", False)
    if has_risk:
        s8 = 0
    elif f.get("minor_risk", False):
        s8 = 3
    else:
        s8 = 5
    details["公告风险"] = s8; total += s8

    # ── Grade ─────────────────────────────────────────────────────────────
    if total >= 80:   grade = "强势吸筹"
    elif total >= 65: grade = "温和流入"
    elif total >= 50: grade = "中性"
    else:             grade = "资金流出"

    # ── State detection ───────────────────────────────────────────────────
    cont_in  = f.get("main_continuous_inflow", False)
    cont_out = f.get("main_continuous_outflow", False)
    vol_exp  = f.get("is_volume_expanding", False)
    breakout = f.get("breakout_platform", False)
    retail   = f.get("retail_taking_over", False)

    if cont_in and not vol_exp and not breakout:
        state = "疑似吸筹"
    elif cont_in and vol_exp and breakout:
        state = "拉升阶段"
    elif cont_out and retail:
        state = "疑似派发"
    elif not cont_in and not cont_out and vr_5_20 < 0.8:
        state = "洗盘阶段"
    else:
        state = "观察中"

    return {"score": total, "grade": grade, "state": state, "reasons": reasons, "details": details}


def calc_position_risk(holding_cost: float, current_price: float, qty: int,
                       account_total: float, factors: Dict) -> Dict:
    cost = holding_cost
    cp   = current_price if current_price else cost

    normal_stop    = round(cost * 0.97, 3)
    hard_stop      = round(cost * 0.95, 3)
    midterm_stop   = round(cost * 0.92, 3)
    tp_alert       = round(cost * 1.05, 3)
    tp_strong      = round(cost * 1.08, 3)

    market_val     = cp * qty
    cost_val       = cost * qty
    pnl            = market_val - cost_val
    pnl_pct        = round((cp - cost) / cost * 100, 2) if cost else 0.0
    position_ratio = round(market_val / account_total * 100, 2) if account_total else 0.0

    if pnl_pct <= -5:
        urgency = "critical"
    elif pnl_pct <= -3:
        urgency = "warning"
    elif pnl_pct >= 5:
        urgency = "profit"
    else:
        urgency = "normal"

    return {
        "normal_stop_loss":  normal_stop,
        "hard_stop_loss":    hard_stop,
        "midterm_stop_loss": midterm_stop,
        "take_profit_alert": tp_alert,
        "take_profit_strong": tp_strong,
        "current_price":     cp,
        "cost_price":        cost,
        "qty":               qty,
        "market_value":      round(market_val, 2),
        "pnl":               round(pnl, 2),
        "pnl_pct":           pnl_pct,
        "position_ratio":    position_ratio,
        "urgency":           urgency,
        "trading_paused":    False,
        "below_normal_stop": cp < normal_stop,
        "below_hard_stop":   cp < hard_stop,
        "above_tp_alert":    cp > tp_alert,
        "above_tp_strong":   cp > tp_strong,
    }


def full_analysis(code: str, name: str, factors: Dict, sectors: List[Dict]) -> Dict:
    r60 = score_v60(factors, sectors)
    r42 = score_v42(factors)
    rzl = score_zhuangli(factors)

    # Goldman Sachs integrated score proxy
    growth_score    = min(100, max(0, (factors.get("net_profit_yoy", 0) or 0) * 0.5 + 50))
    return_score    = min(100, max(0, (factors.get("roe", 0) or 0) * 3))
    pe              = factors.get("pe_dynamic", 30) or 30
    valuation_score = min(100, max(0, 100 - pe))
    integrated      = round(growth_score * 0.4 + return_score * 0.3 + valuation_score * 0.3, 1)

    cost  = factors.get("current_price", 0) or 0
    stop  = round(cost * 0.97, 3)
    tp    = round(cost * 1.08, 3)

    # Buy/sell signals
    buy_signal  = ""
    sell_signal = ""
    advice_parts = []

    can_sim_buy   = r60["score"] >= 85 and not r60["hard_exclude"]
    need_sim_sell = False

    # Check position risk if we have cost info
    holding_cost = factors.get("holding_cost", 0)
    if holding_cost and cost:
        qty   = factors.get("qty", 100)
        total = factors.get("account_total", 440000)
        risk  = calc_position_risk(holding_cost, cost, qty, total, factors)
        need_sim_sell = risk["urgency"] == "critical"
        stop  = risk["hard_stop_loss"]
        tp    = risk["take_profit_strong"]

    if can_sim_buy:
        buy_signal = f"【买入信号】V60得分{r60['score']}分({r60['grade']}), 策略{r60['strategy']}"
        advice_parts.append(f"建议买入, 止损{stop}, 目标{tp}")
    elif r60["hard_exclude"]:
        buy_signal = ""
        sell_signal = f"【排除】{'; '.join(r60['exclude_reasons'])}"
        advice_parts.append("不建议买入, 存在硬性排除条件")
    else:
        advice_parts.append(f"观望, V60得分{r60['score']}分, 未达买入阈值(85分)")

    if need_sim_sell:
        sell_signal = "【止损】浮亏超5%, 触发硬止损"
        advice_parts.append("建议立即止损出场")

    # Risk warnings
    risk_warning = []
    for w in r60.get("warnings", [])[:3]:
        risk_warning.append(w)
    for w in r42.get("warnings", []):
        if w not in risk_warning and len(risk_warning) < 5:
            risk_warning.append(w)

    return {
        "code":          code,
        "name":          name,
        "score_v60":     r60["score"],
        "score_v42":     r42["score"],
        "score_zl":      rzl["score"],
        "grade_v60":     r60["grade"],
        "grade_v42":     r42["grade"],
        "grade_zl":      rzl["grade"],
        "strategy":      r60["strategy"],
        "buy_signal":    buy_signal,
        "sell_signal":   sell_signal,
        "operation_advice": "; ".join(advice_parts),
        "stop_loss":     stop,
        "take_profit":   tp,
        "can_sim_buy":   can_sim_buy,
        "need_sim_sell": need_sim_sell,
        "risk_warning":  risk_warning[:5],
        "hard_exclude":  r60["hard_exclude"],
        "exclude_reasons": r60["exclude_reasons"],
        "integrated_score": integrated,
        "zhuangli_state":   rzl["state"],
        "r60": r60, "r42": r42, "rzl": rzl,
    }

# ── Auto-scan pool ──────────────────────────────────────────────────────────
SCAN_POOL = [
    "300502","688981","300750","002594","300059","601318","000858","600519",
    "002415","300015","000001","000002","600036","601166","002230","300124",
    "000725","002475","300014","688111","000568","600887","601888","002714",
    "300760","688012","002352","300223","600941","603986","002812","300496",
    "600760","688036","300274","002129","300144","000100","002049","300433",
    "603288","000799","300408","600309","000538","688599","300919","300999",
    "600438","688396","600905","002371","300136","688187","002459","300628",
    "603501","688041","601012","300894","600745","300450","300661","688169",
    "300316","603899","300676","002493","002910","300558","300413","688256",
    "300347","600667","002610","300418","601799","603605","002463","688618",
    "002648","603893","002202","300760","300502","688981","002812","300223",
    "300418","600438","002202","300750","300136","600905","688187","002459",
    "300628","688256","300347",
]
# deduplicate
SCAN_POOL = list(dict.fromkeys(SCAN_POOL))[:100]

_scan_lock   = threading.Lock()
_scan_running = False

def _is_weekend() -> bool:
    return datetime.now().weekday() >= 5

def auto_scan():
    global _scan_running
    if _is_weekend():
        log.info("auto_scan: weekend, skip")
        return
    if not _scan_lock.acquire(blocking=False):
        log.info("auto_scan: already running")
        return
    _scan_running = True
    try:
        log.info("auto_scan: start")
        # Market sentiment from SH000001
        sh_quotes = fetch_quotes(["000001"])
        market_change = 0.0
        if sh_quotes:
            market_change = sh_quotes[0].get("change_pct", 0) or 0

        sectors = fetch_sector_hot()

        candidates = []
        batch_size = 20
        pool = list(dict.fromkeys(SCAN_POOL))

        for i in range(0, len(pool), batch_size):
            batch = pool[i:i+batch_size]
            quotes = fetch_quotes(batch)
            for q in quotes:
                code       = q["code"]
                change_pct = q.get("change_pct", 0) or 0
                turnover   = q.get("turnover_amount", 0) or 0
                # Filter: change_pct in [-1%, 20%], turnover > 5000万
                if not (-1 <= change_pct <= 20):
                    continue
                if turnover < 5e7:
                    continue

                cp = q.get("current_price", 0) or 0
                if cp <= 0:
                    continue

                klines = fetch_kline(code, period=101, limit=120)
                k_factors = build_k_factors(klines, cp)
                mf = fetch_moneyflow_detail(code, days=10)

                # Build sector rank from sectors list
                sector_rank = 99
                for sec in sectors[:10]:
                    # rough match by sector name fragment
                    sector_rank = min(sector_rank, sec["rank"])
                # just use 99 unless we have better mapping
                sector_rank = 99

                factors = {
                    **k_factors, **mf,
                    "current_price":   cp,
                    "change_pct":      change_pct,
                    "volume_ratio":    q.get("volume_ratio", 1.0),
                    "turnover_rate":   q.get("turnover_rate", 0),
                    "pe_dynamic":      q.get("pe_dynamic", 0),
                    "pb":              q.get("pb", 0),
                    "sector_rank":     sector_rank,
                    "is_hot_industry": sector_rank <= 10,
                    "is_hot_concept":  sector_rank <= 15,
                    "is_st":           "ST" in q.get("name", ""),
                    "market_change":   market_change,
                }

                r60 = score_v60(factors, sectors)
                if r60["score"] >= 85 and not r60["hard_exclude"]:
                    candidates.append({
                        "stock_code":  code,
                        "stock_name":  q.get("name", ""),
                        "score_v60":   r60["score"],
                        "grade":       r60["grade"],
                        "strategy":    r60["strategy"],
                        "price":       cp,
                        "change_pct":  change_pct,
                        "reason":      "; ".join(r60.get("reasons", [])),
                        "turnover":    turnover,
                    })

        candidates.sort(key=lambda x: x["score_v60"], reverse=True)
        candidates = candidates[:30]

        # Auto-buy top candidates
        _auto_buy_candidates(candidates)

        # Save to DB
        conn = get_db()
        c    = conn.cursor()
        sid  = str(uuid.uuid4())
        c.execute(
            "INSERT OR REPLACE INTO scan_results(id, scan_time, results) VALUES(?,?,?)",
            (sid, int(time.time()), json.dumps(candidates, ensure_ascii=False))
        )
        conn.commit()
        conn.close()
        log.info(f"auto_scan: done, {len(candidates)} candidates")
    except Exception as e:
        log.error(f"auto_scan error: {e}")
    finally:
        _scan_running = False
        _scan_lock.release()


def _auto_buy_candidates(candidates: List[Dict]):
    """Auto-buy top scan candidates if conditions met."""
    try:
        conn = get_db()
        c    = conn.cursor()
        row  = conn.execute("SELECT cash FROM sim_account WHERE id='main'").fetchone()
        if not row:
            conn.close(); return
        cash = row["cash"]

        positions = conn.execute("SELECT stock_code FROM sim_positions").fetchall()
        pos_codes = {p["stock_code"] for p in positions}

        MAX_POSITIONS = 6
        MAX_PER_STOCK = 80000.0

        if len(pos_codes) >= MAX_POSITIONS:
            conn.close(); return

        for cand in candidates:
            if len(pos_codes) >= MAX_POSITIONS:
                break
            code = cand["stock_code"]
            if code in pos_codes:
                continue
            price = cand["price"]
            if price <= 0:
                continue
            budget = min(MAX_PER_STOCK, cash * 0.2)
            if budget < price * 100:
                continue
            qty = int(budget / price / 100) * 100
            if qty <= 0:
                continue
            amount = round(price * qty, 2)
            if amount > cash:
                continue

            # Insert position
            pid = str(uuid.uuid4())
            normal_sl = round(price * 0.97, 3)
            hard_sl   = round(price * 0.95, 3)
            tp_alert  = round(price * 1.05, 3)
            tp_strong = round(price * 1.08, 3)

            c.execute("""
                INSERT INTO sim_positions
                (id, stock_code, stock_name, qty, cost_price,
                 normal_stop_loss, hard_stop_loss, take_profit_alert, take_profit_strong,
                 score_v60, strategy, buy_reason)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """, (pid, code, cand["stock_name"], qty, price,
                  normal_sl, hard_sl, tp_alert, tp_strong,
                  cand["score_v60"], cand["strategy"], cand["reason"]))

            # Insert trade
            tid = str(uuid.uuid4())
            cash -= amount
            c.execute("""
                INSERT INTO sim_trades
                (id, stock_code, stock_name, direction, price, qty, amount,
                 strategy, score_v60, buy_reason, cash_after)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (tid, code, cand["stock_name"], "buy", price, qty, amount,
                  cand["strategy"], cand["score_v60"], cand["reason"], cash))

            # Signal
            c.execute("""
                INSERT INTO signals(id, stock_code, stock_name, signal_type, strategy, score_v60, price, reason, executed)
                VALUES(?,?,?,?,?,?,?,?,1)
            """, (str(uuid.uuid4()), code, cand["stock_name"], "buy",
                  cand["strategy"], cand["score_v60"], price, cand["reason"]))

            pos_codes.add(code)
            log.info(f"auto_buy: {code} {cand['stock_name']} x{qty} @{price}")

        c.execute("UPDATE sim_account SET cash=?, updated_at=? WHERE id='main'",
                  (round(cash, 2), int(time.time())))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"_auto_buy_candidates error: {e}")


def auto_check_exit():
    if _is_weekend():
        return
    try:
        log.info("auto_check_exit: start")
        conn = get_db()
        positions = conn.execute("SELECT * FROM sim_positions").fetchall()
        conn.close()

        if not positions:
            return

        codes = [p["stock_code"] for p in positions]
        quotes_map = {}
        for i in range(0, len(codes), 20):
            batch = fetch_quotes(codes[i:i+20])
            for q in batch:
                quotes_map[q["code"]] = q

        conn = get_db()
        c    = conn.cursor()
        account = conn.execute("SELECT cash FROM sim_account WHERE id='main'").fetchone()
        cash = account["cash"] if account else 440000

        for pos in positions:
            code  = pos["stock_code"]
            q     = quotes_map.get(code)
            if not q:
                continue
            cp    = q.get("current_price", 0) or 0
            if cp <= 0:
                continue
            cost  = pos["cost_price"]
            qty   = pos["qty"]
            pnl_pct = (cp - cost) / cost * 100 if cost else 0

            sell_reason = ""
            do_sell     = False

            if pnl_pct <= -5:
                sell_reason = f"触发硬止损, 浮亏{pnl_pct:.1f}%"
                do_sell = True
            elif pnl_pct <= -3:
                sell_reason = f"触发普通止损, 浮亏{pnl_pct:.1f}%"
                do_sell = True
            elif pnl_pct >= 20:
                sell_reason = f"强烈止盈信号, 浮盈{pnl_pct:.1f}%"
                do_sell = True
            elif pnl_pct >= 8:
                # Partial sell signal only — insert signal, don't auto sell
                c.execute("""
                    INSERT INTO signals(id, stock_code, stock_name, signal_type, price, reason)
                    VALUES(?,?,?,?,?,?)
                """, (str(uuid.uuid4()), code, pos["stock_name"],
                      "partial_sell", cp, f"浮盈{pnl_pct:.1f}%, 建议部分止盈"))

            # Technical: breakdown_platform
            klines = fetch_kline(code, period=101, limit=30)
            if klines:
                kf = build_k_factors(klines, cp)
                if kf.get("breakdown_platform") and not do_sell:
                    c.execute("""
                        INSERT INTO signals(id, stock_code, stock_name, signal_type, price, reason)
                        VALUES(?,?,?,?,?,?)
                    """, (str(uuid.uuid4()), code, pos["stock_name"],
                          "sell_warning", cp, "跌破平台支撑, 建议关注"))

            if do_sell:
                amount = round(cp * qty, 2)
                pnl    = round((cp - cost) * qty, 2)
                hold_days = max(0, int((time.time() - (pos["bought_at"] or time.time())) / 86400))
                cash += amount
                tid = str(uuid.uuid4())
                c.execute("""
                    INSERT INTO sim_trades
                    (id, stock_code, stock_name, direction, price, qty, amount,
                     sell_reason, pnl, pnl_pct, hold_days, cash_after, is_win)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (tid, code, pos["stock_name"], "sell", cp, qty, amount,
                      sell_reason, pnl, round(pnl_pct, 2), hold_days,
                      round(cash, 2), 1 if pnl > 0 else 0))
                c.execute("DELETE FROM sim_positions WHERE id=?", (pos["id"],))
                log.info(f"auto_check_exit sell: {code} @{cp}, pnl={pnl}")

        c.execute("UPDATE sim_account SET cash=?, updated_at=? WHERE id='main'",
                  (round(cash, 2), int(time.time())))
        conn.commit()
        conn.close()
        log.info("auto_check_exit: done")
    except Exception as e:
        log.error(f"auto_check_exit error: {e}")


def save_daily_stats():
    try:
        conn = get_db()
        account = conn.execute("SELECT cash FROM sim_account WHERE id='main'").fetchone()
        cash = account["cash"] if account else 440000

        positions = conn.execute("SELECT * FROM sim_positions").fetchall()
        pos_val = 0.0
        codes   = [p["stock_code"] for p in positions]
        if codes:
            qs = fetch_quotes(codes)
            qm = {q["code"]: q for q in qs}
            for p in positions:
                cp = qm.get(p["stock_code"], {}).get("current_price", p["cost_price"]) or p["cost_price"]
                pos_val += cp * p["qty"]

        total_val   = cash + pos_val
        INIT_CASH   = 440000.0
        total_pnl   = total_val - INIT_CASH
        total_pnl_p = round(total_pnl / INIT_CASH * 100, 2)

        today = date.today().isoformat()
        # yesterday's total for daily pnl
        prev = conn.execute(
            "SELECT total_value FROM daily_stats ORDER BY stat_date DESC LIMIT 1"
        ).fetchone()
        prev_val  = prev["total_value"] if prev else INIT_CASH
        daily_pnl = total_val - prev_val
        daily_pnl_p = round(daily_pnl / prev_val * 100, 2) if prev_val else 0.0

        # max drawdown from peak
        peak_row = conn.execute("SELECT peak_value FROM sim_account WHERE id='main'").fetchone()
        peak = peak_row["peak_value"] if peak_row else INIT_CASH
        if total_val > peak:
            peak = total_val
            conn.execute("UPDATE sim_account SET peak_value=? WHERE id='main'", (peak,))
        max_dd = round((peak - total_val) / peak * 100, 2) if peak else 0.0

        trades_today = conn.execute(
            "SELECT is_win FROM sim_trades WHERE direction='sell' AND DATE(traded_at,'unixepoch')=?",
            (today,)
        ).fetchall()
        win_t  = sum(1 for t in trades_today if t["is_win"])
        lose_t = len(trades_today) - win_t

        sid = str(uuid.uuid4())
        conn.execute("""
            INSERT OR REPLACE INTO daily_stats
            (id, stat_date, total_value, daily_pnl, daily_pnl_pct,
             total_pnl, total_pnl_pct, max_drawdown, win_trades, lose_trades)
            VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (sid, today, round(total_val, 2), round(daily_pnl, 2), daily_pnl_p,
              round(total_pnl, 2), total_pnl_p, max_dd, win_t, lose_t))
        conn.commit()
        conn.close()
        log.info(f"save_daily_stats: {today} total={total_val:.0f}")
    except Exception as e:
        log.error(f"save_daily_stats error: {e}")


# ── Scheduler ───────────────────────────────────────────────────────────────
def _run_scheduler():
    if not HAS_SCHEDULE:
        return
    import schedule as sch

    def _weekday_job(fn):
        def wrapper():
            if not _is_weekend():
                fn()
        return wrapper

    sch.every().monday.at("09:31").do(_weekday_job(auto_scan))
    sch.every().tuesday.at("09:31").do(_weekday_job(auto_scan))
    sch.every().wednesday.at("09:31").do(_weekday_job(auto_scan))
    sch.every().thursday.at("09:31").do(_weekday_job(auto_scan))
    sch.every().friday.at("09:31").do(_weekday_job(auto_scan))

    sch.every(30).minutes.do(_weekday_job(auto_check_exit))

    sch.every().monday.at("15:35").do(_weekday_job(save_daily_stats))
    sch.every().tuesday.at("15:35").do(_weekday_job(save_daily_stats))
    sch.every().wednesday.at("15:35").do(_weekday_job(save_daily_stats))
    sch.every().thursday.at("15:35").do(_weekday_job(save_daily_stats))
    sch.every().friday.at("15:35").do(_weekday_job(save_daily_stats))

    while True:
        sch.run_pending()
        time.sleep(30)

# ── Pydantic models ─────────────────────────────────────────────────────────
class AnalyzeReq(BaseModel):
    stock_code:  str
    stock_name:  str
    factors:     Dict[str, Any] = {}
    sectors:     List[Dict]     = []

class BuyReq(BaseModel):
    stock_code:  str
    stock_name:  str
    price:       Optional[float] = None
    qty:         Optional[int]   = None
    strategy:    str = ""
    reason:      str = ""

class WatchlistReq(BaseModel):
    stock_code: str
    stock_name: str
    industry:   str = ""
    concepts:   str = ""
    note:       str = ""

class ReviewReq(BaseModel):
    review_date:    str
    market_summary: str = ""
    hot_sectors:    str = ""
    reflections:    str = ""
    tomorrow_plan:  str = ""

class AIReq(BaseModel):
    content:    str
    task:       str = "analyze"
    stock_code: Optional[str] = None
    stock_name: Optional[str] = None
    provider:   str = "deepseek"

# ── WeChat Work (企业微信) helper ────────────────────────────────────────────
def send_wecom(content: str) -> bool:
    if not WECOM_KEY:
        return False
    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={WECOM_KEY}"
    payload = {"msgtype": "text", "text": {"content": content}}
    try:
        r = requests.post(url, json=payload, timeout=5)
        return r.json().get("errcode") == 0
    except Exception as e:
        log.warning(f"send_wecom error: {e}")
        return False

# ── DeepSeek AI helper ───────────────────────────────────────────────────────
TASK_PROMPTS = {
    "analyze": "你是A股量化交易专家, 请对以下个股数据进行综合分析, 给出操作建议:",
    "v42":     "你是A股基本面分析专家, 请对以下个股进行V42多维度评分分析:",
    "zhuangli":"你是主力资金行为分析专家, 请分析以下股票的主力动向和筹码变化:",
    "risk":    "你是风险控制专家, 请分析以下持仓的风险状况并给出止损建议:",
    "sector":  "你是行业轮动专家, 请分析以下板块热度数据并给出配置建议:",
    "review":  "你是A股复盘专家, 请根据以下数据生成今日复盘报告:",
}

def call_deepseek(task: str, content: str) -> str:
    if not DEEPSEEK_API_KEY:
        return "未配置DeepSeek API Key, 请在.env中设置DEEPSEEK_API_KEY"
    system_prompt = TASK_PROMPTS.get(task, TASK_PROMPTS["analyze"])
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type":  "application/json",
    }
    payload = {
        "model":    "deepseek-chat",
        "messages": [
            {"role": "system",  "content": system_prompt},
            {"role": "user",    "content": content},
        ],
        "temperature": 0.7,
        "max_tokens":  2048,
    }
    try:
        r = requests.post(
            f"{DEEPSEEK_BASE}/chat/completions",
            headers=headers, json=payload, timeout=60
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        log.warning(f"call_deepseek error: {e}")
        return f"AI分析失败: {e}"

# ── Startup ─────────────────────────────────────────────────────────────────
@app.on_event("startup")
def on_startup():
    init_db()
    t = threading.Thread(target=_run_scheduler, daemon=True)
    t.start()
    log.info("旺财A股AI量化分析系统 V8.0 启动成功")

# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    p = "static/index.html"
    if os.path.exists(p):
        return FileResponse(p)
    return JSONResponse({"message": "旺财A股AI量化分析系统 V8.0", "version": "8.0"})


@app.get("/health")
def health(_=Depends(require_auth)):
    return {"status": "ok", "version": "8.0", "ts": int(time.time())}


@app.get("/api/market")
def api_market(_=Depends(require_auth)):
    indices  = fetch_quotes(["sh000001", "sz399006", "sz399001", "sh000688"])
    # EastMoney returns code without prefix — strip prefix if present
    sectors  = fetch_sector_hot()
    return {"indices": indices, "sectors": sectors[:20], "ts": int(time.time())}


@app.get("/api/search")
def api_search(q: str = "", _=Depends(require_auth)):
    if not q:
        raise HTTPException(400, "Missing q")
    results = search_stocks(q)
    return {"results": results}


@app.get("/api/quotes")
def api_quotes(codes: str = "", _=Depends(require_auth)):
    if not codes:
        raise HTTPException(400, "Missing codes")
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    data = fetch_quotes(code_list)
    return {"quotes": data}


@app.get("/api/kline/{code}")
def api_kline(code: str, period: int = 101, limit: int = 120, _=Depends(require_auth)):
    klines = fetch_kline(code, period=period, limit=limit)
    quotes = fetch_quotes([code])
    cp = quotes[0]["current_price"] if quotes else 0
    kf = build_k_factors(klines, cp)
    return {"code": code, "klines": klines, "indicators": kf}


@app.get("/api/moneyflow/{code}")
def api_moneyflow(code: str, days: int = 10, _=Depends(require_auth)):
    mf = fetch_moneyflow_detail(code, days=days)
    return {"code": code, "moneyflow": mf}


@app.get("/api/sector-hot")
def api_sector_hot(_=Depends(require_auth)):
    sectors = fetch_sector_hot()
    return {"sectors": sectors}


@app.post("/api/analyze")
def api_analyze(req: AnalyzeReq, _=Depends(require_auth)):
    result = full_analysis(req.stock_code, req.stock_name, req.factors, req.sectors)

    # Persist record
    conn = get_db()
    rid  = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO analysis_records
        (id, stock_code, stock_name, score_v42, score_v60, score_zl,
         grade_v42, grade_v60, grade_zl, strategy, buy_signal, sell_signal,
         stop_loss, take_profit, operation_advice, risk_warning,
         can_sim_buy, need_sim_sell, factors)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (rid, req.stock_code, req.stock_name,
          result["score_v42"], result["score_v60"], result["score_zl"],
          result["grade_v42"], result["grade_v60"], result["grade_zl"],
          result["strategy"], result["buy_signal"], result["sell_signal"],
          result["stop_loss"], result["take_profit"], result["operation_advice"],
          json.dumps(result["risk_warning"], ensure_ascii=False),
          int(result["can_sim_buy"]), int(result["need_sim_sell"]),
          json.dumps(req.factors, ensure_ascii=False)))
    conn.commit()
    conn.close()
    return result


@app.get("/api/analysis-history")
def api_analysis_history(stock_code: str = "", limit: int = 50, _=Depends(require_auth)):
    conn = get_db()
    if stock_code:
        rows = conn.execute(
            "SELECT * FROM analysis_records WHERE stock_code=? ORDER BY created_at DESC LIMIT ?",
            (stock_code, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM analysis_records ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return {"records": [dict(r) for r in rows]}


@app.get("/api/account")
def api_account(_=Depends(require_auth)):
    conn     = get_db()
    account  = conn.execute("SELECT * FROM sim_account WHERE id='main'").fetchone()
    positions= conn.execute("SELECT * FROM sim_positions").fetchall()
    conn.close()

    cash = account["cash"] if account else 440000
    pos_list = [dict(p) for p in positions]

    codes = [p["stock_code"] for p in pos_list]
    quotes_map = {}
    if codes:
        qs = fetch_quotes(codes)
        quotes_map = {q["code"]: q for q in qs}

    total_pos_val = 0.0
    for p in pos_list:
        cp = quotes_map.get(p["stock_code"], {}).get("current_price", p["cost_price"]) or p["cost_price"]
        p["current_price"] = cp
        p["market_value"]  = round(cp * p["qty"], 2)
        p["pnl"]           = round((cp - p["cost_price"]) * p["qty"], 2)
        p["pnl_pct"]       = round((cp - p["cost_price"]) / p["cost_price"] * 100, 2) if p["cost_price"] else 0
        total_pos_val     += p["market_value"]

        # Urgency flags
        pnl_pct = p["pnl_pct"]
        if pnl_pct <= -5:
            p["urgency"] = "critical"
        elif pnl_pct <= -3:
            p["urgency"] = "warning"
        elif pnl_pct >= 5:
            p["urgency"] = "profit"
        else:
            p["urgency"] = "normal"

    total_value  = cash + total_pos_val
    INIT_CASH    = 440000.0
    total_pnl    = total_value - INIT_CASH
    total_pnl_p  = round(total_pnl / INIT_CASH * 100, 2)

    return {
        "cash":          round(cash, 2),
        "positions":     pos_list,
        "total_pos_val": round(total_pos_val, 2),
        "total_value":   round(total_value, 2),
        "total_pnl":     round(total_pnl, 2),
        "total_pnl_pct": total_pnl_p,
        "peak_value":    account["peak_value"] if account else INIT_CASH,
    }


@app.get("/api/trades")
def api_trades(direction: str = "", limit: int = 100, _=Depends(require_auth)):
    conn = get_db()
    if direction:
        rows = conn.execute(
            "SELECT * FROM sim_trades WHERE direction=? ORDER BY traded_at DESC LIMIT ?",
            (direction, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM sim_trades ORDER BY traded_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return {"trades": [dict(r) for r in rows]}


@app.get("/api/signals")
def api_signals(limit: int = 50, _=Depends(require_auth)):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return {"signals": [dict(r) for r in rows]}


@app.get("/api/candidates")
def api_candidates(_=Depends(require_auth)):
    conn = get_db()
    row  = conn.execute(
        "SELECT scan_time, results FROM scan_results ORDER BY scan_time DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return {"candidates": [], "scan_time": None}
    return {
        "candidates": json.loads(row["results"]),
        "scan_time":  row["scan_time"],
    }


@app.get("/api/stats")
def api_stats(_=Depends(require_auth)):
    conn = get_db()
    trades = conn.execute(
        "SELECT * FROM sim_trades WHERE direction='sell'"
    ).fetchall()
    daily  = conn.execute(
        "SELECT * FROM daily_stats ORDER BY stat_date ASC"
    ).fetchall()
    conn.close()

    total_trades = len(trades)
    wins         = sum(1 for t in trades if t["is_win"])
    losses       = total_trades - wins
    win_rate     = round(wins / total_trades * 100, 1) if total_trades else 0.0
    total_pnl    = sum(t["pnl"] for t in trades)
    avg_pnl      = round(total_pnl / total_trades, 2) if total_trades else 0.0

    # Strategy breakdown
    strat_map: Dict[str, Dict] = {}
    for t in trades:
        s = t["strategy"] or "未知"
        if s not in strat_map:
            strat_map[s] = {"trades": 0, "wins": 0, "pnl": 0.0}
        strat_map[s]["trades"] += 1
        strat_map[s]["wins"]   += t["is_win"]
        strat_map[s]["pnl"]    += t["pnl"]

    for s, v in strat_map.items():
        v["win_rate"] = round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0.0

    return {
        "total_trades": total_trades,
        "wins":         wins,
        "losses":       losses,
        "win_rate":     win_rate,
        "total_pnl":    round(total_pnl, 2),
        "avg_pnl":      avg_pnl,
        "strategy_breakdown": strat_map,
        "daily_chart": [dict(d) for d in daily],
    }


@app.post("/api/scan-now")
def api_scan_now(background_tasks: BackgroundTasks, _=Depends(require_auth)):
    background_tasks.add_task(auto_scan)
    return {"message": "扫描任务已启动"}


@app.post("/api/check-exit")
def api_check_exit(background_tasks: BackgroundTasks, _=Depends(require_auth)):
    background_tasks.add_task(auto_check_exit)
    return {"message": "退出检查任务已启动"}


@app.post("/api/buy")
def api_buy(req: BuyReq, _=Depends(require_auth)):
    conn    = get_db()
    account = conn.execute("SELECT cash FROM sim_account WHERE id='main'").fetchone()
    cash    = account["cash"] if account else 0

    # Get price
    price = req.price
    if not price:
        qs = fetch_quotes([req.stock_code])
        if qs:
            price = qs[0]["current_price"]
    if not price or price <= 0:
        conn.close()
        raise HTTPException(400, "无法获取价格")

    qty = req.qty
    if not qty:
        budget = min(80000, cash * 0.2)
        qty    = int(budget / price / 100) * 100
    if qty <= 0:
        conn.close()
        raise HTTPException(400, "买入数量为0")

    amount = round(price * qty, 2)
    if amount > cash:
        conn.close()
        raise HTTPException(400, f"余额不足, 需{amount}, 余{cash:.0f}")

    # Check max positions
    pos_count = conn.execute("SELECT COUNT(*) as n FROM sim_positions").fetchone()["n"]
    if pos_count >= 6:
        conn.close()
        raise HTTPException(400, "持仓已达上限6只")

    cash -= amount
    pid   = str(uuid.uuid4())
    normal_sl = round(price * 0.97, 3)
    hard_sl   = round(price * 0.95, 3)
    tp_alert  = round(price * 1.05, 3)
    tp_strong = round(price * 1.08, 3)

    conn.execute("""
        INSERT OR REPLACE INTO sim_positions
        (id, stock_code, stock_name, qty, cost_price,
         normal_stop_loss, hard_stop_loss, take_profit_alert, take_profit_strong,
         strategy, buy_reason)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
    """, (pid, req.stock_code, req.stock_name, qty, price,
          normal_sl, hard_sl, tp_alert, tp_strong,
          req.strategy, req.reason))

    tid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO sim_trades
        (id, stock_code, stock_name, direction, price, qty, amount,
         strategy, buy_reason, cash_after)
        VALUES(?,?,?,?,?,?,?,?,?,?)
    """, (tid, req.stock_code, req.stock_name, "buy", price, qty, amount,
          req.strategy, req.reason, round(cash, 2)))

    conn.execute("UPDATE sim_account SET cash=?, updated_at=? WHERE id='main'",
                 (round(cash, 2), int(time.time())))
    conn.commit()
    conn.close()

    return {
        "message":  "买入成功",
        "code":     req.stock_code,
        "name":     req.stock_name,
        "price":    price,
        "qty":      qty,
        "amount":   amount,
        "cash_after": round(cash, 2),
    }


@app.post("/api/sell/{code}")
async def api_sell(code: str, request: Request, _=Depends(require_auth)):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    sell_reason = body.get("reason", "手动卖出")

    conn = get_db()
    pos  = conn.execute(
        "SELECT * FROM sim_positions WHERE stock_code=?", (code,)
    ).fetchone()
    if not pos:
        conn.close()
        raise HTTPException(404, "持仓不存在")

    price = body.get("price")
    if not price:
        qs = fetch_quotes([code])
        if qs:
            price = qs[0]["current_price"]
    if not price or price <= 0:
        conn.close()
        raise HTTPException(400, "无法获取价格")

    qty    = pos["qty"]
    cost   = pos["cost_price"]
    amount = round(price * qty, 2)
    pnl    = round((price - cost) * qty, 2)
    pnl_p  = round((price - cost) / cost * 100, 2) if cost else 0
    hold_d = max(0, int((time.time() - (pos["bought_at"] or time.time())) / 86400))

    account = conn.execute("SELECT cash FROM sim_account WHERE id='main'").fetchone()
    cash    = (account["cash"] if account else 0) + amount

    tid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO sim_trades
        (id, stock_code, stock_name, direction, price, qty, amount,
         strategy, sell_reason, pnl, pnl_pct, hold_days, cash_after, is_win)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (tid, code, pos["stock_name"], "sell", price, qty, amount,
          pos["strategy"], sell_reason, pnl, pnl_p, hold_d,
          round(cash, 2), 1 if pnl > 0 else 0))

    conn.execute("DELETE FROM sim_positions WHERE stock_code=?", (code,))
    conn.execute("UPDATE sim_account SET cash=?, updated_at=? WHERE id='main'",
                 (round(cash, 2), int(time.time())))
    conn.commit()
    conn.close()

    return {
        "message":  "卖出成功",
        "code":     code,
        "price":    price,
        "qty":      qty,
        "amount":   amount,
        "pnl":      pnl,
        "pnl_pct":  pnl_p,
        "cash_after": round(cash, 2),
    }


@app.post("/api/reset")
def api_reset(_=Depends(require_auth)):
    conn = get_db()
    conn.execute("UPDATE sim_account SET cash=440000, peak_value=440000, updated_at=? WHERE id='main'",
                 (int(time.time()),))
    conn.execute("DELETE FROM sim_positions")
    conn.commit()
    conn.close()
    return {"message": "账户已重置为440000"}


@app.get("/api/watchlist")
def api_watchlist_get(_=Depends(require_auth)):
    conn = get_db()
    rows = conn.execute("SELECT * FROM watchlist ORDER BY added_at DESC").fetchall()
    conn.close()
    return {"watchlist": [dict(r) for r in rows]}


@app.post("/api/watchlist")
def api_watchlist_add(req: WatchlistReq, _=Depends(require_auth)):
    conn = get_db()
    wid  = str(uuid.uuid4())
    try:
        conn.execute("""
            INSERT INTO watchlist(id, stock_code, stock_name, industry, concepts, note)
            VALUES(?,?,?,?,?,?)
        """, (wid, req.stock_code, req.stock_name, req.industry, req.concepts, req.note))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(409, "股票已在自选股中")
    conn.close()
    return {"message": "已添加", "id": wid}


@app.delete("/api/watchlist/{wid}")
def api_watchlist_del(wid: str, _=Depends(require_auth)):
    conn = get_db()
    conn.execute("DELETE FROM watchlist WHERE id=?", (wid,))
    conn.commit()
    conn.close()
    return {"message": "已删除"}


@app.get("/api/reviews")
def api_reviews_get(_=Depends(require_auth)):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM review_records ORDER BY review_date DESC LIMIT 30"
    ).fetchall()
    conn.close()
    return {"reviews": [dict(r) for r in rows]}


@app.post("/api/reviews")
def api_reviews_post(req: ReviewReq, _=Depends(require_auth)):
    conn = get_db()
    rid  = str(uuid.uuid4())
    conn.execute("""
        INSERT OR REPLACE INTO review_records
        (id, review_date, market_summary, hot_sectors, reflections, tomorrow_plan)
        VALUES(?,?,?,?,?,?)
    """, (rid, req.review_date, req.market_summary, req.hot_sectors,
          req.reflections, req.tomorrow_plan))
    conn.commit()
    conn.close()
    return {"message": "复盘记录已保存", "id": rid}


@app.post("/api/ai")
def api_ai(req: AIReq, _=Depends(require_auth)):
    result = call_deepseek(req.task, req.content)
    return {"result": result, "task": req.task, "provider": req.provider}


@app.get("/api/wecom-status")
def api_wecom_status(_=Depends(require_auth)):
    return {"configured": bool(WECOM_KEY), "key_prefix": WECOM_KEY[:8] + "..." if WECOM_KEY else ""}


@app.post("/api/wecom-test")
def api_wecom_test(_=Depends(require_auth)):
    ok = send_wecom("【旺财V8.0】企业微信推送测试消息, 系统运行正常!")
    return {"success": ok}


@app.post("/api/wecom-report")
def api_wecom_report(background_tasks: BackgroundTasks, _=Depends(require_auth)):
    save_daily_stats()

    conn    = get_db()
    account = conn.execute("SELECT * FROM sim_account WHERE id='main'").fetchone()
    daily   = conn.execute(
        "SELECT * FROM daily_stats ORDER BY stat_date DESC LIMIT 1"
    ).fetchone()
    conn.close()

    cash = account["cash"] if account else 440000
    if daily:
        msg = (
            f"【旺财V8.0日报】{daily['stat_date']}\n"
            f"总资产: {daily['total_value']:.0f}\n"
            f"日盈亏: {daily['daily_pnl']:+.0f} ({daily['daily_pnl_pct']:+.2f}%)\n"
            f"累计盈亏: {daily['total_pnl']:+.0f} ({daily['total_pnl_pct']:+.2f}%)\n"
            f"最大回撤: {daily['max_drawdown']:.2f}%\n"
            f"今日胜/负: {daily['win_trades']}/{daily['lose_trades']}"
        )
    else:
        msg = f"【旺财V8.0】日报: 余额{cash:.0f}"

    background_tasks.add_task(send_wecom, msg)
    return {"message": "日报发送中", "content": msg}


# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server_v8:app", host="0.0.0.0", port=8000, reload=False)
