#!/usr/bin/env python3
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

try:
    from dotenv import load_dotenv
    ENV_FILE_PATH = "/home/ubuntu/scanners/.env"
    if os.path.exists(ENV_FILE_PATH):
        load_dotenv(ENV_FILE_PATH)
except ImportError:
    ENV_FILE_PATH = "/home/ubuntu/scanners/.env"
    def load_dotenv(*args, **kwargs):
        pass

# ==============================================================================
# 🔐 1. CREDENTIALS & ENVIRONMENT VARIABLES (AUTO-SANITIZED)
# ==============================================================================
RAW_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
RAW_CHATID = os.environ.get("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "manual")
FORCE_RUN  = "--force" in sys.argv

token_match = re.search(r"(\d+:[A-Za-z0-9_-]+)", RAW_TOKEN)
TELEGRAM_BOT_TOKEN = token_match.group(1) if token_match else RAW_TOKEN.lstrip("bot").strip('"').strip("'")
TELEGRAM_CHAT_ID   = RAW_CHATID

DHAN_CLIENT_ID    = os.environ.get("DHAN_CLIENT_ID", "").strip().strip('"').strip("'")
DHAN_ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "").strip().strip('"').strip("'")

# ==============================================================================
# ⚖️ 2. OFFICIAL NSE HOLIDAYS 2026 & INSTITUTIONAL PARAMETERS
# ==============================================================================
NSE_HOLIDAYS_2026 = {
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 20),   # Id-Ul-Fitr
    date(2026, 4, 2),    # Ram Navami
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 6),    # Mahavir Jayanti
    date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra (Market Holiday)
    date(2026, 11, 10),  # Diwali - Balipratipada
    date(2026, 11, 24),  # Guru Nanak Jayanti (Last Tuesday of Nov -> Expiry shifts to Mon 23NOV!)
    date(2026, 12, 25),  # Christmas
}

TARGET_RISK_PER_TRADE  = 3000     # Target Rupee risk per 1-lot trade (₹3,000)
MAX_SINGLE_LOT_RISK    = 6000     # Max allowed risk for 1 single lot
MAX_CAPITAL_PER_TRADE  = 50000    # Max 1-lot Option Premium outlay (₹50,000 cap)
LOCK_TO_SINGLE_LOT     = True     # Exact 1-Lot Exchange Qty (Matches Dhan Order Window 1:1)
TOP_N_STOCKS           = 10       # Top 10 Stock Option setups per scan
COST_SLIPPAGE_R        = 0.05     # 0.05R brokerage + STT + slippage deducted per trade

# --- Adaptive Spread & Expiry Parameters ---
MAX_SPREAD_FRONT_PCT   = 4.5      # Max live bid-ask spread % for front-month contracts (DTE <= 10)
MAX_SPREAD_FAR_PCT     = 7.2      # Relaxed spread % for far-month contracts (DTE > 10)
MIN_DTE_INTRADAY       = 4        # Minimum 4 DTE for Intraday Scalps
MIN_DTE_POSITIONAL     = 15       # Minimum 15+ DTE for Positional & Index Swings

# --- Risk:Reward & Backtest Thresholds ---
INTRA_OPT_RR           = 1.75     # Aligned 1:1.75 Option Risk:Reward
INTRA_T1_RR            = 1.0      # Book 50% at +1.0R and trail SL to Entry
INTRA_UND_RR           = 1.6      # Underlying ATR target multiple
INTRA_ATR_SL_MULT      = 0.9      # 0.9x ATR stop loss
INTRA_MAX_BARS         = 12       # 12 bars intraday window
INTRA_MIN_SETUPS       = 10       # Minimum historical sample size
INTRA_MIN_EXP_R        = 0.05     # Minimum Net R-Expectancy after costs

SWING_OPT_RR           = 2.5      # Locked 1:2.5 Option Risk:Reward
SWING_T1_RR            = 1.25     # Book 50% at +1.25R and trail SL to Entry
SWING_UND_RR           = 1.8      # Underlying ATR target multiple
SWING_ATR_SL_MULT      = 1.0      # 1.0x Daily ATR stop loss
SWING_MAX_DAYS         = 6        # 6 trading days hold window
SWING_MIN_SETUPS       = 10       # Minimum 5-year historical sample size
SWING_MIN_EXP_R        = 0.04     # Minimum Net R-Expectancy after costs

EXEC_GATE_ENABLED      = True     # Require positive expectancy in option-premium space (replay_executed)
EXEC_MIN_EXP_R         = 0.04     # Minimum executed-geometry Expectancy R
EXEC_MIN_SETUPS        = 12       # Minimum setups in executed-geometry replay

# --- Institutional Volatility & Microstructure Guards ---
MAX_SAFE_IVP           = 75.0     # NYKAA/BDL Guard: Block naked buying above 75 IVP without strong RVOL/OI
MAX_IV_HV_RATIO        = 1.38     # Reject overpriced ATM options where Live IV > 1.38x HV20
MIN_BULL_PCR           = 0.78     # Minimum chain PCR required for unboosted Long Call (CE) setups
MAX_BEAR_PCR           = 1.28     # Maximum chain PCR allowed for unboosted Long Put (PE) setups

RISK_FREE_RATE         = 0.068

# ==============================================================================
# 📋 3. TRADABLE INDEX OPTIONS + LIQUID NSE F&O STOCKS (LIVE LOT SYNC)
# ==============================================================================
FNO_UNIVERSE = {
    "^NSEI":                 {"name": "NIFTY",      "nse": "NIFTY",      "lot": 75,   "step": 50,  "is_idx": True,  "dhan_id": 13,  "dhan_seg": "IDX_I", "exp_rule": "NSE_WEEKLY_TUE"},
    "^NSEBANK":              {"name": "BANKNIFTY",  "nse": "BANKNIFTY",  "lot": 30,   "step": 100, "is_idx": True,  "dhan_id": 25,  "dhan_seg": "IDX_I", "exp_rule": "NSE_MONTHLY_TUE"},
    "^BSESN":                {"name": "SENSEX",     "nse": "SENSEX",     "lot": 20,   "step": 100, "is_idx": True,  "dhan_id": 51,  "dhan_seg": "IDX_I", "exp_rule": "BSE_WEEKLY_THU"},
    "NIFTY_MID_SELECT.NS":   {"name": "MIDCPNIFTY", "nse": "MIDCPNIFTY", "lot": 120,  "step": 25,  "is_idx": True,  "dhan_id": 442, "dhan_seg": "IDX_I", "exp_rule": "NSE_MONTHLY_TUE"},
    "NIFTY_FIN_SERVICE.NS":  {"name": "FINNIFTY",   "nse": "FINNIFTY",   "lot": 65,   "step": 50,  "is_idx": True,  "dhan_id": 27,  "dhan_seg": "IDX_I", "exp_rule": "NSE_MONTHLY_TUE"},

    # --- LIQUID NSE F&O STOCKS ---
    "BAJFINANCE.NS":  {"name": "BAJFINANCE", "nse": "BAJFINANCE", "lot": 125,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "ETERNAL.NS":     {"name": "ETERNAL",    "nse": "ETERNAL",    "lot": 2000, "step": 5,   "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "ANGELONE.NS":    {"name": "ANGELONE",   "nse": "ANGELONE",   "lot": 200,  "step": 10,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "TECHM.NS":       {"name": "TECHM",      "nse": "TECHM",      "lot": 600,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "FEDERALBNK.NS":  {"name": "FEDERALBNK", "nse": "FEDERALBNK", "lot": 5000, "step": 2.5, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "WIPRO.NS":       {"name": "WIPRO",      "nse": "WIPRO",      "lot": 3000, "step": 2.5, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "LTF.NS":         {"name": "LTF",        "nse": "LTF",        "lot": 4462, "step": 2.5, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "CROMPTON.NS":    {"name": "CROMPTON",   "nse": "CROMPTON",   "lot": 1800, "step": 5,   "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "PERSISTENT.NS":  {"name": "PERSISTENT", "nse": "PERSISTENT", "lot": 200,  "step": 50,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
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
    "CONCOR.NS":      {"name": "CONCOR",     "nse": "CONCOR",     "lot": 1250, "step": 10,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "VOLTAS.NS":      {"name": "VOLTAS",     "nse": "VOLTAS",     "lot": 375,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "TITAN.NS":       {"name": "TITAN",      "nse": "TITAN",      "lot": 175,  "step": 50,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "ABB.NS":         {"name": "ABB",        "nse": "ABB",        "lot": 125,  "step": 100, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "RELIANCE.NS":    {"name": "RELIANCE",   "nse": "RELIANCE",   "lot": 500,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "HDFCBANK.NS":    {"name": "HDFCBANK",   "nse": "HDFCBANK",   "lot": 1100, "step": 10,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "ICICIBANK.NS":   {"name": "ICICIBANK",  "nse": "ICICIBANK",  "lot": 700,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "SBIN.NS":        {"name": "SBIN",       "nse": "SBIN",       "lot": 750,  "step": 10,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "TATASTEEL.NS":   {"name": "TATASTEEL",  "nse": "TATASTEEL",  "lot": 5500, "step": 2.5, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "HAL.NS":         {"name": "HAL",        "nse": "HAL",        "lot": 150,  "step": 50,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "BEL.NS":         {"name": "BEL",        "nse": "BEL",        "lot": 2850, "step": 5,   "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "TRENT.NS":       {"name": "TRENT",      "nse": "TRENT",      "lot": 100,  "step": 50,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "CAMS.NS":        {"name": "CAMS",       "nse": "CAMS",       "lot": 750,  "step": 10,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "NTPC.NS":        {"name": "NTPC",       "nse": "NTPC",       "lot": 1500, "step": 5,   "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "ADANIPORTS.NS":  {"name": "ADANIPORTS", "nse": "ADANIPORTS", "lot": 400,  "step": 20,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "INDHOTEL.NS":    {"name": "INDHOTEL",   "nse": "INDHOTEL",   "lot": 1000, "step": 10,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "MOTHERSON.NS":   {"name": "MOTHERSON",  "nse": "MOTHERSON",  "lot": 4100, "step": 2.5, "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "NYKAA.NS":       {"name": "NYKAA",      "nse": "NYKAA",      "lot": 3125, "step": 5,   "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
    "SUPREMEIND.NS":  {"name": "SUPREMEIND", "nse": "SUPREMEIND", "lot": 175,  "step": 50,  "is_idx": False, "exp_rule": "NSE_MONTHLY_TUE"},
}


def _tick(x: float) -> float:
    """Round to the 0.05 NSE option tick."""
    return round(round(x / 0.05) * 0.05, 2)


def is_market_open_now() -> bool:
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    return (
        now.weekday() < 5
        and now.date() not in NSE_HOLIDAYS_2026
        and (9, 15) <= (now.hour, now.minute) < (15, 30)
    )


# ==============================================================================
# 🗓️ 4. HOLIDAY-ADJUSTED EXPIRY ENGINE & SELF-HEALING DHAN LIVE ENGINE
# ==============================================================================
def adjust_for_nse_holiday(exp_dt: date) -> date:
    while exp_dt in NSE_HOLIDAYS_2026 or exp_dt.weekday() >= 5:
        exp_dt -= timedelta(days=1)
    return exp_dt


def _last_weekday_of_month(year: int, month: int, target_weekday: int) -> date:
    cal = calendar.monthcalendar(year, month)
    days = [week[target_weekday] for week in cal if week[target_weekday] != 0]
    return date(year, month, days[-1])


def get_exchange_accurate_expiry(meta: dict, min_dte: int) -> tuple[int, str]:
    ist = pytz.timezone("Asia/Kolkata")
    today = datetime.now(ist).date()
    rule = meta.get("exp_rule", "NSE_MONTHLY_TUE")

    if rule == "NSE_MONTHLY_TUE":
        y, m = today.year, today.month
        for _ in range(4):
            raw_exp = _last_weekday_of_month(y, m, calendar.TUESDAY)
            exp_dt = adjust_for_nse_holiday(raw_exp)
            dte = (exp_dt - today).days
            if dte >= min_dte:
                return dte, exp_dt.strftime("%d%b").upper()
            m += 1
            if m > 12:
                m = 1
                y += 1

    elif rule == "NSE_WEEKLY_TUE":
        target_dt = today + timedelta(days=min_dte)
        while target_dt.weekday() != calendar.TUESDAY:
            target_dt += timedelta(days=1)
        exp_dt = adjust_for_nse_holiday(target_dt)
        dte = (exp_dt - today).days
        return dte, exp_dt.strftime("%d%b").upper()

    elif rule == "BSE_WEEKLY_THU":
        target_dt = today + timedelta(days=min_dte)
        while target_dt.weekday() != calendar.THURSDAY:
            target_dt += timedelta(days=1)
        exp_dt = adjust_for_nse_holiday(target_dt)
        dte = (exp_dt - today).days
        return dte, exp_dt.strftime("%d%b").upper()

    exp_dt = adjust_for_nse_holiday(_last_weekday_of_month(today.year, today.month, calendar.TUESDAY))
    return max((exp_dt - today).days, min_dte), exp_dt.strftime("%d%b").upper()


class DhanOptionEngine:
    def __init__(self, client_id: str, access_token: str):
        self.client_id = client_id
        self.access_token = access_token
        self.base_url = "https://api.dhan.co/v2"
        self.enabled = bool(self.client_id and self.access_token)
        self.eq_sec_map = {}
        self.lot_map = {}
        self.expiry_cache = {}
        self.chain_cache = {}
        self.last_call_start = 0.0
        self.last_call_end = 0.0
        self.chain_gap = 3.8
        self.consec_429 = 0
        self.attempted_401_heal = False
        self.live_quotes_fetched = 0
        self.session = requests.Session()
        self.session.headers.update(self._headers())
        self._load_public_security_master()

    def _headers(self):
        return {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _try_self_heal_401(self) -> bool:
        if self.attempted_401_heal:
            return False
        self.attempted_401_heal = True
        try:
            import subprocess
            refresh_script = "/home/ubuntu/scanners/refresh_token.py"
            if os.path.exists(refresh_script):
                print("🔄 Dhan HTTP 401 detected! Triggering self-healing refresh_token.py...")
                subprocess.run([sys.executable, refresh_script], timeout=25, check=False)
                if os.path.exists(ENV_FILE_PATH):
                    load_dotenv(ENV_FILE_PATH, override=True)
                    new_tok = os.environ.get("DHAN_ACCESS_TOKEN", "").strip().strip('"').strip("'")
                    if new_tok and new_tok != self.access_token:
                        self.access_token = new_tok
                        self.session.headers.update(self._headers())
                        print("✅ Hot-reloaded refreshed Dhan JWT token into active session!")
                        return True
        except Exception as e:
            print(f"⚠️ Self-heal 401 failed: {e}")
        return False

    def _throttle(self):
        now = time.time()
        wait = max(
            self.chain_gap - (now - self.last_call_start),
            (self.chain_gap - 0.5) - (now - self.last_call_end),
        )
        if wait > 0:
            time.sleep(wait)
        self.last_call_start = time.time()

    def _on_429(self, label: str):
        self.consec_429 += 1
        self.chain_gap = min(self.chain_gap + 0.8, 6.0)
        print(f"⚠️ Dhan {label} HTTP 429 -- cooling down 6s, gap now {self.chain_gap:.1f}s")
        time.sleep(6)

    def _load_public_security_master(self):
        cache_file = "dhan_scrip_master_cache.csv"
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        ist_zone = pytz.timezone("Asia/Kolkata")

        is_cached_today = False
        if os.path.exists(cache_file):
            file_mtime = datetime.fromtimestamp(os.path.getmtime(cache_file), ist_zone)
            if file_mtime.date() == datetime.now(ist_zone).date():
                is_cached_today = True

        try:
            if is_cached_today:
                print("Loading Dhan scrip master from local cache...")
                df = pd.read_csv(cache_file, low_memory=False)
            else:
                print("Downloading fresh Dhan scrip master CSV from Dhan...")
                r = requests.get(url, timeout=30)
                if r.status_code != 200:
                    if os.path.exists(cache_file):
                        print("Download failed. Falling back to existing local cache...")
                        df = pd.read_csv(cache_file, low_memory=False)
                    else:
                        print("Error: Failed to download scrip master and no cache found.")
                        return
                else:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        f.write(r.text)
                    df = pd.read_csv(cache_file, low_memory=False)

            df.columns = [str(c).strip() for c in df.columns]

            eq_df = df[(df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_INSTRUMENT_NAME"] == "EQUITY")]
            for sym, sec_id in zip(eq_df["SEM_TRADING_SYMBOL"], eq_df["SEM_SMST_SECURITY_ID"]):
                if pd.notna(sec_id):
                    self.eq_sec_map[str(sym).strip().upper()] = int(sec_id)

            fno_df = df[df["SEM_INSTRUMENT_NAME"].isin(["FUTSTK", "FUTIDX", "OPTSTK", "OPTIDX"])].copy()
            if not fno_df.empty and "SEM_EXPIRY_DATE" in fno_df.columns:
                today_str = datetime.now(ist_zone).strftime("%Y-%m-%d")
                fno_df["EXP_STR"] = fno_df["SEM_EXPIRY_DATE"].astype(str).str.slice(0, 10)
                fno_df = fno_df[fno_df["EXP_STR"] >= today_str].sort_values("EXP_STR")

            target_symbols = {m["nse"] for m in FNO_UNIVERSE.values()}
            for _, row in fno_df.iterrows():
                lot_val = pd.to_numeric(row.get("SEM_LOT_UNITS"), errors="coerce")
                if pd.isna(lot_val) or lot_val <= 0:
                    continue
                raw_tsym = str(row.get("SEM_TRADING_SYMBOL", "")).strip().upper()
                base_sym = re.split(r"[-_\s]", raw_tsym)[0]
                if base_sym not in target_symbols:
                    custom_sym = str(row.get("SEM_CUSTOM_SYMBOL", "")).strip().upper()
                    base_sym = custom_sym.split()[0] if custom_sym else ""
                if base_sym in target_symbols and base_sym not in self.lot_map:
                    self.lot_map[base_sym] = int(round(float(lot_val)))

            updated_count = 0
            for tk, meta in FNO_UNIVERSE.items():
                nse_sym = meta["nse"]
                if nse_sym in self.lot_map and self.lot_map[nse_sym] > 0:
                    meta["lot"] = self.lot_map[nse_sym]
                    updated_count += 1
            print(f"✅ Public Dhan Scrip Master Synced: {updated_count}/{len(FNO_UNIVERSE)} Live Lot Sizes Verified!")
        except Exception as e:
            print(f"⚠️ Public Scrip Master lot-sync warning: {e}")

    def _fetch_expiry_list(self, sec_id: int, seg: str, cache_key: str) -> list:
        if cache_key in self.expiry_cache:
            return self.expiry_cache[cache_key]
        if self.consec_429 >= 2:
            return []
        for attempt in (1, 2):
            self._throttle()
            try:
                resp = self.session.post(
                    f"{self.base_url}/optionchain/expirylist",
                    json={"UnderlyingScrip": int(sec_id), "UnderlyingSeg": seg},
                    timeout=10,
                )
            except Exception:
                self.last_call_end = time.time()
                return []
            self.last_call_end = time.time()
            if resp.status_code == 401 and attempt == 1 and self._try_self_heal_401():
                continue
            if resp.status_code == 429:
                self._on_429("ExpiryList")
                if attempt == 1:
                    continue
            break
        if resp.status_code == 200:
            self.consec_429 = 0
            data = resp.json().get("data", [])
            if data:
                self.expiry_cache[cache_key] = data
            return data
        return []

    def _fetch_option_chain(self, sec_id: int, seg: str, expiry: str):
        ck = (int(sec_id), expiry)
        if ck in self.chain_cache:
            return self.chain_cache[ck]
        if self.consec_429 >= 2:
            return None
        for attempt in (1, 2):
            self._throttle()
            try:
                resp = self.session.post(
                    f"{self.base_url}/optionchain",
                    json={"UnderlyingScrip": int(sec_id), "UnderlyingSeg": seg, "Expiry": expiry},
                    timeout=15,
                )
            except Exception:
                self.last_call_end = time.time()
                return None
            self.last_call_end = time.time()
            if resp.status_code == 401 and attempt == 1 and self._try_self_heal_401():
                continue
            if resp.status_code == 429:
                self._on_429("OptionChain")
                if attempt == 1:
                    continue
            break
        if resp.status_code == 200:
            self.consec_429 = 0
            data = resp.json().get("data")
            self.chain_cache[ck] = data
            return data
        return None

    @staticmethod
    def _compute_chain_structure(oc_data: dict, und_ltp: float) -> dict:
        """Extract PCR, Max Pain, Call Supply Wall, and Put Demand Floor in O(K) memory."""
        strikes = []
        ce_ois = []
        pe_ois = []
        for k_str, node in oc_data.items():
            try:
                k_val = float(k_str)
            except Exception:
                continue
            # Filter to active strikes within +/- 15% of spot to avoid deep illiquid noise
            if abs(k_val - und_ltp) / max(und_ltp, 1.0) > 0.15:
                continue
            ce_leg = node.get("ce") or {}
            pe_leg = node.get("pe") or {}
            c_oi = float(ce_leg.get("oi") or ce_leg.get("open_interest") or 0.0)
            p_oi = float(pe_leg.get("oi") or pe_leg.get("open_interest") or 0.0)
            strikes.append(k_val)
            ce_ois.append(c_oi)
            pe_ois.append(p_oi)

        if not strikes:
            return {"pcr": 1.0, "max_pain": und_ltp, "call_wall": und_ltp * 1.05, "put_wall": und_ltp * 0.95}

        k_arr = np.array(strikes, dtype=float)
        c_arr = np.array(ce_ois, dtype=float)
        p_arr = np.array(pe_ois, dtype=float)

        tot_ce = float(c_arr.sum())
        tot_pe = float(p_arr.sum())
        pcr = round(tot_pe / tot_ce, 2) if tot_ce > 0 else 1.0

        # Call OI Supply Wall (at or above spot) & Put OI Demand Wall (at or below spot)
        up_mask = k_arr >= und_ltp * 0.995
        dn_mask = k_arr <= und_ltp * 1.005
        call_wall = float(k_arr[up_mask][np.argmax(c_arr[up_mask])]) if np.any(up_mask) and c_arr[up_mask].max() > 0 else und_ltp * 1.05
        put_wall = float(k_arr[dn_mask][np.argmax(p_arr[dn_mask])]) if np.any(dn_mask) and p_arr[dn_mask].max() > 0 else und_ltp * 0.95

        # Vectorized Max Pain calculation
        # For candidate settlement S: Call payout = max(0, S - K) * CE_OI; Put payout = max(0, K - S) * PE_OI
        diff = k_arr[:, None] - k_arr[None, :]  # shape (N_settlement, N_strike)
        call_pain = np.maximum(0.0, diff) @ c_arr
        put_pain = np.maximum(0.0, -diff) @ p_arr
        total_pain = call_pain + put_pain
        max_pain = float(k_arr[int(np.argmin(total_pain))]) if (tot_ce + tot_pe) > 0 else und_ltp

        return {
            "pcr": pcr,
            "max_pain": max_pain,
            "call_wall": call_wall,
            "put_wall": put_wall,
        }

    def get_live_atm_option(self, meta: dict, atm_strike: float, opt_type: str, min_dte: int):
        if not self.enabled or self.consec_429 >= 2:
            return None
        sec_id = meta.get("dhan_id") or self.eq_sec_map.get(meta["nse"])
        seg = meta.get("dhan_seg", "NSE_EQ")
        if not sec_id:
            return None

        try:
            cache_key = meta["nse"] if meta["is_idx"] else "NSE_STOCKS"
            expiries = self._fetch_expiry_list(int(sec_id), seg, cache_key)
            if not expiries:
                return None

            ist = pytz.timezone("Asia/Kolkata")
            today = datetime.now(ist).date()
            chosen_expiry = None
            chosen_dte = None
            for exp_str in sorted(expiries):
                try:
                    exp_dt = datetime.strptime(str(exp_str)[:10], "%Y-%m-%d").date()
                    dte_days = (exp_dt - today).days
                    if dte_days >= min_dte:
                        chosen_expiry = str(exp_str)[:10]
                        chosen_dte = dte_days
                        break
                except Exception:
                    continue
            if not chosen_expiry:
                return None

            chain_data = self._fetch_option_chain(int(sec_id), seg, chosen_expiry)
            if not chain_data or not chain_data.get("oc"):
                return None

            oc_data = chain_data["oc"]
            und_ltp = float(chain_data.get("last_price") or atm_strike)
            strike_keys = [float(k) for k in oc_data.keys()]
            if not strike_keys:
                return None
            best_strike = min(strike_keys, key=lambda x: abs(x - und_ltp))
            raw_key = next(k for k in oc_data.keys() if abs(float(k) - best_strike) < 1e-4)
            leg = oc_data.get(raw_key, {}).get("ce" if opt_type == "CE" else "pe", {})
            ltp = float(leg.get("last_price") or 0.0)
            if ltp <= 0.5:
                return None

            bid = float(leg.get("top_bid_price") or 0.0)
            ask = float(leg.get("top_ask_price") or 0.0)
            greeks = leg.get("greeks") or {}
            exp_label = datetime.strptime(chosen_expiry, "%Y-%m-%d").strftime("%d%b").upper()

            structure = self._compute_chain_structure(oc_data, und_ltp)
            self.live_quotes_fetched += 1
            return {
                "ltp": ltp,
                "bid": bid,
                "ask": ask,
                "und_ltp": und_ltp,
                "delta": max(abs(float(greeks.get("delta") or 0.48)), 0.25),
                "gamma": max(float(greeks.get("gamma") or 0.0015), 0.0001),
                "theta": abs(float(greeks.get("theta") or 0.0)),
                "iv": max(float(leg.get("implied_volatility") or 18.0) / 100.0, 0.11),
                "strike": best_strike,
                "dte": chosen_dte,
                "exp_tag": exp_label,
                "pcr": structure["pcr"],
                "max_pain": structure["max_pain"],
                "call_wall": structure["call_wall"],
                "put_wall": structure["put_wall"],
            }
        except Exception:
            return None


# ==============================================================================
# 🕸️ 5. LIVE OI SPURTS & INSTITUTIONAL SUPPLY/DEMAND ZONE INDICATORS
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


def get_closed_bar_slice(df: pd.DataFrame, is_intraday: bool) -> pd.DataFrame:
    if df is None or df.empty or len(df) < 30:
        return df
    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    last_ts = df.index[-1]
    if hasattr(last_ts, "tzinfo") and last_ts.tzinfo is not None:
        last_ts = last_ts.astimezone(ist)
    else:
        last_ts = ist.localize(last_ts)
    bar_end = last_ts + timedelta(minutes=15) if is_intraday else last_ts.replace(hour=15, minute=30, second=0, microsecond=0)
    if now_ist.weekday() < 5 and bar_end > now_ist:
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
        theta_annual = (
            -(S * norm.pdf(d1) * sigma) / (2 * np.sqrt(T))
            - r * K * np.exp(-r * T) * norm.cdf(d2)
        )
    else:
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        delta = -norm.cdf(-d1)
        theta_annual = (
            -(S * norm.pdf(d1) * sigma) / (2 * np.sqrt(T))
            + r * K * np.exp(-r * T) * norm.cdf(-d2)
        )
    return max(float(price), 1.0), abs(float(delta)), float(gamma), abs(float(theta_annual)) / 365.0


def get_dynamic_strike_step(spot: float, meta: dict) -> float:
    if meta["is_idx"] or meta.get("step", 0) >= 50:
        return float(meta["step"])
    if spot < 250:
        return 2.5 if meta["step"] == 2.5 else 5.0
    elif spot < 600:
        return 5.0 if spot < 400 else 10.0
    elif spot < 1500:
        return 10.0 if spot < 900 else 20.0
    elif spot < 3000:
        return 20.0 if spot < 2000 else 50.0
    elif spot < 6000:
        return 50.0
    return 100.0


def compute_daily_indicators(df):
    """
    Upgraded with Dual Bollinger Bands (BB 38 & BB 61), RSI 14 vs RSI-SMA 14,
    RVOL, IVP Proxy, and Institutional Demand/Supply Rejection Guards.
    """
    df = df.copy()
    c = df["Close"]
    o = df["Open"]
    h = df["High"]
    l = df["Low"]

    df["EMA21"] = c.ewm(span=21, adjust=False).mean()
    df["EMA55"] = c.ewm(span=55, adjust=False).mean()

    # Dual Bollinger Bands matching BANKNIFTY institutional chart (BB 38 & BB 61)
    df["BB_SMA_38"] = c.rolling(38).mean()
    std_38 = c.rolling(38).std(ddof=0)
    df["BB_UP_38"] = df["BB_SMA_38"] + (2.0 * std_38)
    df["BB_LO_38"] = df["BB_SMA_38"] - (2.0 * std_38)

    df["BB_SMA_61"] = c.rolling(61).mean()
    std_61 = c.rolling(61).std(ddof=0)
    df["BB_UP_61"] = df["BB_SMA_61"] + (2.0 * std_61)
    df["BB_LO_61"] = df["BB_SMA_61"] - (2.0 * std_61)

    # RSI 14 & RSI SMA 14
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["RSI"] = (100 - (100 / (1 + (gain / loss.replace(0, np.nan))))).fillna(50.0)
    df["RSI_SMA"] = df["RSI"].rolling(14).mean().fillna(50.0)

    # ATR, HV20, and 252-day Volatility Percentile (IVP Proxy)
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
        axis=1,
    ).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    df["HV20"] = np.log(c / c.shift(1)).rolling(20).std() * np.sqrt(252)

    hv_min = df["HV20"].rolling(252, min_periods=40).min()
    hv_max = df["HV20"].rolling(252, min_periods=40).max()
    df["IVP_PROXY"] = (((df["HV20"] - hv_min) / (hv_max - hv_min).replace(0, np.nan)) * 100.0).fillna(50.0)

    # Relative Volume (RVOL)
    vol_sma20 = df["Volume"].rolling(20).mean().replace(0, np.nan)
    df["RVOL"] = (df["Volume"] / vol_sma20).fillna(1.0)

    # Institutional Demand & Supply Zone Guards (BankNifty Lower-BB Hammer Rejection Fix)
    bar_range = (h - l).replace(0, np.nan)
    lower_wick_ratio = ((c - l) / bar_range).fillna(0.5)
    upper_wick_ratio = ((h - c) / bar_range).fillna(0.5)

    demand_bounce = (
        ((l <= df["BB_LO_38"] * 1.006) | (l <= df["BB_LO_61"] * 1.006))
        & (c > o)
        & (lower_wick_ratio >= 0.55)
    )
    supply_rejection = (
        ((h >= df["BB_UP_38"] * 0.994) | (h >= df["BB_UP_61"] * 0.994))
        & (c < o)
        & (upper_wick_ratio >= 0.55)
    )

    df["CE_Signal"] = (
        (c >= df["EMA21"] * 0.997)
        & (c < df["BB_UP_38"] * 0.995)
        & (df["RSI"] >= 46)
        & (df["RSI"] <= 70)
        & (df["RSI"] >= df["RSI_SMA"] * 0.98)
        & (~supply_rejection)
    )
    df["PE_Signal"] = (
        (c <= df["EMA21"] * 1.003)
        & (c > df["BB_LO_38"] * 1.005)
        & (df["RSI"] >= 30)
        & (df["RSI"] <= 54)
        & (df["RSI"] <= df["RSI_SMA"] * 1.02)
        & (~demand_bounce)
    )
    return df


def compute_intraday_15m_indicators(df, is_index=False):
    """
    Upgraded 15M Intraday Engine with VWAP Anchor, Dual BB (38 & 61),
    RSI vs RSI-SMA14, RVOL, and Demand/Supply Zone Rejection Guards.
    """
    df = df.copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
    c = df["Close"]
    o = df["Open"]
    h = df["High"]
    l = df["Low"]

    df["EMA9"] = c.ewm(span=9, adjust=False).mean()
    df["EMA21"] = c.ewm(span=21, adjust=False).mean()

    if is_index or (df["Volume"].sum() == 0):
        df["TREND_ANCHOR"] = df["EMA21"]
        df["RVOL"] = 1.0
    else:
        tp = (h + l + c) / 3.0
        vol_clean = df["Volume"].replace(0, 1)
        dates = pd.Series(df.index.date, index=df.index)
        df["TREND_ANCHOR"] = (tp * vol_clean).groupby(dates).cumsum() / vol_clean.groupby(dates).cumsum()
        vol_sma20 = df["Volume"].rolling(20).mean().replace(0, np.nan)
        df["RVOL"] = (df["Volume"] / vol_sma20).fillna(1.0)

    df["BB_SMA_38"] = c.rolling(38).mean()
    std_38 = c.rolling(38).std(ddof=0)
    df["BB_UP_38"] = df["BB_SMA_38"] + (2.0 * std_38)
    df["BB_LO_38"] = df["BB_SMA_38"] - (2.0 * std_38)

    df["BB_SMA_61"] = c.rolling(61).mean()
    std_61 = c.rolling(61).std(ddof=0)
    df["BB_UP_61"] = df["BB_SMA_61"] + (2.0 * std_61)
    df["BB_LO_61"] = df["BB_SMA_61"] - (2.0 * std_61)

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["RSI"] = (100 - (100 / (1 + (gain / loss.replace(0, np.nan))))).fillna(50.0)
    df["RSI_SMA"] = df["RSI"].rolling(14).mean().fillna(50.0)

    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
        axis=1,
    ).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    df["HV20"] = np.log(c / c.shift(1)).rolling(50).std() * np.sqrt(252 * 25)
    df["IVP_PROXY"] = 50.0

    bar_range = (h - l).replace(0, np.nan)
    lower_wick_ratio = ((c - l) / bar_range).fillna(0.5)
    upper_wick_ratio = ((h - c) / bar_range).fillna(0.5)

    demand_bounce = (
        ((l <= df["BB_LO_38"] * 1.003) | (l <= df["BB_LO_61"] * 1.003))
        & (c > o)
        & (lower_wick_ratio >= 0.55)
    )
    supply_rejection = (
        ((h >= df["BB_UP_38"] * 0.997) | (h >= df["BB_UP_61"] * 0.997))
        & (c < o)
        & (upper_wick_ratio >= 0.55)
    )

    df["CE_Signal"] = (
        (c >= df["TREND_ANCHOR"])
        & (df["EMA9"] >= df["EMA21"] * 0.998)
        & (c < df["BB_UP_38"])
        & (df["RSI"] >= 48)
        & (df["RSI"] <= 72)
        & (df["RSI"] >= df["RSI_SMA"] * 0.98)
        & (~supply_rejection)
    )
    df["PE_Signal"] = (
        (c <= df["TREND_ANCHOR"])
        & (df["EMA9"] <= df["EMA21"] * 1.002)
        & (c > df["BB_LO_38"])
        & (df["RSI"] >= 28)
        & (df["RSI"] <= 52)
        & (df["RSI"] <= df["RSI_SMA"] * 1.02)
        & (~demand_bounce)
    )
    return df


def backtest_signals(df, direction="CE", atr_mult=1.0, und_rr=1.8, max_bars=6, is_intraday=False, t1_on=True):
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
    i = 65

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
                    trade_r = (0.5 * t1_r_Lock + 0.5 * full_r_Lock) if t1_on else full_r_Lock
                    outcome_resolved = True
                    break
                if t1_on and highs[j] >= t1:
                    t1_reached = True
            else:
                curr_sl = entry if t1_reached else sl
                if highs[j] >= curr_sl:
                    trade_r = (0.5 * t1_r_Lock) if t1_reached else -1.0
                    outcome_resolved = True
                    break
                if lows[j] <= tgt:
                    trade_r = (0.5 * t1_r_Lock + 0.5 * full_r_Lock) if t1_on else full_r_Lock
                    outcome_resolved = True
                    break
                if t1_on and lows[j] <= t1:
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


def replay_executed(df: pd.DataFrame, direction: str, eps: float, kappa: float, stop_rel: float, rr: float,
                    cost_r: float, is_intraday: bool, max_bars: int, theta_bar: float = 0.0) -> dict:
    """Replay the EXECUTED option trade at every historical signal bar in OPTION-PREMIUM space."""
    sig = df["CE_Signal" if direction == "CE" else "PE_Signal"].values
    cl, hi, lo = df["Close"].values, df["High"].values, df["Low"].values
    n = len(df)
    sgn = 1.0 if direction == "CE" else -1.0
    tgt = rr * stop_rel
    t1_rel = (INTRA_T1_RR if is_intraday else SWING_T1_RR) * stop_rel

    if is_intraday:
        day_id = (df.index.year * 10000 + df.index.month * 100 + df.index.day).values
        mins   = (df.index.hour * 60 + df.index.minute).values
        last_of_day = np.zeros(n, dtype=int)
        last_of_day[n - 1] = n - 1
        for k in range(n - 2, -1, -1):
            last_of_day[k] = last_of_day[k + 1] if day_id[k] == day_id[k + 1] else k

    def prem(x):
        return max(eps * x + 0.5 * kappa * x * x, -1.0)

    out = []
    i = 65
    while i < n - 1:
        if not sig[i] or cl[i] <= 0 or (is_intraday and not (570 <= mins[i] <= 870)):
            i += 1
            continue
        entry = cl[i]
        end = last_of_day[i] if is_intraday else min(i + max_bars, n - 1)
        if end <= i:
            i += 1
            continue
        level, r_val, exit_j = -stop_rel, None, end
        t1_hit = False
        t1_r_lock = INTRA_T1_RR if is_intraday else SWING_T1_RR

        for j in range(i + 1, end + 1):
            fav = sgn * ((hi[j] if sgn > 0 else lo[j]) - entry) / entry
            adv = sgn * ((lo[j] if sgn > 0 else hi[j]) - entry) / entry
            decay = theta_bar * (j - i)
            p_hi, p_lo = prem(fav) - decay, prem(adv) - decay
            curr_stop = 0.0 if t1_hit else level

            if p_lo <= curr_stop:
                raw_r = (0.5 * t1_r_lock) if t1_hit else (level / stop_rel)
                r_val, exit_j = raw_r - cost_r, j
                break
            if p_hi >= tgt:
                raw_r = (0.5 * t1_r_lock + 0.5 * rr) if t1_hit else rr
                r_val, exit_j = raw_r - cost_r, j
                break
            if p_hi >= t1_rel:
                t1_hit = True

        if r_val is None:
            x = sgn * (cl[end] - entry) / entry
            rem_r = max(prem(x) - theta_bar * (end - i), level) / stop_rel
            raw_r = (0.5 * t1_r_lock + 0.5 * max(0.0, rem_r)) if t1_hit else rem_r
            r_val = raw_r - cost_r
        out.append(r_val)
        i = max(i + 1, exit_j)
    if not out:
        return {"n": 0, "exp": -1.0, "win": 0.0, "full_loss": 0.0}
    arr = np.array(out)
    return {
        "n": len(arr),
        "exp": float(arr.mean()),
        "win": float((arr > 0).mean() * 100.0),
        "full_loss": float((arr <= -0.9).mean() * 100.0),
    }


def build_option_trade_row(
    meta, df, opt_type, total_trades, expectancy, win_pct, india_vix, is_intraday, oi_tag, is_oi_boosted, dhan_engine: DhanOptionEngine
):
    latest = df.iloc[-1]
    spot = float(latest["Close"])
    atr = float(latest["ATR"])
    hv20 = float(latest["HV20"]) if not np.isnan(latest["HV20"]) else 0.22
    rvol = float(latest.get("RVOL", 1.0))
    ivp_proxy = float(latest.get("IVP_PROXY", 50.0))

    step = get_dynamic_strike_step(spot, meta)
    atm_strike = int(round(spot / step) * step) if step >= 5 else round(spot / step) * step

    # --- 1. NYKAA Trap Filter (Block Low-Volume + High-IVP Traps) ---
    if not meta["is_idx"] and not is_oi_boosted:
        if rvol < 0.80 and ivp_proxy > MAX_SAFE_IVP:
            return None

    min_dte_req = MIN_DTE_INTRADAY if is_intraday else MIN_DTE_POSITIONAL
    mkt_open = is_market_open_now()

    dhan_quote = dhan_engine.get_live_atm_option(meta, atm_strike, opt_type, min_dte=min_dte_req) if dhan_engine else None
    if dhan_quote:
        bid = dhan_quote["bid"]
        ask = dhan_quote["ask"]
        dte = dhan_quote["dte"] or min_dte_req
        max_allowed_spread = MAX_SPREAD_FRONT_PCT if dte <= 10 else MAX_SPREAD_FAR_PCT
        if mkt_open and bid > 0 and ask > 0:
            spread_pct = (ask - bid) / ((ask + bid) / 2.0) * 100.0
            if spread_pct > max_allowed_spread:
                return None
            opt_entry = ask
        else:
            opt_entry = dhan_quote["ltp"]

        delta      = dhan_quote["delta"]
        gamma      = dhan_quote["gamma"]
        theta      = dhan_quote["theta"]
        live_iv    = dhan_quote["iv"]
        und_ltp    = dhan_quote["und_ltp"]
        atm_strike = dhan_quote["strike"]
        exp_tag    = dhan_quote["exp_tag"]
        pcr        = dhan_quote["pcr"]
        call_wall  = dhan_quote["call_wall"]
        put_wall   = dhan_quote["put_wall"]
        src_flag   = "*"

        # --- 2. Live Vega / IV Crush Guard & Option Chain Wall Confluence ---
        if not meta["is_idx"] and hv20 > 0:
            if (live_iv / hv20) > MAX_IV_HV_RATIO and rvol < 1.15:
                return None

        und_atr_mult = INTRA_ATR_SL_MULT if is_intraday else SWING_ATR_SL_MULT
        und_rr_mult  = INTRA_UND_RR if is_intraday else SWING_UND_RR
        und_target_dist = atr * und_atr_mult * und_rr_mult

        # Block buying CE right into a Call OI Supply Wall when PCR is weak (< 0.78)
        if opt_type == "CE":
            if pcr < MIN_BULL_PCR and not is_oi_boosted:
                return None
            if call_wall > und_ltp and (call_wall - und_ltp) < (0.45 * und_target_dist) and pcr < 0.95:
                return None
        # Block buying PE right into a Put OI Demand Floor when PCR is bullish (> 1.28)
        else:
            if pcr > MAX_BEAR_PCR and not is_oi_boosted:
                return None
            if put_wall < und_ltp and (und_ltp - put_wall) < (0.45 * und_target_dist) and pcr > 1.05:
                return None
    else:
        dte, exp_tag = get_exchange_accurate_expiry(meta, min_dte=min_dte_req)
        iv = india_vix * 1.12 if meta["is_idx"] else max(hv20 * 1.05, india_vix * 1.35)
        opt_entry, delta, gamma, theta = black_scholes_greeks(
            S=spot, K=atm_strike, T=dte / 365.0, r=RISK_FREE_RATE, sigma=iv, option_type=opt_type
        )
        bid = ask = 0.0
        und_ltp = spot
        pcr = 1.0
        src_flag = ""

    strike_str = f"{int(atm_strike)}" if atm_strike == int(atm_strike) else f"{atm_strike:.1f}"
    lot_size = dhan_engine.lot_map.get(meta["nse"], meta["lot"]) if dhan_engine else meta["lot"]

    atr_mult = INTRA_ATR_SL_MULT if is_intraday else SWING_ATR_SL_MULT
    und_risk = atr * atr_mult
    raw_opt_sl_dist = delta * und_risk - 0.5 * gamma * (und_risk ** 2)
    max_pct_sl = 0.15 if meta["is_idx"] else (0.18 if is_intraday else 0.20)
    min_pct_sl = 0.10 if (is_intraday or meta["is_idx"]) else 0.12

    opt_entry = _tick(opt_entry)
    opt_risk = max(round(min(opt_entry * max_pct_sl, max(opt_entry * min_pct_sl, raw_opt_sl_dist)), 1), 0.3)
    opt_sl = _tick(max(0.5, opt_entry - opt_risk))
    actual_risk = max(round(opt_entry - opt_sl, 2), 0.2)

    target_rr = INTRA_OPT_RR if is_intraday else SWING_OPT_RR
    t1_rr = INTRA_T1_RR if is_intraday else SWING_T1_RR
    opt_t1 = _tick(opt_entry + (actual_risk * t1_rr))
    opt_tgt = _tick(opt_entry + (actual_risk * target_rr))

    risk_per_lot = max(actual_risk * lot_size, 1.0)
    cost_per_lot = max(opt_entry * lot_size, 1.0)

    if LOCK_TO_SINGLE_LOT:
        if risk_per_lot > MAX_SINGLE_LOT_RISK or cost_per_lot > MAX_CAPITAL_PER_TRADE:
            return None
        num_lots = 1
    else:
        lots_by_risk = int(TARGET_RISK_PER_TRADE // risk_per_lot)
        lots_by_cap = int(MAX_CAPITAL_PER_TRADE // cost_per_lot)
        num_lots = min(lots_by_risk, lots_by_cap)
        if num_lots < 1:
            return None

    # --- 3. Executed-Geometry Replay Gate (in Option-Premium Space) ---
    stop_rel = actual_risk / opt_entry
    eps_ = delta * und_ltp / opt_entry
    kap_ = gamma * und_ltp * und_ltp / opt_entry
    spread_r = (0.5 * (ask - bid) / actual_risk) if (mkt_open and ask > 0 and bid > 0) else 0.05
    spread_r = min(spread_r, 0.20)
    cost_r = COST_SLIPPAGE_R + spread_r
    theta_rel_day = min(theta / opt_entry, 0.20) if opt_entry > 0 else 0.0
    theta_bar = theta_rel_day / 25.0 if is_intraday else theta_rel_day

    ex = replay_executed(df, opt_type, eps_, kap_, stop_rel, target_rr, cost_r, is_intraday, SWING_MAX_DAYS, theta_bar)
    if EXEC_GATE_ENABLED:
        min_req_n = 6 if meta["is_idx"] else EXEC_MIN_SETUPS
        min_req_exp = 0.01 if meta["is_idx"] else EXEC_MIN_EXP_R
        if ex["n"] < min_req_n or ex["exp"] < min_req_exp:
            return None

    final_exp = ex["exp"] if ex["n"] >= 8 else expectancy
    final_win = ex["win"] if ex["n"] >= 8 else win_pct
    sample_n  = ex["n"] if ex["n"] >= 8 else total_trades

    # Bayesian Wilson-style sample shrinkage toward 46% prior
    shrunk_win = ((final_win * sample_n) + (46.0 * 8.0)) / (sample_n + 8.0)

    qty = num_lots * lot_size
    capital = int(round(opt_entry * qty))
    total_rupee_risk = int(round(actual_risk * qty))

    exp_rounded = round(final_exp, 2)
    win_rounded = round(shrunk_win, 1)
    quality_score = round((exp_rounded * 85.0) + (win_rounded * 1.25), 1)
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
        "PCR": pcr,
        "Expectancy": exp_rounded,
        "WinProb": win_rounded,
        "Score": quality_score,
        "OIBoost": 1 if is_oi_boosted else 0,
    }


def format_combined_table(idx_records, stk_records, title, now_str, dhan_engine: DhanOptionEngine, oi_count: int):
    lot_sync_cnt = len(dhan_engine.lot_map) if dhan_engine else 0
    if dhan_engine and dhan_engine.enabled and dhan_engine.live_quotes_fetched > 0:
        feed_str = f"Dhan Live ({dhan_engine.live_quotes_fetched}) + {lot_sync_cnt} Lots Synced"
    else:
        feed_str = f"Dhan Lot Master ({lot_sync_cnt}) + NSE OI ({oi_count})"

    col_hdr = (
        f"{'Contract':<19}|{'Exp':>5}|{'Entry':>6}|{'Qty':>4}|{'Cap(₹)':>6}|"
        f"{'SL':>6}|{'T1-50%':>6}|{'Target':>6}|{'OI_Flow':>7}|{'ExpR':>5}|{'Win%':>5}|{'Score':>5}"
    )
    sep = "-" * len(col_hdr)

    lines = [
        f"<b>🔥 {html.escape(title)}</b>",
        f"🕒 <code>{html.escape(now_str)}</code> | Feed: <code>{html.escape(feed_str)}</code> | Sizing: <code>1 Lot Exact</code>\n",
    ]

    if idx_records:
        lines.append("<b>📌 MAJOR INDICES F&amp;O (INSTITUTIONAL S/D + WILSON SCORE)</b>")
        lines.append("<pre>")
        lines.append(html.escape(col_hdr))
        lines.append(html.escape(sep))
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
        lines.append(f"<b>🚀 TOP {len(stk_records[:TOP_N_STOCKS])} STOCK OPTIONS (1-LOT EXCHANGE QTY &amp; QUALITY SCORE)</b>")
        lines.append("<pre>")
        lines.append(html.escape(col_hdr))
        lines.append(html.escape(sep))
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

    all_cands = list(idx_records) + list(stk_records[:TOP_N_STOCKS])
    prime_picks = [
        r for r in all_cands
        if r["Expectancy"] >= 0.08 and r["WinProb"] >= 49.0 and r["OIBoost"] == 1
    ]
    if not prime_picks:
        prime_picks = [r for r in all_cands if r["Expectancy"] >= 0.05 and r["WinProb"] >= 48.0]

    prime_picks.sort(key=lambda x: (x["OIBoost"], x["Score"]), reverse=True)
    top_two = prime_picks[:2]

    if top_two:
        lines.append("\n<b>🏆 TOP HIGH-CONVICTION TRADES (AUTO 3-STEP TRIAGE):</b>")
        for idx, p in enumerate(top_two, 1):
            lines.append(
                f"{idx}. <b>{html.escape(p['Contract'])} ({html.escape(p['Expiry'])})</b> "
                f"👉 Entry: <code>₹{p['Entry']:.2f}</code> | 1-Lot Qty: <code>{p['Qty']}</code> "
                f"| Cap: <code>₹{p['Capital']:,}</code> | Risk: <code>₹{p['RiskRs']:,}</code> "
                f"| SL: <code>{p['Stop']:.2f}</code> | T1: <code>{p['T1']:.2f}</code> | Tgt: <code>{p['Target']:.2f}</code> "
                f"| OI: <code>{html.escape(p['OI_Flow'].strip())}</code> | PCR: <code>{p['PCR']:.2f}</code> | <b>Score: {p['Score']:.1f}</b>"
            )

    lines.append(
        "\n💡 <i>* = Live Dhan Chain (PCR/Walls/IV Guard) | Dual-BB 38/61 + T1 Executed-Geometry Filtered</i>"
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


def main():
    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    now_str = now_ist.strftime("%d-%b %I:%M %p IST")
    current_minutes = now_ist.hour * 60 + now_ist.minute

    if EVENT_NAME == "schedule" and not FORCE_RUN:
        if (
            now_ist.weekday() >= 5
            or now_ist.date() in NSE_HOLIDAYS_2026
            or current_minutes < (9 * 60 + 15)
            or current_minutes > (15 * 60 + 45)
        ):
            print(f"⏸️ Outside Market Hours or NSE Holiday ({now_str}). Exiting.")
            return

    tickers = list(FNO_UNIVERSE.keys())
    india_vix = get_live_vix()
    oi_spurts_map = fetch_nse_oi_spurts_map()
    dhan_engine = DhanOptionEngine(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN)

    messages_sent = 0

    # 1. INTRADAY 15M SCALPS (FRESH CLOSED CANDLE ONLY + T1 ENABLED)
    raw_15m = yf.download(tickers, period="60d", interval="15m", group_by="ticker", progress=False)
    intra_idx, intra_stk = [], []

    for tk, meta in FNO_UNIVERSE.items():
        df15_raw = extract_ticker_df(raw_15m, tk)
        df15 = get_closed_bar_slice(df15_raw, is_intraday=True)
        if len(df15) < 85:
            continue
        df15 = compute_intraday_15m_indicators(df15, is_index=meta["is_idx"])
        last = df15.iloc[-1]

        today_bars = df15[df15.index.date == last.name.date()]
        day_open = float(today_bars["Open"].iloc[0]) if not today_bars.empty else float(last["Open"])
        price_chg_pct = ((float(last["Close"]) - day_open) / day_open) * 100.0
        oi_chg_pct = oi_spurts_map.get(meta["nse"], 0.0)

        opt_type = "CE" if float(last["Close"]) >= float(last["TREND_ANCHOR"]) else "PE"
        sig_col = "CE_Signal" if opt_type == "CE" else "PE_Signal"

        # Eliminate 30-min stale lag: require signal on the freshly closed bar
        if not bool(last[sig_col]):
            continue

        is_blocked, is_boosted, oi_tag = classify_institutional_oi(price_chg_pct, oi_chg_pct, opt_type)
        if is_blocked:
            continue

        tot, exp_r, wp = backtest_signals(df15, opt_type, INTRA_ATR_SL_MULT, INTRA_UND_RR, INTRA_MAX_BARS, True, t1_on=True)
        q_score = (round(exp_r, 2) * 85.0) + (round(wp, 1) * 1.25)

        if meta["is_idx"]:
            if tot >= 6 and exp_r > 0.0:
                intra_idx.append((q_score, is_boosted, meta, df15, opt_type, tot, exp_r, wp, oi_tag))
        else:
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
    intra_stk_rows = []
    for _, b, m, d, o, t, exp_r, wp, tag in intra_stk[:TOP_N_STOCKS + 10]:
        if len(intra_stk_rows) >= TOP_N_STOCKS:
            break
        row = build_option_trade_row(m, d, o, t, exp_r, wp, india_vix, True, tag, b, dhan_engine)
        if row:
            intra_stk_rows.append(row)

    intra_stk_rows.sort(key=lambda x: (x["OIBoost"], x["Score"]), reverse=True)

    if intra_idx_rows or intra_stk_rows:
        msg = format_combined_table(
            intra_idx_rows, intra_stk_rows, "15M INTRADAY F&O SCALPS (LOCKED 1:1.75 RR)", now_str, dhan_engine, len(oi_spurts_map)
        )
        send_telegram_alert(msg)
        messages_sent += 1

    # 2. POSITIONAL 3-5D SWINGS (DUAL-BB 38/61 + T1 ENABLED)
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

        if not bool(last[sig_col]):
            continue

        is_blocked, is_boosted, oi_tag = classify_institutional_oi(price_chg_pct, oi_chg_pct, opt_type)
        if is_blocked:
            continue

        tot, exp_r, wp = backtest_signals(df1d, opt_type, SWING_ATR_SL_MULT, SWING_UND_RR, SWING_MAX_DAYS, False, t1_on=True)
        q_score = (round(exp_r, 2) * 85.0) + (round(wp, 1) * 1.25)

        if meta["is_idx"]:
            if tot >= 6 and exp_r > 0.0:
                swing_idx.append((q_score, is_boosted, meta, df1d, opt_type, tot, exp_r, wp, oi_tag))
        else:
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
    swing_stk_rows = []
    for _, b, m, d, o, t, exp_r, wp, tag in swing_stk[:TOP_N_STOCKS + 10]:
        if len(swing_stk_rows) >= TOP_N_STOCKS:
            break
        row = build_option_trade_row(m, d, o, t, exp_r, wp, india_vix, False, tag, b, dhan_engine)
        if row:
            swing_stk_rows.append(row)

    swing_stk_rows.sort(key=lambda x: (x["OIBoost"], x["Score"]), reverse=True)

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
            f"but no setups passed the Institutional S/D Zone, Vega Guard &amp; Executed-Geometry filters."
        )
        send_telegram_alert(fallback_msg)


if __name__ == "__main__":
    main()
