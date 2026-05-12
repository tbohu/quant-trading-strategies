"""
旺财A股AI量化分析系统 V7.0
严格按照因子库文档实现四套评分模型：
- V4.2 中线机构增强评分模型
- V6.0 超短线量化评分模型
- 主力控盘评分模型
- 持仓风控评分模型
"""

import hmac, json, os, re, smtplib, sqlite3, statistics, time, threading, uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional, Any

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

# ── 环境变量 ──────────────────────────────────────────────────────────────────
APP_ACCESS_TOKEN  = os.getenv("APP_ACCESS_TOKEN", "").strip()
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions").strip()
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL   = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1/chat/completions").strip()
OPENAI_MODEL      = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
CORS_ORIGINS      = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:7777,http://127.0.0.1:7777").split(",") if x.strip()]
DB_PATH           = os.getenv("DB_PATH", "wangcai.db")
TRUST_PROXY       = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"
WECOM_WEBHOOK     = os.getenv("WECOM_WEBHOOK", "").strip()
EMAIL_HOST        = os.getenv("EMAIL_SMTP_HOST", "smtp.163.com").strip()
EMAIL_PORT        = int(os.getenv("EMAIL_SMTP_PORT", "465"))
EMAIL_SSL         = os.getenv("EMAIL_SMTP_SSL", "true").lower() == "true"
EMAIL_USER        = os.getenv("EMAIL_USER", "").strip()
EMAIL_PASS        = os.getenv("EMAIL_PASS", "").strip()
EMAIL_TO          = os.getenv("EMAIL_TO", "").strip()

if not APP_ACCESS_TOKEN:
    raise RuntimeError("APP_ACCESS_TOKEN 未设置")

# ── 数据库 ────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS sim_account (
        id TEXT PRIMARY KEY DEFAULT 'main',
        cash REAL NOT NULL DEFAULT 440000,
        initial_cash REAL NOT NULL DEFAULT 440000,
        peak_value REAL DEFAULT 440000,
        updated_at INTEGER DEFAULT (strftime('%s','now'))
    );
    CREATE TABLE IF NOT EXISTS sim_positions (
        id TEXT PRIMARY KEY,
        stock_code TEXT NOT NULL UNIQUE,
        stock_name TEXT NOT NULL,
        industry TEXT DEFAULT '',
        concepts TEXT DEFAULT '',
        qty INTEGER NOT NULL DEFAULT 0,
        cost_price REAL NOT NULL,
        normal_stop_loss REAL,
        hard_stop_loss REAL,
        take_profit_alert REAL,
        take_profit_strong REAL,
        score_v42 INTEGER DEFAULT 0,
        score_v60 INTEGER DEFAULT 0,
        score_zl INTEGER DEFAULT 0,
        strategy TEXT DEFAULT 'A',
        buy_reason TEXT DEFAULT '',
        max_profit_pct REAL DEFAULT 0,
        max_loss_pct REAL DEFAULT 0,
        bought_at INTEGER DEFAULT (strftime('%s','now')),
        updated_at INTEGER DEFAULT (strftime('%s','now'))
    );
    CREATE TABLE IF NOT EXISTS sim_trades (
        id TEXT PRIMARY KEY,
        stock_code TEXT NOT NULL,
        stock_name TEXT NOT NULL,
        direction TEXT NOT NULL,
        price REAL NOT NULL,
        qty INTEGER NOT NULL,
        amount REAL NOT NULL,
        strategy TEXT DEFAULT '',
        score_v60 INTEGER DEFAULT 0,
        score_v42 INTEGER DEFAULT 0,
        buy_reason TEXT DEFAULT '',
        sell_reason TEXT DEFAULT '',
        pnl REAL DEFAULT 0,
        pnl_pct REAL DEFAULT 0,
        hold_days INTEGER DEFAULT 0,
        max_profit_pct REAL DEFAULT 0,
        max_loss_pct REAL DEFAULT 0,
        is_win INTEGER DEFAULT 0,
        cash_after REAL,
        traded_at INTEGER DEFAULT (strftime('%s','now'))
    );
    CREATE TABLE IF NOT EXISTS signals (
        id TEXT PRIMARY KEY,
        stock_code TEXT NOT NULL,
        stock_name TEXT NOT NULL,
        industry TEXT DEFAULT '',
        signal_type TEXT NOT NULL,
        strategy TEXT DEFAULT '',
        score_v60 INTEGER DEFAULT 0,
        score_v42 INTEGER DEFAULT 0,
        score_zl INTEGER DEFAULT 0,
        price REAL,
        reason TEXT DEFAULT '',
        executed INTEGER DEFAULT 0,
        created_at INTEGER DEFAULT (strftime('%s','now'))
    );
    CREATE TABLE IF NOT EXISTS analysis_records (
        id TEXT PRIMARY KEY,
        stock_code TEXT NOT NULL,
        stock_name TEXT NOT NULL,
        score_v42 INTEGER DEFAULT 0,
        score_v60 INTEGER DEFAULT 0,
        score_zl INTEGER DEFAULT 0,
        grade_v42 TEXT DEFAULT '',
        grade_v60 TEXT DEFAULT '',
        grade_zl TEXT DEFAULT '',
        strategy TEXT DEFAULT '',
        buy_signal TEXT DEFAULT '',
        sell_signal TEXT DEFAULT '',
        stop_loss REAL,
        take_profit REAL,
        operation_advice TEXT DEFAULT '',
        risk_warning TEXT DEFAULT '',
        can_sim_buy INTEGER DEFAULT 0,
        need_sim_sell INTEGER DEFAULT 0,
        factors TEXT DEFAULT '{}',
        created_at INTEGER DEFAULT (strftime('%s','now'))
    );
    CREATE TABLE IF NOT EXISTS watchlist (
        id TEXT PRIMARY KEY,
        stock_code TEXT NOT NULL UNIQUE,
        stock_name TEXT NOT NULL,
        industry TEXT DEFAULT '',
        concepts TEXT DEFAULT '',
        note TEXT DEFAULT '',
        added_at INTEGER DEFAULT (strftime('%s','now'))
    );
    CREATE TABLE IF NOT EXISTS daily_stats (
        id TEXT PRIMARY KEY,
        stat_date TEXT NOT NULL UNIQUE,
        total_value REAL,
        daily_pnl REAL,
        daily_pnl_pct REAL,
        total_pnl REAL,
        total_pnl_pct REAL,
        max_drawdown REAL DEFAULT 0,
        win_trades INTEGER DEFAULT 0,
        lose_trades INTEGER DEFAULT 0,
        created_at INTEGER DEFAULT (strftime('%s','now'))
    );
    CREATE TABLE IF NOT EXISTS review_records (
        id TEXT PRIMARY KEY,
        review_date TEXT NOT NULL UNIQUE,
        market_summary TEXT DEFAULT '',
        hot_sectors TEXT DEFAULT '',
        reflections TEXT DEFAULT '',
        tomorrow_plan TEXT DEFAULT '',
        buy_count INTEGER DEFAULT 0,
        sell_count INTEGER DEFAULT 0,
        daily_pnl REAL DEFAULT 0,
        created_at INTEGER DEFAULT (strftime('%s','now'))
    );
    """)
    if not db.execute("SELECT COUNT(*) FROM sim_account").fetchone()[0]:
        db.execute("INSERT INTO sim_account (id,cash,initial_cash,peak_value) VALUES ('main',440000,440000,440000)")
    db.commit()
    db.close()

init_db()

# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(title="旺财AI量化分析系统 V7.0")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS,
    allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

_buckets: Dict[str, deque] = defaultdict(deque)

def _ip(request: Request) -> str:
    if TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for","")
        return fwd.split(",")[0].strip() if fwd else (request.client.host or "unknown")
    return request.client.host or "unknown"

def rate_limit(ep: str, limit: int):
    def _dep(request: Request):
        key = ep + ":" + _ip(request)
        now = time.time()
        b = _buckets[key]
        while b and now - b[0] > 60: b.popleft()
        if len(b) >= limit: raise HTTPException(429, f"限速{limit}次/分钟")
        b.append(now)
    return _dep

def require_auth(x_wangcai_token: Optional[str] = Header(default=None)):
    if not x_wangcai_token: raise HTTPException(401, "缺少访问令牌")
    if not hmac.compare_digest(str(x_wangcai_token), str(APP_ACCESS_TOKEN)):
        raise HTTPException(403, "访问令牌错误")

# ── 工具函数 ──────────────────────────────────────────────────────────────────
def _f(v, d=0.0):
    try: return float(v) if v is not None else d
    except: return d

def _i(v, d=0):
    try: return int(v) if v is not None else d
    except: return d

_EM_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

def _secid(code: str) -> str:
    digits = "".join(x for x in code if x.isdigit())
    if not digits: raise ValueError(f"无效代码:{code}")
    return ("1." if digits.startswith(("6","9")) else "0.") + digits

# ── 行情数据获取 ──────────────────────────────────────────────────────────────
def fetch_quotes(codes: List[str]) -> dict:
    if not codes: return {}
    try:
        ids = ",".join(_secid(c) for c in codes if c)
        r = requests.get("https://push2.eastmoney.com/api/qt/ulist.np/get", params={
            "fltt":"2","invt":"2",
            "fields":"f12,f14,f2,f3,f4,f5,f6,f7,f8,f10,f15,f16,f17,f18,f20,f21,f23,f9,f115,f116",
            "secids": ids,
        }, headers=_EM_HDR, timeout=10)
        r.raise_for_status()
        items = (r.json().get("data") or {}).get("diff") or []
        result = {}
        for x in items:
            code = x.get("f12","")
            price = _f(x.get("f2"))
            prev_close = _f(x.get("f18"))
            result[code] = {
                "code": code, "name": x.get("f14",""),
                "current_price": price,
                "change_pct": _f(x.get("f3")),
                "chg_amount": _f(x.get("f4")),
                "volume": _f(x.get("f5")),
                "turnover_amount": _f(x.get("f6")),
                "amplitude": _f(x.get("f7")),
                "turnover_rate": _f(x.get("f8")),
                "volume_ratio": _f(x.get("f10")),
                "high_price": _f(x.get("f15")),
                "low_price": _f(x.get("f16")),
                "open_price": _f(x.get("f17")),
                "prev_close_price": prev_close,
                "total_market_cap": _f(x.get("f20")),
                "float_market_cap": _f(x.get("f21")),
                "pe_dynamic": _f(x.get("f9")),
                "pb": _f(x.get("f23")),
            }
        return result
    except Exception as e:
        print(f"[fetch_quotes] {e}")
        return {}

def fetch_kline(code: str, period: str = "101", limit: int = 120) -> List[dict]:
    try:
        r = requests.get("https://push2his.eastmoney.com/api/qt/stock/kline/get", params={
            "secid": _secid(code), "ut":"fa5fd1943c7b386f172d6893dbfba10b",
            "fields1":"f1,f2,f3,f4,f5,f6",
            "fields2":"f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt":period,"fqt":"1","lmt":min(limit,500),"end":"20500101",
        }, headers=_EM_HDR, timeout=10)
        r.raise_for_status()
        klines = ((r.json().get("data") or {}).get("klines") or [])
        result = []
        for k in klines:
            p = k.split(",")
            if len(p) >= 6:
                result.append({
                    "date":p[0],"open":_f(p[1]),"close":_f(p[2]),
                    "high":_f(p[3]),"low":_f(p[4]),"volume":_f(p[5])
                })
        return result
    except Exception as e:
        print(f"[fetch_kline] {e}")
        return []

def fetch_moneyflow_detail(code: str, days: int = 10) -> dict:
    """获取完整主力资金数据"""
    try:
        r = requests.get("https://push2.eastmoney.com/api/qt/stock/fflow/kline/get", params={
            "lmt": min(days, 30), "klt":1, "secid":_secid(code),
            "fields1":"f1,f2,f3,f7","fields2":"f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
            "ut":"b2884a393a59ad64002292a3e90d46a5",
        }, headers=_EM_HDR, timeout=8)
        r.raise_for_status()
        klines = ((r.json().get("data") or {}).get("klines") or [])
        if not klines: return {}
        daily = []
        for k in klines:
            p = k.split(",")
            if len(p) >= 7:
                daily.append({
                    "date": p[0],
                    "main_net": _f(p[1]),
                    "super_large_net": _f(p[2]),
                    "large_net": _f(p[3]),
                    "medium_net": _f(p[4]),
                    "small_net": _f(p[5]),
                    "main_pct": _f(p[6]) if len(p) > 6 else 0,
                })
        if not daily: return {}
        latest = daily[-1]
        # 近3日连续性
        last3 = daily[-3:] if len(daily) >= 3 else daily
        main_pos_days = sum(1 for d in last3 if d["main_net"] > 0)
        main_neg_days = sum(1 for d in last3 if d["main_net"] < 0)
        main_3d = sum(d["main_net"] for d in last3)
        main_5d = sum(d["main_net"] for d in daily[-5:]) if len(daily) >= 5 else main_3d
        main_10d = sum(d["main_net"] for d in daily[-10:]) if len(daily) >= 10 else main_5d
        return {
            "main_net_inflow": latest["main_net"],
            "super_large_net_inflow": latest["super_large_net"],
            "large_net_inflow": latest["large_net"],
            "medium_net_inflow": latest["medium_net"],
            "small_net_inflow": latest["small_net"],
            "main_net_inflow_pct": latest["main_pct"],
            "main_net_inflow_3d": main_3d,
            "main_net_inflow_5d": main_5d,
            "main_net_inflow_10d": main_10d,
            "main_continuous_inflow": main_pos_days >= 2,
            "main_continuous_outflow": main_neg_days >= 2,
            "daily": daily,
        }
    except Exception as e:
        print(f"[fetch_moneyflow] {e}")
        return {}

def fetch_sector_hot() -> List[dict]:
    try:
        r = requests.get("https://push2.eastmoney.com/api/qt/clist/get", params={
            "pn":1,"pz":30,"po":1,"np":1,
            "ut":"fa5fd1943c7b386f172d6893dbfba10b",
            "fltt":2,"invt":2,"fid":"f3","fs":"m:90+t:2",
            "fields":"f12,f14,f3,f8,f6,f20,f104,f105,f62",
        }, headers=_EM_HDR, timeout=8)
        r.raise_for_status()
        items = ((r.json().get("data") or {}).get("diff") or [])
        return [{
            "code":x.get("f12",""),"name":x.get("f14",""),
            "chg_pct":_f(x.get("f3")),
            "turnover_rate":_f(x.get("f8")),
            "amount":_f(x.get("f6")),
            "up_count":_i(x.get("f104")),
            "down_count":_i(x.get("f105")),
            "main_inflow":_f(x.get("f62")),
        } for x in items]
    except: return []

def fetch_market() -> dict:
    codes = ["sh000001","sz399006","sh000688","sz399001"]
    try:
        ids = ",".join(_secid(c) for c in codes)
        r = requests.get("https://push2.eastmoney.com/api/qt/ulist.np/get", params={
            "fltt":"2","invt":"2","fields":"f12,f14,f2,f3,f4","secids":ids,
        }, headers=_EM_HDR, timeout=8)
        r.raise_for_status()
        items = (r.json().get("data") or {}).get("diff") or []
        result = {}
        for x in items:
            code = x.get("f12","")
            result[code] = {"code":code,"name":x.get("f14",""),
                           "price":_f(x.get("f2")),"chg_pct":_f(x.get("f3"))}
        return result
    except: return {}

def search_stock(q: str) -> List[dict]:
    try:
        r = requests.get("https://searchapi.eastmoney.com/api/suggest/get", params={
            "input":q.strip(),"type":"14",
            "token":"D43BF722C8E33BDC906FB84D85E326E8","count":"8",
        }, headers={"User-Agent":"Mozilla/5.0","Referer":"https://www.eastmoney.com/"}, timeout=5)
        r.raise_for_status()
        items = (r.json().get("QuotationCodeTable") or {}).get("Data") or []
        results = [{"code":x.get("Code",""),"name":x.get("Name",""),"industry":x.get("MktNum","")}
                   for x in items if x.get("SecurityType") in ("1","2")]
        if results:
            try:
                qs = fetch_quotes([r["code"] for r in results])
                for item in results:
                    q2 = qs.get(item["code"])
                    if q2:
                        item["price"] = q2.get("current_price")
                        item["chg_pct"] = q2.get("change_pct")
            except: pass
        return results
    except: return []

# ── 技术指标计算 ──────────────────────────────────────────────────────────────
def calc_ema(data, n):
    result, k = [], 2.0/(n+1)
    for i,v in enumerate(data):
        result.append(v if i==0 else v*k+result[-1]*(1-k))
    return result

def calc_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow+signal: return [],[],[]
    ef=calc_ema(closes,fast); es=calc_ema(closes,slow)
    dif=[round(f-s,4) for f,s in zip(ef,es)]
    dea=[round(x,4) for x in calc_ema(dif,signal)]
    macd=[round((d-e)*2,4) for d,e in zip(dif,dea)]
    return dif,dea,macd

def calc_kdj(highs,lows,closes,n=9):
    K,D,J=[],[],[]
    for i in range(len(closes)):
        if i<n-1: K.append(50.0);D.append(50.0);J.append(50.0);continue
        h=max(highs[i-n+1:i+1]); l=min(lows[i-n+1:i+1])
        rsv=(closes[i]-l)/(h-l)*100 if h!=l else 50.0
        k=(2/3)*(K[-1] if K else 50)+(1/3)*rsv
        d=(2/3)*(D[-1] if D else 50)+(1/3)*k
        K.append(round(k,2));D.append(round(d,2));J.append(round(3*k-2*d,2))
    return K,D,J

def calc_boll(closes,n=20,k=2):
    mid,upper,lower=[],[],[]
    for i in range(len(closes)):
        if i<n-1: mid.append(None);upper.append(None);lower.append(None);continue
        w=closes[i-n+1:i+1]; ma=sum(w)/n; std=statistics.stdev(w)
        mid.append(round(ma,3));upper.append(round(ma+k*std,3));lower.append(round(ma-k*std,3))
    return mid,upper,lower

def calc_ma(closes,n):
    result=[None]*(n-1)
    for i in range(n-1,len(closes)):
        result.append(round(sum(closes[i-n+1:i+1])/n,3))
    return result

def build_k_factors(klines: list, current_price: float) -> dict:
    """从K线数据构建技术因子"""
    if not klines or len(klines) < 5:
        return {}
    closes = [k["close"] for k in klines]
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    vols = [k["volume"] for k in klines]
    n = len(closes)
    p = current_price or closes[-1]

    ma5 = sum(closes[-5:])/5 if n>=5 else None
    ma10 = sum(closes[-10:])/10 if n>=10 else None
    ma20 = sum(closes[-20:])/20 if n>=20 else None
    ma60 = sum(closes[-60:])/60 if n>=60 else None

    avg_vol_5 = sum(vols[-5:])/5 if n>=5 else None
    avg_vol_20 = sum(vols[-20:])/20 if n>=20 else None
    vol_ratio_5_20 = avg_vol_5/avg_vol_20 if avg_vol_5 and avg_vol_20 and avg_vol_20>0 else None

    rise_5d = (closes[-1]-closes[-6])/closes[-6]*100 if n>=6 else None
    rise_10d = (closes[-1]-closes[-11])/closes[-11]*100 if n>=11 else None
    rise_20d = (closes[-1]-closes[-21])/closes[-21]*100 if n>=21 else None
    rise_60d = (closes[-1]-closes[-61])/closes[-61]*100 if n>=61 else None

    max_20d = max(closes[-20:]) if n>=20 else None
    min_20d = min(closes[-20:]) if n>=20 else None

    # 高位放量滞涨判断
    high_vol_stag = False
    if rise_20d and rise_20d > 20:
        if vol_ratio_5_20 and vol_ratio_5_20 > 2:
            if klines[-1] and _f(klines[-1].get("close",0)-klines[-1].get("open",0))/max(_f(klines[-1].get("open",1)),1)*100 < 2:
                high_vol_stag = True

    return {
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
        "above_ma5": p > ma5 if ma5 else False,
        "above_ma10": p > ma10 if ma10 else False,
        "above_ma20": p > ma20 if ma20 else False,
        "above_ma60": p > ma60 if ma60 else False,
        "distance_ma5_pct": (p-ma5)/ma5*100 if ma5 else None,
        "distance_ma20_pct": (p-ma20)/ma20*100 if ma20 else None,
        "distance_ma60_pct": (p-ma60)/ma60*100 if ma60 else None,
        "rise_5d_pct": rise_5d, "rise_10d_pct": rise_10d,
        "rise_20d_pct": rise_20d, "rise_60d_pct": rise_60d,
        "avg_volume_5d": avg_vol_5, "avg_volume_20d": avg_vol_20,
        "volume_ratio_5_20": vol_ratio_5_20,
        "is_volume_expanding": vol_ratio_5_20 >= 1.5 if vol_ratio_5_20 else False,
        "is_volume_shrinking": vol_ratio_5_20 <= 0.7 if vol_ratio_5_20 else False,
        "breakout_platform": p > max_20d if max_20d else False,
        "breakdown_platform": p < min_20d if min_20d else False,
        "high_volume_stagnation": high_vol_stag,
        "bullish_ma_alignment": (ma5 and ma10 and ma20 and ma60 and ma5>ma10>ma20>ma60),
        "trend_weakening": (p < ma20 if ma20 else False) or (ma5 < ma10 if (ma5 and ma10) else False),
    }

# ════════════════════════════════════════════════════════════════════════════════
# 评分模型
# ════════════════════════════════════════════════════════════════════════════════

# ── 硬性排除规则 ──────────────────────────────────────────────────────────────
def check_hard_exclude(factors: dict) -> tuple:
    """返回 (是否排除, 原因列表)"""
    reasons = []
    f = factors
    if f.get("is_st"): reasons.append("ST股票")
    if f.get("is_loss"): reasons.append("亏损股")
    if f.get("has_major_risk"): reasons.append("重大风险公告")
    if f.get("has_delisting_risk"): reasons.append("退市风险")
    if f.get("has_investigation"): reasons.append("立案调查")
    if f.get("has_penalty"): reasons.append("监管处罚")
    # 高估值无业绩支撑
    pe = _f(f.get("pe_dynamic"))
    profit_yoy = _f(f.get("net_profit_yoy_pct"))
    if pe > 60 and profit_yoy < 20:
        reasons.append(f"高估值无业绩支撑PE={pe:.0f}")
    # 主力连续流出+小单流入
    if f.get("main_continuous_outflow") and _f(f.get("small_net_inflow")) > 0:
        reasons.append("主力连续流出散户接盘")
    # 高位放量滞涨
    if f.get("high_volume_stagnation"):
        reasons.append("高位放量滞涨")
    return bool(reasons), reasons

# ── V6.0 超短线评分模型 ───────────────────────────────────────────────────────
def score_v60(factors: dict, sectors: list) -> dict:
    """
    总分100分
    板块热度20 + 个股位置15 + 成交量量比15 + 主力资金20 + 技术突破15 + 风险过滤10 + 催化剂5
    """
    score = 0; details = {}; reasons = []; warnings = []

    # 硬性排除
    excluded, exc_reasons = check_hard_exclude(factors)
    if excluded:
        return {"score": 0, "hard_exclude": True, "exclude_reasons": exc_reasons,
                "details": {}, "reasons": exc_reasons, "warnings": exc_reasons,
                "strategy": "N", "grade": "淘汰"}

    change_pct = _f(factors.get("change_pct"))
    if change_pct >= 9.5:
        return {"score": 0, "hard_exclude": True, "exclude_reasons": ["涨停板不追"],
                "details": {}, "reasons": ["涨停"], "warnings": ["涨停"],
                "strategy": "N", "grade": "淘汰"}
    if _f(factors.get("turnover_amount")) < 3e7:
        return {"score": 0, "hard_exclude": True, "exclude_reasons": ["流动性不足"],
                "details": {}, "reasons": ["流动性差"], "warnings": [],
                "strategy": "N", "grade": "淘汰"}

    # 1. 板块热度 20分
    s1 = 0
    hot_sectors = sectors[:10] if sectors else []
    if hot_sectors:
        avg_chg = sum(s.get("chg_pct",0) for s in hot_sectors[:5])/5
        top_amount = hot_sectors[0].get("amount",0) if hot_sectors else 0
        top_rank = 1
        # 判断个股所属板块排名
        industry = factors.get("industry","")
        for i, s in enumerate(sectors):
            if industry and industry in s.get("name",""):
                top_rank = i+1
                top_amount = s.get("amount",0)
                break
        if top_rank <= 5 and top_amount > 5e9:
            s1 = 18; reasons.append("板块排名前5+主力大量流入")
        elif top_rank <= 10 and top_amount > 2e9:
            s1 = 14; reasons.append("板块排名前10")
        elif avg_chg > 2:
            s1 = 10; reasons.append("板块整体强势")
        elif avg_chg > 0.5:
            s1 = 7; reasons.append("板块偏强")
        else:
            s1 = 3; warnings.append("板块一般")
    else:
        s1 = 8
    details["板块热度"] = {"score": min(s1,20), "max": 20}
    score += min(s1, 20)

    # 2. 个股位置 15分
    s2 = 0
    rise_5d = _f(factors.get("rise_5d_pct"))
    rise_20d = _f(factors.get("rise_20d_pct"))
    above_ma5 = factors.get("above_ma5", False)
    above_ma20 = factors.get("above_ma20", False)
    if 3 <= change_pct <= 7:
        s2 += 10; reasons.append(f"涨幅适中{change_pct:.1f}%")
    elif 0 < change_pct < 3:
        s2 += 6; reasons.append("小幅上涨")
    elif -2 <= change_pct <= 0:
        s2 += 4; reasons.append("横盘整理")
    elif change_pct > 7:
        s2 += 2; warnings.append(f"涨幅偏大{change_pct:.1f}%")
    # 位置判断
    if rise_20d > 30: s2 -= 3; warnings.append("近20日涨幅偏大")
    if above_ma5 and above_ma20: s2 += 5; reasons.append("站上MA5/MA20")
    elif above_ma5: s2 += 2
    details["个股位置"] = {"score": max(0,min(s2,15)), "max": 15}
    score += max(0, min(s2, 15))

    # 3. 成交量/量比 15分
    s3 = 0
    vol_ratio = _f(factors.get("volume_ratio"))
    vol_ratio_5_20 = _f(factors.get("volume_ratio_5_20"), 1)
    is_vol_expand = factors.get("is_volume_expanding", False)
    turnover = _f(factors.get("turnover_rate"))
    if vol_ratio >= 1.5 and vol_ratio <= 3:
        s3 += 8; reasons.append(f"量比适中{vol_ratio:.1f}")
    elif vol_ratio >= 3 and vol_ratio <= 8:
        s3 += 6; reasons.append(f"量比放大{vol_ratio:.1f}")
    elif vol_ratio > 8:
        s3 += 3; warnings.append("量比极大防冲高回落")
    elif vol_ratio < 0.8:
        s3 += 1; warnings.append("缩量")
    else:
        s3 += 4
    if is_vol_expand: s3 += 5; reasons.append("放量1.5倍")
    if 1 <= turnover <= 8: s3 += 2; reasons.append(f"换手率{turnover:.1f}%")
    details["成交量量比"] = {"score": max(0,min(s3,15)), "max": 15}
    score += max(0, min(s3, 15))

    # 4. 主力资金 20分
    s4 = 0
    main_net = _f(factors.get("main_net_inflow"))
    super_large = _f(factors.get("super_large_net_inflow"))
    large_net = _f(factors.get("large_net_inflow"))
    small_net = _f(factors.get("small_net_inflow"))
    cont_inflow = factors.get("main_continuous_inflow", False)
    retail_taking = main_net < 0 and small_net > 0
    institution_acc = main_net > 0 and super_large > 0 and small_net < 0
    strong_flow = main_net > 0 and large_net > 0 and super_large > 0
    if strong_flow:
        s4 = 17; reasons.append(f"主力大单超大单均流入{main_net/1e4:.0f}万")
    elif main_net > 1e7 and large_net > 0:
        s4 = 14; reasons.append(f"主力+大单流入{main_net/1e4:.0f}万")
    elif main_net > 0:
        s4 = 10; reasons.append(f"主力小幅流入{main_net/1e4:.0f}万")
    elif main_net > -1e7:
        s4 = 5
    else:
        s4 = 2; warnings.append("主力流出")
    if cont_inflow: s4 = min(s4+2, 20); reasons.append("连续流入")
    if institution_acc: reasons.append("疑似机构吸筹")
    if retail_taking: s4 = max(0, s4-5); warnings.append("散户接盘结构")
    details["主力资金"] = {"score": max(0,min(s4,20)), "max": 20}
    score += max(0, min(s4, 20))

    # 5. 技术突破 15分
    s5 = 0
    breakout = factors.get("breakout_platform", False)
    bullish = factors.get("bullish_ma_alignment", False)
    above_ma10 = factors.get("above_ma10", False)
    above_ma60 = factors.get("above_ma60", False)
    dif = factors.get("macd_dif"); dea = factors.get("macd_dea")
    if breakout and is_vol_expand:
        s5 = 13; reasons.append("放量突破20日平台")
    elif breakout:
        s5 = 9; reasons.append("突破20日平台")
    elif above_ma5 and above_ma10 and above_ma20:
        s5 = 8; reasons.append("站上三条均线")
    elif above_ma5:
        s5 = 5
    if bullish: s5 = min(s5+2, 15); reasons.append("均线多头排列")
    if dif is not None and dea is not None and dif > dea:
        s5 = min(s5+2, 15); reasons.append("MACD金叉")
    if factors.get("trend_weakening"):
        s5 = max(0, s5-3); warnings.append("趋势转弱")
    if factors.get("breakdown_platform"):
        s5 = max(0, s5-5); warnings.append("跌破平台")
    details["技术突破"] = {"score": max(0,min(s5,15)), "max": 15}
    score += max(0, min(s5, 15))

    # 6. 风险过滤 10分
    s6 = 10
    risk_count = _i(factors.get("risk_announcement_count"))
    if factors.get("has_reduction_announcement"): s6 -= 3; warnings.append("减持公告")
    if factors.get("has_unlock_risk"): s6 -= 2; warnings.append("解禁风险")
    if factors.get("has_regulatory_inquiry"): s6 -= 3; warnings.append("监管问询")
    if factors.get("has_pledge_risk"): s6 -= 2; warnings.append("质押风险")
    if factors.get("high_volume_stagnation"): s6 -= 3; warnings.append("高位滞涨")
    s6 = max(0, s6)
    details["风险过滤"] = {"score": min(s6,10), "max": 10}
    score += min(s6, 10)

    # 7. 催化剂 5分
    s7 = 1
    cat_count = _i(factors.get("catalyst_announcement_count"))
    if factors.get("has_real_catalyst"): s7 = 5; reasons.append("有实质催化剂")
    elif factors.get("has_profit_warning_positive"): s7 = 4; reasons.append("业绩预增")
    elif factors.get("has_major_contract") or factors.get("has_order_catalyst"): s7 = 4; reasons.append("重大订单")
    elif factors.get("has_buyback") or factors.get("has_shareholder_increase"): s7 = 3; reasons.append("回购增持")
    elif cat_count > 0: s7 = 2
    details["催化剂"] = {"score": min(s7,5), "max": 5}
    score += min(s7, 5)

    score = max(0, min(100, score))

    # 策略判断
    strategy = "A"
    if main_net > 5e7: strategy = "C"
    elif breakout and is_vol_expand: strategy = "A"
    elif -2 <= change_pct <= 1 and above_ma5: strategy = "B"

    # 评级
    if score >= 90: grade = "强信号"
    elif score >= 85: grade = "可买入"
    elif score >= 80: grade = "候选观察"
    elif score >= 70: grade = "弱信号"
    else: grade = "淘汰"

    return {
        "score": score, "hard_exclude": False, "exclude_reasons": [],
        "details": details, "reasons": reasons[:6], "warnings": warnings[:4],
        "strategy": strategy, "grade": grade,
    }

# ── V4.2 中线机构增强评分模型 ─────────────────────────────────────────────────
def score_v42(factors: dict) -> dict:
    """总分100分，九个维度"""
    score = 0; details = {}; reasons = []; warnings = []

    # 硬性排除
    excluded, exc_reasons = check_hard_exclude(factors)
    if excluded:
        return {"score": 0, "hard_exclude": True, "exclude_reasons": exc_reasons,
                "details": {}, "reasons": exc_reasons, "grade": "淘汰"}

    # 1. 价格与位置 10分
    s1 = 5
    above_ma20 = factors.get("above_ma20", False)
    above_ma60 = factors.get("above_ma60", False)
    rise_60d = _f(factors.get("rise_60d_pct"))
    rise_20d = _f(factors.get("rise_20d_pct"))
    if above_ma20 and above_ma60:
        s1 = 8; reasons.append("站上20/60日线")
    elif above_ma20:
        s1 = 6
    elif not above_ma20 and not above_ma60:
        s1 = 2; warnings.append("价格在均线下方")
    if rise_60d > 50: s1 = max(2, s1-3); warnings.append("60日涨幅偏大")
    elif rise_60d < 0: s1 = min(s1+2, 10); reasons.append("低位企稳")
    if factors.get("bullish_ma_alignment"): s1 = min(s1+2, 10)
    details["价格位置"] = {"score": max(0,min(s1,10)), "max": 10}
    score += max(0, min(s1, 10))

    # 2. 行业逻辑 15分
    s2 = 8  # 基础分，需要用户/AI补充
    if factors.get("is_hot_industry"): s2 = 13; reasons.append("强势主线行业")
    elif factors.get("is_hot_concept"): s2 = 11; reasons.append("强势概念板块")
    industry_chg = _f(factors.get("industry_change_pct"))
    if industry_chg > 3: s2 = min(s2+2, 15); reasons.append(f"行业涨幅{industry_chg:.1f}%")
    details["行业逻辑"] = {"score": max(0,min(s2,15)), "max": 15}
    score += max(0, min(s2, 15))

    # 3. 基本面质量 15分
    s3 = 0
    roe = _f(factors.get("roe_pct"))
    gross_margin = _f(factors.get("gross_margin_pct"))
    debt_ratio = _f(factors.get("debt_ratio_pct"))
    cashflow = _f(factors.get("operating_cashflow"))
    if roe >= 20: s3 += 6; reasons.append(f"ROE优质{roe:.1f}%")
    elif roe >= 12: s3 += 4; reasons.append(f"ROE良好{roe:.1f}%")
    elif roe >= 8: s3 += 2
    else: warnings.append(f"ROE偏低{roe:.1f}%")
    if gross_margin >= 40: s3 += 4; reasons.append(f"高毛利{gross_margin:.1f}%")
    elif gross_margin >= 25: s3 += 2
    if debt_ratio < 40: s3 += 3; reasons.append("低负债")
    elif debt_ratio > 60: s3 -= 2; warnings.append("高负债")
    if cashflow > 0: s3 += 2; reasons.append("现金流健康")
    else: warnings.append("现金流为负")
    details["基本面质量"] = {"score": max(0,min(s3,15)), "max": 15}
    score += max(0, min(s3, 15))

    # 4. 业绩变化 15分
    s4 = 0
    rev_yoy = _f(factors.get("revenue_yoy_pct"))
    profit_yoy = _f(factors.get("net_profit_yoy_pct"))
    deduct_yoy = _f(factors.get("deducted_net_profit_yoy_pct"))
    if rev_yoy >= 50 and profit_yoy >= 50:
        s4 = 14; reasons.append(f"业绩爆发营收+{rev_yoy:.0f}%净利+{profit_yoy:.0f}%")
    elif rev_yoy >= 30 and profit_yoy >= 30:
        s4 = 11; reasons.append(f"高增长营收+{rev_yoy:.0f}%净利+{profit_yoy:.0f}%")
    elif rev_yoy >= 15 and profit_yoy >= 15:
        s4 = 8; reasons.append("稳定增长")
    elif rev_yoy > 0 and profit_yoy > 0:
        s4 = 5
    elif profit_yoy < 0:
        s4 = 1; warnings.append("净利润下滑")
    if deduct_yoy > 0: s4 = min(s4+1, 15); reasons.append("扣非增长")
    details["业绩变化"] = {"score": max(0,min(s4,15)), "max": 15}
    score += max(0, min(s4, 15))

    # 5. 催化剂 10分
    s5 = 1
    if factors.get("has_real_catalyst"): s5 = 9
    elif factors.get("has_profit_warning_positive"): s5 = 8; reasons.append("业绩预增")
    elif factors.get("has_major_contract"): s5 = 7; reasons.append("重大合同")
    elif factors.get("has_buyback"): s5 = 6; reasons.append("回购")
    elif factors.get("has_shareholder_increase"): s5 = 6; reasons.append("增持")
    elif factors.get("has_dividend"): s5 = 5; reasons.append("分红")
    elif factors.get("catalyst_announcement_count", 0) > 0: s5 = 4
    details["催化剂"] = {"score": min(s5,10), "max": 10}
    score += min(s5, 10)

    # 6. 资金面 10分
    s6 = 0
    main_net = _f(factors.get("main_net_inflow"))
    main_5d = _f(factors.get("main_net_inflow_5d"))
    cont_inflow = factors.get("main_continuous_inflow", False)
    if main_net > 5e7 or main_5d > 1e8:
        s6 = 9; reasons.append("主力持续大幅流入")
    elif main_net > 1e7 or (cont_inflow and main_5d > 0):
        s6 = 7; reasons.append("主力持续流入")
    elif main_net > 0:
        s6 = 5
    elif main_net < -3e7:
        s6 = 1; warnings.append("主力大幅流出")
    else:
        s6 = 3
    if factors.get("strong_money_flow"): s6 = min(s6+1, 10)
    details["资金面"] = {"score": min(s6,10), "max": 10}
    score += min(s6, 10)

    # 7. 技术面 10分
    s7 = 0
    breakout = factors.get("breakout_platform", False)
    bullish = factors.get("bullish_ma_alignment", False)
    vol_expand = factors.get("is_volume_expanding", False)
    if breakout and vol_expand and bullish:
        s7 = 9; reasons.append("放量突破+多头排列")
    elif breakout and vol_expand:
        s7 = 7; reasons.append("放量突破")
    elif bullish:
        s7 = 6; reasons.append("多头排列")
    elif factors.get("above_ma20"):
        s7 = 5
    elif factors.get("trend_weakening"):
        s7 = 2; warnings.append("技术趋势转弱")
    if factors.get("high_volume_stagnation"):
        s7 = max(0, s7-3); warnings.append("高位滞涨")
    details["技术面"] = {"score": max(0,min(s7,10)), "max": 10}
    score += max(0, min(s7, 10))

    # 8. 估值 10分
    s8 = 5
    pe = _f(factors.get("pe_dynamic"))
    pb = _f(factors.get("pb"))
    peg = pe / profit_yoy if pe > 0 and profit_yoy > 0 else None
    if pe > 0:
        if pe <= 25 and profit_yoy > 20:
            s8 = 9; reasons.append(f"估值合理PE={pe:.0f}")
        elif pe <= 40 and profit_yoy > 15:
            s8 = 7
        elif pe > 60 and profit_yoy < 30:
            s8 = 2; warnings.append(f"高估值PE={pe:.0f}")
        elif pe > 100:
            s8 = 1; warnings.append("极高估值")
        else:
            s8 = 5
    if peg and peg < 1: s8 = min(s8+2, 10); reasons.append(f"PEG={peg:.2f}低估")
    details["估值"] = {"score": max(0,min(s8,10)), "max": 10}
    score += max(0, min(s8, 10))

    # 9. 风险 5分
    s9 = 5
    if factors.get("has_reduction_announcement"): s9 -= 2; warnings.append("减持")
    if factors.get("has_unlock_risk"): s9 -= 1; warnings.append("解禁")
    if factors.get("has_regulatory_inquiry"): s9 -= 2; warnings.append("监管问询")
    if factors.get("has_goodwill_impairment"): s9 -= 2; warnings.append("商誉减值")
    if factors.get("high_debt"): s9 -= 1; warnings.append("高负债")
    s9 = max(0, s9)
    details["风险"] = {"score": s9, "max": 5}
    score += s9

    score = max(0, min(100, score))

    if score >= 90: grade = "核心机会"
    elif score >= 85: grade = "优质机会"
    elif score >= 80: grade = "跟踪机会"
    elif score >= 75: grade = "一般机会"
    elif score >= 70: grade = "偏弱"
    else: grade = "淘汰"

    return {
        "score": score, "hard_exclude": False, "exclude_reasons": [],
        "details": details, "reasons": reasons[:6], "warnings": warnings[:4], "grade": grade,
    }

# ── 主力控盘评分模型 ──────────────────────────────────────────────────────────
def score_zhuangli(factors: dict) -> dict:
    """总分100分，判断主力行为和控盘程度"""
    score = 0; details = {}; reasons = []; state = "不明"

    main_net = _f(factors.get("main_net_inflow"))
    main_3d = _f(factors.get("main_net_inflow_3d"))
    main_5d = _f(factors.get("main_net_inflow_5d"))
    main_10d = _f(factors.get("main_net_inflow_10d"))
    super_large = _f(factors.get("super_large_net_inflow"))
    large_net = _f(factors.get("large_net_inflow"))
    small_net = _f(factors.get("small_net_inflow"))
    cont_inflow = factors.get("main_continuous_inflow", False)
    cont_outflow = factors.get("main_continuous_outflow", False)
    change_pct = _f(factors.get("change_pct"))

    # 1. 近5日主力资金趋势 20分
    s1 = 0
    if main_5d > 5e7 and cont_inflow: s1 = 19; reasons.append("近5日主力持续大幅流入")
    elif main_5d > 1e7 and cont_inflow: s1 = 15; reasons.append("近5日主力持续流入")
    elif main_5d > 0: s1 = 10
    elif main_5d < -3e7: s1 = 2
    else: s1 = 5
    details["近5日主力趋势"] = {"score": min(s1,20), "max": 20}
    score += min(s1, 20)

    # 2. 近10日主力资金趋势 15分
    s2 = 0
    if main_10d > 8e7: s2 = 14; reasons.append("近10日持续大量流入")
    elif main_10d > 0: s2 = 9
    elif main_10d < -5e7: s2 = 1
    else: s2 = 5
    details["近10日主力趋势"] = {"score": min(s2,15), "max": 15}
    score += min(s2, 15)

    # 3. 大单/超大单占比 15分
    s3 = 0
    if super_large > 0 and large_net > 0: s3 = 14; reasons.append("超大单+大单均流入")
    elif super_large > 0 or large_net > 1e7: s3 = 10
    elif large_net > 0: s3 = 7
    else: s3 = 2
    details["大单超大单"] = {"score": min(s3,15), "max": 15}
    score += min(s3, 15)

    # 4. 小单方向 10分
    s4 = 5
    if small_net < 0 and main_net > 0:
        s4 = 10; reasons.append("机构吸筹：小单流出+主力流入")
    elif small_net > 0 and main_net < 0:
        s4 = 0; reasons.append("散户接盘：小单流入+主力流出")
    details["小单方向"] = {"score": s4, "max": 10}
    score += s4

    # 5. 价格与资金配合 10分
    s5 = 0
    if change_pct > 0 and main_net > 0: s5 = 10; reasons.append("量价齐升")
    elif change_pct > -1 and main_net > 0: s5 = 8
    elif change_pct > 0 and main_net < 0: s5 = 3; reasons.append("价涨量退疑似出货")
    else: s5 = 2
    details["价格资金配合"] = {"score": s5, "max": 10}
    score += s5

    # 6. 筹码集中度 15分（用换手率近似）
    s6 = 8  # 无直接数据时给中间分
    turnover = _f(factors.get("turnover_rate"))
    if 0.5 <= turnover <= 3: s6 = 13; reasons.append("低换手筹码集中")
    elif 3 < turnover <= 7: s6 = 9
    elif turnover > 12: s6 = 4; reasons.append("换手高筹码分散")
    details["筹码集中度"] = {"score": min(s6,15), "max": 15}
    score += min(s6, 15)

    # 7. 龙虎榜机构行为 10分
    s7 = 5
    if factors.get("institution_net_buy_positive"): s7 = 9; reasons.append("机构龙虎榜净买入")
    elif factors.get("hot_money_dominant"): s7 = 5
    elif factors.get("on_lhb"): s7 = 4
    details["龙虎榜机构"] = {"score": min(s7,10), "max": 10}
    score += min(s7, 10)

    # 8. 公告风险过滤 5分
    s8 = 5
    if factors.get("has_major_announcement_risk"): s8 = 0
    elif factors.get("risk_announcement_count", 0) > 0: s8 = 2
    details["公告风险"] = {"score": s8, "max": 5}
    score += s8

    score = max(0, min(100, score))

    # 主力状态判断
    breakout = factors.get("breakout_platform", False)
    vol_expand = factors.get("is_volume_expanding", False)
    vol_shrink = factors.get("is_volume_shrinking", False)
    above_ma20 = factors.get("above_ma20", False)
    high_vol_stag = factors.get("high_volume_stagnation", False)

    if main_net > 0 and cont_inflow and small_net < 0 and -2 <= change_pct <= 3 and not breakout:
        state = "疑似吸筹"
    elif main_net > 0 and large_net > 0 and breakout and vol_expand:
        state = "疑似拉升"
    elif high_vol_stag and cont_outflow and small_net > 0:
        state = "疑似派发"
    elif above_ma20 and vol_shrink and main_net >= -1e6 and -5 <= change_pct <= 0:
        state = "疑似洗盘"
    else:
        state = "状态不明"

    if score >= 80: grade = "强控盘"
    elif score >= 65: grade = "资金较集中"
    elif score >= 50: grade = "一般控盘"
    else: grade = "控盘弱"

    return {
        "score": score, "details": details,
        "reasons": reasons[:5], "state": state, "grade": grade,
    }

# ── 持仓风控评分 ──────────────────────────────────────────────────────────────
def calc_position_risk(holding_cost: float, current_price: float,
                       qty: int, account_total: float, factors: dict = None) -> dict:
    """持仓风控模型"""
    holding_market_value = current_price * qty
    holding_cost_amount = holding_cost * qty
    floating_pnl = holding_market_value - holding_cost_amount
    floating_pnl_pct = (current_price - holding_cost) / holding_cost * 100 if holding_cost else 0
    position_ratio = holding_market_value / account_total * 100 if account_total else 0

    # 止损止盈线（超短线）
    normal_stop_loss = round(holding_cost * 0.97, 3)
    hard_stop_loss = round(holding_cost * 0.95, 3)
    midterm_stop_loss = round(holding_cost * 0.92, 3)
    take_profit_alert = round(holding_cost * 1.05, 3)
    take_profit_strong = round(holding_cost * 1.08, 3)
    take_profit_25 = round(holding_cost * 1.25, 3)

    # 操作建议
    operation = "持有观察"
    urgency = "normal"
    if floating_pnl_pct <= -5:
        operation = "🚨 强制止损（-5%极限已到）"
        urgency = "critical"
    elif floating_pnl_pct <= -3:
        operation = "⚠️ 普通止损提醒（-3%）"
        urgency = "warning"
    elif floating_pnl_pct >= 25:
        operation = "✅ 盈利25%，至少减1/3仓位"
        urgency = "profit"
    elif floating_pnl_pct >= 20:
        operation = "✅ 进入利润保护模式，减仓锁利"
        urgency = "profit"
    elif floating_pnl_pct >= 8:
        operation = "✅ 盈利8%，可模拟减半"
        urgency = "profit"
    elif floating_pnl_pct >= 5:
        operation = "✅ 盈利5%，开始减仓止盈提醒"
        urgency = "profit"
    # 技术止损
    if factors:
        if factors.get("breakdown_platform") and urgency == "normal":
            operation = "⚠️ 跌破平台，建议减仓"
            urgency = "warning"
        if not factors.get("above_ma20") and urgency == "normal":
            operation = "⚠️ 跌破MA20，波段减仓"
            urgency = "warning"

    # 连续亏损暂停（由 api_account 基于历史交易判断，此处不计算）
    trading_paused = False

    return {
        "holding_cost": holding_cost,
        "current_price": current_price,
        "qty": qty,
        "holding_market_value": round(holding_market_value, 2),
        "holding_cost_amount": round(holding_cost_amount, 2),
        "floating_pnl": round(floating_pnl, 2),
        "floating_pnl_pct": round(floating_pnl_pct, 2),
        "position_ratio": round(position_ratio, 2),
        "normal_stop_loss": normal_stop_loss,
        "hard_stop_loss": hard_stop_loss,
        "midterm_stop_loss": midterm_stop_loss,
        "take_profit_alert": take_profit_alert,
        "take_profit_strong": take_profit_strong,
        "take_profit_25": take_profit_25,
        "operation": operation,
        "urgency": urgency,
        "trading_paused": trading_paused,
    }

# ── 综合分析输出 ──────────────────────────────────────────────────────────────
def full_analysis(code: str, name: str, factors: dict, sectors: list) -> dict:
    """生成完整分析结果（第十九部分输出）"""
    r60 = score_v60(factors, sectors)
    r42 = score_v42(factors)
    rzl = score_zhuangli(factors)

    price = _f(factors.get("current_price"))
    cost = _f(factors.get("holding_cost"))
    qty = _i(factors.get("holding_qty"))
    account_val = _f(factors.get("account_total"), 440000)

    # 持仓风控
    risk_info = {}
    if cost > 0 and qty > 0:
        risk_info = calc_position_risk(cost, price, qty, account_val, factors)

    # 买入/卖出信号
    buy_signal = ""
    sell_signal = ""
    can_sim_buy = False
    need_sim_sell = False
    operation_advice = ""
    risk_warning = []

    # 硬性规则检查
    if r60["hard_exclude"]:
        operation_advice = "❌ 不买：" + "、".join(r60["exclude_reasons"])
    elif r60["score"] >= 90:
        buy_signal = f"强买入信号 V6.0={r60['score']}分 策略{r60['strategy']}"
        can_sim_buy = True
        operation_advice = f"强信号，策略{r60['strategy']}，模拟重点买入"
    elif r60["score"] >= 85:
        buy_signal = f"买入信号 V6.0={r60['score']}分"
        can_sim_buy = True
        operation_advice = f"可模拟小中仓买入，策略{r60['strategy']}"
    elif r60["score"] >= 80:
        operation_advice = "候选观察，暂不买入"
    else:
        operation_advice = "不满足买入条件"

    # 卖出信号（有持仓时）
    if risk_info:
        urgency = risk_info.get("urgency","")
        if urgency == "critical":
            sell_signal = "🚨 强制止损"
            need_sim_sell = True
        elif urgency == "warning":
            sell_signal = "⚠️ 止损提醒"
        elif urgency == "profit":
            sell_signal = "✅ " + risk_info.get("operation","止盈")

    # 止损止盈价
    stop_loss = round(price * 0.97, 3) if price else None
    take_profit = round(price * 1.08, 3) if price else None
    if risk_info:
        stop_loss = risk_info.get("normal_stop_loss")
        take_profit = risk_info.get("take_profit_strong")

    # 风险提示
    if r60["warnings"]: risk_warning.extend(r60["warnings"])
    if r42["warnings"]: risk_warning.extend(r42["warnings"])
    risk_warning = list(dict.fromkeys(risk_warning))[:5]

    return {
        "stock_name": name, "stock_code": code,
        "current_price": price, "current_state": rzl["state"],
        "score_v42": r42["score"], "grade_v42": r42["grade"],
        "score_v60": r60["score"], "grade_v60": r60["grade"],
        "score_zl": rzl["score"], "grade_zl": rzl["grade"],
        "score_risk": risk_info,
        "strategy": r60["strategy"],
        "buy_signal": buy_signal, "sell_signal": sell_signal,
        "stop_loss": stop_loss, "take_profit": take_profit,
        "operation_advice": operation_advice,
        "risk_warning": risk_warning,
        "can_sim_buy": can_sim_buy, "need_sim_sell": need_sim_sell,
        "details_v60": r60["details"],
        "details_v42": r42["details"],
        "details_zl": rzl["details"],
        "reasons_v60": r60["reasons"],
        "reasons_v42": r42["reasons"],
        "analysis_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

# ── AI调用 ────────────────────────────────────────────────────────────────────
def call_ai(provider: str, system: str, user: str) -> Optional[str]:
    try:
        if provider == "openai" and OPENAI_API_KEY:
            r = requests.post(OPENAI_BASE_URL, json={
                "model": OPENAI_MODEL, "temperature": 0.2, "max_tokens": 2000,
                "messages": [{"role":"system","content":system},{"role":"user","content":user}],
            }, headers={"Authorization":"Bearer "+OPENAI_API_KEY,"Content-Type":"application/json"}, timeout=60)
        elif DEEPSEEK_API_KEY:
            r = requests.post(DEEPSEEK_BASE_URL, json={
                "model": "deepseek-chat", "temperature": 0.2, "max_tokens": 2000,
                "messages": [{"role":"system","content":system},{"role":"user","content":user}],
            }, headers={"Authorization":"Bearer "+DEEPSEEK_API_KEY,"Content-Type":"application/json"}, timeout=60)
        else: return None
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except: return None

# ── 推送 ──────────────────────────────────────────────────────────────────────
def send_wecom(title: str, content: str):
    if not WECOM_WEBHOOK: return
    try:
        requests.post(WECOM_WEBHOOK, json={
            "msgtype":"markdown","markdown":{"content":"## "+title+"\n"+content}
        }, timeout=8)
    except: pass

# ── 模拟账户操作 ──────────────────────────────────────────────────────────────
def get_cash() -> float:
    db = get_db()
    try:
        row = db.execute("SELECT cash FROM sim_account WHERE id='main'").fetchone()
        return row["cash"] if row else 0.0
    finally: db.close()

def set_cash(v: float):
    db = get_db()
    try:
        db.execute("UPDATE sim_account SET cash=?,updated_at=strftime('%s','now') WHERE id='main'",(v,))
        db.commit()
    finally: db.close()

def get_positions() -> List[dict]:
    db = get_db()
    try:
        return [dict(r) for r in db.execute("SELECT * FROM sim_positions ORDER BY bought_at DESC").fetchall()]
    finally: db.close()

def get_account_value() -> float:
    cash = get_cash()
    positions = get_positions()
    if not positions: return cash
    try:
        qs = fetch_quotes([p["stock_code"] for p in positions])
        pv = sum((qs.get(p["stock_code"]) or {}).get("current_price",p["cost_price"])*p["qty"] for p in positions)
        return round(cash+pv, 2)
    except: return cash

def sim_buy(code,name,price,qty,score_v60_val,score_v42_val,strategy,reason,industry=""):
    cash = get_cash()
    if price*qty > cash: return False,"资金不足"
    db = get_db()
    try:
        if db.execute("SELECT id FROM sim_positions WHERE stock_code=?",(code,)).fetchone():
            return False,"已持有该股"
        db.execute(
            "INSERT INTO sim_positions (id,stock_code,stock_name,industry,qty,cost_price,"
            "normal_stop_loss,hard_stop_loss,take_profit_alert,take_profit_strong,"
            "score_v42,score_v60,strategy,buy_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()),code,name,industry,qty,price,
             round(price*0.97,3),round(price*0.95,3),round(price*1.05,3),round(price*1.08,3),
             score_v42_val,score_v60_val,strategy,reason))
        nc=round(cash-price*qty,2)
        db.execute(
            "INSERT INTO sim_trades (id,stock_code,stock_name,direction,price,qty,amount,"
            "strategy,score_v60,score_v42,buy_reason,cash_after) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()),code,name,"buy",price,qty,price*qty,strategy,score_v60_val,score_v42_val,reason,nc))
        db.commit(); set_cash(nc)
        return True,"买入成功"
    finally: db.close()

def sim_sell(code,price,qty,reason):
    db=get_db()
    try:
        pos=db.execute("SELECT * FROM sim_positions WHERE stock_code=?",(code,)).fetchone()
        if not pos: return False,"无持仓"
        pos=dict(pos)
        sq=min(qty if qty>0 else pos["qty"],pos["qty"])
        if sq<=0: return False,"数量错误"
        cash=get_cash(); nc=round(cash+price*sq,2)
        pnl=round((price-pos["cost_price"])*sq,2)
        pnl_pct=round((price-pos["cost_price"])/pos["cost_price"]*100,2)
        hold_days=(int(time.time())-pos["bought_at"])//86400
        fr=reason+f" | 成本{pos['cost_price']} 盈亏{pnl}元({pnl_pct}%)"
        is_win=1 if pnl>0 else 0
        if sq>=pos["qty"]:
            db.execute("DELETE FROM sim_positions WHERE stock_code=?",(code,))
        else:
            db.execute("UPDATE sim_positions SET qty=? WHERE stock_code=?",(pos["qty"]-sq,code))
        db.execute(
            "INSERT INTO sim_trades (id,stock_code,stock_name,direction,price,qty,amount,"
            "strategy,sell_reason,pnl,pnl_pct,hold_days,is_win,cash_after) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()),code,pos["stock_name"],"sell",price,sq,price*sq,
             pos.get("strategy",""),fr,pnl,pnl_pct,hold_days,is_win,nc))
        db.commit(); set_cash(nc)
        return True,fr
    finally: db.close()

# ── 自动扫描 ──────────────────────────────────────────────────────────────────
SCAN_POOL = [
    "601318","600036","600900","601166","601398","601988","601628","600887",
    "600104","600309","601888","600030","600000","601012","600196","600028",
    "601088","601169","600703","601111","600050","601668","601229","600009",
    "601186","600019","600031","601800","600547","601857","600029","601919",
    "600115","601006","601211","600011","601985","601816","601939","600690",
    "600999","601766","601688","600585","600600","601618","600018","601238",
    "600837","601901","601728","600015","600332","600060","600741","000858",
    "000333","000651","000002","000001","002415","300750","002594","000725",
    "000568","002475","000776","002049","000063","002230","000661","000596",
    "002129","000538","300059","300124","300015","300014","300122","300033",
    "002241","002236","000157","002024","002714","601818","601377","601288",
    "000895","300274","300760","688111","688012","688599","300919","300999",
]
SCAN_POOL = list(dict.fromkeys(SCAN_POOL))
_last_candidates = []
_scan_lock = threading.Lock()

def auto_scan():
    global _last_candidates
    now = datetime.now()
    if now.weekday() >= 5:
        print("[SCAN] 周末跳过"); return
    if not _scan_lock.acquire(blocking=False):
        print("[SCAN] 已在扫描中"); return
    try:
        print(f"[SCAN] 开始 {now.strftime('%H:%M:%S')}")
        sectors = []
        try: sectors = fetch_sector_hot()
        except: pass
        positions = get_positions()
        held = {p["stock_code"] for p in positions}
        pos_count = len(positions)
        cash = get_cash()
        max_new = 6 - pos_count
        if max_new <= 0 or cash < 10000:
            print("[SCAN] 满仓或现金不足"); return
        # 市场情绪判断
        market = {}
        try: market = fetch_market()
        except: pass
        sh_chg = (market.get("000001") or {}).get("chg_pct",0)
        if sh_chg > 1: pos_ratio = 0.18
        elif sh_chg > 0: pos_ratio = 0.13
        elif sh_chg > -1: pos_ratio = 0.09
        else: pos_ratio = 0.05
        max_per = min(get_account_value()*pos_ratio, cash/max(max_new,1), 100000)
        codes = [c for c in SCAN_POOL if c not in held]
        candidates = []
        for i in range(0, len(codes), 20):
            batch = codes[i:i+20]
            try:
                qs = fetch_quotes(batch)
                for code in batch:
                    q = qs.get(code)
                    if not q or q.get("current_price",0) <= 0: continue
                    chg = q.get("change_pct",0)
                    amt = q.get("turnover_amount",0)
                    if chg >= 9.5 or chg <= -8: continue
                    if amt < 3e7: continue
                    # 构建因子
                    factors = dict(q)
                    klines = []
                    try: klines = fetch_kline(code,"101",120)
                    except: pass
                    if klines:
                        kf = build_k_factors(klines, q.get("current_price",0))
                        factors.update(kf)
                        # MACD
                        closes=[k["close"] for k in klines]
                        highs=[k["high"] for k in klines]
                        lows=[k["low"] for k in klines]
                        dif,dea,macd=calc_macd(closes)
                        if dif and dea:
                            factors["macd_dif"]=dif[-1]; factors["macd_dea"]=dea[-1]
                    mf = {}
                    try: mf = fetch_moneyflow_detail(code, 10)
                    except: pass
                    factors.update(mf)
                    r60 = score_v60(factors, sectors)
                    if r60["hard_exclude"]: continue
                    if r60["score"] >= 85:
                        candidates.append({
                            "code":code,"name":q.get("name",code),
                            "price":q["current_price"],"change_pct":chg,
                            "amount":amt,"score_v60":r60["score"],
                            "strategy":r60["strategy"],"grade":r60["grade"],
                            "reason":"; ".join(r60.get("reasons",[])),"warnings":r60.get("warnings",[]),
                            "details":r60["details"],
                        })
            except Exception as e:
                print(f"[SCAN] 批次异常:{e}")
            time.sleep(0.2)
        candidates.sort(key=lambda x:x["score_v60"],reverse=True)
        _last_candidates = candidates[:30]
        # 自动买入
        bought = []
        for c in candidates[:max_new]:
            budget = min(max_per, get_cash()*0.95)
            qty = int(budget/c["price"]/100)*100
            if qty<100: continue
            ok,msg = sim_buy(c["code"],c["name"],c["price"],qty,
                             c["score_v60"],0,c["strategy"],c["reason"])
            if ok:
                bought.append(c)
                print(f"[SCAN] 买入 {c['name']} {c['price']} {qty}股 评分{c['score_v60']}")
                # 保存信号
                db=get_db()
                try:
                    db.execute(
                        "INSERT INTO signals (id,stock_code,stock_name,signal_type,strategy,score_v60,price,reason,executed)"
                        " VALUES (?,?,?,?,?,?,?,?,1)",
                        (str(uuid.uuid4()),c["code"],c["name"],"buy",c["strategy"],c["score_v60"],c["price"],c["reason"]))
                    db.commit()
                finally: db.close()
        if bought and WECOM_WEBHOOK:
            parts=[f"**旺财自动买入 {now.strftime('%m-%d %H:%M')}**","",
                   f"> 现金{get_cash()/10000:.1f}万 持仓{len(get_positions())}/6",""]
            for c in bought:
                parts.append(f"> 🔴 {c['name']} {c['price']}元 V6={c['score_v60']}分 [{c['strategy']}]")
                parts.append(f">   {c['reason']}")
            send_wecom("旺财·自动买入","\n".join(parts))
        print(f"[SCAN] 完成 候选{len(candidates)}只 买入{len(bought)}只")
    finally:
        _scan_lock.release()

def auto_check_exit():
    now = datetime.now()
    if now.weekday()>=5: return
    positions = get_positions()
    if not positions: return
    try: qs=fetch_quotes([p["stock_code"] for p in positions])
    except: return
    actions=[]
    for p in positions:
        q=qs.get(p["stock_code"]) or {}
        price=q.get("current_price") or p["cost_price"]
        pnl_pct=(price-p["cost_price"])/p["cost_price"]*100
        held=(int(time.time())-p["bought_at"])//86400
        code=p["stock_code"]; sq=0; reason=""
        # 更新浮盈浮亏
        db=get_db()
        try:
            max_p=max(p.get("max_profit_pct",0),pnl_pct)
            max_l=min(p.get("max_loss_pct",0),pnl_pct)
            db.execute("UPDATE sim_positions SET max_profit_pct=?,max_loss_pct=? WHERE stock_code=?",(max_p,max_l,code))
            db.commit()
        finally: db.close()
        if pnl_pct<=-5: sq=p["qty"]; reason=f"🚨 强制止损-5% 亏{pnl_pct:.1f}%"
        elif pnl_pct<=-3: sq=p["qty"]; reason=f"⚠️ 普通止损-3% 亏{pnl_pct:.1f}%"
        elif p.get("take_profit_strong") and price>=p["take_profit_strong"]:
            sq=p["qty"]; reason=f"✅ 止盈+8% 盈{pnl_pct:.1f}%"
        elif p.get("take_profit_alert") and price>=p["take_profit_alert"]:
            half=(p["qty"]//200)*100
            if half>=100: sq=half; reason=f"✅ 减半止盈+5% 盈{pnl_pct:.1f}%"
        elif held>=3 and pnl_pct<0:
            sq=p["qty"]; reason=f"⏰ 持{held}日未盈利清仓"
        elif held>=1:
            try:
                klines=fetch_kline(code,"101",30)
                if klines and len(klines)>=5:
                    closes=[k["close"] for k in klines]
                    ma5=sum(closes[-5:])/5
                    mf=fetch_moneyflow_detail(code,3)
                    if price<ma5 and mf.get("main_continuous_outflow"):
                        sq=p["qty"]; reason="📉 跌破MA5+主力连续流出"
            except: pass
        if sq>0:
            ok,msg=sim_sell(code,price,sq,reason)
            if ok:
                actions.append({"code":code,"name":p["stock_name"],"price":price,
                                "pnl_pct":round(pnl_pct,1),"reason":reason})
                print(f"[EXIT] 卖出 {p['stock_name']} {reason}")
                db=get_db()
                try:
                    db.execute(
                        "INSERT INTO signals (id,stock_code,stock_name,signal_type,strategy,score_v60,price,reason,executed)"
                        " VALUES (?,?,?,?,?,?,?,?,1)",
                        (str(uuid.uuid4()),code,p["stock_name"],"sell",p.get("strategy",""),
                         p.get("score_v60",0),price,reason))
                    db.commit()
                finally: db.close()
    if actions and WECOM_WEBHOOK:
        parts=["**旺财·自动卖出**",""]
        for a in actions:
            c="🟢" if a["pnl_pct"]>=0 else "🔴"
            parts.append(f"> {c} {a['name']} {a['price']}元 {'+' if a['pnl_pct']>=0 else ''}{a['pnl_pct']}%")
            parts.append(f">   {a['reason']}")
        send_wecom("旺财·自动卖出","\n".join(parts))

def save_daily_stats():
    now=datetime.now()
    if now.weekday()>=5: return
    date_str=now.strftime("%Y-%m-%d")
    account_val=get_account_value(); cash=get_cash()
    db=get_db()
    try:
        today_start=int(datetime(now.year,now.month,now.day).timestamp())
        trades=db.execute("SELECT * FROM sim_trades WHERE traded_at>=?",(today_start,)).fetchall()
        daily_pnl=sum(_f(t["pnl"]) for t in trades)
        win=sum(1 for t in trades if t["direction"]=="sell" and _f(t["pnl"])>0)
        lose=sum(1 for t in trades if t["direction"]=="sell" and _f(t["pnl"])<=0)
        initial=440000; total_pnl=account_val-initial
        # 更新峰值
        peak_row=db.execute("SELECT peak_value FROM sim_account WHERE id='main'").fetchone()
        peak=_f(peak_row["peak_value"] if peak_row else 0)
        new_peak=max(peak,account_val)
        max_dd=round((new_peak-account_val)/new_peak*100,2) if new_peak>0 else 0
        db.execute("UPDATE sim_account SET peak_value=? WHERE id='main'",(new_peak,))
        db.execute("""
            INSERT OR REPLACE INTO daily_stats
            (id,stat_date,total_value,daily_pnl,daily_pnl_pct,total_pnl,total_pnl_pct,max_drawdown,win_trades,lose_trades)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """,(str(uuid.uuid4()),date_str,account_val,daily_pnl,
             round(daily_pnl/initial*100,2),total_pnl,round(total_pnl/initial*100,2),
             max_dd,win,lose))
        db.commit()
    finally: db.close()
    if WECOM_WEBHOOK:
        initial=440000; total_pnl=account_val-initial
        sign="+" if total_pnl>=0 else ""
        parts=[f"**旺财模拟账户日报 {date_str}**","",
               f"> 账户总值：**{account_val/10000:.2f}万**",
               f"> 累计盈亏：**{sign}{total_pnl/10000:.2f}万 ({sign}{total_pnl/initial*100:.2f}%)**",
               f"> 持仓：{len(get_positions())}/6只 现金：{cash/10000:.1f}万"]
        send_wecom("旺财·模拟账户日报","\n".join(parts))

# ── 调度器 ────────────────────────────────────────────────────────────────────
try:
    import schedule as _sch
    _has_sch = True
except: _has_sch = False

def _scheduler():
    if not _has_sch: return
    _sch.every().day.at("09:31").do(auto_scan)
    _sch.every(30).minutes.do(auto_check_exit)
    _sch.every().day.at("15:35").do(save_daily_stats)
    print("[SCHEDULER] 调度器已启动")
    while True:
        _sch.run_pending()
        time.sleep(30)

threading.Thread(target=_scheduler, daemon=True).start()

# ════════════════════════════════════════════════════════════════════════════════
# API路由
# ════════════════════════════════════════════════════════════════════════════════

@app.get("/health")
def health():
    return {"status":"ok","version":"7.0","ts":int(time.time())}

@app.get("/api/search",dependencies=[Depends(require_auth),Depends(rate_limit("search",60))])
def api_search(q:str):
    if not q or len(q.strip())<1: raise HTTPException(400,"请输入关键词")
    return search_stock(q)

@app.get("/api/market",dependencies=[Depends(require_auth),Depends(rate_limit("market",60))])
def api_market():
    return {"data":fetch_market(),"sectors":fetch_sector_hot()[:10],"ts":int(time.time())}

@app.get("/api/quotes",dependencies=[Depends(require_auth),Depends(rate_limit("quotes",60))])
def api_quotes(codes:str):
    cl=[c.strip() for c in codes.split(",") if c.strip()]
    if not cl or len(cl)>50: raise HTTPException(400,"codes不合法")
    return fetch_quotes(cl)

@app.get("/api/kline/{code}",dependencies=[Depends(require_auth),Depends(rate_limit("kline",60))])
def api_kline(code:str,period:str="101",limit:int=120):
    klines=fetch_kline(code,period,min(limit,200))
    closes=[k["close"] for k in klines]
    highs=[k["high"] for k in klines]
    lows=[k["low"] for k in klines]
    dif,dea,macd=calc_macd(closes)
    K,D,J=calc_kdj(highs,lows,closes)
    mid,upper,lower=calc_boll(closes)
    return {
        "code":code,"period":period,"klines":klines,
        "ma":{"ma5":calc_ma(closes,5),"ma10":calc_ma(closes,10),
              "ma20":calc_ma(closes,20),"ma60":calc_ma(closes,60)},
        "macd":{"dif":dif,"dea":dea,"macd":macd},
        "kdj":{"K":K,"D":D,"J":J},
        "boll":{"mid":mid,"upper":upper,"lower":lower},
    }

@app.get("/api/moneyflow/{code}",dependencies=[Depends(require_auth),Depends(rate_limit("mf",30))])
def api_moneyflow(code:str):
    return fetch_moneyflow_detail(code,10)

@app.get("/api/sector-hot",dependencies=[Depends(require_auth),Depends(rate_limit("sector",30))])
def api_sector():
    return {"data":fetch_sector_hot(),"ts":int(time.time())}

# ── 核心分析接口 ──────────────────────────────────────────────────────────────
class AnalyzeReq(BaseModel):
    stock_code: str = Field(...,max_length=20)
    stock_name: str = Field(...,max_length=60)
    known_info: str = Field(default="",max_length=6000)
    industry: str = Field(default="",max_length=100)
    concepts: str = Field(default="",max_length=200)
    holding_cost: Optional[float] = None
    holding_qty: Optional[int] = None
    # 财报数据（可选）
    revenue_yoy_pct: Optional[float] = None
    net_profit_yoy_pct: Optional[float] = None
    deducted_net_profit_yoy_pct: Optional[float] = None
    roe_pct: Optional[float] = None
    gross_margin_pct: Optional[float] = None
    debt_ratio_pct: Optional[float] = None
    operating_cashflow: Optional[float] = None
    # 公告因子（可选）
    has_reduction_announcement: bool = False
    has_unlock_risk: bool = False
    has_regulatory_inquiry: bool = False
    has_profit_warning_positive: bool = False
    has_major_contract: bool = False
    has_buyback: bool = False
    has_dividend: bool = False
    has_goodwill_impairment: bool = False
    has_investigation: bool = False
    has_penalty: bool = False
    is_st: bool = False
    is_loss: bool = False
    use_ai: bool = False
    ai_provider: str = Field(default="deepseek",max_length=20)

@app.post("/api/analyze",dependencies=[Depends(require_auth),Depends(rate_limit("analyze",10))])
def api_analyze(req: AnalyzeReq):
    # 获取实时行情
    quote = {}
    try:
        qs = fetch_quotes([req.stock_code])
        quote = qs.get(req.stock_code) or {}
    except: pass

    # 获取K线技术因子
    kf = {}
    klines = []
    try:
        klines = fetch_kline(req.stock_code,"101",120)
        if klines:
            kf = build_k_factors(klines, quote.get("current_price",0))
            closes=[k["close"] for k in klines]
            highs=[k["high"] for k in klines]
            lows=[k["low"] for k in klines]
            dif,dea,_=calc_macd(closes)
            if dif and dea: kf["macd_dif"]=dif[-1]; kf["macd_dea"]=dea[-1]
            K,D,J=calc_kdj(highs,lows,closes)
            if K: kf["kdj_k"]=K[-1]; kf["kdj_d"]=D[-1]; kf["kdj_j"]=J[-1]
    except: pass

    # 获取主力资金
    mf = {}
    try: mf = fetch_moneyflow_detail(req.stock_code,10)
    except: pass

    # 获取板块热度
    sectors = []
    try: sectors = fetch_sector_hot()
    except: pass

    # 构建完整因子
    factors = {}
    factors.update(quote)
    factors.update(kf)
    factors.update(mf)
    factors.update({
        "industry": req.industry, "concepts": req.concepts,
        "is_st": req.is_st, "is_loss": req.is_loss,
        "has_reduction_announcement": req.has_reduction_announcement,
        "has_unlock_risk": req.has_unlock_risk,
        "has_regulatory_inquiry": req.has_regulatory_inquiry,
        "has_profit_warning_positive": req.has_profit_warning_positive,
        "has_major_contract": req.has_major_contract,
        "has_buyback": req.has_buyback, "has_dividend": req.has_dividend,
        "has_goodwill_impairment": req.has_goodwill_impairment,
        "has_investigation": req.has_investigation,
        "has_penalty": req.has_penalty,
        "revenue_yoy_pct": req.revenue_yoy_pct or 0,
        "net_profit_yoy_pct": req.net_profit_yoy_pct or 0,
        "deducted_net_profit_yoy_pct": req.deducted_net_profit_yoy_pct or 0,
        "roe_pct": req.roe_pct or 0,
        "gross_margin_pct": req.gross_margin_pct or 0,
        "debt_ratio_pct": req.debt_ratio_pct or 0,
        "operating_cashflow": req.operating_cashflow or 0,
        "holding_cost": req.holding_cost or 0,
        "holding_qty": req.holding_qty or 0,
        "account_total": get_account_value(),
    })
    # 从已知信息提取公告因子
    info_upper = (req.known_info or "").upper()
    if "减持" in info_upper: factors["has_reduction_announcement"] = True
    if "回购" in info_upper: factors["has_buyback"] = True
    if "增持" in info_upper: factors["has_shareholder_increase"] = True
    if "预增" in info_upper or "业绩增" in info_upper: factors["has_profit_warning_positive"] = True
    if "中标" in info_upper or "订单" in info_upper: factors["has_major_contract"] = True
    if "分红" in info_upper: factors["has_dividend"] = True
    if "问询" in info_upper: factors["has_regulatory_inquiry"] = True
    catalyst_count = sum([
        bool(factors.get("has_profit_warning_positive")),
        bool(factors.get("has_major_contract")),
        bool(factors.get("has_buyback")),
        bool(factors.get("has_dividend")),
        bool(factors.get("has_shareholder_increase")),
    ])
    factors["catalyst_announcement_count"] = catalyst_count
    factors["has_real_catalyst"] = catalyst_count >= 2
    risk_count = sum([
        bool(factors.get("has_reduction_announcement")),
        bool(factors.get("has_unlock_risk")),
        bool(factors.get("has_regulatory_inquiry")),
        bool(factors.get("has_goodwill_impairment")),
    ])
    factors["risk_announcement_count"] = risk_count
    factors["has_major_announcement_risk"] = risk_count >= 2 or req.has_investigation or req.has_penalty

    result = full_analysis(req.stock_code, req.stock_name, factors, sectors)

    # AI增强
    if req.use_ai and (DEEPSEEK_API_KEY or OPENAI_API_KEY):
        prompt = (
            f"你是旺财A股AI量化分析系统V7.0分析师，严格按规则分析，A股涨红跌绿。\n"
            f"股票：{req.stock_name}({req.stock_code}) 行业：{req.industry}\n"
            f"当前价：{quote.get('current_price','未知')} 涨跌：{quote.get('change_pct','未知')}%\n"
            f"V6.0超短线评分：{result['score_v60']}分({result['grade_v60']}) 策略：{result['strategy']}\n"
            f"V4.2中线评分：{result['score_v42']}分({result['grade_v42']})\n"
            f"主力控盘：{result['score_zl']}分 状态：{result['current_state']}\n"
            f"技术信号：{result['reasons_v60']}\n"
            f"风险提示：{result['risk_warning']}\n"
            f"已知信息：{req.known_info[:2000]}\n\n"
            f"请补充：1.行业逻辑判断 2.估值合理性 3.催化剂评估 4.最终操作建议 5.止损止盈建议"
        )
        ai_result = call_ai(req.ai_provider, "你是专业A股量化分析师，分析简洁有据。", prompt)
        if ai_result:
            result["ai_analysis"] = ai_result
            result["ai_provider"] = req.ai_provider

    # 保存分析记录
    db=get_db()
    try:
        db.execute(
            "INSERT INTO analysis_records (id,stock_code,stock_name,score_v42,score_v60,score_zl,"
            "grade_v42,grade_v60,grade_zl,strategy,buy_signal,sell_signal,stop_loss,take_profit,"
            "operation_advice,risk_warning,can_sim_buy,need_sim_sell,factors) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()),req.stock_code,req.stock_name,result["score_v42"],result["score_v60"],
             result["score_zl"],result["grade_v42"],result["grade_v60"],result["grade_zl"],
             result["strategy"],result["buy_signal"],result["sell_signal"],
             result.get("stop_loss"),result.get("take_profit"),result["operation_advice"],
             json.dumps(result["risk_warning"],ensure_ascii=False),
             int(result["can_sim_buy"]),int(result["need_sim_sell"]),
             json.dumps(factors,ensure_ascii=False,default=str)))
        db.commit()
    finally: db.close()
    return result

@app.get("/api/analysis-history",dependencies=[Depends(require_auth)])
def api_history(stock_code:str="",limit:int=50):
    db=get_db()
    try:
        if stock_code:
            rows=db.execute("SELECT * FROM analysis_records WHERE stock_code=? ORDER BY created_at DESC LIMIT ?",(stock_code,min(limit,200))).fetchall()
        else:
            rows=db.execute("SELECT * FROM analysis_records ORDER BY created_at DESC LIMIT ?",(min(limit,200),)).fetchall()
        return [dict(r) for r in rows]
    finally: db.close()

# ── 模拟账户 ──────────────────────────────────────────────────────────────────
@app.get("/api/account",dependencies=[Depends(require_auth)])
def api_account():
    db=get_db()
    try:
        acct=dict(db.execute("SELECT * FROM sim_account WHERE id='main'").fetchone() or {})
    finally: db.close()
    positions=get_positions()
    pos_val=0.0
    # 连续亏损判断
    db=get_db()
    try:
        last_sells=db.execute("SELECT is_win FROM sim_trades WHERE direction='sell' ORDER BY traded_at DESC LIMIT 5").fetchall()
        cont_loss=0
        for t in last_sells:
            if not t["is_win"]: cont_loss+=1
            else: break
    finally: db.close()
    trading_paused = cont_loss >= 3
    if positions:
        try:
            qs=fetch_quotes([p["stock_code"] for p in positions])
            for p in positions:
                q=qs.get(p["stock_code"]) or {}
                price=q.get("current_price") or p["cost_price"]
                pnl=(price-p["cost_price"])*p["qty"]
                pnl_pct=(price-p["cost_price"])/p["cost_price"]*100
                p["current_price"]=price; p["market_value"]=round(price*p["qty"],2)
                p["pnl"]=round(pnl,2); p["pnl_pct"]=round(pnl_pct,2)
                p["held_days"]=(int(time.time())-p["bought_at"])//86400
                p["change_pct"]=q.get("change_pct",0)
                p["urgency"]="critical" if pnl_pct<=-5 else "warning" if pnl_pct<=-3 else "profit" if pnl_pct>=5 else "normal"
                pos_val+=price*p["qty"]
                # 风控信息
                p["normal_stop_loss"]=p.get("normal_stop_loss") or round(p["cost_price"]*0.97,3)
                p["hard_stop_loss"]=p.get("hard_stop_loss") or round(p["cost_price"]*0.95,3)
                p["take_profit_alert"]=p.get("take_profit_alert") or round(p["cost_price"]*1.05,3)
                p["take_profit_strong"]=p.get("take_profit_strong") or round(p["cost_price"]*1.08,3)
        except: pass
    initial=440000
    acct["position_value"]=round(pos_val,2)
    acct["total_value"]=round(acct.get("cash",0)+pos_val,2)
    acct["total_pnl"]=round(acct["total_value"]-initial,2)
    acct["total_pnl_pct"]=round(acct["total_pnl"]/initial*100,2)
    acct["position_ratio"]=round(pos_val/acct["total_value"]*100,1) if acct["total_value"] else 0
    acct["trading_paused"]=trading_paused
    acct["cont_loss_count"]=cont_loss
    return {"account":acct,"positions":positions}

@app.get("/api/trades",dependencies=[Depends(require_auth)])
def api_trades(direction:str="",limit:int=100):
    db=get_db()
    try:
        if direction:
            rows=db.execute("SELECT * FROM sim_trades WHERE direction=? ORDER BY traded_at DESC LIMIT ?",(direction,min(limit,500))).fetchall()
        else:
            rows=db.execute("SELECT * FROM sim_trades ORDER BY traded_at DESC LIMIT ?",(min(limit,500),)).fetchall()
        return [dict(r) for r in rows]
    finally: db.close()

@app.get("/api/signals",dependencies=[Depends(require_auth)])
def api_signals(limit:int=50):
    db=get_db()
    try:
        rows=db.execute("SELECT * FROM signals ORDER BY created_at DESC LIMIT ?",(min(limit,200),)).fetchall()
        return [dict(r) for r in rows]
    finally: db.close()

@app.get("/api/candidates",dependencies=[Depends(require_auth)])
def api_candidates():
    candidates = list(_last_candidates)
    return {"candidates":candidates,"count":len(candidates),"ts":int(time.time())}

@app.get("/api/stats",dependencies=[Depends(require_auth)])
def api_stats():
    db=get_db()
    try:
        total=db.execute("SELECT COUNT(*) FROM sim_trades WHERE direction='sell'").fetchone()[0]
        win=db.execute("SELECT COUNT(*) FROM sim_trades WHERE direction='sell' AND is_win=1").fetchone()[0]
        lose=db.execute("SELECT COUNT(*) FROM sim_trades WHERE direction='sell' AND is_win=0").fetchone()[0]
        total_pnl=_f(db.execute("SELECT SUM(pnl) FROM sim_trades WHERE direction='sell'").fetchone()[0])
        avg_win=_f(db.execute("SELECT AVG(pnl_pct) FROM sim_trades WHERE direction='sell' AND is_win=1").fetchone()[0])
        avg_lose=_f(db.execute("SELECT AVG(pnl_pct) FROM sim_trades WHERE direction='sell' AND is_win=0").fetchone()[0])
        avg_hold=_f(db.execute("SELECT AVG(hold_days) FROM sim_trades WHERE direction='sell'").fetchone()[0])
        max_profit=_f(db.execute("SELECT MAX(pnl) FROM sim_trades WHERE direction='sell'").fetchone()[0])
        max_loss=_f(db.execute("SELECT MIN(pnl) FROM sim_trades WHERE direction='sell'").fetchone()[0])
        # 策略统计
        strat_stats=[]
        for s in ["A","B","C"]:
            row=db.execute("SELECT COUNT(*) as c,SUM(pnl) as p,AVG(pnl_pct) as a FROM sim_trades WHERE direction='sell' AND strategy=?",(s,)).fetchone()
            if row and row["c"]:
                strat_stats.append({"strategy":s,"name":{"A":"主线突破","B":"强势回踩","C":"资金异动"}[s],
                                   "count":row["c"],"total_pnl":round(_f(row["p"]),2),"avg_pct":round(_f(row["a"]),2)})
        daily=[dict(r) for r in db.execute("SELECT * FROM daily_stats ORDER BY stat_date DESC LIMIT 60").fetchall()]
        # 最大回撤
        max_dd=_f(db.execute("SELECT MAX(max_drawdown) FROM daily_stats").fetchone()[0])
        return {
            "total_trades":total,"win_trades":win,"lose_trades":lose,
            "win_rate":round(win/total*100,1) if total else 0,
            "total_pnl":round(total_pnl,2),
            "avg_win_pct":round(avg_win,2),"avg_lose_pct":round(avg_lose,2),
            "profit_loss_ratio":round(abs(avg_win/avg_lose),2) if avg_lose else 0,
            "avg_hold_days":round(avg_hold,1),
            "max_single_profit":round(max_profit,2),"max_single_loss":round(max_loss,2),
            "max_drawdown":round(max_dd,2),
            "strategy_stats":strat_stats,"daily":daily,
        }
    finally: db.close()

@app.post("/api/scan-now",dependencies=[Depends(require_auth)])
def api_scan_now():
    threading.Thread(target=auto_scan,daemon=True).start()
    return {"msg":"选股扫描已启动，约3-5分钟完成"}

@app.post("/api/check-exit",dependencies=[Depends(require_auth)])
def api_check_exit():
    threading.Thread(target=auto_check_exit,daemon=True).start()
    return {"msg":"止损止盈检查已启动"}

class BuyReq(BaseModel):
    stock_code: str = Field(..., max_length=20)
    stock_name: str = Field(..., max_length=60)
    price: Optional[float] = None
    qty: Optional[int] = None
    strategy: str = Field(default="手动", max_length=10)
    reason: str = Field(default="手动买入", max_length=200)

@app.post("/api/buy",dependencies=[Depends(require_auth),Depends(rate_limit("buy",20))])
def api_buy(req: BuyReq):
    positions = get_positions()
    if len(positions) >= 6:
        raise HTTPException(400,"持仓已满6只")
    # 获取实时价格
    try:
        price = req.price or (fetch_quotes([req.stock_code]).get(req.stock_code) or {}).get("current_price")
    except:
        price = req.price
    if not price or price <= 0:
        raise HTTPException(400,"无法获取实时价格，请手动填写买入价")
    cash = get_cash()
    if req.qty:
        qty = (req.qty // 100) * 100
    else:
        budget = min(cash * 0.3, 80000)
        qty = int(budget / price / 100) * 100
    if qty < 100:
        raise HTTPException(400,"资金不足或价格过高，最小100股")
    if price * qty > cash:
        raise HTTPException(400,f"资金不足，需{price*qty:.0f}元，可用{cash:.0f}元")
    ok, msg = sim_buy(req.stock_code, req.stock_name, price, qty, 0, 0, req.strategy, req.reason)
    if ok:
        return {"msg": msg, "price": price, "qty": qty, "amount": round(price*qty,2)}
    raise HTTPException(400, msg)

@app.post("/api/sell/{code}",dependencies=[Depends(require_auth)])
def api_sell(code:str):
    positions=get_positions()
    pos=next((p for p in positions if p["stock_code"]==code),None)
    if not pos: raise HTTPException(404,"无该持仓")
    try: price=(fetch_quotes([code]).get(code) or {}).get("current_price") or pos["cost_price"]
    except: price=pos["cost_price"]
    ok,msg=sim_sell(code,price,0,"手动强制卖出")
    if ok: return {"msg":msg}
    raise HTTPException(400,msg)

@app.post("/api/reset",dependencies=[Depends(require_auth)])
def api_reset():
    db=get_db()
    try:
        db.execute("DELETE FROM sim_positions")
        db.execute("DELETE FROM sim_trades")
        db.execute("DELETE FROM signals")
        db.execute("DELETE FROM daily_stats")
        db.execute("DELETE FROM analysis_records")
        db.execute("UPDATE sim_account SET cash=440000,peak_value=440000 WHERE id='main'")
        db.commit()
        return {"msg":"模拟账户已重置，初始资金44万"}
    finally: db.close()

# ── 观察池 ────────────────────────────────────────────────────────────────────
class WatchIn(BaseModel):
    stock_code:str=Field(...,max_length=20); stock_name:str=Field(...,max_length=60)
    industry:str=Field(default="",max_length=100); concepts:str=Field(default="",max_length=200)
    note:str=Field(default="",max_length=500)

@app.get("/api/watchlist",dependencies=[Depends(require_auth)])
def api_watchlist():
    db=get_db()
    try: rows=[dict(r) for r in db.execute("SELECT * FROM watchlist ORDER BY added_at DESC").fetchall()]
    finally: db.close()
    if rows:
        try:
            qs=fetch_quotes([r["stock_code"] for r in rows])
            for row in rows: row["quote"]=qs.get(row["stock_code"])
        except: pass
    return {"items":rows}

@app.post("/api/watchlist",dependencies=[Depends(require_auth)])
def api_add_watch(w:WatchIn):
    db=get_db()
    try:
        db.execute("INSERT OR REPLACE INTO watchlist (id,stock_code,stock_name,industry,concepts,note) VALUES (?,?,?,?,?,?)",
                   (str(uuid.uuid4()),w.stock_code,w.stock_name,w.industry,w.concepts,w.note))
        db.commit(); return {"msg":"已加入观察池"}
    finally: db.close()

@app.delete("/api/watchlist/{wid}",dependencies=[Depends(require_auth)])
def api_del_watch(wid:str):
    db=get_db()
    try: db.execute("DELETE FROM watchlist WHERE id=?",(wid,)); db.commit(); return {"msg":"已移除"}
    finally: db.close()

# ── 复盘 ──────────────────────────────────────────────────────────────────────
class ReviewIn(BaseModel):
    review_date:str=Field(...,max_length=12)
    market_summary:str=Field(default="",max_length=2000)
    hot_sectors:str=Field(default="",max_length=500)
    reflections:str=Field(default="",max_length=2000)
    tomorrow_plan:str=Field(default="",max_length=1000)

@app.get("/api/reviews",dependencies=[Depends(require_auth)])
def api_reviews():
    db=get_db()
    try: return [dict(r) for r in db.execute("SELECT * FROM review_records ORDER BY review_date DESC LIMIT 60").fetchall()]
    finally: db.close()

@app.post("/api/reviews",dependencies=[Depends(require_auth)])
def api_add_review(rev:ReviewIn):
    db=get_db()
    try:
        db.execute("INSERT OR REPLACE INTO review_records (id,review_date,market_summary,hot_sectors,reflections,tomorrow_plan) VALUES (?,?,?,?,?,?)",
                   (str(uuid.uuid4()),rev.review_date,rev.market_summary,rev.hot_sectors,rev.reflections,rev.tomorrow_plan))
        db.commit(); return {"msg":"复盘已保存"}
    finally: db.close()

# ── AI分析 ────────────────────────────────────────────────────────────────────
class AIIn(BaseModel):
    content:str=Field(...,max_length=8000)
    task:str=Field(default="analyze",max_length=50)
    stock_code:Optional[str]=None; stock_name:Optional[str]=None
    provider:str=Field(default="deepseek",max_length=20)

@app.post("/api/ai",dependencies=[Depends(require_auth),Depends(rate_limit("ai",10))])
def api_ai(req:AIIn):
    tasks={
        "analyze":"请分析以下A股超短线机会，V6.0评分模型视角，给出操作建议：",
        "v42":"请按V4.2中线机构评分模型分析，给出中线持股建议：",
        "zhuangli":"请分析主力控盘行为，判断当前是吸筹/拉升/派发/洗盘：",
        "risk":"请评估持仓风险，给出止损止盈建议：",
        "sector":"请分析以下板块热度和短线机会：",
        "review":"请复盘以下交易，指出优缺点和改进建议：",
    }
    prompt=tasks.get(req.task,tasks["analyze"])+f"\n\n股票：{req.stock_name}({req.stock_code})\n\n"+req.content
    result=call_ai(req.provider,"你是专业A股量化分析师，A股涨红跌绿，分析简洁有据。",prompt)
    if not result: raise HTTPException(502,"AI服务暂时不可用")
    return {"result":result,"provider":req.provider}

# ── 推送 ──────────────────────────────────────────────────────────────────────
@app.get("/api/wecom-status",dependencies=[Depends(require_auth)])
def api_wecom_status():
    masked=(WECOM_WEBHOOK[:40]+"...") if len(WECOM_WEBHOOK)>40 else WECOM_WEBHOOK
    return {"enabled":bool(WECOM_WEBHOOK),"webhook_preview":masked}

@app.post("/api/wecom-test",dependencies=[Depends(require_auth)])
def api_wecom_test():
    if not WECOM_WEBHOOK: raise HTTPException(400,"企业微信未配置")
    send_wecom("🐾 旺财V7.0·连接测试","> 企业微信配置成功！止损/止盈触发时自动推送。")
    return {"msg":"测试消息已发送"}

@app.post("/api/wecom-report",dependencies=[Depends(require_auth)])
def api_wecom_report():
    if not WECOM_WEBHOOK: raise HTTPException(400,"企业微信未配置")
    save_daily_stats()
    return {"msg":"日报已发送"}

# ── 静态文件 ──────────────────────────────────────────────────────────────────
if os.path.isdir("static"):
    app.mount("/static",StaticFiles(directory="static"),name="static")

@app.get("/")
def index():
    return FileResponse("static/index.html")
