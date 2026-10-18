import os
import re
import io
import sys
import time
import html
import warnings
warnings.filterwarnings('ignore')

from datetime import datetime
import calendar
import pytz
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from scipy.stats import norm

# ==============================================================================
# 🔐 1. CREDENTIALS & ENVIRONMENT VARIABLES
# ==============================================================================
RAW_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
RAW_CHATID = os.environ.get("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "manual")

token_match = re.search(r'(\d+:[A-Za-z0-9_-]+)', RAW_TOKEN)
TELEGRAM_BOT_TOKEN = token_match.group(1) if token_match else RAW_TOKEN.lstrip("bot").strip('"').strip("'")
TELEGRAM_CHAT_ID   = RAW_CHATID

DHAN_CLIENT_ID    = os.environ.get("DHAN_CLIENT_ID", "").strip().strip('"').strip("'")
DHAN_ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "").strip().strip('"').strip("'")

# ==============================================================================
# ⚖️ 2. STRICT INSTITUTIONAL RISK MANAGEMENT PARAMETERS
# ==============================================================================
RISK_PER_TRADE        = 3000      # Max Rupee risk per option trade (₹3,000 strict cap)
MAX_CAPITAL_PER_TRADE = 45000     # Max Option Premium outlay per trade (₹45,000 strict cap)
TOP_N_STOCKS          = 10        # Top 10 Stock Options

INTRA_OPT_RR          = 2.0       # Locked 1:2.0 Option Risk:Reward for Intraday
INTRA_UND_RR          = 1.6       # Underlying ATR move required in backtest
INTRA_ATR_SL_MULT     = 0.9       # 0.9x ATR stop loss
INTRA_MAX_BARS        = 12        # 12 bars intraday window
INTRA_MIN_SETUPS      = 5         # Minimum historical setups
INTRA_MIN_WIN_PROB    = 22.0      # Minimum Win% filter

SWING_OPT_RR          = 2.5       # Locked 1:2.5 Option Risk:Reward for Positional
SWING_UND_RR          = 1.8       # Underlying ATR move required in backtest
SWING_ATR_SL_MULT     = 1.0       # 1.0x Daily ATR stop loss
SWING_MAX_DAYS        = 6         # 6 trading days max hold
SWING_MIN_SETUPS      = 5         # Minimum historical setups
SWING_MIN_WIN_PROB    = 22.0      # Minimum Win% filter

RISK_FREE_RATE        = 0.068

# ==============================================================================
# 📋 3. VERIFIED LIQUID NSE F&O UNIVERSE (2026 LOT SIZES)
# ==============================================================================
FNO_UNIVERSE = {
    "^NSEI":                 {"name": "NIFTY",      "nse": "NIFTY",      "lot": 65,   "step": 50,  "is_idx": True,  "dhan_id": 13, "dhan_seg": "IDX_I"},
    "^NSEBANK":              {"name": "BANKNIFTY",  "nse": "BANKNIFTY",  "lot": 30,   "step": 100, "is_idx": True,  "dhan_id": 25, "dhan_seg": "IDX_I"},
    "^BSESN":                {"name": "SENSEX",     "nse": "SENSEX",     "lot": 20,   "step": 100, "is_idx": True,  "dhan_id": 51, "dhan_seg": "IDX_I"},
    "NIFTY_MID_SELECT.NS":   {"name": "MIDCPNIFT",  "nse": "MIDCPNIFTY", "lot": 120,  "step": 25,  "is_idx": True,  "dhan_id": 442, "dhan_seg": "IDX_I"},
    "NIFTY_FIN_SERVICE.NS":  {"name": "FINNIFTY",   "nse": "FINNIFTY",   "lot": 65,   "step": 50,  "is_idx": True,  "dhan_id": 27, "dhan_seg": "IDX_I"},
    "^CNXIT":                {"name": "NIFTYIT",    "nse": "NIFTYIT",    "lot": 25,   "step": 100, "is_idx": True,  "dhan_id": 19, "dhan_seg": "IDX_I"},

    "ETERNAL.NS":     {"name": "ETERNAL",    "nse": "ETERNAL",    "lot": 2000, "step": 5,   "is_idx": False},
    "ANGELONE.NS":    {"name": "ANGELONE",   "nse": "ANGELONE",   "lot": 200,  "step": 50,  "is_idx": False},
    "TECHM.NS":       {"name": "TECHM",      "nse": "TECHM",      "lot": 600,  "step": 20,  "is_idx": False},
    "FEDERALBNK.NS":  {"name": "FEDERALBNK", "nse": "FEDERALBNK", "lot": 5000, "step": 2.5, "is_idx": False},
    "WIPRO.NS":       {"name": "WIPRO",      "nse": "WIPRO",      "lot": 3000, "step": 2.5, "is_idx": False},
    "LTF.NS":         {"name": "LTF",        "nse": "LTF",        "lot": 4462, "step": 2.5, "is_idx": False},
    "CROMPTON.NS":    {"name": "CROMPTON",   "nse": "CROMPTON",   "lot": 1800, "step": 5,   "is_idx": False},
    "PERSISTENT.NS":  {"name": "PERSISTENT", "nse": "PERSISTENT", "lot": 100,  "step": 100, "is_idx": False},
    "TCS.NS":         {"name": "TCS",        "nse": "TCS",        "lot": 175,  "step": 50,  "is_idx": False},
    "OBEROIRLTY.NS":  {"name": "OBEROIRLTY", "nse": "OBEROIRLTY", "lot": 350,  "step": 20,  "is_idx": False},
    "DIXON.NS":       {"name": "DIXON",      "nse": "DIXON",      "lot": 50,   "step": 200, "is_idx": False},
    "DLF.NS":         {"name": "DLF",        "nse": "DLF",        "lot": 825,  "step": 10,  "is_idx": False},
    "BHARTIARTL.NS":  {"name": "BHARTIARTL", "nse": "BHARTIARTL", "lot": 475,  "step": 20,  "is_idx": False},
    "AXISBANK.NS":    {"name": "AXISBANK",   "nse": "AXISBANK",   "lot": 625,  "step": 20,  "is_idx": False},
    "KOTAKBANK.NS":   {"name": "KOTAKBANK",  "nse": "KOTAKBANK",  "lot": 400,  "step": 20,  "is_idx": False},
    "MARUTI.NS":      {"name": "MARUTI",     "nse": "MARUTI",     "lot": 50,   "step": 100, "is_idx": False},
    "M&M.NS":         {"name": "M&M",        "nse": "M&M",        "lot": 175,  "step": 50,  "is_idx": False},
    "LT.NS":          {"name": "LT",         "nse": "LT",         "lot": 150,  "step": 50,  "is_idx": False},
    "INFY.NS":        {"name": "INFY",       "nse": "INFY",       "lot": 400,  "step": 20,  "is_idx": False},
    "AUROPHARMA.NS":  {"name": "AUROPHARMA", "nse": "AUROPHARMA", "lot": 550,  "step": 20,  "is_idx": False},
    "ZYDUSLIFE.NS":   {"name": "ZYDUSLIFE",  "nse": "ZYDUSLIFE",  "lot": 900,  "step": 20,  "is_idx": False},
    "RBLBANK.NS":     {"name": "RBLBANK",    "nse": "RBLBANK",    "lot": 2500, "step": 5,   "is_idx": False},
    "CONCOR.NS":      {"name": "CONCOR",     "nse": "CONCOR",     "lot": 1000, "step": 10,  "is_idx": False},
    "VOLTAS.NS":      {"name": "VOLTAS",     "nse": "VOLTAS",     "lot": 375,  "step": 20,  "is_idx": False},
    "TITAN.NS":       {"name": "TITAN",      "nse": "TITAN",      "lot": 175,  "step": 50,  "is_idx": False},
    "ABB.NS":         {"name": "ABB",        "nse": "ABB",        "lot": 125,  "step": 100, "is_idx": False},
    "RELIANCE.NS":    {"name": "RELIANCE",   "nse": "RELIANCE",   "lot": 500,  "step": 20,  "is_idx": False},
    "HDFCBANK.NS":    {"name": "HDFCBANK",   "nse": "HDFCBANK",   "lot": 550,  "step": 20,  "is_idx": False},
    "ICICIBANK.NS":   {"name": "ICICIBANK",  "nse": "ICICIBANK",  "lot": 700,  "step": 20,  "is_idx": False},
    "SBIN.NS":        {"name": "SBIN",       "nse": "SBIN",       "lot": 750,  "step": 10,  "is_idx": False},
    "TATASTEEL.NS":   {"name": "TATASTEEL",  "nse": "TATASTEEL",  "lot": 5500, "step": 2.5, "is_idx": False},
    "BAJFINANCE.NS":  {"name": "BAJFINANCE", "nse": "BAJFINANCE", "lot": 125,  "step": 100, "is_idx": False},
    "HAL.NS":         {"name": "HAL",        "nse": "HAL",        "lot": 150,  "step": 50,  "is_idx": False},
    "BEL.NS":         {"name": "BEL",        "nse": "BEL",        "lot": 2850, "step": 5,   "is_idx": False},
    "TRENT.NS":       {"name": "TRENT",      "nse": "TRENT",      "lot": 100,  "step": 100, "is_idx": False},
    "CAMS.NS":        {"name": "CAMS",       "nse": "CAMS",       "lot": 150,  "step": 50,  "is_idx": False},
    "NTPC.NS":        {"name": "NTPC",       "nse": "NTPC",       "lot": 1500, "step": 5,   "is_idx": False},
    "ADANIPORTS.NS":  {"name": "ADANIPORTS", "nse": "ADANIPORTS", "lot": 400,  "step": 20,  "is_idx": False},
    "INDHOTEL.NS":    {"name": "INDHOTEL",   "nse": "INDHOTEL",   "lot": 1000, "step": 10,  "is_idx": False},
    "MOTHERSON.NS":   {"name": "MOTHERSON",  "nse": "MOTHERSON",  "lot": 4100, "step": 2.5, "is_idx": False},
    "NYKAA.NS":       {"name": "NYKAA",      "nse": "NYKAA",      "lot": 3125, "step": 5,   "is_idx": False},
    "SUPREMEIND.NS":  {"name": "SUPREMEIND", "nse": "SUPREMEIND", "lot": 175,  "step": 50,  "is_idx": False},
}

# ==============================================================================
# 🔌 4. DHAN API v2 CLIENT
# ==============================================================================
class DhanOptionEngine:
    def __init__(self, client_id: str, access_token: str):
        self.client_id = client_id
        self.access_token = access_token
        self.base_url = "https://api.dhan.co/v2"
        self.enabled = bool(self.client_id and self.access_token)
        self.eq_sec_map = {}
        self.active_calls = 0
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
                print(f"✅ Dhan API Configured: Mapped {len(self.eq_sec_map)} NSE Security IDs.")
        except Exception as e:
            print(f"⚠️ Dhan Scrip Master load warning ({e}).")

    def get_live_atm_option(self, meta: dict, atm_strike: float, opt_type: str):
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

            oc_resp = requests.post(
                f"{self.base_url}/optionchain",
                headers=self._headers(),
                json={"UnderlyingScrip": int(sec_id), "UnderlyingSeg": seg, "Expiry": expiries[0]},
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
            return {
                "ltp": ltp,
                "delta": max(float(greeks.get("delta", 0.52)), 0.25),
                "gamma": max(float(greeks.get("gamma", 0.002)), 0.0001),
                "iv": max(float(leg.get("implied_volatility", 20.0)) / 100.0, 0.12),
                "strike": best_strike,
            }
        except Exception:
            return None

# ==============================================================================
# 🕸️ 5. LIVE NSE OI SPURTS SCRAPER
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
    except Exception:
        pass
    return oi_map

def classify_institutional_oi(price_chg_pct: float, oi_chg_pct: float, opt_type: str):
    if abs(oi_chg_pct) < 0.5:
        return False, False, "—"
    sign_str = f"{oi_chg_pct:+.0f}%"
    if price_chg_pct >= 0.0 and oi_chg_pct >= 3.0:
        if opt_type == "PE" and oi_chg_pct >= 8.0 and price_chg_pct >= 0.3:
            return True, False, f"🔥{sign_str}LB"
        return False, (opt_type == "CE"), f"🔥{sign_str}LB"
    if price_chg_pct < 0.0 and oi_chg_pct >= 3.0:
        if opt_type == "CE" and oi_chg_pct >= 8.0 and price_chg_pct <= -0.3:
            return True, False, f"🐻{sign_str}SB"
        return False, (opt_type == "PE"), f"🐻{sign_str}SB"
    if price_chg_pct >= 0.2 and oi_chg_pct <= -2.5:
        return False, (opt_type == "CE"), f"⚡{sign_str}SC"
    if price_chg_pct <= -0.2 and oi_chg_pct <= -2.5:
        return False, (opt_type == "PE"), f"💧{sign_str}LU"
    return False, False, f"{sign_str}OI"

# ==============================================================================
# 📈 6. TECHNICAL INDICATORS & GREEKS
# ==============================================================================
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
        for col in ['Open', 'Close', 'High', 'Low']:
            if col not in df.columns:
                return pd.DataFrame()
            if isinstance(df[col], pd.DataFrame):
                df[col] = df[col].iloc[:, 0]
        if 'Volume' not in df.columns:
            df['Volume'] = 0
        else:
            if isinstance(df['Volume'], pd.DataFrame):
                df['Volume'] = df['Volume'].iloc[:, 0]
            df['Volume'] = df['Volume'].fillna(0)
        return df.dropna(subset=['Open', 'Close', 'High', 'Low']).copy()
    except Exception:
        return pd.DataFrame()

def black_scholes_greeks(S, K, T, r, sigma, option_type="CE"):
    T = max(T, 2.0 / 365.0)
    sigma = max(sigma, 0.12)
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

def get_dte(is_intraday=False):
    ist = pytz.timezone('Asia/Kolkata')
    today = datetime.now(ist).replace(tzinfo=None)
    if is_intraday:
        return max((3 - today.weekday()) % 7, 3)
    cal = calendar.monthcalendar(today.year, today.month)
    thursdays = [week[calendar.THURSDAY] for week in cal if week[calendar.THURSDAY] != 0]
    last_thursday = datetime(today.year, today.month, thursdays[-1])
    dte = (last_thursday - today).days
    return dte if dte >= 6 else dte + 28

def compute_daily_indicators(df):
    df = df.copy()
    c = df['Close']
    df['EMA21'] = c.ewm(span=21, adjust=False).mean()
    df['EMA55'] = c.ewm(span=55, adjust=False).mean()
    df['BB_SMA_38'] = c.rolling(38).mean()
    std_38 = c.rolling(38).std(ddof=0)
    df['BB_UP_38'] = df['BB_SMA_38'] + (2.0 * std_38)
    df['BB_LO_38'] = df['BB_SMA_38'] - (2.0 * std_38)
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI'] = (100 - (100 / (1 + (gain / loss.replace(0, np.nan))))).fillna(50.0)
    tr = pd.concat([df['High'] - df['Low'], (df['High'] - c.shift()).abs(), (df['Low'] - c.shift()).abs()], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    df['HV20'] = np.log(c / c.shift(1)).rolling(20).std() * np.sqrt(252)
    df['CE_Signal'] = (c >= df['EMA21'] * 0.995) & (c < df['BB_UP_38']) & (df['RSI'] >= 42) & (df['RSI'] <= 72)
    df['PE_Signal'] = (c <= df['EMA21'] * 1.005) & (c > df['BB_LO_38']) & (df['RSI'] >= 28) & (df['RSI'] <= 58)
    return df

def compute_intraday_15m_indicators(df):
    df = df.copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert('Asia/Kolkata').tz_localize(None)
    c = df['Close']
    tp = (df['High'] + df['Low'] + c) / 3.0
    vol_clean = df['Volume'].replace(0, 1)
    dates = pd.Series(df.index.date, index=df.index)
    df['VWAP'] = (tp * vol_clean).groupby(dates).cumsum() / vol_clean.groupby(dates).cumsum()
    df['EMA9'] = c.ewm(span=9, adjust=False).mean()
    df['EMA21'] = c.ewm(span=21, adjust=False).mean()
    df['BB_SMA_38'] = c.rolling(38).mean()
    std_38 = c.rolling(38).std(ddof=0)
    df['BB_UP_38'] = df['BB_SMA_38'] + (2.0 * std_38)
    df['BB_LO_38'] = df['BB_SMA_38'] - (2.0 * std_38)
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI'] = (100 - (100 / (1 + (gain / loss.replace(0, np.nan))))).fillna(50.0)
    tr = pd.concat([df['High'] - df['Low'], (df['High'] - c.shift()).abs(), (df['Low'] - c.shift()).abs()], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    df['HV20'] = np.log(c / c.shift(1)).rolling(50).std() * np.sqrt(252 * 25)
    df['CE_Signal'] = (c >= df['VWAP'] * 0.998) & (c < df['BB_UP_38']) & (df['RSI'] >= 45) & (df['RSI'] <= 75)
    df['PE_Signal'] = (c <= df['VWAP'] * 1.002) & (c > df['BB_LO_38']) & (df['RSI'] >= 25) & (df['RSI'] <= 55)
    return df

# ==============================================================================
# 🧪 7. BACKTESTER & PATCH A (STRICT RISK-CAP ENFORCEMENT)
# ==============================================================================
def backtest_signals(df, direction="CE", atr_mult=1.0, und_rr=1.8, max_bars=6, is_intraday=False):
    sig_col = 'CE_Signal' if direction == "CE" else 'PE_Signal'
    closes, highs, lows, atrs, sigs = df['Close'].values, df['High'].values, df['Low'].values, df['ATR'].values, df[sig_col].values
    dates = df.index.date if is_intraday else None
    wins, total = 0, 0
    n = len(df)
    i = 50
    while i < n - max_bars - 1:
        if not sigs[i] or np.isnan(atrs[i]) or atrs[i] <= 0:
            i += 1
            continue
        entry = closes[i]
        risk  = atrs[i] * atr_mult
        sl    = entry - risk if direction == "CE" else entry + risk
        t1    = entry + risk if direction == "CE" else entry - risk
        tgt   = entry + (risk * und_rr) if direction == "CE" else entry - (risk * und_rr)
        total += 1
        won = False
        t1_reached = False
        exit_bar = i + 1
        for j in range(i + 1, min(i + 1 + max_bars, n)):
            exit_bar = j
            if is_intraday and dates[j] != dates[i]:
                break
            if direction == "CE":
                curr_sl = entry if t1_reached else sl
                if lows[j] <= curr_sl:
                    if t1_reached: won = True
                    break
                if highs[j] >= tgt:
                    won = True
                    break
                if highs[j] >= t1:
                    t1_reached = True
            else:
                curr_sl = entry if t1_reached else sl
                if highs[j] >= curr_sl:
                    if t1_reached: won = True
                    break
                if lows[j] <= tgt:
                    won = True
                    break
                if lows[j] <= t1:
                    t1_reached = True
        if won: wins += 1
        i = max(i + 1, exit_bar)
    return wins, total, (wins / total * 100.0) if total > 0 else 0.0

def build_option_trade_row(meta, df, opt_type, wins, total, win_prob, india_vix, is_intraday, oi_tag, is_oi_boosted, dhan_engine: DhanOptionEngine):
    latest = df.iloc[-1]
    spot   = float(latest['Close'])
    atr    = float(latest['ATR'])
    hv20   = float(latest['HV20']) if not np.isnan(latest['HV20']) else 0.25
    step   = meta["step"]
    atm_strike = int(round(spot / step) * step) if step >= 5 else round(spot / step) * step

    dhan_quote = dhan_engine.get_live_atm_option(meta, atm_strike, opt_type) if dhan_engine else None
    if dhan_quote:
        opt_entry  = dhan_quote["ltp"]
        delta      = dhan_quote["delta"]
        gamma      = dhan_quote["gamma"]
        atm_strike = dhan_quote["strike"]
        src_flag   = "⚡"
    else:
        dte = get_dte(is_intraday=is_intraday)
        iv = india_vix * 1.15 if meta["is_idx"] else max(hv20 * 1.08, india_vix * 1.40)
        opt_entry, delta, gamma = black_scholes_greeks(S=spot, K=atm_strike, T=dte/365.0, r=RISK_FREE_RATE, sigma=iv, option_type=opt_type)
        src_flag = ""

    strike_str = f"{int(atm_strike)}" if atm_strike == int(atm_strike) else f"{atm_strike:.1f}"

    atr_mult   = INTRA_ATR_SL_MULT if is_intraday else SWING_ATR_SL_MULT
    und_risk   = atr * atr_mult
    raw_opt_sl_dist = delta * und_risk - 0.5 * gamma * (und_risk ** 2)
    max_pct_sl = 0.22 if is_intraday else 0.28
    min_pct_sl = 0.14 if is_intraday else 0.18
    opt_risk   = min(opt_entry * max_pct_sl, max(opt_entry * min_pct_sl, raw_opt_sl_dist))

    opt_entry = round(opt_entry, 1)
    opt_risk  = max(round(opt_risk, 1), 0.3)
    opt_sl    = round(max(0.5, opt_entry - opt_risk), 1)
    actual_risk = max(round(opt_entry - opt_sl, 1), 0.2)

    target_rr = INTRA_OPT_RR if is_intraday else SWING_OPT_RR
    t1_rr     = 1.0 if is_intraday else 1.25
    opt_t1    = round(opt_entry + (actual_risk * t1_rr), 1)
    opt_tgt   = round(opt_entry + (actual_risk * target_rr), 1)

    lot_size     = meta["lot"]
    risk_per_lot = max(actual_risk * lot_size, 1.0)
    cost_per_lot = max(opt_entry * lot_size, 1.0)

    # --- PATCH A: STRICT RISK-CAP ENFORCEMENT ---
    lots_by_risk = int(RISK_PER_TRADE // risk_per_lot)
    lots_by_cap  = int(MAX_CAPITAL_PER_TRADE // cost_per_lot)
    num_lots     = min(lots_by_risk, lots_by_cap)
    
    if num_lots < 1:
        return None  # Rejects trade cleanly if 1 lot breaches risk/capital limits!

    qty     = num_lots * lot_size
    capital = int(round(opt_entry * qty))

    return {
        "Contract": f"{src_flag}{meta['name'][:7]} {strike_str}{opt_type}",
        "Entry": opt_entry,
        "Qty": qty,
        "Capital": capital,
        "Stop": opt_sl,
        "T1": opt_t1,
        "Target": opt_tgt,
        "RR": f"1:{target_rr:.1f}",
        "OI_Flow": oi_tag,
        "WinProb": round(win_prob, 1),
        "OIBoost": 1 if is_oi_boosted else 0,
    }

# ==============================================================================
# 📱 8. PATCH B: HTML ESCAPING FORMATTER & TELEGRAM SENDER
# ==============================================================================
def format_combined_table(idx_records, stk_records, title, now_str, dhan_active: bool):
    feed_str = "Dhan Live API ⚡ + NSE OI" if dhan_active else "NSE OI Spurts + BS Model"
    col_hdr = f"{'Contract':<15}|{'Entry':>6}|{'Qty':>5}|{'Cap(₹)':>6}|{'SL':>5}|{'T1-50%':>6}|{'Target':>6}|{'R:R':>5}|{'OI_Flow':<8}|{'Win%':>5}"
    sep = "-" * 77

    lines = [
        f"<b>🔥 {html.escape(title)}</b>",
        f"🕒 <code>{html.escape(now_str)}</code> | Feed: <code>{html.escape(feed_str)}</code> | Risk: <code>₹{RISK_PER_TRADE:,}</code>\n",
    ]

    if idx_records:
        lines.append("<b>📌 MAJOR INDICES F&amp;O</b>")
        lines.append("<pre>")
        lines.append(col_hdr)
        lines.append(sep)
        for r in idx_records:
            row_str = (
                f"{r['Contract'][:15]:<15}|"
                f"{r['Entry']:>6.1f}|"
                f"{r['Qty']:>5d}|"
                f"{r['Capital']:>6d}|"
                f"{r['Stop']:>5.1f}|"
                f"{r['T1']:>6.1f}|"
                f"{r['Target']:>6.1f}|"
                f"{r['RR']:>5}|"
                f"{r['OI_Flow'][:8]:<8}|"
                f"{r['WinProb']:>4.1f}%"
            )
            lines.append(html.escape(row_str))
        lines.append("</pre>\n")

    if stk_records:
        lines.append(f"<b>🚀 TOP {len(stk_records[:TOP_N_STOCKS])} STOCK OPTIONS</b>")
        lines.append("<pre>")
        lines.append(col_hdr)
        lines.append(sep)
        for r in stk_records[:TOP_N_STOCKS]:
            row_str = (
                f"{r['Contract'][:15]:<15}|"
                f"{r['Entry']:>6.1f}|"
                f"{r['Qty']:>5d}|"
                f"{r['Capital']:>6d}|"
                f"{r['Stop']:>5.1f}|"
                f"{r['T1']:>6.1f}|"
                f"{r['Target']:>6.1f}|"
                f"{r['RR']:>5}|"
                f"{r['OI_Flow'][:8]:<8}|"
                f"{r['WinProb']:>4.1f}%"
            )
            lines.append(html.escape(row_str))
        lines.append("</pre>")

    lines.append("💡 <i>⚡ = Live Dhan Option Chain | 🔥LB = Long Build | ⚡SC = Short Cover</i>")
    return "\n".join(lines)

def send_telegram_alert(html_text):
    if not TELEGRAM_BOT_TOKEN or ":" not in TELEGRAM_BOT_TOKEN:
        print(html_text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": html_text, "parse_mode": "HTML", "disable_web_page_preview": True}
    requests.post(url, json=payload, timeout=15)

def get_live_vix():
    try:
        vix_df = yf.download("^INDIAVIX", period="5d", interval="1d", progress=False)
        if vix_df.empty: return 0.135
        close_col = vix_df['Close']
        if isinstance(close_col, pd.DataFrame): close_col = close_col.iloc[:, 0]
        return float(close_col.dropna().iloc[-1]) / 100.0
    except Exception:
        return 0.135

# ==============================================================================
# 🚀 9. MAIN EXECUTION ENGINE
# ==============================================================================
def main():
    ist = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(ist)
    now_str = now_ist.strftime('%d-%b %I:%M %p IST')
    current_minutes = now_ist.hour * 60 + now_ist.minute

    if EVENT_NAME == "schedule":
        if now_ist.weekday() >= 5 or current_minutes < (9 * 60 + 15) or current_minutes > (15 * 60 + 35):
            return

    tickers = list(FNO_UNIVERSE.keys())
    india_vix = get_live_vix()
    oi_spurts_map = fetch_nse_oi_spurts_map()
    dhan_engine = DhanOptionEngine(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN)

    messages_sent = 0

    # 1. Intraday 15M Scalps
    raw_15m = yf.download(tickers, period="60d", interval="15m", group_by="ticker", progress=False)
    intra_idx, intra_stk = [], []

    for tk, meta in FNO_UNIVERSE.items():
        df15 = extract_ticker_df(raw_15m, tk)
        if len(df15) < 80: continue
        df15 = compute_intraday_15m_indicators(df15)
        last = df15.iloc[-1]

        today_bars = df15[df15.index.date == last.name.date()]
        day_open = float(today_bars['Open'].iloc[0]) if not today_bars.empty else float(last['Open'])
        price_chg_pct = ((float(last['Close']) - day_open) / day_open) * 100.0
        oi_chg_pct = oi_spurts_map.get(meta["nse"], 0.0)

        opt_type = "CE" if float(last['Close']) >= float(last['VWAP']) else "PE"
        is_blocked, is_boosted, oi_tag = classify_institutional_oi(price_chg_pct, oi_chg_pct, opt_type)
        wins, tot, wp = backtest_signals(df15, opt_type, INTRA_ATR_SL_MULT, INTRA_UND_RR, INTRA_MAX_BARS, True)

        if meta["is_idx"]:
            if tot >= 1: intra_idx.append((wp, is_boosted, meta, df15, opt_type, wins, tot, oi_tag))
        else:
            if is_blocked: continue
            if not (bool(last['CE_Signal' if opt_type=="CE" else 'PE_Signal']) or abs(oi_chg_pct) >= 3.0): continue
            if tot >= INTRA_MIN_SETUPS and wp >= INTRA_MIN_WIN_PROB:
                intra_stk.append((wp, is_boosted, meta, df15, opt_type, wins, tot, oi_tag))

    intra_idx_rows = [r for r in (build_option_trade_row(m, d, o, w, t, wp, india_vix, True, tag, b, dhan_engine) for wp, b, m, d, o, w, t, tag in intra_idx) if r]
    intra_stk_rows = [r for r in (build_option_trade_row(m, d, o, w, t, wp, india_vix, True, tag, b, dhan_engine) for wp, b, m, d, o, w, t, tag in sorted(intra_stk, key=lambda x: (1 if x[1] else 0, x[0]), reverse=True)[:TOP_N_STOCKS]) if r]

    if intra_idx_rows or intra_stk_rows:
        msg = format_combined_table(intra_idx_rows, intra_stk_rows, "15M INTRADAY F&O SCALPS (LOCKED 1:2.0 RR)", now_str, dhan_engine.enabled)
        send_telegram_alert(msg)
        messages_sent += 1

    # 2. Positional 3-5D Swings
    raw_1d = yf.download(tickers, period="5y", interval="1d", group_by="ticker", progress=False)
    swing_idx, swing_stk = [], []

    for tk, meta in FNO_UNIVERSE.items():
        df1d = extract_ticker_df(raw_1d, tk)
        if len(df1d) < 200: continue
        df1d = compute_daily_indicators(df1d)
        last = df1d.iloc[-1]
        prev = df1d.iloc[-2]

        price_chg_pct = ((float(last['Close']) - float(prev['Close'])) / float(prev['Close'])) * 100.0
        oi_chg_pct = oi_spurts_map.get(meta["nse"], 0.0)

        opt_type = "CE" if float(last['Close']) >= float(last['EMA21']) else "PE"
        is_blocked, is_boosted, oi_tag = classify_institutional_oi(price_chg_pct, oi_chg_pct, opt_type)
        wins, tot, wp = backtest_signals(df1d, opt_type, SWING_ATR_SL_MULT, SWING_UND_RR, SWING_MAX_DAYS, False)

        if meta["is_idx"]:
            if tot >= 1: swing_idx.append((wp, is_boosted, meta, df1d, opt_type, wins, tot, oi_tag))
        else:
            if is_blocked: continue
            if not (bool(last['CE_Signal' if opt_type=="CE" else 'PE_Signal']) or abs(oi_chg_pct) >= 3.0): continue
            if tot >= SWING_MIN_SETUPS and wp >= SWING_MIN_WIN_PROB:
                swing_stk.append((wp, is_boosted, meta, df1d, opt_type, wins, tot, oi_tag))

    swing_idx_rows = [r for r in (build_option_trade_row(m, d, o, w, t, wp, india_vix, False, tag, b, dhan_engine) for wp, b, m, d, o, w, t, tag in swing_idx) if r]
    swing_stk_rows = [r for r in (build_option_trade_row(m, d, o, w, t, wp, india_vix, False, tag, b, dhan_engine) for wp, b, m, d, o, w, t, tag in sorted(swing_stk, key=lambda x: (1 if x[1] else 0, x[0]), reverse=True)[:TOP_N_STOCKS]) if r]

    if swing_idx_rows or swing_stk_rows:
        msg = format_combined_table(swing_idx_rows, swing_stk_rows, "3-5D POSITIONAL F&O (LOCKED 1:2.5 RR)", now_str, dhan_engine.enabled)
        send_telegram_alert(msg)
        messages_sent += 1

if __name__ == "__main__":
    main()
