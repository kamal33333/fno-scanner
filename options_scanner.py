import os
import re
import io
import sys
import time
import html
import warnings
import calendar
from datetime import datetime, date, timedelta

warnings.filterwarnings("ignore")

import pytz
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from scipy.stats import norm

# ==============================================================================
# 🔐 1. CREDENTIALS & ENVIRONMENT VARIABLES (AUTO-SANITIZED)
# ==============================================================================
RAW_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
RAW_CHATID = os.environ.get("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "manual")

token_match = re.search(r"(\d+:[A-Za-z0-9_-]+)", RAW_TOKEN)
TELEGRAM_BOT_TOKEN = token_match.group(1) if token_match else RAW_TOKEN.lstrip("bot").strip('"').strip("'")
TELEGRAM_CHAT_ID   = RAW_CHATID

DHAN_CLIENT_ID    = os.environ.get("DHAN_CLIENT_ID", "").strip().strip('"').strip("'")
DHAN_ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "").strip().strip('"').strip("'")

# ==============================================================================
# ⚖️ 2. INSTITUTIONAL RISK, EXPIRY (15+ DTE) & EXPECTANCY PARAMETERS
# ==============================================================================
RISK_PER_TRADE         = 3000     # Strict Max Rupee risk per stock option trade (₹3,000 hard cap)
INDEX_POS_MAX_RISK     = 5500     # Max 1-lot risk buffer for 15+ DTE Positional Index Options
MAX_CAPITAL_PER_TRADE  = 45000    # Strict Max Option Premium outlay (₹45,000 hard cap)
TOP_N_STOCKS           = 10       # Top 10 Stock Option setups per scan
COST_SLIPPAGE_R        = 0.05     # 0.05R (~₹150) brokerage + STT + slippage deducted per trade

# Expiry DTE Rules
MIN_DTE_INTRADAY       = 4        # Minimum 4 DTE for Intraday Scalps (Avoids 0-2 DTE theta crush)
MIN_DTE_POSITIONAL     = 15       # Minimum 15+ DTE for Positional & Index Swings

# Intraday 15-Minute Scalp Parameters (Locked 1:2.0 Option RR, T1 at +1.0R)
INTRA_OPT_RR           = 2.0      # Locked 1:2.0 Option Risk:Reward
INTRA_T1_RR            = 1.0      # Book 50% at +1.0R and trail SL to Entry
INTRA_UND_RR           = 1.6      # Underlying ATR target multiple
INTRA_ATR_SL_MULT      = 0.9      # 0.9x ATR stop loss
INTRA_MAX_BARS         = 12       # 12 bars intraday window (mark-to-market if session ends)
INTRA_MIN_SETUPS       = 10       # Minimum historical sample size
INTRA_MIN_EXP_R        = 0.00     # Minimum Net R-Expectancy after costs

# Positional 3-5 Day Swing F&O Parameters (Locked 1:2.5 Option RR, T1 at +1.25R, 15+ DTE)
SWING_OPT_RR           = 2.5      # Locked 1:2.5 Option Risk:Reward
SWING_T1_RR            = 1.25     # Book 50% at +1.25R and trail SL to Entry
SWING_UND_RR           = 1.8      # Underlying ATR target multiple
SWING_ATR_SL_MULT      = 1.0      # 1.0x Daily ATR stop loss
SWING_MAX_DAYS         = 6        # 6 trading days hold window on 15+ DTE contract
SWING_MIN_SETUPS       = 10       # Minimum 5-year historical sample size
SWING_MIN_EXP_R        = 0.02     # Minimum Net R-Expectancy after costs

RISK_FREE_RATE         = 0.068

# ==============================================================================
# 📋 3. FULL REAL NSE/BSE SYMBOLS & VERIFIED 2026 LOT SIZES
# ==============================================================================
# exp_rule:
#   - "NSE_WEEKLY_TUE": NIFTY (has weekly Tuesdays + monthly last Tuesday)
#   - "NSE_MONTHLY_TUE": All NSE Stock Options + BANKNIFTY/FINNIFTY/MIDCPNIFTY (Last Tuesday of Month)
#   - "BSE_WEEKLY_THU": SENSEX (Weekly Thursdays + monthly last Thursday)
FNO_UNIVERSE = {
    # --- 5 LIQUID TRADABLE INDIAN INDEX OPTIONS (FULL REAL NAMES) ---
    "^NSEI":                 {"name": "NIFTY",      "nse": "NIFTY",      "lot": 65,   "step": 50,  "is_idx": True,  "dhan_id": 13,  "dhan_seg": "IDX_I", "exp_rule": "NSE_WEEKLY_TUE"},
    "^NSEBANK":              {"name": "BANKNIFTY",  "nse": "BANKNIFTY",  "lot": 30,   "step": 100, "is_idx": True,  "dhan_id": 25,  "dhan_seg": "IDX_I", "exp_rule": "NSE_MONTHLY_TUE"},
    "^BSESN":                {"name": "SENSEX",     "nse": "SENSEX",     "lot": 20,   "step": 100, "is_idx": True,  "dhan_id": 51,  "dhan_seg": "IDX_I", "exp_rule": "BSE_WEEKLY_THU"},
    "NIFTY_MID_SELECT.NS":   {"name": "MIDCPNIFTY", "nse": "MIDCPNIFTY", "lot": 120,  "step": 25,  "is_idx": True,  "dhan_id": 442, "dhan_seg": "IDX_I", "exp_rule": "NSE_MONTHLY_TUE"},
    "NIFTY_FIN_SERVICE.NS":  {"name": "FINNIFTY",   "nse": "FINNIFTY",   "lot": 65,   "step": 50,  "is_idx": True,  "dhan_id": 27,  "dhan_seg": "IDX_I", "exp_rule": "NSE_MONTHLY_TUE"},

    # --- LIQUID NSE F&O STOCKS (FULL REAL OFFICIAL NSE SYMBOLS, MONTHLY EXPIRY ONLY) ---
    "ETERNAL.NS":     {"name": "ETERNAL",    "nse": "ETERNAL",    "lot": 2000, "step": 5,   "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "ANGELONE.NS":    {"name": "ANGELONE",   "nse": "ANGELONE",   "lot": 200,  "step": 50,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "TECHM.NS":       {"name": "TECHM",      "nse": "TECHM",      "lot": 600,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "FEDERALBNK.NS":  {"name": "FEDERALBNK", "nse": "FEDERALBNK", "lot": 5000, "step": 2.5, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "WIPRO.NS":       {"name": "WIPRO",      "nse": "WIPRO",      "lot": 3000, "step": 2.5, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "LTF.NS":         {"name": "LTF",        "nse": "LTF",        "lot": 4462, "step": 2.5, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "CROMPTON.NS":    {"name": "CROMPTON",   "nse": "CROMPTON",   "lot": 1800, "step": 5,   "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "PERSISTENT.NS":  {"name": "PERSISTENT", "nse": "PERSISTENT", "lot": 100,  "step": 100, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "TCS.NS":         {"name": "TCS",        "nse": "TCS",        "lot": 175,  "step": 50,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "OBEROIRLTY.NS":  {"name": "OBEROIRLTY", "nse": "OBEROIRLTY", "lot": 350,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "DIXON.NS":       {"name": "DIXON",      "nse": "DIXON",      "lot": 50,   "step": 200, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "DLF.NS":         {"name": "DLF",        "nse": "DLF",        "lot": 825,  "step": 10,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "BHARTIARTL.NS":  {"name": "BHARTIARTL", "nse": "BHARTIARTL", "lot": 475,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "AXISBANK.NS":    {"name": "AXISBANK",   "nse": "AXISBANK",   "lot": 625,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "KOTAKBANK.NS":   {"name": "KOTAKBANK",  "nse": "KOTAKBANK",  "lot": 400,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "MARUTI.NS":      {"name": "MARUTI",     "nse": "MARUTI",     "lot": 50,   "step": 100, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "M&M.NS":         {"name": "M&M",        "nse": "M&M",        "lot": 175,  "step": 50,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "LT.NS":          {"name": "LT",         "nse": "LT",         "lot": 150,  "step": 50,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "INFY.NS":        {"name": "INFY",       "nse": "INFY",       "lot": 400,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "AUROPHARMA.NS":  {"name": "AUROPHARMA", "nse": "AUROPHARMA", "lot": 550,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "ZYDUSLIFE.NS":   {"name": "ZYDUSLIFE",  "nse": "ZYDUSLIFE",  "lot": 900,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "RBLBANK.NS":     {"name": "RBLBANK",    "nse": "RBLBANK",    "lot": 2500, "step": 5,   "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "CONCOR.NS":      {"name": "CONCOR",     "nse": "CONCOR",     "lot": 1000, "step": 10,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "VOLTAS.NS":      {"name": "VOLTAS",     "nse": "VOLTAS",     "lot": 375,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "TITAN.NS":       {"name": "TITAN",      "nse": "TITAN",      "lot": 175,  "step": 50,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "ABB.NS":         {"name": "ABB",        "nse": "ABB",        "lot": 125,  "step": 100, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "RELIANCE.NS":    {"name": "RELIANCE",   "nse": "RELIANCE",   "lot": 500,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "HDFCBANK.NS":    {"name": "HDFCBANK",   "nse": "HDFCBANK",   "lot": 550,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "ICICIBANK.NS":   {"name": "ICICIBANK",  "nse": "ICICIBANK",  "lot": 700,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "SBIN.NS":        {"name": "SBIN",       "nse": "SBIN",       "lot": 750,  "step": 10,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "TATASTEEL.NS":   {"name": "TATASTEEL",  "nse": "TATASTEEL",  "lot": 5500, "step": 2.5, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "BAJFINANCE.NS":  {"name": "BAJFINANCE", "nse": "BAJFINANCE", "lot": 125,  "step": 100, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "HAL.NS":         {"name": "HAL",        "nse": "HAL",        "lot": 150,  "step": 50,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "BEL.NS":         {"name": "BEL",        "nse": "BEL",        "lot": 2850, "step": 5,   "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "TRENT.NS":       {"name": "TRENT",      "nse": "TRENT",      "lot": 100,  "step": 100, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "CAMS.NS":        {"name": "CAMS",       "nse": "CAMS",       "lot": 150,  "step": 50,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "NTPC.NS":        {"name": "NTPC",       "nse": "NTPC",       "lot": 1500, "step": 5,   "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "ADANIPORTS.NS":  {"name": "ADANIPORTS", "nse": "ADANIPORTS", "lot": 400,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "INDHOTEL.NS":    {"name": "INDHOTEL",   "nse": "INDHOTEL",   "lot": 1000, "step": 10,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "MOTHERSON.NS":   {"name": "MOTHERSON",  "nse": "MOTHERSON",  "lot": 4100, "step": 2.5, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "NYKAA.NS":       {"name": "NYKAA",      "nse": "NYKAA",      "lot": 3125, "step": 5,   "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "SUPREMEIND.NS":  {"name": "SUPREMEIND", "nse": "SUPREMEIND", "lot": 175,  "step": 50,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
}

# ==============================================================================
# 🗓️ 4. EXCHANGE-ACCURATE EXPIRY ENGINE (MONTHLY STOCKS vs WEEKLY INDICES)
# ==============================================================================
def _last_weekday_of_month(year: int, month: int, target_weekday: int) -> date:
    """
    Returns the exact last Tuesday (weekday=1) or last Thursday (weekday=3) of a given month.
    """
    cal = calendar.monthcalendar(year, month)
    days = [week[target_weekday] for week in cal if week[target_weekday] != 0]
    return date(year, month, days[-1])


def get_exchange_accurate_expiry(meta: dict, min_dte: int) -> tuple[int, str]:
    """
    Calculates the TRUE NSE/BSE expiry date for any instrument:
      1. NSE Stock Options + BANKNIFTY/FINNIFTY/MIDCPNIFTY ("NSE_MONTHLY_TUE"):
         ONLY expire on the LAST TUESDAY of the month (e.g., 27OCT, 24NOV, 29DEC).
         Never assigns fake weekly expiries (like 03NOV) to stocks!
      2. NIFTY ("NSE_WEEKLY_TUE"):
         Expires every Tuesday (e.g., 27OCT, 03NOV, 10NOV, 17NOV, 24NOV).
      3. SENSEX ("BSE_WEEKLY_THU"):
         Expires every Thursday (e.g., 22OCT, 29OCT, 05NOV, 12NOV, 19NOV, 26NOV).
    """
    ist = pytz.timezone("Asia/Kolkata")
    today = datetime.now(ist).date()
    rule = meta.get("exp_rule", "NSE_MONTHLY_TUE")

    if rule == "NSE_MONTHLY_TUE":
        # Check current month, next month, and month+2 for the first Last-Tuesday >= min_dte
        y, m = today.year, today.month
        for _ in range(4):
            exp_dt = _last_weekday_of_month(y, m, calendar.TUESDAY)
            dte = (exp_dt - today).days
            if dte >= min_dte:
                return dte, exp_dt.strftime("%d%b").upper()
            # Advance to next month
            m += 1
            if m > 12:
                m = 1
                y += 1

    elif rule == "NSE_WEEKLY_TUE":
        # Weekly Tuesday expiry (NIFTY) >= min_dte
        target_dt = today + timedelta(days=min_dte)
        while target_dt.weekday() != calendar.TUESDAY:
            target_dt += timedelta(days=1)
        dte = (target_dt - today).days
        return dte, target_dt.strftime("%d%b").upper()

    elif rule == "BSE_WEEKLY_THU":
        # Weekly Thursday expiry (SENSEX) >= min_dte
        target_dt = today + timedelta(days=min_dte)
        while target_dt.weekday() != calendar.THURSDAY:
            target_dt += timedelta(days=1)
        dte = (target_dt - today).days
        return dte, target_dt.strftime("%d%b").upper()

    # Default fallback: Last Tuesday of next month
    exp_dt = _last_weekday_of_month(today.year, today.month, calendar.TUESDAY)
    return max((exp_dt - today).days, min_dte), exp_dt.strftime("%d%b").upper()


class DhanOptionEngine:
    """
    Fetches live Option Chain from Dhan v2 API and specifically selects the first
    official exchange expiry that meets `min_dte`.
    """
    def __init__(self, client_id: str, access_token: str):
        self.client_id = client_id
        self.access_token = access_token
        self.base_url = "https://api.dhan.co/v2"
        self.enabled = bool(self.client_id and self.access_token)
        self.eq_sec_map = {}
        self.active_calls = 0
        self.live_quotes_fetched = 0
        if self.enabled:
            self._load_security_master()

    def _headers(self):
        return {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _load_security_master(self):
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        try:
            r = requests.get(url, timeout=12)
            if r.status_code == 200:
                df = pd.read_csv(io.StringIO(r.text), low_memory=False)
                eq_df = df[(df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_INSTRUMENT_NAME"] == "EQUITY")]
                for _, row in eq_df.iterrows():
                    sym = str(row.get("SEM_TRADING_SYMBOL", "")).strip().upper()
                    sec_id = row.get("SEM_SMST_SECURITY_ID")
                    if sym and pd.notna(sec_id):
                        self.eq_sec_map[sym] = int(sec_id)
                print(f"✅ Dhan Master Loaded: {len(self.eq_sec_map)} Equities mapped.")
        except Exception as e:
            print(f"⚠️ Dhan Scrip Master warning: {e}")

    def get_live_atm_option(self, meta: dict, atm_strike: float, opt_type: str, min_dte: int):
        if not self.enabled:
            return None
        sec_id = meta.get("dhan_id") or self.eq_sec_map.get(meta["nse"])
        seg = meta.get("dhan_seg", "NSE_EQ")
        if not sec_id:
            return None

        try:
            if self.active_calls > 0:
                time.sleep(3.1)
            self.active_calls += 1

            exp_resp = requests.post(
                f"{self.base_url}/optionchain/expirylist",
                headers=self._headers(),
                json={"UnderlyingScrip": int(sec_id), "UnderlyingSeg": seg},
                timeout=8,
            )
            if exp_resp.status_code != 200:
                return None
            expiries = exp_resp.json().get("data", [])
            if not expiries:
                return None

            ist = pytz.timezone("Asia/Kolkata")
            today = datetime.now(ist).date()
            chosen_expiry = expiries[-1]
            chosen_dte = min_dte
            for exp_str in sorted(expiries):
                try:
                    exp_dt = datetime.strptime(str(exp_str)[:10], "%Y-%m-%d").date()
                    dte_days = (exp_dt - today).days
                    if dte_days >= min_dte:
                        chosen_expiry = exp_str
                        chosen_dte = dte_days
                        break
                except Exception:
                    continue

            oc_resp = requests.post(
                f"{self.base_url}/optionchain",
                headers=self._headers(),
                json={"UnderlyingScrip": int(sec_id), "UnderlyingSeg": seg, "Expiry": chosen_expiry},
                timeout=8,
            )
            if oc_resp.status_code != 200:
                return None

            oc_data = oc_resp.json().get("data", {}).get("oc", {})
            if not oc_data:
                return None

            strike_keys = [float(k) for k in oc_data.keys()]
            if not strike_keys:
                return None
            best_strike = min(strike_keys, key=lambda x: abs(x - float(atm_strike)))
            raw_key = next(k for k in oc_data.keys() if abs(float(k) - best_strike) < 1e-4)
            leg = oc_data.get(raw_key, {}).get("ce" if opt_type == "CE" else "pe", {})
            ltp = float(leg.get("last_price", 0.0))
            if ltp <= 0.5:
                return None

            greeks = leg.get("greeks", {})
            exp_label = datetime.strptime(str(chosen_expiry)[:10], "%Y-%m-%d").strftime("%d%b").upper()
            self.live_quotes_fetched += 1
            return {
                "ltp": ltp,
                "delta": max(abs(float(greeks.get("delta", 0.48))), 0.25),
                "gamma": max(float(greeks.get("gamma", 0.0015)), 0.0001),
                "iv": max(float(leg.get("implied_volatility", 18.0)) / 100.0, 0.11),
                "strike": best_strike,
                "dte": chosen_dte,
                "exp_tag": exp_label,
            }
        except Exception:
            return None


# ==============================================================================
# 🕸️ 5. LIVE NSE OI SPURTS SCRAPER & STRICT INSTITUTIONAL CLASSIFIER
# ==============================================================================
def fetch_nse_oi_spurts_map() -> dict:
    base_url = "https://www.nseindia.com"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/market-data/oi-spurts",
        "X-Requested-With": "XMLHttpRequest",
    }
    oi_map = {}
    try:
        session = requests.Session()
        session.headers.update(headers)
        session.get(base_url, timeout=8)
        time.sleep(0.3)
        session.get(f"{base_url}/market-data/oi-spurts", timeout=8)
        resp = session.get(f"{base_url}/api/live-analysis-oi-spurts-underlyings", timeout=10)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            for item in data:
                sym = str(item.get("symbol", "")).strip().upper()
                oi_pct = pd.to_numeric(item.get("avgInOI", 0.0), errors="coerce")
                if sym and not np.isnan(oi_pct):
                    oi_map[sym] = float(oi_pct)
            print(f"✅ Scraped live NSE OI Spurts for {len(oi_map)} F&O underlyings.")
    except Exception as e:
        print(f"⚠️ NSE OI Spurts warning: {e}")
    return oi_map


def classify_institutional_oi(price_chg_pct: float, oi_chg_pct: float, opt_type: str):
    """
    Strictly blocks any conflicting institutional flow:
      - Never allows PE during Long Build-Up (LB) or Short Covering (SC)
      - Never allows CE during Short Build-Up (SB) or Long Unwinding (LU)
    """
    if abs(oi_chg_pct) < 0.5:
        return False, False, "   --  "

    sign_str = f"{oi_chg_pct:+.0f}%"
    if price_chg_pct >= 0.0 and oi_chg_pct >= 2.5:
        if opt_type == "PE":
            return True, False, f"LB{sign_str:>5}"
        return False, True, f"LB{sign_str:>5}"

    if price_chg_pct < 0.0 and oi_chg_pct >= 2.5:
        if opt_type == "CE":
            return True, False, f"SB{sign_str:>5}"
        return False, True, f"SB{sign_str:>5}"

    if price_chg_pct >= 0.2 and oi_chg_pct <= -2.0:
        if opt_type == "PE":
            return True, False, f"SC{sign_str:>5}"
        return False, True, f"SC{sign_str:>5}"

    if price_chg_pct <= -0.2 and oi_chg_pct <= -2.0:
        if opt_type == "CE":
            return True, False, f"LU{sign_str:>5}"
        return False, True, f"LU{sign_str:>5}"

    return False, False, f"OI{sign_str:>5}"


# ==============================================================================
# 📈 6. ZERO-REPAINTING DATA SLICER, INDICATORS & BLACK-SCHOLES GREEKS
# ==============================================================================
def get_closed_bar_slice(df: pd.DataFrame, is_intraday: bool) -> pd.DataFrame:
    if df.empty or len(df) < 30:
        return df
    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    now_mins = now_ist.hour * 60 + now_ist.minute
    cutoff_mins = (15 * 60 + 30) if is_intraday else (14 * 60 + 15)
    in_forming_window = (
        now_ist.weekday() < 5
        and (9 * 60 + 15) <= now_mins < cutoff_mins
    )
    if in_forming_window:
        last_ts = df.index[-1]
        last_date = last_ts.date() if hasattr(last_ts, "date") else now_ist.date()
        if last_date == now_ist.date():
            return df.iloc[:-1].copy()
    return df.copy()


def extract_ticker_df(raw_data, ticker):
    if raw_data is None or raw_data.empty:
        return pd.DataFrame()
    try:
        if isinstance(raw_data.columns, pd.MultiIndex):
            lvl0 = raw_data.columns.get_level_values(0)
            lvl1 = raw_data.columns.get_level_values(1)
            if ticker in lvl0:
                df = raw_data[ticker].copy()
            elif ticker in lvl1:
                df = raw_data.xs(ticker, axis=1, level=1).copy()
            else:
                return pd.DataFrame()
        else:
            df = raw_data.copy()
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


def black_scholes_greeks(S, K, T, r, sigma, option_type="CE"):
    T = max(T, 3.0 / 365.0)
    sigma = max(sigma, 0.11)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    if option_type == "CE":
        price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
        delta = norm.cdf(d1)
    else:
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        delta = -norm.cdf(-d1)
    return max(float(price), 1.0), abs(float(delta)), float(gamma)


def compute_daily_indicators(df):
    df = df.copy()
    c = df["Close"]
    df["EMA21"] = c.ewm(span=21, adjust=False).mean()
    df["EMA55"] = c.ewm(span=55, adjust=False).mean()

    df["BB_SMA_38"] = c.rolling(38).mean()
    std_38 = c.rolling(38).std(ddof=0)
    df["BB_UP_38"] = df["BB_SMA_38"] + (2.0 * std_38)
    df["BB_LO_38"] = df["BB_SMA_38"] - (2.0 * std_38)
    df["BB_SMA_61"] = c.rolling(61).mean()

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["RSI"] = (100 - (100 / (1 + (gain / loss.replace(0, np.nan))))).fillna(50.0)

    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - c.shift()).abs(), (df["Low"] - c.shift()).abs()],
        axis=1,
    ).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    df["HV20"] = np.log(c / c.shift(1)).rolling(20).std() * np.sqrt(252)

    df["CE_Signal"] = (
        (c >= df["EMA21"] * 0.995)
        & (c < df["BB_UP_38"])
        & (df["RSI"] >= 42)
        & (df["RSI"] <= 70)
    )
    df["PE_Signal"] = (
        (c <= df["EMA21"] * 1.005)
        & (c > df["BB_LO_38"])
        & (df["RSI"] >= 30)
        & (df["RSI"] <= 58)
    )
    return df


def compute_intraday_15m_indicators(df, is_index=False):
    df = df.copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
    c = df["Close"]
    df["EMA9"] = c.ewm(span=9, adjust=False).mean()
    df["EMA21"] = c.ewm(span=21, adjust=False).mean()

    if is_index or (df["Volume"].sum() == 0):
        df["TREND_ANCHOR"] = df["EMA21"]
    else:
        tp = (df["High"] + df["Low"] + c) / 3.0
        vol_clean = df["Volume"].replace(0, 1)
        dates = pd.Series(df.index.date, index=df.index)
        df["TREND_ANCHOR"] = (tp * vol_clean).groupby(dates).cumsum() / vol_clean.groupby(dates).cumsum()

    df["BB_SMA_38"] = c.rolling(38).mean()
    std_38 = c.rolling(38).std(ddof=0)
    df["BB_UP_38"] = df["BB_SMA_38"] + (2.0 * std_38)
    df["BB_LO_38"] = df["BB_SMA_38"] - (2.0 * std_38)

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["RSI"] = (100 - (100 / (1 + (gain / loss.replace(0, np.nan))))).fillna(50.0)

    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - c.shift()).abs(), (df["Low"] - c.shift()).abs()],
        axis=1,
    ).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    df["HV20"] = np.log(c / c.shift(1)).rolling(50).std() * np.sqrt(252 * 25)

    df["CE_Signal"] = (
        (c >= df["TREND_ANCHOR"])
        & (df["EMA9"] >= df["EMA21"] * 0.998)
        & (c < df["BB_UP_38"])
        & (df["RSI"] >= 46)
        & (df["RSI"] <= 74)
    )
    df["PE_Signal"] = (
        (c <= df["TREND_ANCHOR"])
        & (df["EMA9"] <= df["EMA21"] * 1.002)
        & (c > df["BB_LO_38"])
        & (df["RSI"] >= 26)
        & (df["RSI"] <= 54)
    )
    return df


# ==============================================================================
# 🧪 7. TRUE MARK-TO-MARKET EXPECTANCY BACKTESTER
# ==============================================================================
def backtest_signals(df, direction="CE", atr_mult=1.0, und_rr=1.8, max_bars=6, is_intraday=False):
    sig_col = "CE_Signal" if direction == "CE" else "PE_Signal"
    closes, highs, lows, atrs, sigs = (
        df["Close"].values,
        df["High"].values,
        df["Low"].values,
        df["ATR"].values,
        df[sig_col].values,
    )
    dates = df.index.date if is_intraday else None

    t1_r_Lock = INTRA_T1_RR if is_intraday else SWING_T1_RR
    full_r_Lock = INTRA_OPT_RR if is_intraday else SWING_OPT_RR

    total_trades = 0
    net_r_sum = 0.0
    profitable_trades = 0
    n = len(df)
    i = 50

    while i < n - max_bars - 1:
        if not sigs[i] or np.isnan(atrs[i]) or atrs[i] <= 0:
            i += 1
            continue
        entry = closes[i]
        risk = atrs[i] * atr_mult
        sl = entry - risk if direction == "CE" else entry + risk
        t1 = entry + risk if direction == "CE" else entry - risk
        tgt = entry + (risk * und_rr) if direction == "CE" else entry - (risk * und_rr)

        total_trades += 1
        t1_reached = False
        outcome_resolved = False
        trade_r = 0.0
        exit_bar = i + 1

        for j in range(i + 1, min(i + 1 + max_bars, n)):
            exit_bar = j
            if is_intraday and dates[j] != dates[i]:
                prev_close = closes[j - 1]
                mtm_r = ((prev_close - entry) / risk) if direction == "CE" else ((entry - prev_close) / risk)
                mtm_r = float(np.clip(mtm_r, -1.0, full_r_Lock))
                trade_r = (0.5 * t1_r_Lock + 0.5 * max(0.0, mtm_r)) if t1_reached else mtm_r
                outcome_resolved = True
                break

            if direction == "CE":
                curr_sl = entry if t1_reached else sl
                if lows[j] <= curr_sl:
                    trade_r = (0.5 * t1_r_Lock) if t1_reached else -1.0
                    outcome_resolved = True
                    break
                if highs[j] >= tgt:
                    trade_r = 0.5 * t1_r_Lock + 0.5 * full_r_Lock
                    outcome_resolved = True
                    break
                if highs[j] >= t1:
                    t1_reached = True
            else:
                curr_sl = entry if t1_reached else sl
                if highs[j] >= curr_sl:
                    trade_r = (0.5 * t1_r_Lock) if t1_reached else -1.0
                    outcome_resolved = True
                    break
                if lows[j] <= tgt:
                    trade_r = 0.5 * t1_r_Lock + 0.5 * full_r_Lock
                    outcome_resolved = True
                    break
                if lows[j] <= t1:
                    t1_reached = True

        if not outcome_resolved:
            end_close = closes[exit_bar]
            mtm_r = ((end_close - entry) / risk) if direction == "CE" else ((entry - end_close) / risk)
            mtm_r = float(np.clip(mtm_r, -1.0, full_r_Lock))
            trade_r = (0.5 * t1_r_Lock + 0.5 * max(0.0, mtm_r)) if t1_reached else mtm_r

        net_r = trade_r - COST_SLIPPAGE_R
        net_r_sum += net_r
        if net_r > 0:
            profitable_trades += 1
        i = max(i + 1, exit_bar)

    expectancy = (net_r_sum / total_trades) if total_trades > 0 else -1.0
    win_pct = (profitable_trades / total_trades * 100.0) if total_trades > 0 else 0.0
    return total_trades, expectancy, win_pct


# ==============================================================================
# 💰 8. OPTION TRADE BUILDER (FULL REAL NAMES + EXCHANGE-ACCURATE EXPIRY)
# ==============================================================================
def build_option_trade_row(
    meta, df, opt_type, total_trades, expectancy, win_pct, india_vix, is_intraday, oi_tag, is_oi_boosted, dhan_engine: DhanOptionEngine
):
    latest = df.iloc[-1]
    spot = float(latest["Close"])
    atr = float(latest["ATR"])
    hv20 = float(latest["HV20"]) if not np.isnan(latest["HV20"]) else 0.22
    step = meta["step"]
    atm_strike = int(round(spot / step) * step) if step >= 5 else round(spot / step) * step

    min_dte_req = MIN_DTE_INTRADAY if is_intraday else MIN_DTE_POSITIONAL

    dhan_quote = dhan_engine.get_live_atm_option(meta, atm_strike, opt_type, min_dte=min_dte_req) if dhan_engine else None
    if dhan_quote:
        opt_entry  = dhan_quote["ltp"]
        delta      = dhan_quote["delta"]
        gamma      = dhan_quote["gamma"]
        atm_strike = dhan_quote["strike"]
        exp_tag    = dhan_quote["exp_tag"]
        src_flag   = "*"
    else:
        dte, exp_tag = get_exchange_accurate_expiry(meta, min_dte=min_dte_req)
        iv = india_vix * 1.12 if meta["is_idx"] else max(hv20 * 1.05, india_vix * 1.35)
        opt_entry, delta, gamma = black_scholes_greeks(
            S=spot, K=atm_strike, T=dte / 365.0, r=RISK_FREE_RATE, sigma=iv, option_type=opt_type
        )
        src_flag = ""

    strike_str = f"{int(atm_strike)}" if atm_strike == int(atm_strike) else f"{atm_strike:.1f}"

    atr_mult = INTRA_ATR_SL_MULT if is_intraday else SWING_ATR_SL_MULT
    und_risk = atr * atr_mult
    raw_opt_sl_dist = delta * und_risk - 0.5 * gamma * (und_risk ** 2)
    max_pct_sl = 0.16 if meta["is_idx"] else (0.20 if is_intraday else 0.24)
    min_pct_sl = 0.12 if (is_intraday or meta["is_idx"]) else 0.15
    opt_risk = min(opt_entry * max_pct_sl, max(opt_entry * min_pct_sl, raw_opt_sl_dist))

    opt_entry = round(opt_entry, 1)
    opt_risk = max(round(opt_risk, 1), 0.3)
    opt_sl = round(max(0.5, opt_entry - opt_risk), 1)
    actual_risk = max(round(opt_entry - opt_sl, 1), 0.2)

    target_rr = INTRA_OPT_RR if is_intraday else SWING_OPT_RR
    t1_rr = INTRA_T1_RR if is_intraday else SWING_T1_RR
    opt_t1 = round(opt_entry + (actual_risk * t1_rr), 1)
    opt_tgt = round(opt_entry + (actual_risk * target_rr), 1)

    lot_size = meta["lot"]
    risk_per_lot = max(actual_risk * lot_size, 1.0)
    cost_per_lot = max(opt_entry * lot_size, 1.0)

    effective_max_risk = INDEX_POS_MAX_RISK if (meta["is_idx"] and not is_intraday) else RISK_PER_TRADE
    lots_by_risk = int(effective_max_risk // risk_per_lot)
    lots_by_cap = int(MAX_CAPITAL_PER_TRADE // cost_per_lot)
    num_lots = min(lots_by_risk, lots_by_cap)

    if num_lots < 1:
        return None

    qty = num_lots * lot_size
    capital = int(round(opt_entry * qty))
    total_rupee_risk = int(round(actual_risk * qty))

    exp_rounded = round(expectancy, 2)
    win_rounded = round(win_pct, 1)
    quality_score = round((exp_rounded * 100.0) + win_rounded, 1)

    # Full Real NSE/BSE Symbol + Strike + Option Type (Zero Truncation!)
    full_contract_name = f"{src_flag}{meta['nse']} {strike_str}{opt_type}"

    return {
        "Contract": full_contract_name,
        "Expiry": exp_tag,
        "Entry": opt_entry,
        "Qty": qty,
        "Capital": capital,
        "RiskRs": total_rupee_risk,
        "Stop": opt_sl,
        "T1": opt_t1,
        "Target": opt_tgt,
        "OI_Flow": oi_tag,
        "Expectancy": exp_rounded,
        "WinProb": win_rounded,
        "Score": quality_score,
        "OIBoost": 1 if is_oi_boosted else 0,
    }


# ==============================================================================
# 📱 9. TABLE FORMATTER (19-CHAR FULL CONTRACT NAMES + Cap(₹) + Score)
# ==============================================================================
def format_combined_table(idx_records, stk_records, title, now_str, dhan_engine: DhanOptionEngine, oi_count: int):
    if dhan_engine.enabled and dhan_engine.live_quotes_fetched > 0:
        feed_str = f"Dhan Live ({dhan_engine.live_quotes_fetched}) + NSE OI ({oi_count})"
    else:
        feed_str = f"NSE OI ({oi_count}) + Exchange Calendar BS"

    # 19-char Contract column ensures full real names like BAJFINANCE 1000PE or MIDCPNIFTY 13575PE never get cut off!
    col_hdr = (
        f"{'Contract':<19}|{'Exp':>5}|{'Entry':>6}|{'Qty':>4}|{'Cap(₹)':>6}|"
        f"{'SL':>6}|{'T1-50%':>6}|{'Target':>6}|{'OI_Flow':>7}|{'ExpR':>5}|{'Win%':>5}|{'Score':>5}"
    )
    sep = "-" * len(col_hdr)

    lines = [
        f"<b>🔥 {html.escape(title)}</b>",
        f"🕒 <code>{html.escape(now_str)}</code> | Feed: <code>{html.escape(feed_str)}</code> | Max Risk: <code>₹{RISK_PER_TRADE:,}</code>\n",
    ]

    if idx_records:
        lines.append("<b>📌 MAJOR INDICES F&amp;O (SORTED BY QUALITY SCORE)</b>")
        lines.append("<pre>")
        lines.append(col_hdr)
        lines.append(sep)
        for r in idx_records:
            row_str = (
                f"{r['Contract']:<19}|"
                f"{r['Expiry']:>5}|"
                f"{r['Entry']:>6.1f}|"
                f"{r['Qty']:>4d}|"
                f"{r['Capital']:>6d}|"
                f"{r['Stop']:>6.1f}|"
                f"{r['T1']:>6.1f}|"
                f"{r['Target']:>6.1f}|"
                f"{r['OI_Flow'][:7]:>7}|"
                f"{r['Expectancy']:>+5.2f}|"
                f"{r['WinProb']:>4.1f}%|"
                f"{r['Score']:>5.1f}"
            )
            lines.append(html.escape(row_str))
        lines.append("</pre>\n")

    if stk_records:
        lines.append(f"<b>🚀 TOP {len(stk_records[:TOP_N_STOCKS])} STOCK OPTIONS (RANKED BY OI FLOW &amp; QUALITY SCORE)</b>")
        lines.append("<pre>")
        lines.append(col_hdr)
        lines.append(sep)
        for r in stk_records[:TOP_N_STOCKS]:
            row_str = (
                f"{r['Contract']:<19}|"
                f"{r['Expiry']:>5}|"
                f"{r['Entry']:>6.1f}|"
                f"{r['Qty']:>4d}|"
                f"{r['Capital']:>6d}|"
                f"{r['Stop']:>6.1f}|"
                f"{r['T1']:>6.1f}|"
                f"{r['Target']:>6.1f}|"
                f"{r['OI_Flow'][:7]:>7}|"
                f"{r['Expectancy']:>+5.2f}|"
                f"{r['WinProb']:>4.1f}%|"
                f"{r['Score']:>5.1f}"
            )
            lines.append(html.escape(row_str))
        lines.append("</pre>")

    # ------------------------------------------------------------------
    # 🏆 AUTO 3-STEP TRIAGE: HIGHLIGHT TOP 2 BEST TRADES AUTOMATICALLY
    # ------------------------------------------------------------------
    all_cands = list(idx_records) + list(stk_records[:TOP_N_STOCKS])
    prime_picks = [
        r for r in all_cands
        if r["Expectancy"] >= 0.08 and r["WinProb"] >= 48.0 and r["OIBoost"] == 1
    ]
    if not prime_picks:
        prime_picks = [r for r in all_cands if r["Expectancy"] >= 0.05 and r["WinProb"] >= 47.0]

    prime_picks.sort(key=lambda x: (x["OIBoost"], x["Score"]), reverse=True)
    top_two = prime_picks[:2]

    if top_two:
        lines.append("\n<b>🏆 TOP HIGH-CONVICTION TRADES (AUTO 3-STEP TRIAGE):</b>")
        for idx, p in enumerate(top_two, 1):
            lines.append(
                f"{idx}. <b>{html.escape(p['Contract'])} ({html.escape(p['Expiry'])})</b> "
                f"👉 Entry: <code>₹{p['Entry']:.1f}</code> | Qty: <code>{p['Qty']}</code> "
                f"| Cap: <code>₹{p['Capital']:,}</code> | Risk: <code>₹{p['RiskRs']:,}</code> "
                f"| SL: <code>{p['Stop']:.1f}</code> | T1: <code>{p['T1']:.1f}</code> | Tgt: <code>{p['Target']:.1f}</code> "
                f"| OI: <code>{html.escape(p['OI_Flow'].strip())}</code> | <b>Score: {p['Score']:.1f}</b>"
            )

    lines.append(
        "\n💡 <i>Score = (ExpR×100) + Win% | Stocks = Monthly Expiry Only (27OCT / 24NOV) | LB=Long Build, SB=Short Build</i>"
    )
    return "\n".join(lines)


def send_telegram_alert(html_text):
    if not TELEGRAM_BOT_TOKEN or ":" not in TELEGRAM_BOT_TOKEN:
        print(html_text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=15)
    if r.status_code == 200:
        print("✅ Telegram notification delivered successfully!")
    else:
        print(f"❌ Telegram API Error ({r.status_code}): {r.text}")


def get_live_vix():
    try:
        vix_df = yf.download("^INDIAVIX", period="5d", interval="1d", progress=False)
        if vix_df.empty:
            return 0.135
        close_col = vix_df["Close"]
        if isinstance(close_col, pd.DataFrame):
            close_col = close_col.iloc[:, 0]
        return float(close_col.dropna().iloc[-1]) / 100.0
    except Exception:
        return 0.135


# ==============================================================================
# 🚀 10. MAIN EXECUTION ENGINE
# ==============================================================================
def main():
    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    now_str = now_ist.strftime("%d-%b %I:%M %p IST")
    current_minutes = now_ist.hour * 60 + now_ist.minute

    if EVENT_NAME == "schedule":
        if now_ist.weekday() >= 5 or current_minutes < (9 * 60 + 15) or current_minutes > (15 * 60 + 35):
            print(f"⏸️ Outside Indian Market Hours ({now_str}). Exiting.")
            return

    tickers = list(FNO_UNIVERSE.keys())
    india_vix = get_live_vix()
    oi_spurts_map = fetch_nse_oi_spurts_map()
    dhan_engine = DhanOptionEngine(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN)

    messages_sent = 0

    # ------------------------------------------------------------------
    # 1. INTRADAY 15-MINUTE F&O SCALPS (CLOSED CANDLES, >= 4 DTE)
    # ------------------------------------------------------------------
    raw_15m = yf.download(tickers, period="60d", interval="15m", group_by="ticker", progress=False)
    intra_idx, intra_stk = [], []

    for tk, meta in FNO_UNIVERSE.items():
        df15_raw = extract_ticker_df(raw_15m, tk)
        df15 = get_closed_bar_slice(df15_raw, is_intraday=True)
        if len(df15) < 80:
            continue
        df15 = compute_intraday_15m_indicators(df15, is_index=meta["is_idx"])
        last = df15.iloc[-1]
        prev = df15.iloc[-2]

        today_bars = df15[df15.index.date == last.name.date()]
        day_open = float(today_bars["Open"].iloc[0]) if not today_bars.empty else float(last["Open"])
        price_chg_pct = ((float(last["Close"]) - day_open) / day_open) * 100.0
        oi_chg_pct = oi_spurts_map.get(meta["nse"], 0.0)

        opt_type = "CE" if float(last["Close"]) >= float(last["TREND_ANCHOR"]) else "PE"
        sig_col = "CE_Signal" if opt_type == "CE" else "PE_Signal"
        is_blocked, is_boosted, oi_tag = classify_institutional_oi(price_chg_pct, oi_chg_pct, opt_type)

        if is_blocked:
            continue

        tot, exp_r, wp = backtest_signals(df15, opt_type, INTRA_ATR_SL_MULT, INTRA_UND_RR, INTRA_MAX_BARS, True)
        q_score = (round(exp_r, 2) * 100.0) + round(wp, 1)

        if meta["is_idx"]:
            if tot >= 5:
                intra_idx.append((q_score, is_boosted, meta, df15, opt_type, tot, exp_r, wp, oi_tag))
        else:
            if not (bool(last[sig_col]) or bool(prev[sig_col]) or is_boosted):
                continue
            if tot >= INTRA_MIN_SETUPS and exp_r >= INTRA_MIN_EXP_R:
                intra_stk.append((q_score, is_boosted, meta, df15, opt_type, tot, exp_r, wp, oi_tag))

    intra_idx.sort(key=lambda x: (1 if x[1] else 0, x[0]), reverse=True)
    intra_stk.sort(key=lambda x: (1 if x[1] else 0, x[0]), reverse=True)

    intra_idx_rows = [
        r for r in (
            build_option_trade_row(m, d, o, t, exp_r, wp, india_vix, True, tag, b, dhan_engine)
            for _, b, m, d, o, t, exp_r, wp, tag in intra_idx
        ) if r
    ]
    intra_stk_rows = [
        r for r in (
            build_option_trade_row(m, d, o, t, exp_r, wp, india_vix, True, tag, b, dhan_engine)
            for _, b, m, d, o, t, exp_r, wp, tag in intra_stk[:TOP_N_STOCKS + 4]
        ) if r
    ][:TOP_N_STOCKS]

    if intra_idx_rows or intra_stk_rows:
        msg = format_combined_table(
            intra_idx_rows, intra_stk_rows, "15M INTRADAY F&O SCALPS (LOCKED 1:2.0 RR)", now_str, dhan_engine, len(oi_spurts_map)
        )
        send_telegram_alert(msg)
        messages_sent += 1

    # ------------------------------------------------------------------
    # 2. POSITIONAL 3-5D F&O SWINGS (15+ DTE FAR EXPIRY: 24NOV FOR STOCKS!)
    # ------------------------------------------------------------------
    raw_1d = yf.download(tickers, period="5y", interval="1d", group_by="ticker", progress=False)
    swing_idx, swing_stk = [], []

    for tk, meta in FNO_UNIVERSE.items():
        df1d_raw = extract_ticker_df(raw_1d, tk)
        df1d = get_closed_bar_slice(df1d_raw, is_intraday=False)
        if len(df1d) < 200:
            continue
        df1d = compute_daily_indicators(df1d)
        last = df1d.iloc[-1]
        prev = df1d.iloc[-2]

        price_chg_pct = ((float(last["Close"]) - float(prev["Close"])) / float(prev["Close"])) * 100.0
        oi_chg_pct = oi_spurts_map.get(meta["nse"], 0.0)

        opt_type = "CE" if float(last["Close"]) >= float(last["EMA21"]) else "PE"
        sig_col = "CE_Signal" if opt_type == "CE" else "PE_Signal"
        is_blocked, is_boosted, oi_tag = classify_institutional_oi(price_chg_pct, oi_chg_pct, opt_type)

        if is_blocked:
            continue

        tot, exp_r, wp = backtest_signals(df1d, opt_type, SWING_ATR_SL_MULT, SWING_UND_RR, SWING_MAX_DAYS, False)
        q_score = (round(exp_r, 2) * 100.0) + round(wp, 1)

        if meta["is_idx"]:
            if tot >= 5:
                swing_idx.append((q_score, is_boosted, meta, df1d, opt_type, tot, exp_r, wp, oi_tag))
        else:
            if not (bool(last[sig_col]) or is_boosted):
                continue
            if tot >= SWING_MIN_SETUPS and exp_r >= SWING_MIN_EXP_R:
                swing_stk.append((q_score, is_boosted, meta, df1d, opt_type, tot, exp_r, wp, oi_tag))

    swing_idx.sort(key=lambda x: (1 if x[1] else 0, x[0]), reverse=True)
    swing_stk.sort(key=lambda x: (1 if x[1] else 0, x[0]), reverse=True)

    swing_idx_rows = [
        r for r in (
            build_option_trade_row(m, d, o, t, exp_r, wp, india_vix, False, tag, b, dhan_engine)
            for _, b, m, d, o, t, exp_r, wp, tag in swing_idx
        ) if r
    ]
    swing_stk_rows = [
        r for r in (
            build_option_trade_row(m, d, o, t, exp_r, wp, india_vix, False, tag, b, dhan_engine)
            for _, b, m, d, o, t, exp_r, wp, tag in swing_stk[:TOP_N_STOCKS + 4]
        ) if r
    ][:TOP_N_STOCKS]

    if swing_idx_rows or swing_stk_rows:
        msg = format_combined_table(
            swing_idx_rows, swing_stk_rows, "3-5D POSITIONAL F&O (15+ DTE FAR EXPIRY | 1:2.5 RR)", now_str, dhan_engine, len(oi_spurts_map)
        )
        send_telegram_alert(msg)
        messages_sent += 1

    if messages_sent == 0:
        fallback_msg = (
            f"<b>ℹ️ F&amp;O OPTIONS SCANNER ({html.escape(now_str)})</b>\n"
            f"Scanner ran cleanly (Scraped {len(oi_spurts_map)} NSE OI records), "
            f"but no setups passed the strict risk &amp; positive ExpR filters."
        )
        send_telegram_alert(fallback_msg)


if __name__ == "__main__":
    main()
