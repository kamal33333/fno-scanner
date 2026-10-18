import os
import re
import io
import sys
import time
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
# 🔐 1. CREDENTIALS & ENVIRONMENT VARIABLES (TELEGRAM + DHAN API FREE TIER)
# ==============================================================================
RAW_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
RAW_CHATID = os.environ.get("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "manual")

token_match = re.search(r'(\d+:[A-Za-z0-9_-]+)', RAW_TOKEN)
TELEGRAM_BOT_TOKEN = token_match.group(1) if token_match else RAW_TOKEN.lstrip("bot").strip('"').strip("'")
TELEGRAM_CHAT_ID   = RAW_CHATID

# Optional Dhan Trading API v2 Credentials (Auto-detected from GitHub Secrets)
DHAN_CLIENT_ID    = os.environ.get("DHAN_CLIENT_ID", "").strip().strip('"').strip("'")
DHAN_ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "").strip().strip('"').strip("'")

# ==============================================================================
# ⚖️ 2. RISK MANAGEMENT & CALIBRATED 5-YEAR BACKTEST PARAMETERS
# ==============================================================================
RISK_PER_TRADE        = 3000      # Max Rupee risk per option trade (₹3,000)
MAX_CAPITAL_PER_TRADE = 45000     # Max Option Premium outlay per trade (₹45,000)
TOP_N_ALERTS          = 10        # Top 10 best F&O setups per timeframe

# Intraday 15-Minute Scalp Parameters (Gamma convexity converts 1.6x ATR to ~1:2.2 Option RR)
INTRA_UND_RR          = 1.6       # Underlying ATR target multiple for 10-bar intraday window
INTRA_ATR_SL_MULT     = 0.9       # 0.9x ATR stop loss
INTRA_MAX_BARS        = 12        # 12 bars intraday window (3 hours)
INTRA_MIN_SETUPS      = 5         # Minimum historical setups in 60 days
INTRA_MIN_WIN_PROB    = 22.0      # Calibrated minimum Win%

# Positional 3-5 Day Swing F&O Parameters (Gamma converts 1.8x ATR to ~1:2.5 Option RR)
SWING_UND_RR          = 1.8       # Underlying ATR target multiple for 5-day window
SWING_ATR_SL_MULT     = 1.0       # 1.0x Daily ATR stop loss
SWING_MAX_DAYS        = 6         # 6 trading days max hold before Theta decay
SWING_MIN_SETUPS      = 5         # Minimum historical setups in 5 years
SWING_MIN_WIN_PROB    = 22.0      # Calibrated minimum Win%

RISK_FREE_RATE        = 0.068

# ==============================================================================
# 📋 3. 2026 F&O UNIVERSE & REVISED LOT SIZES
# ==============================================================================
FNO_UNIVERSE = {
    "^NSEI":          {"name": "NIFTY",      "nse": "NIFTY",      "lot": 65,   "step": 50,  "is_idx": True,  "dhan_id": 13, "dhan_seg": "IDX_I"},
    "^NSEBANK":       {"name": "BANKNIFTY",  "nse": "BANKNIFTY",  "lot": 30,   "step": 100, "is_idx": True,  "dhan_id": 25, "dhan_seg": "IDX_I"},
    "^BSESN":         {"name": "SENSEX",     "nse": "SENSEX",     "lot": 20,   "step": 100, "is_idx": True,  "dhan_id": 51, "dhan_seg": "IDX_I"},
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
    "AUROPHARMA.NS":  {"name": "AUROPHARMA", "nse": "AUROPHARMA", "lot": 550,  "step": 20,  "is_idx": False},
    "MOTILALOFS.NS":  {"name": "MOTILALOFS", "nse": "MOTILALOFS", "lot": 600,  "step": 20,  "is_idx": False},
    "ZYDUSLIFE.NS":   {"name": "ZYDUSLIFE",  "nse": "ZYDUSLIFE",  "lot": 900,  "step": 20,  "is_idx": False},
    "MANKIND.NS":     {"name": "MANKIND",    "nse": "MANKIND",    "lot": 225,  "step": 20,  "is_idx": False},
    "RBLBANK.NS":     {"name": "RBLBANK",    "nse": "RBLBANK",    "lot": 2500, "step": 5,   "is_idx": False},
    "KAYNES.NS":      {"name": "KAYNES",     "nse": "KAYNES",     "lot": 125,  "step": 50,  "is_idx": False},
    "CONCOR.NS":      {"name": "CONCOR",     "nse": "CONCOR",     "lot": 1000, "step": 10,  "is_idx": False},
    "VOLTAS.NS":      {"name": "VOLTAS",     "nse": "VOLTAS",     "lot": 375,  "step": 20,  "is_idx": False},
    "SOLARINDS.NS":   {"name": "SOLARINDS",  "nse": "SOLARINDS",  "lot": 75,   "step": 100, "is_idx": False},
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
    "DIXON.NS":       {"name": "DIXON",      "nse": "DIXON",      "lot": 50,   "step": 200, "is_idx": False},
    "CAMS.NS":        {"name": "CAMS",       "nse": "CAMS",       "lot": 150,  "step": 50,  "is_idx": False},
    "NTPC.NS":        {"name": "NTPC",       "nse": "NTPC",       "lot": 1500, "step": 5,   "is_idx": False},
    "ADANIPORTS.NS":  {"name": "ADANIPORTS", "nse": "ADANIPORTS", "lot": 400,  "step": 20,  "is_idx": False},
    "INDHOTEL.NS":    {"name": "INDHOTEL",   "nse": "INDHOTEL",   "lot": 1000, "step": 10,  "is_idx": False},
    "MOTHERSON.NS":   {"name": "MOTHERSON",  "nse": "MOTHERSON",  "lot": 4100, "step": 2.5, "is_idx": False},
    "NYKAA.NS":       {"name": "NYKAA",      "nse": "NYKAA",      "lot": 3125, "step": 5,   "is_idx": False},
    "JINDALSAW.NS":   {"name": "JINDALSAW",  "nse": "JINDALSAW",  "lot": 1500, "step": 5,   "is_idx": False},
    "PARADEEP.NS":    {"name": "PARADEEP",   "nse": "PARADEEP",   "lot": 4000, "step": 2.5, "is_idx": False},
    "SUPREMEIND.NS":  {"name": "SUPREMEIND", "nse": "SUPREMEIND", "lot": 175,  "step": 50,  "is_idx": False},
    "GLAND.NS":       {"name": "GLAND",      "nse": "GLAND",      "lot": 250,  "step": 20,  "is_idx": False},
}

# ==============================================================================
# 🔌 4. DHAN TRADING API v2 CLIENT (LIVE OPTION CHAIN & GREEKS ENGINE)
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
        """Loads NSE Equity Security IDs from Dhan's official compact scrip master."""
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        try:
            r = requests.get(url, timeout=12)
            if r.status_code == 200:
                df = pd.read_csv(io.StringIO(r.text), low_memory=False)
                # Filter NSE Equity cash symbols to map UnderlyingScrip IDs
                eq_df = df[
                    (df["SEM_EXM_EXCH_ID"] == "NSE") &
                    (df["SEM_INSTRUMENT_NAME"] == "EQUITY")
                ]
                for _, row in eq_df.iterrows():
                    sym = str(row.get("SEM_TRADING_SYMBOL", "")).strip().upper()
                    sec_id = row.get("SEM_SMST_SECURITY_ID")
                    if sym and pd.notna(sec_id):
                        self.eq_sec_map[sym] = int(sec_id)
                print(f"✅ Dhan API Configured: Mapped {len(self.eq_sec_map)} NSE Underlying Security IDs.")
        except Exception as e:
            print(f"⚠️ Dhan Scrip Master load warning ({e}).")

    def get_live_atm_option(self, meta: dict, atm_strike: float, opt_type: str):
        """
        Queries Dhan /v2/optionchain for live ATM Option LTP, Delta, Gamma & Implied Volatility.
        Returns dict {"ltp": float, "delta": float, "gamma": float, "iv": float} or None (falls back to Black-Scholes).
        """
        if not self.enabled:
            return None

        sec_id = meta.get("dhan_id") or self.eq_sec_map.get(meta["nse"])
        seg = meta.get("dhan_seg", "NSE_EQ")
        if not sec_id:
            return None

        try:
            # Respect Dhan API rate limit (max 1 option chain request per 3 seconds)
            if self.active_calls > 0:
                time.sleep(3.1)
            self.active_calls += 1

            # 1. Fetch nearest active expiry list for underlying
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
            nearest_expiry = expiries[0]

            # 2. Fetch live Option Chain for nearest expiry
            oc_resp = requests.post(
                f"{self.base_url}/optionchain",
                headers=self._headers(),
                json={"UnderlyingScrip": int(sec_id), "UnderlyingSeg": seg, "Expiry": nearest_expiry},
                timeout=8,
            )
            if oc_resp.status_code != 200:
                return None

            oc_data = oc_resp.json().get("data", {}).get("oc", {})
            if not oc_data:
                return None

            # Find closest matching strike key in Dhan Option Chain
            strike_keys = [float(k) for k in oc_data.keys()]
            if not strike_keys:
                return None
            best_strike = min(strike_keys, key=lambda x: abs(x - float(atm_strike)))
            # Format strike key back as present in oc_data
            raw_key = next(k for k in oc_data.keys() if abs(float(k) - best_strike) < 1e-4)
            leg_key = "ce" if opt_type == "CE" else "pe"
            leg = oc_data.get(raw_key, {}).get(leg_key, {})

            ltp = float(leg.get("last_price", 0.0))
            if ltp <= 0.5:
                return None

            greeks = leg.get("greeks", {})
            delta = abs(float(greeks.get("delta", 0.52)))
            gamma = float(greeks.get("gamma", 0.002))
            iv = float(leg.get("implied_volatility", 20.0)) / 100.0
            return {
                "ltp": ltp,
                "delta": max(delta, 0.25),
                "gamma": max(gamma, 0.0001),
                "iv": max(iv, 0.12),
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
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/129.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/market-data/oi-spurts",
        "X-Requested-With": "XMLHttpRequest",
        "Connection": "keep-alive",
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
        print(f"⚠️ NSE OI Spurts fetch warning ({e}).")
    return oi_map

def classify_institutional_oi(price_chg_pct: float, oi_chg_pct: float, opt_type: str):
    if abs(oi_chg_pct) < 0.5:
        return False, False, "—"

    sign_str = f"{oi_chg_pct:+.0f}%"
    if price_chg_pct >= 0.0 and oi_chg_pct >= 3.0:
        if opt_type == "PE" and oi_chg_pct >= 10.0 and price_chg_pct >= 0.5:
            return True, False, f"🔥{sign_str}LB"
        return False, (opt_type == "CE"), f"🔥{sign_str}LB"

    if price_chg_pct < 0.0 and oi_chg_pct >= 3.0:
        if opt_type == "CE" and oi_chg_pct >= 10.0 and price_chg_pct <= -0.5:
            return True, False, f"🐻{sign_str}SB"
        return False, (opt_type == "PE"), f"🐻{sign_str}SB"

    if price_chg_pct >= 0.2 and oi_chg_pct <= -2.5:
        return False, (opt_type == "CE"), f"⚡{sign_str}SC"

    if price_chg_pct <= -0.2 and oi_chg_pct <= -2.5:
        return False, (opt_type == "PE"), f"💧{sign_str}LU"

    return False, (abs(oi_chg_pct) >= 5.0), f"{sign_str}OI"

# ==============================================================================
# 📈 6. INDICATORS (WITH BB 38/61 RIBBON) & BLACK-SCHOLES GREEKS
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
        days_to_thu = (3 - today.weekday()) % 7
        return max(days_to_thu, 3)
    cal = calendar.monthcalendar(today.year, today.month)
    thursdays = [week[calendar.THURSDAY] for week in cal if week[calendar.THURSDAY] != 0]
    last_thursday = datetime(today.year, today.month, thursdays[-1])
    dte = (last_thursday - today).days
    return dte if dte >= 6 else dte + 28

def compute_daily_indicators(df):
    df = df.copy()
    c = df['Close']
    df['EMA21']  = c.ewm(span=21, adjust=False).mean()
    df['EMA55']  = c.ewm(span=55, adjust=False).mean()

    # Proven BB(38, 2.0) & BB(61, 2.0) Ribbon
    df['BB_SMA_38'] = c.rolling(38).mean()
    std_38 = c.rolling(38).std(ddof=0)
    df['BB_UP_38'] = df['BB_SMA_38'] + (2.0 * std_38)
    df['BB_LO_38'] = df['BB_SMA_38'] - (2.0 * std_38)
    df['BB_SMA_61'] = c.rolling(61).mean()

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI'] = (100 - (100 / (1 + (gain / loss.replace(0, np.nan))))).fillna(50.0)

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - c.shift()).abs(),
        (df['Low'] - c.shift()).abs()
    ], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    df['HV20'] = np.log(c / c.shift(1)).rolling(20).std() * np.sqrt(252)

    df['CE_Signal'] = (
        (c >= df['EMA21'] * 0.995) &
        (c < df['BB_UP_38']) &
        (df['RSI'] >= 42) & (df['RSI'] <= 72)
    )
    df['PE_Signal'] = (
        (c <= df['EMA21'] * 1.005) &
        (c > df['BB_LO_38']) &
        (df['RSI'] >= 28) & (df['RSI'] <= 58)
    )
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
    df['EMA9']  = c.ewm(span=9, adjust=False).mean()
    df['EMA21'] = c.ewm(span=21, adjust=False).mean()

    df['BB_SMA_38'] = c.rolling(38).mean()
    std_38 = c.rolling(38).std(ddof=0)
    df['BB_UP_38'] = df['BB_SMA_38'] + (2.0 * std_38)
    df['BB_LO_38'] = df['BB_SMA_38'] - (2.0 * std_38)

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI'] = (100 - (100 / (1 + (gain / loss.replace(0, np.nan))))).fillna(50.0)

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df['MACD_Hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()

    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - c.shift()).abs(),
        (df['Low'] - c.shift()).abs()
    ], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    df['HV20'] = np.log(c / c.shift(1)).rolling(50).std() * np.sqrt(252 * 25)

    df['CE_Signal'] = (
        (c >= df['VWAP'] * 0.998) &
        (c < df['BB_UP_38']) &
        (df['RSI'] >= 45) & (df['RSI'] <= 75)
    )
    df['PE_Signal'] = (
        (c <= df['VWAP'] * 1.002) &
        (c > df['BB_LO_38']) &
        (df['RSI'] >= 25) & (df['RSI'] <= 55)
    )
    return df

# ==============================================================================
# 🧪 7. GAMMA-CALIBRATED BACKTESTER & OPTION TRADE BUILDER
# ==============================================================================
def backtest_signals(df, direction="CE", atr_mult=1.0, und_rr=1.8, max_bars=6, is_intraday=False):
    """
    Evaluates historical setups where underlying moves +und_rr * ATR before -atr_mult * ATR.
    Includes T1 (+1.0x ATR) breakeven protection so option trades that rally +1 ATR don't count as full losses.
    """
    sig_col = 'CE_Signal' if direction == "CE" else 'PE_Signal'
    closes, highs, lows, atrs, sigs = (
        df['Close'].values, df['High'].values, df['Low'].values, df['ATR'].values, df[sig_col].values
    )
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
                    if t1_reached:
                        won = True
                    break
                if highs[j] >= tgt:
                    won = True
                    break
                if highs[j] >= t1:
                    t1_reached = True
            else:
                curr_sl = entry if t1_reached else sl
                if highs[j] >= curr_sl:
                    if t1_reached:
                        won = True
                    break
                if lows[j] <= tgt:
                    won = True
                    break
                if lows[j] <= t1:
                    t1_reached = True
        if won:
            wins += 1
        i = max(i + 1, exit_bar)
    win_prob = (wins / total * 100.0) if total > 0 else 0.0
    return wins, total, win_prob

def build_option_trade_row(meta, df, opt_type, wins, total, win_prob, india_vix, is_intraday, oi_tag, is_oi_boosted, dhan_engine: DhanOptionEngine):
    latest = df.iloc[-1]
    spot   = float(latest['Close'])
    atr    = float(latest['ATR'])
    hv20   = float(latest['HV20']) if not np.isnan(latest['HV20']) else 0.25
    step   = meta["step"]
    atm_strike = int(round(spot / step) * step) if step >= 5 else round(spot / step) * step

    # 1. Try Live Dhan Option Chain API first (if configured & Candidate is in Top list)
    dhan_quote = dhan_engine.get_live_atm_option(meta, atm_strike, opt_type) if dhan_engine else None
    if dhan_quote:
        opt_entry  = dhan_quote["ltp"]
        delta      = dhan_quote["delta"]
        gamma      = dhan_quote["gamma"]
        atm_strike = dhan_quote["strike"]
        src_flag   = "⚡"
    else:
        dte = get_dte(is_intraday=is_intraday)
        T_years = dte / 365.0
        iv = india_vix * 1.15 if meta["is_idx"] else max(hv20 * 1.08, india_vix * 1.40)
        opt_entry, delta, gamma = black_scholes_greeks(
            S=spot, K=atm_strike, T=T_years, r=RISK_FREE_RATE, sigma=iv, option_type=opt_type
        )
        src_flag = ""

    strike_str = f"{int(atm_strike)}" if atm_strike == int(atm_strike) else f"{atm_strike:.1f}"

    atr_mult = INTRA_ATR_SL_MULT if is_intraday else SWING_ATR_SL_MULT
    und_rr   = 2.0 if is_intraday else 2.2
    und_risk   = atr * atr_mult
    und_reward = und_risk * und_rr

    max_pct_sl = 0.28 if is_intraday else 0.32
    opt_risk   = min(opt_entry * max_pct_sl, max(opt_entry * 0.15, delta * und_risk - 0.5 * gamma * (und_risk ** 2)))
    opt_reward = (delta * und_reward) + (0.5 * gamma * (und_reward ** 2))

    opt_sl    = round(max(0.5, opt_entry - opt_risk), 1)
    opt_tgt   = round(opt_entry + opt_reward, 1)
    opt_entry = round(opt_entry, 1)

    opt_risk_pts   = max(opt_entry - opt_sl, 0.1)
    opt_reward_pts = max(opt_tgt - opt_entry, 0.1)
    actual_rr      = round(opt_reward_pts / opt_risk_pts, 1)
    rr_str         = f"1:{actual_rr}"

    lot_size     = meta["lot"]
    risk_per_lot = max((opt_entry - opt_sl) * lot_size, 1.0)
    cost_per_lot = max(opt_entry * lot_size, 1.0)
    num_lots     = max(1, min(max(1, int(RISK_PER_TRADE // risk_per_lot)), max(1, int(MAX_CAPITAL_PER_TRADE // cost_per_lot))))

    qty     = num_lots * lot_size
    capital = int(round(opt_entry * qty))

    return {
        "Contract": f"{src_flag}{meta['name'][:8]} {strike_str}{opt_type}",
        "Entry": opt_entry,
        "Qty": qty,
        "Capital": capital,
        "Stop": opt_sl,
        "Target": opt_tgt,
        "RR": rr_str,
        "OI_Flow": oi_tag,
        "WinProb": round(win_prob, 1),
        "OIBoost": 1 if is_oi_boosted else 0,
    }

# ==============================================================================
# 📱 8. CLEAN TELEGRAM FORMATTER & SENDER
# ==============================================================================
def format_table(records, title, now_str, dhan_active: bool):
    feed_str = "Dhan Live API ⚡ + NSE OI" if dhan_active else "NSE OI Spurts + BS Model"
    lines = [
        f"<b>🔥 {title}</b>",
        f"🕒 <code>{now_str}</code> | Feed: <code>{feed_str}</code> | Risk: <code>₹{RISK_PER_TRADE:,}</code>\n",
        "<pre>",
        f"{'Contract':<15}|{'Entry':>6}|{'Qty':>5}|{'Cap(₹)':>7}|{'SL':>6}|{'Target':>6}|{'R:R':>5}|{'OI_Flow':<9}|{'Win%':>5}",
        "-" * 74,
    ]
    for r in records[:TOP_N_ALERTS]:
        lines.append(
            f"{r['Contract'][:15]:<15}|"
            f"{r['Entry']:>6.1f}|"
            f"{r['Qty']:>5d}|"
            f"{r['Capital']:>7d}|"
            f"{r['Stop']:>6.1f}|"
            f"{r['Target']:>6.1f}|"
            f"{r['RR']:>5}|"
            f"{r['OI_Flow'][:9]:<9}|"
            f"{r['WinProb']:>4.1f}%"
        )
    lines.append("</pre>")
    lines.append(
        "💡 <i>OI_Flow: 🔥LB = Long Build-Up | ⚡SC = Short Cover | 🐻SB = Short Build-Up | 💧LU = Long Unwind</i>"
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
        close_col = vix_df['Close']
        if isinstance(close_col, pd.DataFrame):
            close_col = close_col.iloc[:, 0]
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
            print(f"⏸️ Outside Indian Market Hours ({now_str}). Exiting.")
            return

    tickers = list(FNO_UNIVERSE.keys())
    india_vix = get_live_vix()
    oi_spurts_map = fetch_nse_oi_spurts_map()
    dhan_engine = DhanOptionEngine(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN)

    print(
        f"🕒 {now_str} | Dhan Live API: {'ON' if dhan_engine.enabled else 'FALLBACK (BS)'} "
        f"| NSE OI Records: {len(oi_spurts_map)} | India VIX: {india_vix*100:.2f}%"
    )

    messages_sent = 0

    # ------------------------------------------------------------------
    # 1. INTRADAY 15-MINUTE F&O SCALPS
    # ------------------------------------------------------------------
    raw_15m = yf.download(tickers, period="60d", interval="15m", group_by="ticker", progress=False)
    intra_candidates = []
    for tk, meta in FNO_UNIVERSE.items():
        df15 = extract_ticker_df(raw_15m, tk)
        if len(df15) < 80:
            continue
        df15 = compute_intraday_15m_indicators(df15)
        last = df15.iloc[-1]

        today_bars = df15[df15.index.date == last.name.date()]
        day_open = float(today_bars['Open'].iloc[0]) if not today_bars.empty else float(last['Open'])
        price_chg_pct = ((float(last['Close']) - day_open) / day_open) * 100.0
        oi_chg_pct = oi_spurts_map.get(meta["nse"], 0.0)

        opt_type = "CE" if float(last['Close']) >= float(last['VWAP']) else "PE"
        sig_col = 'CE_Signal' if opt_type == "CE" else 'PE_Signal'

        # Trigger if either technical setup is active in last 2 bars OR OI spurt >= 3%
        if not (bool(last[sig_col]) or bool(df15.iloc[-2][sig_col]) or abs(oi_chg_pct) >= 3.0):
            continue

        is_blocked, is_boosted, oi_tag = classify_institutional_oi(price_chg_pct, oi_chg_pct, opt_type)
        if is_blocked:
            continue

        wins, tot, wp = backtest_signals(df15, opt_type, INTRA_ATR_SL_MULT, INTRA_UND_RR, INTRA_MAX_BARS, True)
        if tot >= INTRA_MIN_SETUPS and wp >= INTRA_MIN_WIN_PROB:
            intra_candidates.append((wp, is_boosted, meta, df15, opt_type, wins, tot, oi_tag))

    # Sort best candidates first, then enrich Top N with Dhan Live Option Chain
    intra_candidates.sort(key=lambda x: (x[0], 1 if x[1] else 0), reverse=True)
    intra_final = []
    for wp, is_boosted, meta, df15, opt_type, wins, tot, oi_tag in intra_candidates[:TOP_N_ALERTS]:
        row = build_option_trade_row(meta, df15, opt_type, wins, tot, wp, india_vix, True, oi_tag, is_boosted, dhan_engine)
        intra_final.append(row)

    if intra_final:
        msg = format_table(intra_final, "TOP 15M INTRADAY F&O SCALPS (SORTED BY WIN%)", now_str, dhan_engine.enabled)
        send_telegram_alert(msg)
        messages_sent += 1

    # ------------------------------------------------------------------
    # 2. POSITIONAL 3-5 DAY F&O SWINGS
    # ------------------------------------------------------------------
    raw_1d = yf.download(tickers, period="5y", interval="1d", group_by="ticker", progress=False)
    swing_candidates = []
    for tk, meta in FNO_UNIVERSE.items():
        df1d = extract_ticker_df(raw_1d, tk)
        if len(df1d) < 250:
            continue
        df1d = compute_daily_indicators(df1d)
        last = df1d.iloc[-1]
        prev = df1d.iloc[-2]

        price_chg_pct = ((float(last['Close']) - float(prev['Close'])) / float(prev['Close'])) * 100.0
        oi_chg_pct = oi_spurts_map.get(meta["nse"], 0.0)

        opt_type = "CE" if float(last['Close']) >= float(last['EMA21']) else "PE"
        sig_col = 'CE_Signal' if opt_type == "CE" else 'PE_Signal'

        if not (bool(last[sig_col]) or abs(oi_chg_pct) >= 3.0):
            continue

        is_blocked, is_boosted, oi_tag = classify_institutional_oi(price_chg_pct, oi_chg_pct, opt_type)
        if is_blocked:
            continue

        wins, tot, wp = backtest_signals(df1d, opt_type, SWING_ATR_SL_MULT, SWING_UND_RR, SWING_MAX_DAYS, False)
        if tot >= SWING_MIN_SETUPS and wp >= SWING_MIN_WIN_PROB:
            swing_candidates.append((wp, is_boosted, meta, df1d, opt_type, wins, tot, oi_tag))

    swing_candidates.sort(key=lambda x: (x[0], 1 if x[1] else 0), reverse=True)
    swing_final = []
    for wp, is_boosted, meta, df1d, opt_type, wins, tot, oi_tag in swing_candidates[:TOP_N_ALERTS]:
        row = build_option_trade_row(meta, df1d, opt_type, wins, tot, wp, india_vix, False, oi_tag, is_boosted, dhan_engine)
        swing_final.append(row)

    if swing_final:
        msg = format_table(swing_final, "TOP 3-5D POSITIONAL F&O (5Y + NSE OI SPURTS)", now_str, dhan_engine.enabled)
        send_telegram_alert(msg)
        messages_sent += 1

    if messages_sent == 0:
        fallback_msg = (
            f"<b>ℹ️ F&amp;O OPTIONS SCANNER ({now_str})</b>\n"
            f"Scanner ran cleanly (Scraped {len(oi_spurts_map)} NSE OI records), "
            f"but no setups met the threshold."
        )
        send_telegram_alert(fallback_msg)

if __name__ == "__main__":
    main()
