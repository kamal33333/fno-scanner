#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NSE F&O OPTIONS-BUYING SCANNER  -  v2 (institutional-grade rebuild)
===================================================================
Same strategy signals as v1 (VWAP / EMA21 + BB38 + RSI), but the statistical
engine, pricing, sizing, alerting and state handling are rebuilt:

  * R-multiple backtest (partial booking at T1, break-even trail, gap fills,
    EOD square-off, mark-to-market timeouts) instead of a binary "win" flag
  * Option ladder (SL / T1 / Target) and the underlying ladder are tied
    together with a delta-gamma map, so the backtest tests what you trade
  * Random-entry permutation test + Benjamini-Hochberg FDR across symbols
    (is the signal better than entering at random bars?)
  * Costs (brokerage, STT, exchange, GST, stamp, slippage) and theta drag
    deducted from expectancy; hard risk cap (no forced 1-lot breach)
  * Completed-bar signals only (no repainting), real expiry calendar,
    liquidity / spread / HV-percentile gates, sector + portfolio-heat caps
  * Alert de-duplication + lifecycle alerts (T1 / Target / SL / time exit)
  * Telegram-safe HTML, chunking, retry; feed-health flags in every message

Run:   python options_scanner.py             (normal / GitHub Actions)
       python options_scanner.py --selftest  (offline sanity checks)
"""
from __future__ import annotations

import calendar
import html
import io
import json
import logging
import math
import os
import re
import sys
import time
import warnings
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import pytz
import requests
import yfinance as yf
from scipy.stats import norm

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("fno")
IST = pytz.timezone("Asia/Kolkata")


# ==============================================================================
# 1. CONFIGURATION (every number can be overridden by an environment variable)
# ==============================================================================
def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _envi(name: str, default: int) -> int:
    return int(_envf(name, default))


RAW_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
RAW_CHATID = os.environ.get("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "manual")
_tm = re.search(r"(\d+:[A-Za-z0-9_-]+)", RAW_TOKEN)
TELEGRAM_BOT_TOKEN = _tm.group(1) if _tm else RAW_TOKEN.lstrip("bot").strip('"').strip("'")
TELEGRAM_CHAT_ID = RAW_CHATID
DHAN_CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "").strip().strip('"').strip("'")
DHAN_ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "").strip().strip('"').strip("'")

# --- risk & portfolio -----------------------------------------------------------
RISK_PER_TRADE = _envf("RISK_PER_TRADE", 3000)             # max Rs loss at stop, INCLUDING costs
MAX_CAPITAL_PER_TRADE = _envf("MAX_CAPITAL_PER_TRADE", 45000)
TOP_N_STOCKS = _envi("TOP_N_STOCKS", 10)
MAX_PORTFOLIO_HEAT = _envf("MAX_PORTFOLIO_HEAT", 5 * RISK_PER_TRADE)  # sum of risk of all live picks
MAX_PER_SECTOR = _envi("MAX_PER_SECTOR", 2)
MAX_INDEX_ACTIONABLE = _envi("MAX_INDEX_ACTIONABLE", 2)    # indices are highly correlated

# --- statistical gates ------------------------------------------------------------
T1_BOOK_FRAC = 0.5          # fraction booked at T1; stop of the rest moves to entry
SHRINK_K = _envf("SHRINK_K", 20)             # expectancy shrinkage toward 0: n/(n+K)
PERM_ITERS = _envi("PERM_ITERS", 2000)       # random-entry permutations
FDR_Q = _envf("FDR_Q", 0.25)                 # Benjamini-Hochberg false-discovery rate
MIN_NET_R = _envf("MIN_NET_R", 0.05)         # min expectancy (R) after theta + costs
MAX_COST_PCT_OF_RISK = _envf("MAX_COST_PCT_OF_RISK", 0.15)
MIN_STOP_FIDELITY = _envf("MIN_STOP_FIDELITY", 0.6)  # option stop vs ATR stop (see build_ladder)
MAX_HV_PCTL = _envf("MAX_HV_PCTL", 0.90)     # swing: skip when HV20 percentile is extreme
MAX_SPREAD_PCT = _envf("MAX_SPREAD_PCT", 0.06)
MIN_OPT_OI_VALUE = _envf("MIN_OPT_OI_VALUE", 1_000_000)  # open interest x LTP, Rs; 0 disables
INTRA_LAST_ENTRY = os.environ.get("INTRA_LAST_ENTRY", "13:30")  # last 15m bar START allowed for entries

# --- costs (verify against your broker contract note; override via env) -----------
RISK_FREE_RATE = 0.068
BROKERAGE_PER_ORDER = _envf("BROKERAGE_PER_ORDER", 20)
STT_OPT_SELL = _envf("STT_OPT_SELL", 0.0015)       # on sell premium - VERIFY current rate
EXCH_TXN_PCT = _envf("EXCH_TXN_PCT", 0.00035)
STAMP_BUY = _envf("STAMP_BUY", 0.00003)
SLIPPAGE_PCT = _envf("SLIPPAGE_PCT", 0.005)        # per side, used when no live bid/ask
TICK = 0.05

# --- calendar (VERIFY: NSE/BSE change expiry days from time to time) --------------
NSE_EXPIRY_WEEKDAY = _envi("NSE_EXPIRY_WEEKDAY", 1)   # 0=Mon .. 1=Tue
BSE_EXPIRY_WEEKDAY = _envi("BSE_EXPIRY_WEEKDAY", 3)   # 3=Thu (SENSEX)
WEEKLY_EXPIRY_NAMES = set(os.environ.get("WEEKLY_EXPIRY_NAMES", "NIFTY,SENSEX").split(","))
NSE_HOLIDAYS = {d.strip() for d in os.environ.get("NSE_HOLIDAYS", "").split(",") if d.strip()}  # YYYY-MM-DD
SKIP_SYMBOLS = {s.strip().upper() for s in os.environ.get("SKIP_SYMBOLS", "").split(",") if s.strip()}

STATE_FILE = os.environ.get("SCANNER_STATE_FILE", "scanner_state.json")


@dataclass(frozen=True)
class TFConfig:
    key: str
    title: str
    is_intraday: bool
    opt_rr: float        # locked OPTION reward:risk for the final target
    t1_rr: float         # option R at which 50% is booked
    atr_mult: float      # underlying stop = ATR * atr_mult (before option-level clamp)
    max_bars: int
    min_setups: int
    min_dte: int
    bars_per_day: float
    min_pct_sl: float    # option stop as % of premium, lower clamp
    max_pct_sl: float    # option stop as % of premium, upper clamp
    cooldown_h: float
    min_bars: int


INTRA = TFConfig("intra", "15M INTRADAY F&O SCALPS (LOCKED 1:2.0 RR)", True, 2.0, 1.0, 0.9, 12,
                 _envi("INTRA_MIN_SETUPS", 20), _envi("INTRA_MIN_DTE", 1), 25.0, 0.14, 0.22, 8.0, 80)
SWING = TFConfig("swing", "3-5D POSITIONAL F&O (LOCKED 1:2.5 RR)", False, 2.5, 1.25, 1.0, 6,
                 _envi("SWING_MIN_SETUPS", 20), _envi("SWING_MIN_DTE", 7), 1.0, 0.18, 0.28, 72.0, 200)


# ==============================================================================
# 2. UNIVERSE (hardcoded values are only a fallback - see apply_master_overrides)
# ==============================================================================
FNO_UNIVERSE = {
    "^NSEI":                {"name": "NIFTY",      "nse": "NIFTY",      "lot": 65,   "step": 50,  "is_idx": True,  "dhan_id": 13,  "dhan_seg": "IDX_I"},
    "^NSEBANK":             {"name": "BANKNIFTY",  "nse": "BANKNIFTY",  "lot": 30,   "step": 100, "is_idx": True,  "dhan_id": 25,  "dhan_seg": "IDX_I"},
    "^BSESN":               {"name": "SENSEX",     "nse": "SENSEX",     "lot": 20,   "step": 100, "is_idx": True,  "dhan_id": 51,  "dhan_seg": "IDX_I", "bse": True},
    "NIFTY_MID_SELECT.NS":  {"name": "MIDCPNIFT",  "nse": "MIDCPNIFTY", "lot": 120,  "step": 25,  "is_idx": True,  "dhan_id": 442, "dhan_seg": "IDX_I"},
    "NIFTY_FIN_SERVICE.NS": {"name": "FINNIFTY",   "nse": "FINNIFTY",   "lot": 65,   "step": 50,  "is_idx": True,  "dhan_id": 27,  "dhan_seg": "IDX_I"},
    "^CNXIT":               {"name": "NIFTYIT",    "nse": "NIFTYIT",    "lot": 25,   "step": 100, "is_idx": True,  "dhan_id": 19,  "dhan_seg": "IDX_I"},

    "ETERNAL.NS":    {"name": "ETERNAL",    "nse": "ETERNAL",    "lot": 2000, "step": 5,   "is_idx": False},
    "ANGELONE.NS":   {"name": "ANGELONE",   "nse": "ANGELONE",   "lot": 200,  "step": 50,  "is_idx": False},
    "TECHM.NS":      {"name": "TECHM",      "nse": "TECHM",      "lot": 600,  "step": 20,  "is_idx": False},
    "FEDERALBNK.NS": {"name": "FEDERALBNK", "nse": "FEDERALBNK", "lot": 5000, "step": 2.5, "is_idx": False},
    "WIPRO.NS":      {"name": "WIPRO",      "nse": "WIPRO",      "lot": 3000, "step": 2.5, "is_idx": False},
    "LTF.NS":        {"name": "LTF",        "nse": "LTF",        "lot": 4462, "step": 2.5, "is_idx": False},
    "CROMPTON.NS":   {"name": "CROMPTON",   "nse": "CROMPTON",   "lot": 1800, "step": 5,   "is_idx": False},
    "PERSISTENT.NS": {"name": "PERSISTENT", "nse": "PERSISTENT", "lot": 100,  "step": 100, "is_idx": False},
    "TCS.NS":        {"name": "TCS",        "nse": "TCS",        "lot": 175,  "step": 50,  "is_idx": False},
    "OBEROIRLTY.NS": {"name": "OBEROIRLTY", "nse": "OBEROIRLTY", "lot": 350,  "step": 20,  "is_idx": False},
    "DIXON.NS":      {"name": "DIXON",      "nse": "DIXON",      "lot": 50,   "step": 200, "is_idx": False},
    "DLF.NS":        {"name": "DLF",        "nse": "DLF",        "lot": 825,  "step": 10,  "is_idx": False},
    "BHARTIARTL.NS": {"name": "BHARTIARTL", "nse": "BHARTIARTL", "lot": 475,  "step": 20,  "is_idx": False},
    "AXISBANK.NS":   {"name": "AXISBANK",   "nse": "AXISBANK",   "lot": 625,  "step": 20,  "is_idx": False},
    "KOTAKBANK.NS":  {"name": "KOTAKBANK",  "nse": "KOTAKBANK",  "lot": 400,  "step": 20,  "is_idx": False},
    "MARUTI.NS":     {"name": "MARUTI",     "nse": "MARUTI",     "lot": 50,   "step": 100, "is_idx": False},
    "M&M.NS":        {"name": "M&M",        "nse": "M&M",        "lot": 175,  "step": 50,  "is_idx": False},
    "LT.NS":         {"name": "LT",         "nse": "LT",         "lot": 150,  "step": 50,  "is_idx": False},
    "INFY.NS":       {"name": "INFY",       "nse": "INFY",       "lot": 400,  "step": 20,  "is_idx": False},
    "AUROPHARMA.NS": {"name": "AUROPHARMA", "nse": "AUROPHARMA", "lot": 550,  "step": 20,  "is_idx": False},
    "ZYDUSLIFE.NS":  {"name": "ZYDUSLIFE",  "nse": "ZYDUSLIFE",  "lot": 900,  "step": 20,  "is_idx": False},
    "RBLBANK.NS":    {"name": "RBLBANK",    "nse": "RBLBANK",    "lot": 2500, "step": 5,   "is_idx": False},
    "CONCOR.NS":     {"name": "CONCOR",     "nse": "CONCOR",     "lot": 1000, "step": 10,  "is_idx": False},
    "VOLTAS.NS":     {"name": "VOLTAS",     "nse": "VOLTAS",     "lot": 375,  "step": 20,  "is_idx": False},
    "TITAN.NS":      {"name": "TITAN",      "nse": "TITAN",      "lot": 175,  "step": 50,  "is_idx": False},
    "ABB.NS":        {"name": "ABB",        "nse": "ABB",        "lot": 125,  "step": 100, "is_idx": False},
    "RELIANCE.NS":   {"name": "RELIANCE",   "nse": "RELIANCE",   "lot": 500,  "step": 20,  "is_idx": False},
    "HDFCBANK.NS":   {"name": "HDFCBANK",   "nse": "HDFCBANK",   "lot": 550,  "step": 20,  "is_idx": False},
    "ICICIBANK.NS":  {"name": "ICICIBANK",  "nse": "ICICIBANK",  "lot": 700,  "step": 20,  "is_idx": False},
    "SBIN.NS":       {"name": "SBIN",       "nse": "SBIN",       "lot": 750,  "step": 10,  "is_idx": False},
    "TATASTEEL.NS":  {"name": "TATASTEEL",  "nse": "TATASTEEL",  "lot": 5500, "step": 2.5, "is_idx": False},
    "BAJFINANCE.NS": {"name": "BAJFINANCE", "nse": "BAJFINANCE", "lot": 125,  "step": 100, "is_idx": False},
    "HAL.NS":        {"name": "HAL",        "nse": "HAL",        "lot": 150,  "step": 50,  "is_idx": False},
    "BEL.NS":        {"name": "BEL",        "nse": "BEL",        "lot": 2850, "step": 5,   "is_idx": False},
    "TRENT.NS":      {"name": "TRENT",      "nse": "TRENT",      "lot": 100,  "step": 100, "is_idx": False},
    "CAMS.NS":       {"name": "CAMS",       "nse": "CAMS",       "lot": 150,  "step": 50,  "is_idx": False},
    "NTPC.NS":       {"name": "NTPC",       "nse": "NTPC",       "lot": 1500, "step": 5,   "is_idx": False},
    "ADANIPORTS.NS": {"name": "ADANIPORTS", "nse": "ADANIPORTS", "lot": 400,  "step": 20,  "is_idx": False},
    "INDHOTEL.NS":   {"name": "INDHOTEL",   "nse": "INDHOTEL",   "lot": 1000, "step": 10,  "is_idx": False},
    "MOTHERSON.NS":  {"name": "MOTHERSON",  "nse": "MOTHERSON",  "lot": 4100, "step": 2.5, "is_idx": False},
    "NYKAA.NS":      {"name": "NYKAA",      "nse": "NYKAA",      "lot": 3125, "step": 5,   "is_idx": False},
    "SUPREMEIND.NS": {"name": "SUPREMEIND", "nse": "SUPREMEIND", "lot": 175,  "step": 50,  "is_idx": False},
}

# Sector buckets drive the correlation cap (MAX_PER_SECTOR). Unlisted names -> "OTHER".
SECTOR = {
    "FEDERALBNK": "BANK", "AXISBANK": "BANK", "KOTAKBANK": "BANK", "HDFCBANK": "BANK",
    "ICICIBANK": "BANK", "SBIN": "BANK", "RBLBANK": "BANK",
    "ANGELONE": "FIN", "LTF": "FIN", "BAJFINANCE": "FIN", "CAMS": "FIN",
    "TECHM": "IT", "PERSISTENT": "IT", "TCS": "IT", "INFY": "IT", "WIPRO": "IT",
    "MARUTI": "AUTO", "M&M": "AUTO", "MOTHERSON": "AUTO",
    "AUROPHARMA": "PHARMA", "ZYDUSLIFE": "PHARMA", "TATASTEEL": "METAL",
    "HAL": "DEFENCE", "BEL": "DEFENCE", "ABB": "CAPGOODS", "LT": "CAPGOODS",
    "OBEROIRLTY": "REALTY", "DLF": "REALTY", "RELIANCE": "ENERGY", "NTPC": "ENERGY",
    "ETERNAL": "CONSUMER", "NYKAA": "CONSUMER", "TRENT": "CONSUMER", "TITAN": "CONSUMER",
    "INDHOTEL": "CONSUMER", "DIXON": "CONSUMER", "CROMPTON": "CONSUMER", "VOLTAS": "CONSUMER",
    "SUPREMEIND": "INDUSTRIAL", "BHARTIARTL": "TELECOM", "CONCOR": "LOGISTICS", "ADANIPORTS": "LOGISTICS",
}


# ==============================================================================
# 3. CALENDAR / EXPIRY
# ==============================================================================
def last_weekday_of_month(y: int, m: int, wd: int) -> date:
    d = date(y, m, calendar.monthrange(y, m)[1])
    while d.weekday() != wd:
        d -= timedelta(days=1)
    return d


def next_expiry(today: date, wd: int, monthly: bool, min_dte: int) -> date:
    """First expiry on weekday `wd` with at least `min_dte` calendar days left."""
    if monthly:
        y, m = today.year, today.month
        e = today
        for _ in range(4):
            e = last_weekday_of_month(y, m, wd)
            if (e - today).days >= min_dte:
                return e
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return e
    d = today + timedelta(days=(wd - today.weekday()) % 7)
    while (d - today).days < min_dte:
        d += timedelta(days=7)
    return d


def model_expiry(meta: dict, tfc: TFConfig, today: date) -> date:
    wd = BSE_EXPIRY_WEEKDAY if meta.get("bse") else NSE_EXPIRY_WEEKDAY
    monthly = meta["name"] not in WEEKLY_EXPIRY_NAMES and meta["nse"] not in WEEKLY_EXPIRY_NAMES
    return next_expiry(today, wd, monthly, tfc.min_dte)


def is_market_hours(now: datetime) -> bool:
    mins = now.hour * 60 + now.minute
    return now.weekday() < 5 and (9 * 60 + 15) <= mins <= (15 * 60 + 30)


# ==============================================================================
# 4. PRICING: Black-Scholes (fallback), delta-gamma ladder, costs
# ==============================================================================
def black_scholes(S: float, K: float, T: float, r: float, sigma: float, opt: str):
    """Returns (price, |delta|, gamma, |theta| per calendar day)."""
    T = max(T, 1.0 / 365.0)
    sigma = min(max(sigma, 0.12), 1.2)
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sq)
    d2 = d1 - sigma * sq
    pdf, disc = norm.pdf(d1), math.exp(-r * T)
    gamma = pdf / (S * sigma * sq)
    if opt == "CE":
        price = S * norm.cdf(d1) - K * disc * norm.cdf(d2)
        delta = norm.cdf(d1)
        theta = -(S * pdf * sigma) / (2 * sq) - r * K * disc * norm.cdf(d2)
    else:
        price = K * disc * norm.cdf(-d2) - S * norm.cdf(-d1)
        delta = -norm.cdf(-d1)
        theta = -(S * pdf * sigma) / (2 * sq) + r * K * disc * norm.cdf(-d2)
    return max(float(price), 0.5), abs(float(delta)), float(gamma), abs(float(theta)) / 365.0


def tick(x: float) -> float:
    return round(round(x / TICK) * TICK, 2)


def solve_move(delta: float, gamma: float, pnl: float, gain: bool) -> float:
    """Underlying move m>0 that produces option P&L `pnl` under a delta-gamma Taylor map.
    gain:  delta*m + 0.5*gamma*m^2 = pnl     loss: delta*m - 0.5*gamma*m^2 = pnl"""
    if gamma < 1e-9:
        return pnl / delta
    if gain:
        return (-delta + math.sqrt(delta * delta + 2 * gamma * pnl)) / gamma
    disc = delta * delta - 2 * gamma * pnl
    return delta / gamma if disc <= 0 else (delta - math.sqrt(disc)) / gamma


@dataclass
class Ladder:
    entry: float
    sl: float
    t1: float
    tgt: float
    risk: float        # option risk per unit (entry - sl)
    ds: float          # underlying stop distance implied by the option stop
    d1: float          # underlying distance to T1
    dt: float          # underlying distance to target
    fidelity: float    # ds / (ATR*mult): <1 means the option stop is tighter than the tested stop


def build_ladder(entry: float, delta: float, gamma: float, atr: float, tfc: TFConfig) -> Ladder:
    """Option SL/T1/Target (same clamps as v1) AND the exact underlying distances they imply.
    The backtest runs on those distances, so tested ladder == traded ladder."""
    entry = max(tick(entry), TICK)
    und_risk = atr * tfc.atr_mult
    raw = max(delta * und_risk - 0.5 * gamma * und_risk ** 2, 1e-6)
    risk = min(entry * tfc.max_pct_sl, max(entry * tfc.min_pct_sl, raw))
    sl = max(tick(entry - risk), TICK)
    actual = max(round(entry - sl, 2), TICK)
    t1 = tick(entry + actual * tfc.t1_rr)
    tgt = tick(entry + actual * tfc.opt_rr)
    ds = min(solve_move(delta, gamma, actual, False), 5 * und_risk)
    d1 = solve_move(delta, gamma, actual * tfc.t1_rr, True)
    dt = solve_move(delta, gamma, actual * tfc.opt_rr, True)
    return Ladder(entry, sl, t1, tgt, actual, ds, d1, dt, ds / und_risk)


def round_trip_cost(premium: float, qty: int, slip_pct: float) -> float:
    """Approximate all-in round-trip cost in Rs for an options buy+sell at the same premium."""
    b = s = premium * qty
    brokerage = 2 * BROKERAGE_PER_ORDER
    stt = STT_OPT_SELL * s
    exch = EXCH_TXN_PCT * (b + s)
    sebi = 1e-6 * (b + s)
    stamp = STAMP_BUY * b
    gst = 0.18 * (brokerage + exch + sebi)
    return brokerage + stt + exch + sebi + stamp + gst + slip_pct * (b + s)


# ==============================================================================
# 5. DHAN API v2 CLIENT (rate-limited, cached, bid/ask + liquidity aware)
# ==============================================================================
class DhanOptionEngine:
    MIN_GAP = 3.1  # Dhan allows ~1 option-chain request per 3 seconds

    def __init__(self, client_id: str, access_token: str):
        self.client_id, self.access_token = client_id, access_token
        self.base_url = "https://api.dhan.co/v2"
        self.enabled = bool(client_id and access_token)
        self.eq_sec_map: Dict[str, int] = {}
        self.master: Optional[pd.DataFrame] = None
        self._last_call = 0.0
        self._exp_cache: Dict[tuple, list] = {}
        self._oc_cache: Dict[tuple, dict] = {}
        self.session = requests.Session()
        self.auth_failed = False
        if self.enabled:
            self._load_master()

    def _headers(self):
        return {"access-token": self.access_token, "client-id": self.client_id,
                "Content-Type": "application/json", "Accept": "application/json"}

    def _load_master(self):
        try:
            r = self.session.get("https://images.dhan.co/api-data/api-scrip-master.csv", timeout=20)
            if r.status_code != 200:
                log.warning("Dhan scrip master HTTP %s", r.status_code)
                return
            df = pd.read_csv(io.StringIO(r.text), low_memory=False)
            self.master = df
            eq = df[(df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_INSTRUMENT_NAME"] == "EQUITY")]
            syms = eq["SEM_TRADING_SYMBOL"].astype(str).str.strip().str.upper()
            self.eq_sec_map = {s: int(i) for s, i in zip(syms, eq["SEM_SMST_SECURITY_ID"]) if pd.notna(i)}
            log.info("Dhan master loaded: %d NSE equity IDs", len(self.eq_sec_map))
        except Exception as e:
            log.warning("Dhan scrip master load failed: %s", e)

    def _post(self, path: str, payload: dict, tries: int = 3) -> Optional[dict]:
        for attempt in range(tries):
            wait = self.MIN_GAP - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                r = self.session.post(f"{self.base_url}{path}", headers=self._headers(), json=payload, timeout=10)
            except requests.RequestException as e:
                log.warning("Dhan %s error: %s", path, e)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in (401, 403):
                self.auth_failed = True
                log.warning("Dhan auth failed (%s) - token expired? Falling back to model pricing.", r.status_code)
                self.enabled = False
                return None
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            return None
        return None

    def _expiries(self, sec_id: int, seg: str) -> List[date]:
        key = (sec_id, seg)
        if key not in self._exp_cache:
            js = self._post("/optionchain/expirylist", {"UnderlyingScrip": int(sec_id), "UnderlyingSeg": seg})
            out = []
            for x in (js or {}).get("data", []) or []:
                try:
                    out.append(datetime.strptime(str(x)[:10], "%Y-%m-%d").date())
                except ValueError:
                    pass
            self._exp_cache[key] = sorted(out)
        return self._exp_cache[key]

    def _chain(self, sec_id: int, seg: str, expiry: date) -> dict:
        key = (sec_id, seg, expiry)
        if key not in self._oc_cache:
            js = self._post("/optionchain", {"UnderlyingScrip": int(sec_id), "UnderlyingSeg": seg,
                                              "Expiry": expiry.isoformat()})
            self._oc_cache[key] = ((js or {}).get("data") or {}).get("oc") or {}
        return self._oc_cache[key]

    def quote(self, meta: dict, atm: float, opt: str, min_dte: int, today: date) -> Optional[dict]:
        if not self.enabled:
            return None
        sec_id = meta.get("dhan_id") or self.eq_sec_map.get(meta["nse"].upper())
        seg = meta.get("dhan_seg", "NSE_EQ")
        if not sec_id:
            return None
        try:
            exps = [e for e in self._expiries(sec_id, seg) if e >= today]
            if not exps:
                return None
            # v1 used expiries[0] (can be tomorrow!). Take the first expiry with enough time left.
            expiry = next((e for e in exps if (e - today).days >= min_dte), exps[-1])
            oc = self._chain(sec_id, seg, expiry)
            if not oc:
                return None
            strikes = {float(k): k for k in oc.keys()}
            best = min(strikes, key=lambda x: abs(x - float(atm)))
            leg = (oc.get(strikes[best]) or {}).get("ce" if opt == "CE" else "pe") or {}
            ltp = float(leg.get("last_price") or 0.0)
            if ltp <= 0.5:
                return None
            bid = float(leg.get("top_bid_price") or 0.0)
            ask = float(leg.get("top_ask_price") or 0.0)
            two_sided = bid > 0 and ask >= bid
            greeks = leg.get("greeks") or {}
            delta = abs(float(greeks.get("delta") or 0.52))
            gamma = float(greeks.get("gamma") or 0.002)
            theta = abs(float(greeks.get("theta") or 0.0))
            oi = float(leg.get("oi") or 0.0)
            return {
                "entry": ask if two_sided else ltp, "ltp": ltp,
                "delta": max(delta, 0.25), "gamma": max(gamma, 1e-4),
                "theta": theta, "strike": best, "expiry": expiry, "dte": (expiry - today).days,
                "spread_pct": ((ask - bid) / ((ask + bid) / 2)) if two_sided else None,
                "oi_value": oi * ltp,
            }
        except Exception as e:
            log.warning("Dhan quote failed for %s: %s", meta["name"], e)
            return None


def apply_master_overrides(universe: Dict[str, dict], master: Optional[pd.DataFrame], today: date) -> None:
    """Replace hardcoded lot sizes / strike steps with the exchange's own numbers (best effort),
    and flag underlyings with no listed options at all."""
    if master is None or "SEM_INSTRUMENT_NAME" not in master.columns:
        return
    try:
        d = master[master["SEM_INSTRUMENT_NAME"].isin(["OPTSTK", "OPTIDX"])].copy()
        if d.empty:
            return
        d["root"] = d["SEM_TRADING_SYMBOL"].astype(str).str.split("-").str[0].str.upper()
        d["exp"] = pd.to_datetime(d["SEM_EXPIRY_DATE"], errors="coerce").dt.date
        for tk, meta in universe.items():
            rows = d[d["root"] == meta["nse"].upper()]
            if rows.empty:
                meta["no_fno"] = True
                log.warning("%s: no listed options in Dhan master - skipped", meta["name"])
                continue
            lot = pd.to_numeric(rows["SEM_LOT_UNITS"], errors="coerce").dropna()
            if not lot.empty and 0 < int(lot.mode().iloc[0]) < 1_000_000:
                new_lot = int(lot.mode().iloc[0])
                if new_lot != meta["lot"]:
                    log.info("%s lot %s -> %s", meta["name"], meta["lot"], new_lot)
                meta["lot"] = new_lot
            live = rows[rows["exp"] >= today]
            if not live.empty:
                near = live[live["exp"] == live["exp"].min()]
                strikes = np.sort(pd.to_numeric(near["SEM_STRIKE_PRICE"], errors="coerce").dropna().unique())
                diffs = np.diff(strikes)
                diffs = diffs[diffs > 0]
                if len(diffs):
                    new_step = float(pd.Series(diffs).mode().iloc[0])
                    if new_step > 0 and new_step != meta["step"]:
                        log.info("%s strike step %s -> %s", meta["name"], meta["step"], new_step)
                        meta["step"] = new_step
    except Exception as e:
        log.warning("Master override skipped: %s", e)


# ==============================================================================
# 6. NSE OI SPURTS
# ==============================================================================
def fetch_nse_oi_spurts_map() -> tuple[dict, bool]:
    base = "https://www.nseindia.com"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"{base}/market-data/oi-spurts",
        "X-Requested-With": "XMLHttpRequest",
    }
    for attempt in range(2):
        try:
            s = requests.Session()
            s.headers.update(headers)
            s.get(base, timeout=8)
            time.sleep(0.3)
            s.get(f"{base}/market-data/oi-spurts", timeout=8)
            r = s.get(f"{base}/api/live-analysis-oi-spurts-underlyings", timeout=10)
            if r.status_code == 200:
                out = {}
                for item in r.json().get("data", []):
                    sym = str(item.get("symbol", "")).strip().upper()
                    v = pd.to_numeric(item.get("avgInOI", 0.0), errors="coerce")
                    if sym and not np.isnan(v):
                        out[sym] = float(v)
                log.info("NSE OI spurts: %d underlyings", len(out))
                return out, bool(out)
        except Exception as e:
            log.warning("NSE OI fetch attempt %d failed: %s", attempt + 1, e)
        time.sleep(1.5)
    return {}, False


def classify_institutional_oi(price_chg_pct: float, oi_chg_pct: float, opt: str):
    """Returns (blocked, boosted, tag). Tags are ASCII so Telegram columns stay aligned."""
    if abs(oi_chg_pct) < 0.5:
        return False, False, "-"
    s = f"{oi_chg_pct:+.0f}%"
    if price_chg_pct >= 0.0 and oi_chg_pct >= 3.0:
        if opt == "PE" and oi_chg_pct >= 8.0 and price_chg_pct >= 0.3:
            return True, False, f"{s}LB"
        return False, opt == "CE", f"{s}LB"
    if price_chg_pct < 0.0 and oi_chg_pct >= 3.0:
        if opt == "CE" and oi_chg_pct >= 8.0 and price_chg_pct <= -0.3:
            return True, False, f"{s}SB"
        return False, opt == "PE", f"{s}SB"
    if price_chg_pct >= 0.2 and oi_chg_pct <= -2.5:
        return False, opt == "CE", f"{s}SC"
    if price_chg_pct <= -0.2 and oi_chg_pct <= -2.5:
        return False, opt == "PE", f"{s}LU"
    return False, False, f"{s}OI"


# ==============================================================================
# 7. DATA + INDICATORS
# ==============================================================================
def download(tickers: list, period: str, interval: str, tries: int = 3) -> pd.DataFrame:
    for a in range(tries):
        try:
            raw = yf.download(tickers, period=period, interval=interval, group_by="ticker",
                              progress=False, threads=True, auto_adjust=True)
            if raw is not None and not raw.empty:
                return raw
        except Exception as e:
            log.warning("yfinance %s/%s attempt %d failed: %s", period, interval, a + 1, e)
        time.sleep(2 * (a + 1))
    return pd.DataFrame()


def extract_ticker_df(raw, ticker) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    try:
        if isinstance(raw.columns, pd.MultiIndex):
            l0, l1 = raw.columns.get_level_values(0), raw.columns.get_level_values(1)
            if ticker in l0:
                df = raw[ticker].copy()
            elif ticker in l1:
                df = raw.xs(ticker, axis=1, level=1).copy()
            else:
                return pd.DataFrame()
        else:
            df = raw.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        for col in ["Open", "Close", "High", "Low"]:
            if col not in df.columns:
                return pd.DataFrame()
            if isinstance(df[col], pd.DataFrame):
                df[col] = df[col].iloc[:, 0]
        if "Volume" not in df.columns:
            df["Volume"] = 0
        else:
            if isinstance(df["Volume"], pd.DataFrame):
                df["Volume"] = df["Volume"].iloc[:, 0]
            df["Volume"] = df["Volume"].fillna(0)
        return df.dropna(subset=["Open", "Close", "High", "Low"]).copy()
    except Exception:
        return pd.DataFrame()


def to_ist_naive(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
    return df


def _rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    g = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - (100 / (1 + (g / l.replace(0, np.nan))))).fillna(50.0)


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    c = df["Close"]
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - c.shift()).abs(), (df["Low"] - c.shift()).abs()],
                   axis=1).max(axis=1)
    return tr.rolling(n).mean()


def compute_daily_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c = df["Close"]
    df["EMA21"] = c.ewm(span=21, adjust=False).mean()
    df["EMA55"] = c.ewm(span=55, adjust=False).mean()
    df["BB_SMA_38"] = c.rolling(38).mean()
    sd = c.rolling(38).std(ddof=0)
    df["BB_UP_38"] = df["BB_SMA_38"] + 2.0 * sd
    df["BB_LO_38"] = df["BB_SMA_38"] - 2.0 * sd
    df["BB_SMA_61"] = c.rolling(61).mean()
    df["RSI"] = _rsi(c)
    df["ATR"] = _atr(df)
    df["HV20"] = np.log(c / c.shift(1)).rolling(20).std() * np.sqrt(252)
    df["HV_PCT"] = df["HV20"].rolling(252, min_periods=100).rank(pct=True)
    df["CE_Signal"] = (c >= df["EMA21"] * 0.995) & (c < df["BB_UP_38"]) & (df["RSI"] >= 42) & (df["RSI"] <= 72)
    df["PE_Signal"] = (c <= df["EMA21"] * 1.005) & (c > df["BB_LO_38"]) & (df["RSI"] >= 28) & (df["RSI"] <= 58)
    df["ENTRY_OK"] = True
    return df


def compute_intraday_15m_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """df must already be IST-naive. NOTE: indices carry no volume on Yahoo, so for them the
    'VWAP' degenerates to a session mean of typical price (documented limitation)."""
    df = df.copy()
    c = df["Close"]
    tp = (df["High"] + df["Low"] + c) / 3.0
    vol = df["Volume"].replace(0, 1)
    dates = pd.Series(df.index.date, index=df.index)
    df["VWAP"] = (tp * vol).groupby(dates).cumsum() / vol.groupby(dates).cumsum()
    df["BB_SMA_38"] = c.rolling(38).mean()
    sd = c.rolling(38).std(ddof=0)
    df["BB_UP_38"] = df["BB_SMA_38"] + 2.0 * sd
    df["BB_LO_38"] = df["BB_SMA_38"] - 2.0 * sd
    df["RSI"] = _rsi(c)
    df["ATR"] = _atr(df)
    df["HV20"] = np.log(c / c.shift(1)).rolling(50).std() * np.sqrt(252 * 25)
    df["CE_Signal"] = (c >= df["VWAP"] * 0.998) & (c < df["BB_UP_38"]) & (df["RSI"] >= 45) & (df["RSI"] <= 75)
    df["PE_Signal"] = (c <= df["VWAP"] * 1.002) & (c > df["BB_LO_38"]) & (df["RSI"] >= 25) & (df["RSI"] <= 55)
    hh, mm = (int(x) for x in INTRA_LAST_ENTRY.split(":"))
    cutoff = dtime(hh, mm)
    df["ENTRY_OK"] = np.array([t <= cutoff for t in df.index.time])
    return df


def drop_forming_bar(df: pd.DataFrame, now: datetime, minutes: int) -> pd.DataFrame:
    """yfinance returns the in-progress candle; signals must only use COMPLETED bars."""
    if not df.empty and df.index[-1] + pd.Timedelta(minutes=minutes) > pd.Timestamp(now):
        return df.iloc[:-1]
    return df


def drop_partial_daily(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    if (not df.empty and df.index[-1].date() == now.date() and now.weekday() < 5
            and now.hour * 60 + now.minute < 15 * 60 + 45):
        return df.iloc[:-1]
    return df


# ==============================================================================
# 8. BACKTEST ENGINE (R-multiples, gap-aware, EOD-aware, random-entry null)
# ==============================================================================
def _simulate(i, sgn, o, h, l, c, d_s, d_1, d_t, max_bars, day_ids):
    """One trade from the close of bar i. Returns (R, exit_index, bars_held).
    R is measured in units of the stop distance d_s.
      * stop -1R (gap-aware fill)      * T1: book T1_BOOK_FRAC, stop of remainder -> entry
      * target: full ladder             * timeout / session end: mark-to-market on remainder
    Within one bar the adverse side is evaluated first (conservative)."""
    entry = c[i]
    r1, rt = d_1 / d_s, d_t / d_s
    sl, t1, tg = entry - sgn * d_s, entry + sgn * d_1, entry + sgn * d_t
    frac = T1_BOOK_FRAC
    t1_hit = False
    last_j = i
    end = min(i + max_bars, len(c) - 1)
    for j in range(i + 1, end + 1):
        if day_ids is not None and day_ids[j] != day_ids[i]:
            break                                   # square-off at previous bar's close
        last_j = j
        stop = entry if t1_hit else sl
        adverse = l[j] if sgn > 0 else h[j]
        if sgn * (adverse - stop) <= 0:
            fill = o[j] if sgn * (o[j] - stop) <= 0 else stop      # gap through stop fills at open
            rem = sgn * (fill - entry) / d_s
            return (frac * r1 + (1 - frac) * rem if t1_hit else rem), j, j - i
        fav = h[j] if sgn > 0 else l[j]
        if sgn * (fav - tg) >= 0:
            return frac * r1 + (1 - frac) * rt, j, j - i
        if not t1_hit and sgn * (fav - t1) >= 0:
            t1_hit = True
            if sgn * (adverse - entry) <= 0:        # same bar also tagged break-even: assume the worst
                return frac * r1, j, j - i
    mtm = sgn * (c[last_j] - entry) / d_s
    return (frac * r1 + (1 - frac) * mtm if t1_hit else mtm), last_j, last_j - i


@dataclass
class BTStats:
    n: int = 0
    hit: float = 0.0        # share of trades finishing with R > 0 (a random entry scores ~50%)
    exp_r: float = 0.0      # mean R (underlying, before costs/theta)
    score_r: float = 0.0    # exp_r shrunk toward 0 by n/(n+K)
    tstat: float = 0.0
    pf: float = 0.0
    avg_held: float = 0.0   # bars
    base_r: float = 0.0     # mean R of ALL eligible bars = random-entry benchmark
    p: float = 1.0          # permutation p-value: P(random entries do at least as well)


def run_backtest(df: pd.DataFrame, direction: str, ks: float, k1: float, kt: float,
                 tfc: TFConfig, seed: int = 0) -> BTStats:
    """ks/k1/kt are stop / T1 / target distances in multiples of the bar's own ATR."""
    sgn = 1 if direction == "CE" else -1
    sig = df["CE_Signal" if direction == "CE" else "PE_Signal"].values.astype(bool)
    o, h, l, c = (df[x].values.astype(float) for x in ("Open", "High", "Low", "Close"))
    atr = df["ATR"].values.astype(float)
    ok = df["ENTRY_OK"].values.astype(bool)
    n = len(df)
    day_ids = pd.factorize(pd.Series(df.index.date))[0] if tfc.is_intraday else None
    R = np.full(n, np.nan)
    X = np.zeros(n, dtype=int)
    H = np.zeros(n)
    for i in range(50, n - tfc.max_bars - 1):
        a = atr[i]
        if not (a > 0) or not ok[i]:
            continue
        R[i], X[i], H[i] = _simulate(i, sgn, o, h, l, c, a * ks, a * k1, a * kt, tfc.max_bars, day_ids)
    valid = R[~np.isnan(R)]
    if len(valid) == 0:
        return BTStats()
    rs, held, last_exit = [], [], -1
    for i in np.flatnonzero(sig & ~np.isnan(R)):        # non-overlapping signal trades
        if i <= last_exit:
            continue
        rs.append(R[i])
        held.append(H[i])
        last_exit = X[i]
    base = float(valid.mean())
    if not rs:
        return BTStats(base_r=base)
    arr = np.asarray(rs)
    m = len(arr)
    exp = float(arr.mean())
    sd = float(arr.std(ddof=1)) if m > 1 else 0.0
    pos, neg = arr[arr > 0].sum(), -arr[arr < 0].sum()
    rng = np.random.default_rng(seed)
    means = valid[rng.integers(0, len(valid), size=(PERM_ITERS, m))].mean(axis=1)
    return BTStats(
        n=m, hit=float((arr > 0).mean()), exp_r=exp, score_r=exp * m / (m + SHRINK_K),
        tstat=(exp / (sd / math.sqrt(m))) if sd > 0 else 0.0,
        pf=float(min(pos / neg, 99.0)) if neg > 0 else 99.0,
        avg_held=float(np.mean(held)), base_r=base,
        p=float((np.sum(means >= exp) + 1) / (PERM_ITERS + 1)),
    )


def bh_pass(pvals: Dict[str, float], q: float) -> set:
    """Benjamini-Hochberg: with ~46 symbols tested, some will look 'significant' by luck."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m, k = len(items), 0
    for rank, (_, p) in enumerate(items, 1):
        if p <= q * rank / m:
            k = rank
    return {key for key, _ in items[:k]}


# ==============================================================================
# 9. PER-SYMBOL EVALUATION
# ==============================================================================
@dataclass
class Ctx:
    now: datetime
    today: date
    vix: float
    oi_map: dict
    oi_ok: bool
    hv_map: dict
    hvp_map: dict
    market_open: bool
    force: bool


def evaluate_symbol(tk: str, meta: dict, df: pd.DataFrame, tfc: TFConfig, ctx: Ctx,
                    diag: Counter, dhan: Optional[DhanOptionEngine] = None) -> Optional[dict]:
    last = df.iloc[-1]
    spot, atr = float(last["Close"]), float(last["ATR"])
    if not (spot > 0 and atr > 0):
        diag["bad_data"] += 1
        return None
    ref = float(last["VWAP"] if tfc.is_intraday else last["EMA21"])
    opt = "CE" if spot >= ref else "PE"
    sgn = 1 if opt == "CE" else -1
    step = float(meta["step"])
    atm = round(spot / step) * step
    hv = ctx.hv_map.get(tk, float("nan"))
    if not np.isfinite(hv):
        hv = float(last["HV20"]) if np.isfinite(last["HV20"]) else 0.25

    quote = dhan.quote(meta, atm, opt, tfc.min_dte, ctx.today) if (dhan is not None and dhan.enabled) else None
    if quote:
        entry, delta, gamma = quote["entry"], quote["delta"], quote["gamma"]
        strike, dte, spread, live = quote["strike"], quote["dte"], quote["spread_pct"], True
        theta = quote["theta"] or entry / (2.0 * max(dte, 1))
        oi_value = quote["oi_value"]
    else:
        exp = model_expiry(meta, tfc, ctx.today)
        dte = (exp - ctx.today).days
        iv = ctx.vix * 1.15 if meta["is_idx"] else max(hv * 1.08, ctx.vix * 1.40)
        entry, delta, gamma, theta = black_scholes(spot, atm, dte / 365.0, RISK_FREE_RATE, min(iv, 0.9), opt)
        strike, spread, live, oi_value = atm, None, False, 0.0

    lad = build_ladder(entry, delta, gamma, atr, tfc)
    seed = zlib.crc32(f"{meta['name']}|{tfc.key}|{opt}".encode())
    st = run_backtest(df, opt, lad.ds / atr, lad.d1 / atr, lad.dt / atr, tfc, seed)

    # ---- sizing with a HARD cap (v1 forced >=1 lot even when it broke the risk limit)
    lot = int(meta["lot"])
    slip = (spread / 2.0) if spread else SLIPPAGE_PCT
    eff_risk_lot = lad.risk * lot + round_trip_cost(lad.entry, lot, slip)
    lots = min(int(RISK_PER_TRADE // eff_risk_lot), int(MAX_CAPITAL_PER_TRADE // (lad.entry * lot)))
    oversize = False
    if lots < 1:
        if not meta["is_idx"]:
            diag["size"] += 1
            return None
        lots, oversize = 1, True          # indices stay visible, flagged as not actionable
    qty = lots * lot
    risk_rs = lad.risk * qty
    cost_rs = round_trip_cost(lad.entry, qty, slip)
    cost_r = cost_rs / risk_rs
    hold_days = st.avg_held / tfc.bars_per_day
    theta_r = theta * hold_days / lad.risk
    net_r = st.score_r - theta_r - cost_r

    why = []
    if st.n < tfc.min_setups:
        why.append("n")
    if net_r < MIN_NET_R:
        why.append("net")
    if cost_r > MAX_COST_PCT_OF_RISK:
        why.append("cost")
    if lad.fidelity < MIN_STOP_FIDELITY:
        why.append("fid")
    if oversize:
        why.append("size")
    hvp = ctx.hvp_map.get(tk, float("nan"))
    if not tfc.is_intraday and np.isfinite(hvp) and hvp > MAX_HV_PCTL:
        why.append("hv")
    if live:
        if spread is not None and spread > MAX_SPREAD_PCT:
            why.append("spread")
        if ctx.market_open and MIN_OPT_OI_VALUE > 0 and 0 < oi_value < MIN_OPT_OI_VALUE:
            why.append("liq")

    strike_s = f"{strike:g}"
    return {
        "tk": tk, "name": meta["name"], "meta": meta, "opt": opt, "is_idx": meta["is_idx"],
        "sector": SECTOR.get(meta["name"], "OTHER"),
        "contract": f"{meta['name'][:8]} {strike_s}{opt}" + ("*" if live else ""),
        "contract_plain": f"{meta['name']} {strike_s}{opt}",
        "entry": lad.entry, "sl": lad.sl, "t1": lad.t1, "tgt": lad.tgt,
        "rr": f"1:{tfc.opt_rr:.1f}", "qty": qty, "lots": lots, "capital": int(round(lad.entry * qty)),
        "risk_rs": risk_rs, "cost_rs": cost_rs, "cost_r": cost_r, "theta_r": theta_r, "net_r": net_r,
        "st": st, "live": live, "dte": dte, "fidelity": lad.fidelity, "hvp": hvp,
        "und_entry": spot, "und_sl": spot - sgn * lad.ds, "und_t1": spot + sgn * lad.d1,
        "und_tgt": spot + sgn * lad.dt,
        # lifecycle tracking replays 15m bars AFTER this stamp; a daily bar closes at 15:30
        "t0": (df.index[-1] if tfc.is_intraday else df.index[-1] + pd.Timedelta(hours=15, minutes=30)).isoformat(),
        "why": why, "oi_tag": "-", "boost": False,
    }


def signal_context(df: pd.DataFrame, tfc: TFConfig, row: dict, ctx: Ctx):
    """Technical-signal state + OI classification for the CURRENT completed bar."""
    last = df.iloc[-1]
    col = "CE_Signal" if row["opt"] == "CE" else "PE_Signal"
    if tfc.is_intraday:
        day = df[df.index.date == df.index[-1].date()]
        day_open = float(day["Open"].iloc[0])
        chg = (float(last["Close"]) - day_open) / day_open * 100.0
        sig = (bool(last[col]) or (len(df) > 1 and bool(df.iloc[-2][col]))) and bool(last["ENTRY_OK"])
    else:
        prev = float(df["Close"].iloc[-2])
        chg = (float(last["Close"]) - prev) / prev * 100.0
        sig = bool(last[col])
    oi = ctx.oi_map.get(row["meta"]["nse"], 0.0)
    blocked, boosted, tag = classify_institutional_oi(chg, oi, row["opt"])
    return sig, blocked, boosted, tag


# ==============================================================================
# 10. TIMEFRAME SCAN + PORTFOLIO CONSTRUCTION
# ==============================================================================
@dataclass
class TFResult:
    tfc: TFConfig
    idx_rows: list = field(default_factory=list)
    stk_rows: list = field(default_factory=list)
    watch_rows: list = field(default_factory=list)
    diag: Counter = field(default_factory=Counter)
    univ_exp: float = 0.0
    univ_base: float = 0.0
    universe_n: int = 0


def scan_timeframe(tfc: TFConfig, frames: Dict[str, pd.DataFrame], universe: Dict[str, dict],
                   ctx: Ctx, dhan: DhanOptionEngine, state: dict) -> TFResult:
    res, diag = TFResult(tfc), Counter()
    res.diag = diag
    p1: Dict[str, dict] = {}
    # ---- pass 1: model pricing + backtest for EVERY symbol (cheap, no API calls)
    for tk, meta in universe.items():
        if meta.get("no_fno") or meta["name"].upper() in SKIP_SYMBOLS:
            diag["skipped"] += 1
            continue
        df = frames.get(tk)
        if df is None or len(df) < tfc.min_bars:
            diag["no_data"] += 1
            continue
        if tfc.is_intraday and ctx.market_open and not ctx.force:
            if df.index[-1] < pd.Timestamp(ctx.now) - pd.Timedelta(minutes=45):
                diag["stale"] += 1
                continue
        row = evaluate_symbol(tk, meta, df, tfc, ctx, diag, None)
        if row is None:
            continue
        p1[tk] = row
        st = row["st"]
        if st.n > 0:
            res.universe_n += st.n
            res.univ_exp += st.exp_r * st.n
            res.univ_base += st.base_r * st.n
    if res.universe_n:
        res.univ_exp /= res.universe_n
        res.univ_base /= res.universe_n

    pvals = {tk: r["st"].p for tk, r in p1.items() if r["st"].n >= tfc.min_setups}
    edge_ok = bh_pass(pvals, FDR_Q)
    diag["edge_pass"] = len(edge_ok)
    diag["tested"] = len(pvals)

    # ---- signal state, OI veto, gates
    def finalize(tk, row, sig, blocked, boosted, tag):
        row["oi_tag"], row["boost"] = tag, boosted
        why = [w for w in row["why"] if w != "edge"]
        if not sig:
            why.append("sig")
        if tk not in edge_ok:
            why.append("edge")
        if blocked and not row["is_idx"]:
            why.append("oi")
        row["why"] = why
        row["ok"] = not why

    for tk, row in p1.items():
        sig, blocked, boosted, tag = signal_context(frames[tk], tfc, row, ctx)
        finalize(tk, row, sig, blocked, boosted, tag)
        row["_sig"] = (sig, blocked, boosted, tag)
        if not row["is_idx"]:
            for w in row["why"]:
                diag[f"x_{w}"] += 1

    # ---- pass 2: re-price shortlisted rows against the LIVE option chain
    short = [r for r in p1.values() if r["is_idx"]]
    cands = sorted((r for r in p1.values() if not r["is_idx"] and r["ok"]),
                   key=lambda r: (-r["net_r"], -int(r["boost"])))
    short += cands[: TOP_N_STOCKS + 4]
    final: Dict[str, dict] = {}
    for r in short:
        tk = r["tk"]
        row = r
        if dhan is not None and dhan.enabled:
            r2 = evaluate_symbol(tk, r["meta"], frames[tk], tfc, ctx, diag, dhan)
            if r2 is not None:
                finalize(tk, r2, *r["_sig"])
                row = r2
        final[tk] = row

    # ---- portfolio construction: ranking, sector cap, heat cap, index cap
    heat, sector_ct, idx_act = 0.0, Counter(), 0
    for row in sorted((x for x in final.values() if x["is_idx"] and x["ok"]), key=lambda x: -x["net_r"]):
        if idx_act >= MAX_INDEX_ACTIONABLE or heat + row["risk_rs"] > MAX_PORTFOLIO_HEAT:
            row["ok"], row["why"] = False, row["why"] + ["cap"]
            continue
        idx_act += 1
        heat += row["risk_rs"]
    chosen = []
    for row in sorted((x for x in final.values() if not x["is_idx"] and x["ok"]),
                      key=lambda x: (-x["net_r"], -int(x["boost"]))):
        if len(chosen) >= TOP_N_STOCKS:
            break
        if sector_ct[row["sector"]] >= MAX_PER_SECTOR:
            row["ok"], row["why"] = False, row["why"] + ["sector"]
            diag["x_sector"] += 1
            continue
        if heat + row["risk_rs"] > MAX_PORTFOLIO_HEAT:
            row["ok"], row["why"] = False, row["why"] + ["heat"]
            diag["x_heat"] += 1
            continue
        sector_ct[row["sector"]] += 1
        heat += row["risk_rs"]
        chosen.append(row)

    now = ctx.now
    for row in list(final.values()):
        key = f"{tfc.key}|{row['name']}|{row['opt']}"
        sent_at = state["sent"].get(key)
        row["new"] = ctx.force or not sent_at or (now - datetime.fromisoformat(sent_at)) > timedelta(hours=tfc.cooldown_h)
        row["key"] = key
    res.idx_rows = sorted((x for x in final.values() if x["is_idx"]), key=lambda x: (-int(x["ok"]), -x["net_r"]))
    res.stk_rows = chosen
    res.watch_rows = sorted((x for x in final.values() if not x["is_idx"] and not x["ok"] and "sig" not in x["why"]),
                            key=lambda x: -x["net_r"])[:3]
    diag["selected"] = len(chosen)
    res.heat = heat  # type: ignore[attr-defined]
    return res


# ==============================================================================
# 11. STATE (de-dup + open-signal lifecycle)
# ==============================================================================
def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    st.setdefault("sent", {})
    st.setdefault("open", [])
    st.setdefault("heartbeat", "")
    return st


def save_state(state: dict, now: datetime) -> None:
    cut = now - timedelta(days=5)
    state["sent"] = {k: v for k, v in state["sent"].items() if datetime.fromisoformat(v) > cut}
    state["open"] = state["open"][-60:]
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError as e:
        log.warning("Could not save state: %s", e)


def register_signals(state: dict, rows: list, tfc: TFConfig, now: datetime) -> None:
    for r in rows:
        if not (r["ok"] and r["new"]):
            continue
        state["sent"][r["key"]] = now.isoformat()
        expires = (datetime.combine(now.date(), dtime(15, 15)) if tfc.is_intraday else now + timedelta(days=8))
        state["open"].append({
            "id": f"{r['key']}|{now:%Y%m%d%H%M}", "tk": r["tk"], "name": r["name"], "opt": r["opt"],
            "contract": r["contract_plain"], "t0": r["t0"], "t1_hit": False,
            "sl": r["und_sl"], "t1": r["und_t1"], "tgt": r["und_tgt"], "entry": r["und_entry"],
            "o_sl": r["sl"], "o_t1": r["t1"], "o_tgt": r["tgt"], "expires": expires.isoformat(),
        })


def update_open_signals(state: dict, frames15: Dict[str, pd.DataFrame], now: datetime) -> List[str]:
    """Replays 15m bars since each open signal and emits T1 / target / stop / time-exit alerts,
    using exactly the same bar logic as the backtest."""
    msgs, keep = [], []
    for s in state["open"]:
        df = frames15.get(s["tk"])
        sgn = 1 if s["opt"] == "CE" else -1
        closed = False
        name = html.escape(s["contract"])
        if df is not None and not df.empty:
            bars = df[df.index > pd.Timestamp(s["t0"])]
            for ts, b in bars.iterrows():
                stop = s["entry"] if s["t1_hit"] else s["sl"]
                adverse = b["Low"] if sgn > 0 else b["High"]
                fav = b["High"] if sgn > 0 else b["Low"]
                if sgn * (adverse - stop) <= 0:
                    if s["t1_hit"]:
                        msgs.append(f"↩️ <b>BREAKEVEN EXIT</b> {name}: remaining half back at entry - exit.")
                    else:
                        msgs.append(f"🛑 <b>STOP</b> {name}: underlying breached {s['sl']:.2f} - exit (option SL ≈ ₹{s['o_sl']:.2f}).")
                    closed = True
                elif sgn * (fav - s["tgt"]) >= 0:
                    msgs.append(f"✅ <b>TARGET</b> {name}: exit the rest (option ≈ ₹{s['o_tgt']:.2f}).")
                    closed = True
                elif not s["t1_hit"] and sgn * (fav - s["t1"]) >= 0:
                    s["t1_hit"] = True
                    msgs.append(f"🎯 <b>T1 HIT</b> {name}: book 50% (option ≈ ₹{s['o_t1']:.2f}), move stop to entry.")
                s["t0"] = ts.isoformat()
                if closed:
                    break
        if not closed and now >= datetime.fromisoformat(s["expires"]):
            msgs.append(f"⏱ <b>TIME EXIT</b> {name}: holding window ended - square off.")
            closed = True
        if not closed:
            keep.append(s)
    state["open"] = keep
    return msgs


# ==============================================================================
# 12. TELEGRAM (escaped, aligned, chunked, retried)
# ==============================================================================
COLS = [("St", 2, "<"), ("Contract", 16, "<"), ("Entry", 7, ">"), ("Qty", 6, ">"), ("Cap", 6, ">"),
        ("SL", 7, ">"), ("T1-50%", 7, ">"), ("Target", 7, ">"), ("R:R", 5, ">"), ("OI", 7, "<"),
        ("Hit%", 5, ">"), ("E[R]", 5, ">"), ("p", 4, ">")]


def _fmt_row(vals) -> str:
    return "|".join(f"{str(v)[:w]:{a}{w}}" for v, (_, w, a) in zip(vals, COLS))


def _fp(x: float) -> str:
    return f"{x:.2f}" if x < 100 else f"{x:.1f}"


def _row_line(r: dict) -> str:
    st = r["st"]
    mark = "+" if r["ok"] else ("!" if "size" in r["why"] else ".")
    p = "1.0" if st.p >= 0.995 else f"{st.p:.2f}"[1:]
    return _fmt_row([mark, r["contract"], _fp(r["entry"]), r["qty"], r["capital"], _fp(r["sl"]), _fp(r["t1"]),
                     _fp(r["tgt"]), r["rr"], r["oi_tag"], f"{st.hit * 100:.0f}%", f"{r['net_r']:+.2f}", p])


def _table(rows: list) -> list:
    hdr = _fmt_row([c[0] for c in COLS])
    return ["<pre>", html.escape(hdr), "-" * len(hdr)] + [html.escape(_row_line(r)) for r in rows] + ["</pre>"]


def format_message(res: TFResult, now_str: str, dhan_on: bool, ctx: Ctx, show_rows: bool = True) -> str:
    tfc = res.tfc
    feed = ("Dhan live" if dhan_on else "Black-Scholes model") + (" + NSE OI" if ctx.oi_ok else " | OI feed DOWN")
    lines = [f"<b>🔥 {html.escape(tfc.title)}</b>",
             f"🕒 <code>{now_str}</code> | Feed: <code>{html.escape(feed)}</code> | VIX <code>{ctx.vix * 100:.1f}</code>",
             f"Risk/trade <code>₹{RISK_PER_TRADE:,.0f}</code> (incl. costs) | Live heat <code>₹{getattr(res, 'heat', 0):,.0f}</code>/<code>₹{MAX_PORTFOLIO_HEAT:,.0f}</code>", ""]
    if res.idx_rows:
        lines.append("<b>📌 MAJOR INDICES</b>  (+ = actionable, . = watch only)")
        lines += _table(res.idx_rows)
    if res.stk_rows:
        lines.append("")
        lines.append(f"<b>🚀 TOP {len(res.stk_rows)} STOCK OPTIONS</b>  (ranked by net expectancy after theta + costs)")
        lines += _table(res.stk_rows)
    elif show_rows and res.watch_rows:
        lines.append("<b>👀 No stock passed every gate. Closest:</b>")
        lines += _table(res.watch_rows)
        lines.append("<i>why: " + html.escape("; ".join(f"{r['name']}={','.join(r['why'])}" for r in res.watch_rows)) + "</i>")
    d = res.diag
    lines.append("")
    lines.append(f"<i>Universe edge: signal <code>{res.univ_exp:+.3f}R</code> vs random-entry <code>{res.univ_base:+.3f}R</code> "
                 f"| FDR-passed {d['edge_pass']}/{d['tested']} | no-data {d['no_data']} stale {d['stale']}</i>")
    lines.append("<i>* live quote | LB long-build SB short-build SC short-cover LU long-unwind | Hit% ≈ 50 is random | "
                 "E[R] = expectancy in R net of theta+costs | p = chance random entries match the signal</i>")
    return "\n".join(lines)


def _chunk_html(text: str, limit: int = 3900) -> List[str]:
    out, cur, in_pre = [], [], False
    size = 0
    for line in text.split("\n"):
        if size + len(line) + 1 > limit and cur:
            if in_pre:
                cur.append("</pre>")
            out.append("\n".join(cur))
            cur, size = (["<pre>"] if in_pre else []), 0
        cur.append(line)
        size += len(line) + 1
        if line == "<pre>":
            in_pre = True
        elif line == "</pre>":
            in_pre = False
    if cur:
        out.append("\n".join(cur))
    return out


def send_telegram_alert(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or ":" not in TELEGRAM_BOT_TOKEN:
        print(re.sub(r"</?(b|i|code|pre)>", "", html.unescape(text)))
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for part in _chunk_html(text):
        for attempt in range(3):
            try:
                r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": part, "parse_mode": "HTML",
                                             "disable_web_page_preview": True}, timeout=15)
            except requests.RequestException as e:
                log.warning("Telegram network error: %s", e)
                time.sleep(2)
                continue
            if r.status_code == 200:
                break
            if r.status_code == 429:
                time.sleep(float(r.json().get("parameters", {}).get("retry_after", 3)) + 0.5)
                continue
            log.error("Telegram API error %s: %s", r.status_code, r.text[:200])
            break


def get_live_vix() -> float:
    for _ in range(2):
        try:
            v = yf.download("^INDIAVIX", period="5d", interval="1d", progress=False, auto_adjust=True)
            if not v.empty:
                col = v["Close"]
                col = col.iloc[:, 0] if isinstance(col, pd.DataFrame) else col
                return float(col.dropna().iloc[-1]) / 100.0
        except Exception:
            pass
        time.sleep(1)
    return 0.135


# ==============================================================================
# 13. MAIN
# ==============================================================================
def main() -> None:
    now_ist = datetime.now(IST)
    now = now_ist.replace(tzinfo=None)
    now_str = now_ist.strftime("%d-%b %I:%M %p IST")
    mins = now.hour * 60 + now.minute
    force = EVENT_NAME != "schedule"                         # manual runs always report
    if EVENT_NAME == "schedule" and (now.weekday() >= 5 or mins < 9 * 60 + 15 or mins > 15 * 60 + 35
                                     or now.strftime("%Y-%m-%d") in NSE_HOLIDAYS):
        log.info("Outside market hours / holiday (%s). Exiting.", now_str)
        return

    state = load_state()
    dhan = DhanOptionEngine(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN)
    universe = {k: dict(v) for k, v in FNO_UNIVERSE.items()}
    apply_master_overrides(universe, dhan.master, now.date())
    tickers = [k for k, m in universe.items() if not m.get("no_fno")]

    vix = get_live_vix()
    oi_map, oi_ok = fetch_nse_oi_spurts_map()

    # ---- data (daily first: HV / HV-percentile feed the pricing of both timeframes)
    raw_1d = download(tickers, "5y", "1d")
    frames1d, hv_map, hvp_map = {}, {}, {}
    for tk in tickers:
        df = extract_ticker_df(raw_1d, tk)
        if len(df) < 200:
            continue
        df = drop_partial_daily(to_ist_naive(df), now)
        if len(df) < 200:
            continue
        df = compute_daily_indicators(df)
        frames1d[tk] = df
        hv_map[tk] = float(df["HV20"].iloc[-1])
        hvp_map[tk] = float(df["HV_PCT"].iloc[-1]) if np.isfinite(df["HV_PCT"].iloc[-1]) else float("nan")

    raw_15m = download(tickers, "60d", "15m")
    frames15 = {}
    for tk in tickers:
        df = extract_ticker_df(raw_15m, tk)
        if len(df) < 80:
            continue
        df = drop_forming_bar(to_ist_naive(df), now, 15)
        if len(df) < 80:
            continue
        frames15[tk] = compute_intraday_15m_indicators(df)

    ctx = Ctx(now=now, today=now.date(), vix=vix, oi_map=oi_map, oi_ok=oi_ok, hv_map=hv_map, hvp_map=hvp_map,
              market_open=is_market_hours(now), force=force)
    log.info("%s | Dhan: %s | OI records: %d | VIX %.2f%% | daily %d / 15m %d symbols",
             now_str, "ON" if dhan.enabled else "model", len(oi_map), vix * 100, len(frames1d), len(frames15))

    sent_any = False
    # ---- lifecycle alerts for signals that are already open
    life = update_open_signals(state, frames15, now)
    if life:
        send_telegram_alert(f"<b>📡 POSITION UPDATES</b> <code>{now_str}</code>\n" + "\n".join(life))
        sent_any = True

    # ---- new signals
    results = []
    intraday_window = force or (ctx.market_open and mins <= int(INTRA_LAST_ENTRY.split(":")[0]) * 60
                                + int(INTRA_LAST_ENTRY.split(":")[1]) + 15)
    if frames15 and intraday_window:
        results.append(scan_timeframe(INTRA, frames15, universe, ctx, dhan, state))
    if frames1d:
        results.append(scan_timeframe(SWING, frames1d, universe, ctx, dhan, state))

    for res in results:
        has_new = any(r["ok"] and r["new"] for r in res.idx_rows + res.stk_rows)
        if has_new or force:
            send_telegram_alert(format_message(res, now_str, dhan.enabled, ctx))
            sent_any = True
        register_signals(state, res.idx_rows + res.stk_rows, res.tfc, now)
        log.info("%s: %s", res.tfc.key, dict(res.diag))

    # ---- one heartbeat per day on scheduled runs so silence never means 'broken'
    today_s = now.strftime("%Y-%m-%d")
    if not sent_any and state.get("heartbeat") != today_s and results:
        d = results[-1].diag
        send_telegram_alert(
            f"<b>ℹ️ F&amp;O SCANNER HEARTBEAT</b> <code>{now_str}</code>\nRunning normally - no new actionable setups. "
            f"Dhan: {'live' if dhan.enabled else 'model' + (' (auth failed)' if dhan.auth_failed else '')} | "
            f"OI feed: {'ok' if oi_ok else 'DOWN'}\n<i>Universe edge: signal {results[-1].univ_exp:+.3f}R vs random "
            f"{results[-1].univ_base:+.3f}R | FDR-passed {d['edge_pass']}/{d['tested']}</i>")
        state["heartbeat"] = today_s
    save_state(state, now)


# ==============================================================================
# 14. OFFLINE SELF-TEST
# ==============================================================================
def selftest() -> None:
    rng = np.random.default_rng(7)

    def mk(drift: float, n: int = 1500) -> pd.DataFrame:
        r = rng.normal(drift, 0.01, n)
        close = 100 * np.exp(np.cumsum(r))
        open_ = np.r_[close[0], close[:-1]]
        hi = np.maximum.reduce([open_, close]) * (1 + np.abs(rng.normal(0, 0.004, n)))
        lo = np.minimum.reduce([open_, close]) * (1 - np.abs(rng.normal(0, 0.004, n)))
        return pd.DataFrame({"Open": open_, "High": hi, "Low": lo, "Close": close, "Volume": 1e6},
                            index=pd.bdate_range("2021-01-01", periods=n))

    print("Backtest honesty check (1R stop, T1 at 1R, target 2R, daily bars)")
    for label, drift in [("random walk", 0.0), ("trending +0.15%/day", 0.0015)]:
        df = compute_daily_indicators(mk(drift))
        st = run_backtest(df, "CE", 1.0, 1.0, 2.0, SWING, seed=1)
        print(f"  {label:22s} n={st.n:3d} hit={st.hit*100:4.1f}% E[R]={st.exp_r:+.3f} "
              f"random-entry={st.base_r:+.3f} p={st.p:.3f}")
    print("Expiry calendar: 2026-10-18 monthly(Tue,min 7d) ->", next_expiry(date(2026, 10, 18), 1, True, 7),
          "| weekly(Tue,min 1d) ->", next_expiry(date(2026, 10, 18), 1, False, 1))
    lad = build_ladder(10.0, 0.52, 0.01, 12.0, INTRA)
    print("Ladder demo:", lad)
    print("Round-trip cost on Rs 15,000 premium:", round(round_trip_cost(10.0, 1500, SLIPPAGE_PCT), 1))
    print("BH demo:", bh_pass({"a": 0.001, "b": 0.04, "c": 0.5, "d": 0.9}, 0.25))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
