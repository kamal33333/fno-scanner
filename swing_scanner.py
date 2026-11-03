import os
import re
import gc
import sys
import json
import time
import html
import warnings
from pathlib import Path
from datetime import datetime, date, timedelta

warnings.filterwarnings("ignore")

import pytz
import requests
import yfinance as yf
import pandas as pd
import numpy as np

# ==============================================================================
# 🔐 1. CREDENTIALS & ENVIRONMENT VARIABLES (TELEGRAM + DHAN HQ v2)
# ==============================================================================
RAW_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
RAW_CHATID = os.environ.get("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")
FORCE_RUN  = ("--force" in sys.argv) or (os.environ.get("FORCE_RUN", "0") == "1")

token_match = re.search(r"(\d+:[A-Za-z0-9_-]+)", RAW_TOKEN)
TELEGRAM_BOT_TOKEN = token_match.group(1) if token_match else RAW_TOKEN.lstrip("bot").strip('"').strip("'")
TELEGRAM_CHAT_ID   = RAW_CHATID

# Dhan API Credentials (reuses the same env vars as options_scanner.py)
DHAN_CLIENT_ID    = os.environ.get("DHAN_CLIENT_ID", "").strip().strip('"').strip("'")
DHAN_ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "").strip().strip('"').strip("'")
DHAN_CACHE_FILE   = Path("/tmp/dhan_nse_eq_map.json")

# ==============================================================================
# ⚖️ 2. OFFICIAL NSE HOLIDAYS 2026 & RISK PARAMETERS
# ==============================================================================
NSE_HOLIDAYS_2026 = {
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra
    date(2026, 11, 10),  # Diwali - Balipratipada
    date(2026, 11, 24),  # Guru Nanak Jayanti
    date(2026, 12, 25),  # Christmas
}

RISK_PER_TRADE         = 1000     # Max Rupee risk per trade (₹1,000 hard cap)
MAX_CAPITAL_PER_TRADE  = 35000    # Max Capital outlay per trade (₹35,000 hard cap)
INTRA_MIS_LEVERAGE     = 2.0      # Conservative 2x MIS Intraday Margin multiplier
TOP_N_PER_SECTION      = 5        # Keep tables ultra-clean: Top 5 setups per section!
COST_SLIPPAGE_R        = 0.05     # 0.05R deduction per backtest trade
BT_SE_K                = 1.0      # gate uses ExpR - BT_SE_K x standard-error (0 = old point-estimate gate)
SWING_LATE_SCAN_ONLY   = True     # True: swing signals only in the last ~20 min of the session or after it (daily candle >=95% formed)
SWING_SCAN_FROM        = (15, 10) # IST time from which the swing scan runs; at 15:15 ~92% of signals hold to the 15:30 close, at 15:20 ~95%
INTRADAY_WHEN_CLOSED   = False    # True = also scan intraday when the market is closed (testing only; those rows are not tradable)

INTRA_RR               = 2.0
INTRA_T1_RR            = 1.0
INTRA_ATR_SL_MULT      = 1.1
INTRA_MAX_BARS         = 12
INTRA_MIN_SETUPS       = 30       # was 8: 8 trades cannot support any expectancy estimate
INTRA_MIN_EXP_R        = 0.05     # now tested as ExpR - BT_SE_K*SE >= 0.05 (was point ExpR >= 0.10)

SWING_RR               = 2.5
SWING_T1_RR            = 1.25
SWING_ATR_SL_MULT      = 1.2
SWING_MAX_DAYS         = 8
SWING_MIN_SETUPS       = 30       # was 8
SWING_MIN_EXP_R        = 0.05     # now tested as ExpR - BT_SE_K*SE >= 0.05 (was point ExpR >= 0.10)

INDEX_UNIVERSE = {
    "^NSEI":                "NIFTY",
    "^NSEBANK":             "BANKNIFTY",
    "NIFTY_FIN_SERVICE.NS": "FINNIFTY",
    "NIFTY_MID_SELECT.NS":  "MIDCPNIFTY",
}

# Dhan IDX_I Security IDs for Instant Spot & OHLC Fetching
DHAN_INDEX_SEC_IDS = {
    "NIFTY":      13,
    "BANKNIFTY":  25,
    "FINNIFTY":   27,
    "MIDCPNIFTY": 442,
}

STOCK_UNIVERSE = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS",
    "SBIN.NS", "BHARTIARTL.NS", "LT.NS", "AXISBANK.NS", "KOTAKBANK.NS",
    "BAJFINANCE.NS", "MARUTI.NS", "M&M.NS", "TITAN.NS", "TATASTEEL.NS",
    "SUNPHARMA.NS", "NTPC.NS", "HAL.NS", "BEL.NS", "TRENT.NS",
    "ADANIPORTS.NS", "DLF.NS", "WIPRO.NS", "TECHM.NS", "PERSISTENT.NS",
    "DIXON.NS", "OBEROIRLTY.NS", "FEDERALBNK.NS", "VOLTAS.NS", "CONCOR.NS",
    "ZYDUSLIFE.NS", "AUROPHARMA.NS", "CROMPTON.NS", "ANGELONE.NS", "ETERNAL.NS",
    "LTF.NS", "RBLBANK.NS", "ABB.NS", "CAMS.NS", "INDHOTEL.NS",
    "MOTHERSON.NS", "NYKAA.NS", "SUPREMEIND.NS", "POLYCAB.NS", "COFORGE.NS",
    "CHAMBLFERT.NS", "COROMANDEL.NS", "DEEPAKNTR.NS", "NAVINFLUOR.NS", "ATUL.NS",
    "REDINGTON.NS", "CASTROLIND.NS", "CESC.NS", "KARURVYSYA.NS", "CUB.NS",
    "IDFCFIRSTB.NS", "MANAPPURAM.NS", "MUTHOOTFIN.NS", "CHOLAFIN.NS", "LICHSGFIN.NS",
    "CANBK.NS", "PNB.NS", "BANKBARODA.NS", "UNIONBANK.NS", "INDIANB.NS",
    "BEML.NS", "MAZDOCK.NS", "COCHINSHIP.NS", "BDL.NS", "DATAPATTNS.NS",
    "KPITTECH.NS", "TATAELXSI.NS", "CYIENT.NS", "ZENSARTECH.NS", "BSOFT.NS",
    "SONACOMS.NS", "ENDURANCE.NS", "EXIDEIND.NS", "ARE&M.NS", "APOLLOTYRE.NS",
    "CEATLTD.NS", "JKTYRE.NS", "BALKRISIND.NS", "ASHOKLEY.NS", "ESCORTS.NS",
    "KEI.NS", "HAVELLS.NS", "BLUESTARCO.NS", "WHIRLPOOL.NS", "CUMMINSIND.NS",
    "THERMAX.NS", "BHEL.NS", "CGPOWER.NS", "SIEMENS.NS", "APARINDS.NS",
    "NATIONALUM.NS", "HINDALCO.NS", "VEDL.NS", "NMDC.NS", "JINDALSTEL.NS",
    "SAIL.NS", "JINDALSAW.NS", "WELCORP.NS", "APLAPOLLO.NS", "RATNAMANI.NS",
    "GNFC.NS", "GSFC.NS", "RCF.NS", "FACT.NS", "PARADEEP.NS",
    "RADICO.NS", "UBL.NS", "UNITDSPR.NS", "ITC.NS", "TATACONSUM.NS",
    "VBL.NS", "JUBLFOOD.NS", "DEVYANI.NS", "CHALET.NS", "EIHOTEL.NS",
    "LEMONTREE.NS", "IRCTC.NS", "RVNL.NS", "IRCON.NS", "IRFC.NS",
    "RECLTD.NS", "PFC.NS", "IREDA.NS", "SJVN.NS", "NHPC.NS",
    "TATAPOWER.NS", "JSWENERGY.NS", "TORNTPOWER.NS", "IGL.NS", "MGL.NS",
    "PETRONET.NS", "GAIL.NS", "ONGC.NS", "OIL.NS", "IOC.NS",
    "BPCL.NS", "HINDPETRO.NS", "CHENNPETRO.NS", "MRPL.NS", "NATCOPHARM.NS",
    "LAURUSLABS.NS", "GRANULES.NS", "GLENMARK.NS", "BIOCON.NS", "LUPIN.NS",
    "ALKEM.NS", "TORNTPHARM.NS", "IPCALAB.NS", "AJANTPHARM.NS", "JBCHEPHARM.NS",
    "APOLLOHOSP.NS", "MAXHEALTH.NS", "FORTIS.NS", "NH.NS", "ASTERDM.NS",
    "METROPOLIS.NS", "LALPATHLAB.NS", "SYNGENE.NS", "DIVISLAB.NS", "CIPLA.NS",
    "DRREDDY.NS", "PRESTIGE.NS", "BRIGADE.NS", "SOBHA.NS", "GODREJPROP.NS",
    "PHOENIXLTD.NS", "MAHLIFE.NS", "NCC.NS", "KNRCON.NS", "PNCINFRA.NS",
]
STOCK_UNIVERSE = list(dict.fromkeys(STOCK_UNIVERSE))


# ==============================================================================
# ⚡ 3. DHAN HQ v2 DATA ENGINE (SCRIP MAPPER + LIVE BATCH QUOTE + 15M CHARTS)
# ==============================================================================
class DhanDataEngine:
    def __init__(self, client_id: str, access_token: str):
        self.client_id = client_id
        self.access_token = access_token
        self.enabled = bool(self.client_id and self.access_token)
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "client-id": self.client_id,
            "access-token": self.access_token,
        }
        self.sym_to_secid = {}
        self.secid_to_sym = {}
        if self.enabled:
            self._load_or_build_scrip_map()

    def _load_or_build_scrip_map(self):
        try:
            if DHAN_CACHE_FILE.exists():
                mtime = datetime.fromtimestamp(DHAN_CACHE_FILE.stat().st_mtime)
                if (datetime.now() - mtime).total_seconds() < 86400:
                    with open(DHAN_CACHE_FILE, "r") as f:
                        self.sym_to_secid = json.load(f)
                        self.secid_to_sym = {int(v): k for k, v in self.sym_to_secid.items()}
                        return

            url = "https://images.dhan.co/api-data/api-scrip-master.csv"
            df_scrip = pd.read_csv(
                url,
                usecols=[
                    "SEM_EXM_EXCH_ID",
                    "SEM_SEGMENT",
                    "SEM_SMST_SECURITY_ID",
                    "SEM_TRADING_SYMBOL",
                    "SEM_SERIES",
                ],
                low_memory=False,
            )
            nse_eq = df_scrip[
                (df_scrip["SEM_EXM_EXCH_ID"] == "NSE")
                & (df_scrip["SEM_SEGMENT"] == "E")
                & (df_scrip["SEM_SERIES"] == "EQ")
            ]
            mapping = {}
            for _, row in nse_eq.iterrows():
                raw_sym = str(row["SEM_TRADING_SYMBOL"]).strip().upper()
                clean_sym = raw_sym[:-3] if raw_sym.endswith("-EQ") else raw_sym
                sec_id = int(row["SEM_SMST_SECURITY_ID"])
                mapping[clean_sym] = sec_id

            if mapping:
                self.sym_to_secid = mapping
                self.secid_to_sym = {int(v): k for k, v in mapping.items()}
                with open(DHAN_CACHE_FILE, "w") as f:
                    json.dump(mapping, f)
                print(f"✅ Dhan Scrip Master cached ({len(mapping)} NSE EQ symbols).")
        except Exception as e:
            print(f"⚠️ Dhan Scrip Master load warning: {e}")

    def fetch_batch_live_quotes(self, symbols: list) -> tuple[dict, dict]:
        """
        Fetches real-time LTP, Day OHLC, Volume, ATP (VWAP), and Bid/Ask Orderbook Quantities
        for all NSE Stocks and Indices in a SINGLE HTTP request via Dhan /v2/marketfeed/quote.
        """
        eq_quotes = {}
        idx_quotes = {}
        if not self.enabled or not self.sym_to_secid:
            return eq_quotes, idx_quotes

        eq_sec_ids = [self.sym_to_secid[s] for s in symbols if s in self.sym_to_secid]
        idx_sec_ids = list(DHAN_INDEX_SEC_IDS.values())

        payload = {
            "NSE_EQ": eq_sec_ids,
            "IDX_I": idx_sec_ids,
        }
        try:
            resp = requests.post(
                "https://api.dhan.co/v2/marketfeed/quote",
                headers=self.headers,
                json=payload,
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                nse_eq_data = data.get("NSE_EQ", {})
                for sec_str, q in nse_eq_data.items():
                    sec_id = int(sec_str)
                    sym = self.secid_to_sym.get(sec_id)
                    if not sym:
                        continue
                    ohlc = q.get("ohlc", {})
                    buy_q = float(q.get("buy_quantity", 0) or 0)
                    sell_q = float(q.get("sell_quantity", 0) or 0)
                    ob_ratio = (buy_q / sell_q) if sell_q > 0 else 1.0
                    eq_quotes[sym] = {
                        "ltp": float(q.get("last_price", 0.0) or 0.0),
                        "open": float(ohlc.get("open", 0.0) or 0.0),
                        "high": float(ohlc.get("high", 0.0) or 0.0),
                        "low": float(ohlc.get("low", 0.0) or 0.0),
                        "prev_close": float(ohlc.get("close", 0.0) or 0.0),
                        "volume": float(q.get("volume", 0.0) or 0.0),
                        "vwap": float(q.get("average_price", 0.0) or 0.0),
                        "ob_ratio": round(ob_ratio, 2),
                    }

                idx_data = data.get("IDX_I", {})
                inv_idx = {v: k for k, v in DHAN_INDEX_SEC_IDS.items()}
                for sec_str, q in idx_data.items():
                    idx_name = inv_idx.get(int(sec_str))
                    if not idx_name:
                        continue
                    ohlc = q.get("ohlc", {})
                    idx_quotes[idx_name] = {
                        "ltp": float(q.get("last_price", 0.0) or 0.0),
                        "open": float(ohlc.get("open", 0.0) or 0.0),
                        "high": float(ohlc.get("high", 0.0) or 0.0),
                        "low": float(ohlc.get("low", 0.0) or 0.0),
                        "prev_close": float(ohlc.get("close", 0.0) or 0.0),
                    }
                print(f"✅ Dhan Live Batch Quote fetched for {len(eq_quotes)} equities & {len(idx_quotes)} indices.")
            else:
                print(f"⚠️ Dhan Quote API status {resp.status_code}: {resp.text[:160]}")
        except Exception as e:
            print(f"⚠️ Dhan Batch Quote error: {e}")
        return eq_quotes, idx_quotes

    def fetch_intraday_15m_df(self, sym: str, days_back: int = 45) -> pd.DataFrame:
        """
        Fetches exact 15-minute institutional OHLCV bars from Dhan /v2/charts/intraday
        for shortlisted candidates to eliminate Yahoo Finance 15m delay/missing ticks.
        """
        if not self.enabled or sym not in self.sym_to_secid:
            return pd.DataFrame()
        sec_id = str(self.sym_to_secid[sym])
        ist = pytz.timezone("Asia/Kolkata")
        now_ist = datetime.now(ist)
        from_date = (now_ist - timedelta(days=days_back)).strftime("%Y-%m-%d")
        to_date = now_ist.strftime("%Y-%m-%d")

        payload = {
            "securityId": sec_id,
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "interval": "15",
            "oi": False,
            "fromDate": from_date,
            "toDate": to_date,
        }
        try:
            resp = requests.post(
                "https://api.dhan.co/v2/charts/intraday",
                headers=self.headers,
                json=payload,
                timeout=8,
            )
            if resp.status_code == 200:
                d = resp.json()
                ts = d.get("timestamp", [])
                if not ts:
                    return pd.DataFrame()
                dt_idx = pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Kolkata").tz_localize(None)
                df = pd.DataFrame(
                    {
                        "Open": pd.to_numeric(d.get("open", []), errors="coerce"),
                        "High": pd.to_numeric(d.get("high", []), errors="coerce"),
                        "Low": pd.to_numeric(d.get("low", []), errors="coerce"),
                        "Close": pd.to_numeric(d.get("close", []), errors="coerce"),
                        "Volume": pd.to_numeric(d.get("volume", []), errors="coerce").fillna(0),
                    },
                    index=dt_idx,
                )
                return df.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()
        except Exception:
            pass
        return pd.DataFrame()


# ==============================================================================
# 🕸️ 4. LIVE NSE OI SPURTS SCRAPER & HYBRID FLOW + ORDER-BOOK CLASSIFIER
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


def classify_hybrid_flow(price_chg_pct: float, oi_chg_pct: float, rvol: float, side: str, ob_ratio: float = 1.0):
    # Block extreme opposite Order-Book Walls from Dhan Live Depth
    if side == "BUY" and ob_ratio < 0.38:
        return True, False, "OB-ASK!"
    if side == "SHORT" and ob_ratio > 2.60:
        return True, False, "OB-BID!"

    if abs(oi_chg_pct) >= 0.5:
        sign_str = f"{oi_chg_pct:+.0f}%"
        if price_chg_pct >= 0.0 and oi_chg_pct >= 2.0:
            if side == "SHORT":
                return True, False, f"LB{sign_str:>5}"
            return False, True, f"LB{sign_str:>5}"
        if price_chg_pct < 0.0 and oi_chg_pct >= 2.0:
            if side == "BUY":
                return True, False, f"SB{sign_str:>5}"
            return False, True, f"SB{sign_str:>5}"
        if price_chg_pct >= 0.2 and oi_chg_pct <= -2.0:
            if side == "SHORT":
                return True, False, f"SC{sign_str:>5}"
            return False, True, f"SC{sign_str:>5}"
        if price_chg_pct <= -0.2 and oi_chg_pct <= -2.0:
            if side == "BUY":
                return True, False, f"LU{sign_str:>5}"
            return False, True, f"LU{sign_str:>5}"

    if 1.35 <= rvol <= 9.0:
        if (side == "BUY" and price_chg_pct > 0) or (side == "SHORT" and price_chg_pct < 0):
            return False, True, f"VL{rvol:>4.1f}x"
        return False, False, f"VL{rvol:>4.1f}x"

    # Boost if Dhan Live Order-Book shows strong institutional accumulation/distribution
    if side == "BUY" and price_chg_pct > 0.25 and ob_ratio >= 1.35 and rvol >= 1.15:
        return False, True, f"OB{ob_ratio:>4.1f}x"
    if side == "SHORT" and price_chg_pct < -0.25 and 0 < ob_ratio <= 0.72 and rvol >= 1.15:
        return False, True, f"OB{ob_ratio:>4.1f}x"

    return False, False, "   --  "


# ==============================================================================
# 📈 5. DATA PATCHING, PRECISION INDICATORS & TIME-DECAYED BACKTEST ENGINE
# ==============================================================================
def get_closed_bar_slice(df: pd.DataFrame, is_intraday: bool) -> pd.DataFrame:
    if df.empty or len(df) < 30:
        return df
    df = df.copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
    if not is_intraday:
        return df

    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist).replace(tzinfo=None)
    now_mins = now_ist.hour * 60 + now_ist.minute
    in_forming_window = (
        now_ist.weekday() < 5
        and (9 * 60 + 15) <= now_mins < (15 * 60 + 30)
    )
    if in_forming_window:
        last_ts = df.index[-1]
        # Only drop the last 15m bar if its 15-minute completion timestamp hasn't passed yet!
        bar_close_time = last_ts + timedelta(minutes=15)
        if last_ts.date() == now_ist.date() and now_ist < bar_close_time:
            return df.iloc[:-1].copy()
    return df


def patch_daily_df_with_dhan_quote(df: pd.DataFrame, dhan_q: dict) -> pd.DataFrame:
    """
    Injects or updates today's real-time NSE Daily OHLCV bar using Dhan's zero-latency quote,
    and scales intraday volume to full-day pace so Daily RVOL works accurately mid-market.
    """
    if df.empty or not dhan_q or dhan_q.get("ltp", 0) <= 0:
        return df
    df = df.copy()
    # yfinance daily Volume is int64; assigning a pace-adjusted (float) volume into it raises TypeError on pandas>=3
    df = df.astype({c: "float64" for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns})
    if df.index.tz is not None:
        df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)

    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    today_date = now_ist.date()

    # Calculate intraday elapsed trading fraction (out of 375 mins) to pace-adjust today's volume
    mins_elapsed = max(15.0, min(375.0, (now_ist.hour * 60 + now_ist.minute) - (9 * 60 + 15)))
    vol_pace_mult = (375.0 / mins_elapsed) if (now_ist.weekday() < 5 and 555 <= (now_ist.hour * 60 + now_ist.minute) <= 930) else 1.0
    projected_vol = float(dhan_q.get("volume", 0.0) or 0.0) * min(vol_pace_mult, 3.5)   # index quotes carry no volume

    last_idx = df.index[-1]
    last_date = last_idx.date() if hasattr(last_idx, "date") else today_date

    o = dhan_q["open"] if dhan_q["open"] > 0 else dhan_q["ltp"]
    h = max(dhan_q["high"], dhan_q["ltp"], o)
    l = min(dhan_q["low"] if dhan_q["low"] > 0 else dhan_q["ltp"], dhan_q["ltp"], o)
    c = dhan_q["ltp"]

    if last_date == today_date:
        df.iloc[-1, df.columns.get_loc("Open")] = o
        df.iloc[-1, df.columns.get_loc("High")] = max(float(df.iloc[-1]["High"]), h)
        df.iloc[-1, df.columns.get_loc("Low")] = min(float(df.iloc[-1]["Low"]), l)
        df.iloc[-1, df.columns.get_loc("Close")] = c
        if projected_vol > 0:
            df.iloc[-1, df.columns.get_loc("Volume")] = max(float(df.iloc[-1]["Volume"]), projected_vol)
    elif now_ist.weekday() < 5 and today_date not in NSE_HOLIDAYS_2026 and (now_ist.hour * 60 + now_ist.minute) >= 555:
        new_ts = pd.Timestamp(today_date)
        new_row = pd.DataFrame(
            {"Open": [o], "High": [h], "Low": [l], "Close": [c], "Volume": [projected_vol]},
            index=[new_ts],
        )
        df = pd.concat([df, new_row])
    return df


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


def compute_wilder_atr_and_adx(df: pd.DataFrame, period: int = 14):
    h, l, c = df["High"], df["Low"], df["Close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()

    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_di = 100.0 * pd.Series(plus_dm, index=df.index).ewm(alpha=1.0 / period, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100.0 * pd.Series(minus_dm, index=df.index).ewm(alpha=1.0 / period, adjust=False).mean() / atr.replace(0, np.nan)
    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0.0)
    adx = dx.ewm(alpha=1.0 / period, adjust=False).mean().fillna(20.0)
    return atr, adx


def compute_daily_swing_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    df["EMA9"]  = c.ewm(span=9, adjust=False).mean()
    df["EMA21"] = c.ewm(span=21, adjust=False).mean()
    df["EMA55"] = c.ewm(span=55, adjust=False).mean()

    df["BB_SMA_38"] = c.rolling(38).mean()
    std_38 = c.rolling(38).std(ddof=0)
    df["BB_UP_38"] = df["BB_SMA_38"] + (2.0 * std_38)
    df["BB_LO_38"] = df["BB_SMA_38"] - (2.0 * std_38)

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["RSI"] = (100 - (100 / (1 + (gain / loss.replace(0, np.nan))))).fillna(50.0)

    df["ATR"], df["ADX"] = compute_wilder_atr_and_adx(df, 14)

    vol_sma20 = df["Volume"].rolling(20).mean().replace(0, np.nan)
    df["RVOL"] = (df["Volume"] / vol_sma20).fillna(1.0)

    # Candle Anatomy & Momentum Confirmation (Eliminates Falling Knife / Dead Chop Signals)
    bar_range = (h - l).replace(0, np.nan)
    close_pos = ((c - l) / bar_range).fillna(0.5)

    df["BUY_Signal"] = (
        (c >= df["EMA21"] * 0.995)
        & (df["EMA21"] >= df["EMA55"] * 0.99)
        & (c < df["BB_UP_38"])
        & (df["RSI"] >= 46)
        & (df["RSI"] <= 68)
        & (df["ADX"] >= 16)
        & ((c > o) | (close_pos >= 0.55))
        & ((c >= df["EMA9"]) | (c > h.shift(1)))
    )
    df["SHORT_Signal"] = (
        (c <= df["EMA21"] * 1.005)
        & (df["EMA21"] <= df["EMA55"] * 1.01)
        & (c > df["BB_LO_38"])
        & (df["RSI"] >= 30)
        & (df["RSI"] <= 53)
        & (df["ADX"] >= 16)
        & ((c < o) | (close_pos <= 0.45))
        & ((c <= df["EMA9"]) | (c < l.shift(1)))
    )
    return df


def compute_intraday_15m_indicators(df: pd.DataFrame, is_index: bool = False, dhan_vwap: float = 0.0) -> pd.DataFrame:
    df = df.copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    df["EMA9"]  = c.ewm(span=9, adjust=False).mean()
    df["EMA21"] = c.ewm(span=21, adjust=False).mean()

    if is_index or (df["Volume"].sum() == 0):
        df["TREND_ANCHOR"] = df["EMA21"]
    else:
        tp = (h + l + c) / 3.0
        vol_clean = df["Volume"].replace(0, 1)
        dates = pd.Series(df.index.date, index=df.index)
        df["TREND_ANCHOR"] = (tp * vol_clean).groupby(dates).cumsum() / vol_clean.groupby(dates).cumsum()
        # Anchor the latest session bar to Dhan's exact NSE Exchange ATP/VWAP when available
        if dhan_vwap > 0:
            df.iloc[-1, df.columns.get_loc("TREND_ANCHOR")] = dhan_vwap

    df["BB_SMA_38"] = c.rolling(38).mean()
    std_38 = c.rolling(38).std(ddof=0)
    df["BB_UP_38"] = df["BB_SMA_38"] + (2.0 * std_38)
    df["BB_LO_38"] = df["BB_SMA_38"] - (2.0 * std_38)

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["RSI"] = (100 - (100 / (1 + (gain / loss.replace(0, np.nan))))).fillna(50.0)

    df["ATR"], df["ADX"] = compute_wilder_atr_and_adx(df, 14)

    # Time-of-Day (ToD) Normalized RVOL: Compares 10:15 AM bar strictly against historical 10:15 AM bars!
    tod_groups = df["Volume"].groupby(df.index.time)
    tod_mean = tod_groups.transform(lambda x: x.rolling(10, min_periods=3).mean()).replace(0, np.nan)
    fallback_mean = df["Volume"].rolling(20).mean().replace(0, np.nan)
    df["RVOL"] = (df["Volume"] / tod_mean.fillna(fallback_mean)).fillna(1.0)

    bar_range = (h - l).replace(0, np.nan)
    close_pos = ((c - l) / bar_range).fillna(0.5)

    df["BUY_Signal"] = (
        (c >= df["TREND_ANCHOR"])
        & (df["EMA9"] >= df["EMA21"] * 0.998)
        & (c < df["BB_UP_38"])
        & (df["RSI"] >= 48)
        & (df["RSI"] <= 72)
        & (df["ADX"] >= 16)
        & ((c > o) | (close_pos >= 0.55))
    )
    df["SHORT_Signal"] = (
        (c <= df["TREND_ANCHOR"])
        & (df["EMA9"] <= df["EMA21"] * 1.002)
        & (c > df["BB_LO_38"])
        & (df["RSI"] >= 28)
        & (df["RSI"] <= 52)
        & (df["ADX"] >= 16)
        & ((c < o) | (close_pos <= 0.45))
    )
    return df


def _stop_exit_r(side, open_j, stop_px, entry, risk, t1_reached, t1_r):
    """R of a stop exit with a GAP-AWARE fill: if the bar opens beyond the stop, the fill is the open (worse), not the stop."""
    if not np.isfinite(open_j):
        open_j = stop_px
    fill = min(open_j, stop_px) if side == "BUY" else max(open_j, stop_px)
    r_fill = ((fill - entry) / risk) if side == "BUY" else ((entry - fill) / risk)
    return (0.5 * t1_r + 0.5 * min(r_fill, 0.0)) if t1_reached else r_fill


def backtest_cash_signals(
    df: pd.DataFrame, side: str = "BUY", atr_mult: float = 1.2, rr_target: float = 2.5, max_bars: int = 8, is_intraday: bool = False
):
    """
    Institutional Backtest Engine:
    - Enforces identical percentage risk bounds ([1.2%-3.0%] intra / [1.5%-5.5%] swing) as live execution.
    - Resolves dual SL+Target same-bar collisions using bar Open proximity.
    - Applies exponential recency weighting (recent setups carry higher weight than 3-year-old setups).
    """
    sig_col = "BUY_Signal" if side == "BUY" else "SHORT_Signal"
    opens, closes, highs, lows, atrs, sigs = (
        df["Open"].values,
        df["Close"].values,
        df["High"].values,
        df["Low"].values,
        df["ATR"].values,
        df[sig_col].values,
    )
    dates = df.index.date if is_intraday else None

    t1_r_lock = INTRA_T1_RR if is_intraday else SWING_T1_RR
    full_r_lock = rr_target
    min_pct = 0.012 if is_intraday else 0.015
    max_pct = 0.030 if is_intraday else 0.055

    trade_results = []
    n = len(df)
    i = 65

    while i < n - max_bars - 1:
        if not sigs[i] or np.isnan(atrs[i]) or atrs[i] <= 0:
            i += 1
            continue
        entry = closes[i]
        raw_risk = atrs[i] * atr_mult
        risk = max(min(entry * max_pct, max(entry * min_pct, raw_risk)), 0.2)
        if risk <= 0:
            i += 1
            continue

        sl = (entry - risk) if side == "BUY" else (entry + risk)
        t1 = (entry + risk * t1_r_lock) if side == "BUY" else (entry - risk * t1_r_lock)
        tgt = (entry + risk * full_r_lock) if side == "BUY" else (entry - risk * full_r_lock)

        t1_reached = False
        outcome_resolved = False
        trade_r = 0.0
        exit_bar = i + 1

        for j in range(i + 1, min(i + 1 + max_bars, n)):
            exit_bar = j
            if is_intraday and dates[j] != dates[i]:
                prev_close = closes[j - 1]
                mtm_r = ((prev_close - entry) / risk) if side == "BUY" else ((entry - prev_close) / risk)
                mtm_r = float(np.clip(mtm_r, -1.0, full_r_lock))
                trade_r = (0.5 * t1_r_lock + 0.5 * max(0.0, mtm_r)) if t1_reached else mtm_r
                outcome_resolved = True
                break

            if side == "BUY":
                curr_sl = entry if t1_reached else sl
                hit_sl = lows[j] <= curr_sl
                hit_tgt = highs[j] >= tgt
                if hit_sl and hit_tgt:
                    # Resolve same-bar collision via Open proximity
                    if abs(opens[j] - curr_sl) <= abs( tgt - opens[j]):
                        trade_r = _stop_exit_r(side, opens[j], curr_sl, entry, risk, t1_reached, t1_r_lock)
                    else:
                        trade_r = 0.5 * t1_r_lock + 0.5 * full_r_lock
                    outcome_resolved = True
                    break
                elif hit_sl:
                    trade_r = _stop_exit_r(side, opens[j], curr_sl, entry, risk, t1_reached, t1_r_lock)
                    outcome_resolved = True
                    break
                elif hit_tgt:
                    trade_r = 0.5 * t1_r_lock + 0.5 * full_r_lock
                    outcome_resolved = True
                    break
                if highs[j] >= t1:
                    t1_reached = True
            else:
                curr_sl = entry if t1_reached else sl
                hit_sl = highs[j] >= curr_sl
                hit_tgt = lows[j] <= tgt
                if hit_sl and hit_tgt:
                    if abs(curr_sl - opens[j]) <= abs(opens[j] - tgt):
                        trade_r = _stop_exit_r(side, opens[j], curr_sl, entry, risk, t1_reached, t1_r_lock)
                    else:
                        trade_r = 0.5 * t1_r_lock + 0.5 * full_r_lock
                    outcome_resolved = True
                    break
                elif hit_sl:
                    trade_r = _stop_exit_r(side, opens[j], curr_sl, entry, risk, t1_reached, t1_r_lock)
                    outcome_resolved = True
                    break
                elif hit_tgt:
                    trade_r = 0.5 * t1_r_lock + 0.5 * full_r_lock
                    outcome_resolved = True
                    break
                if lows[j] <= t1:
                    t1_reached = True

        if not outcome_resolved:
            end_close = closes[exit_bar]
            mtm_r = ((end_close - entry) / risk) if side == "BUY" else ((entry - end_close) / risk)
            mtm_r = float(np.clip(mtm_r, -1.0, full_r_lock))
            trade_r = (0.5 * t1_r_lock + 0.5 * max(0.0, mtm_r)) if t1_reached else mtm_r

        net_r = trade_r - COST_SLIPPAGE_R
        trade_results.append(net_r)
        i = max(i + 1, exit_bar)

    total_trades = len(trade_results)
    if total_trades == 0:
        return 0, -1.0, 0.0, 9.9

    arr_r = np.array(trade_results, dtype=float)
    # Exponential Recency Weighting: Recent regime setups weigh more than oldest setups
    weights = np.exp(np.linspace(-1.0, 0.0, total_trades))
    weights /= weights.sum()

    expectancy = float(np.sum(arr_r * weights))
    win_pct = float(np.sum((arr_r > 0) * weights) * 100.0)
    se = float(arr_r.std(ddof=1) * np.sqrt(np.sum(weights ** 2))) if total_trades > 1 else 9.9
    return total_trades, expectancy, win_pct, se


def build_stock_trade_row(
    sym: str,
    df: pd.DataFrame,
    side: str,
    is_intraday: bool,
    flow_tag: str,
    is_boosted: bool,
    exp_r: float,
    win_pct: float,
    horizon_tag: str,
    rs_bonus: float = 0.0,
):
    latest = df.iloc[-1]
    entry = round(float(latest["Close"]), 1)
    atr = float(latest["ATR"])
    if entry <= 10.0 or np.isnan(atr) or atr <= 0:
        return None

    atr_mult = INTRA_ATR_SL_MULT if is_intraday else SWING_ATR_SL_MULT
    t1_rr = INTRA_T1_RR if is_intraday else SWING_T1_RR
    full_rr = INTRA_RR if is_intraday else SWING_RR

    raw_risk = atr * atr_mult
    min_risk = entry * (0.012 if is_intraday else 0.015)
    max_risk = entry * (0.030 if is_intraday else 0.055)
    risk_per_share = max(round(min(max_risk, max(min_risk, raw_risk)), 1), 0.2)

    if side == "BUY":
        sl = round(entry - risk_per_share, 1)
        actual_risk = max(round(entry - sl, 1), 0.2)
        t1 = round(entry + actual_risk * t1_rr, 1)
        tgt = round(entry + actual_risk * full_rr, 1)
    else:
        sl = round(entry + risk_per_share, 1)
        actual_risk = max(round(sl - entry, 1), 0.2)
        t1 = round(entry - actual_risk * t1_rr, 1)
        tgt = round(entry - actual_risk * full_rr, 1)

    qty_by_risk = int(RISK_PER_TRADE // actual_risk)
    effective_buying_power = (MAX_CAPITAL_PER_TRADE * INTRA_MIS_LEVERAGE) if is_intraday else MAX_CAPITAL_PER_TRADE
    qty_by_cap = int(effective_buying_power // entry)
    qty = min(qty_by_risk, qty_by_cap)

    if qty < 1:
        return None

    margin_divisor = INTRA_MIS_LEVERAGE if is_intraday else 1.0
    capital = int(round((entry * qty) / margin_divisor))
    total_rupee_risk = int(round(actual_risk * qty))

    exp_rounded = round(exp_r, 2)
    win_rounded = round(win_pct, 1)
    score = round((exp_rounded * 100.0) + win_rounded + rs_bonus, 1)

    return {
        "Symbol": sym,
        "Side": side,
        "Horizon": horizon_tag,
        "Entry": entry,
        "Qty": qty,
        "Capital": capital,
        "RiskRs": total_rupee_risk,
        "Stop": sl,
        "T1": t1,
        "Target": tgt,
        "Flow": flow_tag,
        "Expectancy": exp_rounded,
        "WinProb": win_rounded,
        "Score": score,
        "FlowBoost": 1 if is_boosted else 0,
        "DirectCashExec": 1 if (is_intraday or side == "BUY") else 0,
    }


# ==============================================================================
# 📱 6. UNIFIED SINGLE-MESSAGE FORMATTER (SIMPLE, CLEAN & ACTIONABLE)
# ==============================================================================
def format_stock_table_block(rows):
    stk_hdr = (
        f"{'Symbol':<11}|{'Side':<5}|{'Entry':>6}|{'Qty':>4}|{'Cap(₹)':>6}|"
        f"{'SL':>6}|{'T1-50%':>6}|{'Target':>6}|{'Flow':>7}|{'ExpR':>5}|{'Win%':>5}|{'Score':>5}"
    )
    stk_sep = "-" * len(stk_hdr)
    block = ["<pre>", stk_hdr, stk_sep]
    for r in rows:
        row_str = (
            f"{r['Symbol']:<11}|"
            f"{r['Side']:<5}|"
            f"{r['Entry']:>6.1f}|"
            f"{r['Qty']:>4d}|"
            f"{r['Capital']:>6d}|"
            f"{r['Stop']:>6.1f}|"
            f"{r['T1']:>6.1f}|"
            f"{r['Target']:>6.1f}|"
            f"{r['Flow'][:7]:>7}|"
            f"{r['Expectancy']:>+5.2f}|"
            f"{r['WinProb']:>4.1f}%|"
            f"{r['Score']:>5.1f}"
        )
        block.append(html.escape(row_str))
    block.append("</pre>")
    return "\n".join(block)


def build_single_master_message(mood_summary, intra_rows, short_1d_rows, buy_1d_rows, now_str, dhan_active: bool, market_open: bool = True):
    feed_badge = "Dhan HQ v2 Live ⚡" if dhan_active else "YF Feed"
    lines = [
        f"<b>🎯 MASTER STOCK SCANNER ({feed_badge})</b>",
        f"🕒 <code>{html.escape(now_str)}</code> | Max Risk: <code>₹{RISK_PER_TRADE:,}</code> | Max Cap: <code>₹{MAX_CAPITAL_PER_TRADE:,}</code>\n",
        mood_summary,
    ]

    if intra_rows:
        lines.append(f"\n<b>⚡ 1. TOP {len(intra_rows)} INTRADAY 15M SCALPS (1:2.0 RR | 2x MIS)</b>")
        lines.append(format_stock_table_block(intra_rows))

    if short_1d_rows:
        lines.append(f"\n<b>📉 2. TOP {len(short_1d_rows)} 1D BEARISH BREAKDOWNS (MIS SHORT / PUTS ONLY)</b>")
        lines.append(format_stock_table_block(short_1d_rows))

    if buy_1d_rows:
        lines.append(f"\n<b>🚀 3. TOP {len(buy_1d_rows)} POSITIONAL CASH SWING BUYS (1D | CNC 1:2.5 RR)</b>")
        lines.append(format_stock_table_block(buy_1d_rows))

    exec_cands = list(intra_rows) + list(buy_1d_rows)
    if not exec_cands:
        exec_cands = list(short_1d_rows)

    boosted = [r for r in exec_cands if r["FlowBoost"] == 1 and r["Expectancy"] >= 0.12]
    if not boosted:
        boosted = exec_cands
    boosted.sort(key=lambda x: (x["DirectCashExec"], x["FlowBoost"], x["Score"]), reverse=True)

    seen_syms = set()
    top_three = []
    for r in boosted:
        if r["Symbol"] not in seen_syms:
            seen_syms.add(r["Symbol"])
            top_three.append(r)
        if len(top_three) == 3:
            break

    if top_three and market_open:
        lines.append("\n<b>🏆 TOP 3 DIRECT CASH TRADES TO TAKE RIGHT NOW:</b>")
        for idx, p in enumerate(top_three, 1):
            lines.append(
                f"{idx}. <b>{html.escape(p['Symbol'])} ({p['Side']} - {p['Horizon']})</b> "
                f"👉 Entry: <code>₹{p['Entry']:.1f}</code> | Qty: <code>{p['Qty']}</code> "
                f"| Cap: <code>₹{p['Capital']:,}</code> | Risk: <code>₹{p['RiskRs']:,}</code> "
                f"| SL: <code>{p['Stop']:.1f}</code> | T1: <code>{p['T1']:.1f}</code> | Tgt: <code>{p['Target']:.1f}</code> "
                f"| Flow: <code>{html.escape(p['Flow'].strip())}</code> | <b>Score: {p['Score']:.1f}</b>"
            )

    if not market_open and (intra_rows or short_1d_rows or buy_1d_rows):
        lines.append("\n⚠️ <i>Market closed: rows are for planning only (prices are the last close, not tradable now).</i>")
    lines.append("\n💡 <i>15M Cap uses 2x MIS Margin | 1D Shorts = Trade via Intraday MIS or F&amp;O Puts</i>")
    return "\n".join(lines)


def send_telegram_alert(html_text: str):
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


# ==============================================================================
# 🚀 7. MAIN EXECUTION ENGINE
# ==============================================================================
def _flow_change_pct(spot, prev_close, fallback_pct):
    """% change vs the PREVIOUS CLOSE: the reference period NSE's OI change is measured against.
    Open-based change misclassifies gap days (e.g. +1% from a gap-down open but -2% vs prev close).
    Falls back to the supplied open-based change when either price is missing."""
    if spot and prev_close and spot > 0 and prev_close > 0:
        return (spot - prev_close) / prev_close * 100.0
    return fallback_pct


def _new_tel():
    keys = ("scanned", "signal", "blocked", "no_boost", "laggard", "bt_few", "bt_fail", "passed",
            "sh_blk_n", "sh_blk_pass", "sh_nb_n", "sh_nb_pass")      # sh_* = flow-filter shadow check
    tel = {k: dict.fromkeys(keys, 0) for k in ("intra", "sw_buy", "sw_short")}
    for t in tel.values():
        t["near"] = []          # (gap to gate, sym, side, ExpR, SE, N) for names that failed the ExpR-SE gate
        t["shadow_pass"] = []   # same tuple, for names the FLOW FILTER removed that WOULD have passed the gate
    return tel


def _shadow_eval(tel_t, sym, side, df, atr_mult, rr, max_bars, is_intraday, min_n, floor, reason):
    """Shadow check of the flow filter: backtest a signal the filter REMOVED against the SAME gate, so we can see
    whether the filter removes worse setups than it keeps (compare pass rates) or just removes setups."""
    try:
        n, e, w, se = backtest_cash_signals(df, side, atr_mult, rr, max_bars, is_intraday)
    except Exception:
        return
    gap = e - BT_SE_K * se - floor
    ok = n >= min_n and gap >= 0
    k = "sh_blk" if reason == "blocked" else "sh_nb"
    tel_t[k + "_n"] += 1
    tel_t[k + "_pass"] += int(ok)
    if ok:
        tel_t["shadow_pass"].append((gap, sym, side, e, se, n))


def _telemetry_text(tel, skipped_idx, n_idx_used, n_idx_total, ran):
    """Where did each scanned name drop out? One line per scan type (also printed to the run log)."""
    def one(k, label):
        t = tel[k]
        if not ran[k]:
            return f"{label}: not scanned (" + ("market closed" if k == "intra" else "before 15:10 IST") + ")"
        extra = f" | {t['laggard']} rel-strength" if k != "intra" else ""
        return (f"{label}: {t['scanned']} scanned | {t['signal']} with signal | {t['blocked']} flow-blocked | "
                f"{t['no_boost']} no flow boost{extra} | {t['bt_few']} too few trades | "
                f"{t['bt_fail']} failed ExpR-SE gate | {t['passed']} passed")
    lines = [one("intra", "Intraday"), one("sw_buy", "Swing BUY"), one("sw_short", "Swing SHORT"),
             f"Indices used: {n_idx_used}/{n_idx_total}" + (" | skipped: " + "; ".join(skipped_idx) if skipped_idx else "")]

    def near(k, label):
        t = tel[k]
        if not ran[k] or not t["near"]:
            return None
        best = sorted(t["near"], key=lambda x: -x[0])[:3]            # closest to passing first
        return (f"{label} near-misses (ExpR+-SE, trades, gap to gate): "
                + "; ".join(f"{s} {sd} {e:+.2f}+-{se:.2f} n{n} ({g:+.2f})" for g, s, sd, e, se, n in best))

    def shadow(k, label):
        t = tel[k]
        rem_n = t["sh_blk_n"] + t["sh_nb_n"]
        if not ran[k] or rem_n == 0:
            return None
        kept_n = t["bt_few"] + t["bt_fail"] + t["passed"]
        rem_p = t["sh_blk_pass"] + t["sh_nb_pass"]
        txt = (f"{label} flow-filter check: kept {t['passed']}/{kept_n} passed the gate; removed {rem_p}/{rem_n} would pass "
               f"(blocked {t['sh_blk_pass']}/{t['sh_blk_n']}, no-boost {t['sh_nb_pass']}/{t['sh_nb_n']})")
        if t["shadow_pass"]:
            top = sorted(t["shadow_pass"], key=lambda x: -x[0])[:3]
            txt += " | e.g. " + ", ".join(f"{sy} {sd} {e:+.2f}+-{se:.2f} n{n}" for g, sy, sd, e, se, n in top)
        return txt

    for k, label in (("intra", "Intraday"), ("sw_buy", "Swing BUY"), ("sw_short", "Swing SHORT")):
        for extra in (near(k, label), shadow(k, label)):
            if extra:
                lines.append(extra)
    return lines


def main():
    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    now_str = now_ist.strftime("%d-%b %I:%M %p IST")
    current_minutes = now_ist.hour * 60 + now_ist.minute
    market_open_now = now_ist.weekday() < 5 and now_ist.date() not in NSE_HOLIDAYS_2026 and (9 * 60 + 15) <= current_minutes < (15 * 60 + 30)
    run_intraday = market_open_now or INTRADAY_WHEN_CLOSED
    if not run_intraday:
        print('Market closed: intraday scan skipped (those rows would use stale prices).')
    run_swing = (not SWING_LATE_SCAN_ONLY) or FORCE_RUN or current_minutes >= (SWING_SCAN_FROM[0] * 60 + SWING_SCAN_FROM[1])
    if not run_swing:
        print('Swing scan deferred: daily signals are evaluated from 15:10 IST, once the daily candle is nearly complete.')

    if not FORCE_RUN:
        if (
            now_ist.weekday() >= 5
            or now_ist.date() in NSE_HOLIDAYS_2026
            or current_minutes < (9 * 60 + 15)
            or current_minutes > (15 * 60 + 50)
        ):
            print(f"⏸️ Outside Market Hours or NSE Holiday ({now_str}). Use --force to override.")
            return

    clean_syms = [tk.replace(".NS", "") for tk in STOCK_UNIVERSE]
    dhan = DhanDataEngine(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN)
    dhan_eq_quotes, dhan_idx_quotes = dhan.fetch_batch_live_quotes(clean_syms)
    oi_spurts_map = fetch_nse_oi_spurts_map()

    all_tickers = list(INDEX_UNIVERSE.keys()) + STOCK_UNIVERSE
    raw_15m = yf.download(all_tickers, period="60d", interval="15m", group_by="ticker", progress=False)
    raw_1d  = yf.download(all_tickers, period="5y", interval="1d", group_by="ticker", progress=False)

    intraday_chg_map = {}
    skipped_idx = []
    idx_badges = []
    bear_count = 0
    nifty_5d_ret = 0.0

    # 1. TRUE MACRO REGIME CHECK (Fuses Dhan Live Index Quote + Daily EMA21 + OI Flow)
    for tk, idx_name in INDEX_UNIVERSE.items():
        df15 = get_closed_bar_slice(extract_ticker_df(raw_15m, tk), is_intraday=True)
        df1d = patch_daily_df_with_dhan_quote(
            get_closed_bar_slice(extract_ticker_df(raw_1d, tk), is_intraday=False),
            dhan_idx_quotes.get(idx_name, {}),
        )
        if len(df1d) < 60:
            skipped_idx.append(f"{idx_name} ({len(df1d)} daily bars from Yahoo/Dhan)")
            print(f"Index {idx_name} skipped: only {len(df1d)} daily bars (need 60). Check the Yahoo ticker {tk}.")
            continue
        df1d = compute_daily_swing_indicators(df1d)
        last1d = df1d.iloc[-1]
        prev1d = df1d.iloc[-2]

        if idx_name == "NIFTY" and len(df1d) >= 6:
            nifty_5d_ret = ((float(last1d["Close"]) - float(df1d["Close"].iloc[-6])) / float(df1d["Close"].iloc[-6])) * 100.0

        idx_q = dhan_idx_quotes.get(idx_name, {})
        if idx_q.get("ltp", 0) > 0 and idx_q.get("open", 0) > 0:
            spot = idx_q["ltp"]
            price_chg_pct = ((spot - idx_q["open"]) / idx_q["open"]) * 100.0
        elif len(df15) >= 20:
            last15 = df15.iloc[-1]
            today_bars = df15[df15.index.date == last15.name.date()]
            day_open = float(today_bars["Open"].iloc[0]) if not today_bars.empty else float(last15["Open"])
            spot = float(last15["Close"])
            price_chg_pct = ((spot - day_open) / day_open) * 100.0
        else:
            spot = float(last1d["Close"])
            price_chg_pct = ((spot - float(prev1d["Close"])) / float(prev1d["Close"])) * 100.0

        intraday_chg_map[idx_name] = price_chg_pct
        oi_chg_pct = oi_spurts_map.get(idx_name, 0.0)
        idx_flow_chg = _flow_change_pct(spot, float(prev1d["Close"]), price_chg_pct)   # OI is measured vs prev close

        is_bearish = (idx_flow_chg < 0 and oi_chg_pct >= 2.0) or (spot < float(last1d["EMA21"]))
        if is_bearish:
            bear_count += 1
            flow_lbl = f"SB {oi_chg_pct:+.0f}%" if (idx_flow_chg < 0 and oi_chg_pct >= 1.0) else f"{idx_flow_chg:+.1f}%"
            idx_badges.append(f"<code>{idx_name}: {spot:,.0f} (BEAR 🔴 | {flow_lbl})</code>")
        else:
            flow_lbl = f"LB {oi_chg_pct:+.0f}%" if (idx_flow_chg >= 0 and oi_chg_pct >= 1.0) else f"{idx_flow_chg:+.1f}%"
            idx_badges.append(f"<code>{idx_name}: {spot:,.0f} (BULL 🟢 | {flow_lbl})</code>")

    overall_mood = "BEARISH 🔴 (Focus on SHORTS / PUTS)" if bear_count >= 2 else "BULLISH 🟢 (Focus on BUYS / CALLS)"
    mood_lines = [f"<b>🧭 MARKET MOOD: {overall_mood}</b>"]
    if len(idx_badges) >= 2:
        mood_lines.append("• " + " | ".join(idx_badges[:2]))
    if len(idx_badges) >= 3:
        mood_lines.append("• " + " | ".join(idx_badges[2:]))
    mood_summary = "\n".join(mood_lines)

    # 2. SCAN STOCKS ON 15M (INTRADAY) & 1D (POSITIONAL SHORT & BUY)
    TEL = _new_tel()
    intra_stk_cands = []
    swing_short_cands = []
    swing_buy_cands = []
    nifty_intra_chg = intraday_chg_map.get("NIFTY", 0.0)

    for tk in STOCK_UNIVERSE:
        sym = tk.replace(".NS", "")
        dhan_q = dhan_eq_quotes.get(sym, {})
        ob_ratio = dhan_q.get("ob_ratio", 1.0)
        dhan_vwap = dhan_q.get("vwap", 0.0)

        # --- A. 15M INTRADAY SCAN ---
        df15_raw = extract_ticker_df(raw_15m, tk)
        df15 = get_closed_bar_slice(df15_raw, is_intraday=True)
        if run_intraday and len(df15) >= 80:
            df15 = compute_intraday_15m_indicators(df15, is_index=False, dhan_vwap=dhan_vwap)
            last = df15.iloc[-1]
            prev = df15.iloc[-2]

            if dhan_q.get("ltp", 0) > 0 and dhan_q.get("open", 0) > 0:
                price_chg_pct = ((dhan_q["ltp"] - dhan_q["open"]) / dhan_q["open"]) * 100.0
            else:
                today_bars = df15[df15.index.date == last.name.date()]
                day_open = float(today_bars["Open"].iloc[0]) if not today_bars.empty else float(last["Open"])
                price_chg_pct = ((float(last["Close"]) - day_open) / day_open) * 100.0

            intraday_chg_map[sym] = price_chg_pct
            oi_chg_pct = oi_spurts_map.get(sym, 0.0)
            rvol = float(last.get("RVOL", 1.0))

            spot_15 = dhan_q["ltp"] if dhan_q.get("ltp", 0) > 0 else float(last["Close"])
            side = "BUY" if spot_15 >= float(last["TREND_ANCHOR"]) else "SHORT"
            sig_col = "BUY_Signal" if side == "BUY" else "SHORT_Signal"
            _prior = df15[df15.index.date < last.name.date()]
            _prev_close = float(_prior["Close"].iloc[-1]) if (not _prior.empty and last.name.date() == now_ist.date()) else 0.0
            flow_chg_pct = _flow_change_pct(spot_15, _prev_close, price_chg_pct)   # momentum (rs_alpha) stays open-based
            is_blocked, is_boosted, flow_tag = classify_hybrid_flow(flow_chg_pct, oi_chg_pct, rvol, side, ob_ratio)

            _sig = bool(last[sig_col]) or bool(prev[sig_col])
            _t = TEL["intra"]; _t["scanned"] += 1; _t["signal"] += int(_sig); _t["blocked"] += int(_sig and is_blocked); _t["no_boost"] += int(_sig and not is_blocked and not is_boosted)
            if _sig and (is_blocked or not is_boosted):
                _shadow_eval(TEL["intra"], sym, side, df15, INTRA_ATR_SL_MULT, INTRA_RR, INTRA_MAX_BARS, True, INTRA_MIN_SETUPS, INTRA_MIN_EXP_R, "blocked" if is_blocked else "no_boost")
            if not is_blocked and is_boosted and (bool(last[sig_col]) or bool(prev[sig_col])):
                # Upgrade candidate to exact Dhan 15m Chart bars if Dhan API is active
                dhan_15m = get_closed_bar_slice(dhan.fetch_intraday_15m_df(sym, days_back=85), is_intraday=True)
                if len(dhan_15m) >= 80:
                    df15 = compute_intraday_15m_indicators(dhan_15m, is_index=False, dhan_vwap=dhan_vwap)

                tot, exp_r, wp, se = backtest_cash_signals(df15, side, INTRA_ATR_SL_MULT, INTRA_RR, INTRA_MAX_BARS, True)
                _few = tot < INTRA_MIN_SETUPS; _ok = (not _few) and exp_r - BT_SE_K * se >= INTRA_MIN_EXP_R
                TEL["intra"]["bt_few"] += int(_few); TEL["intra"]["bt_fail"] += int((not _few) and not _ok); TEL["intra"]["passed"] += int(_ok)
                if (not _few) and not _ok:
                    TEL["intra"]["near"].append((exp_r - BT_SE_K * se - INTRA_MIN_EXP_R, sym, side, exp_r, se, tot))
                if _ok:
                    rs_alpha = (price_chg_pct - nifty_intra_chg) if side == "BUY" else (nifty_intra_chg - price_chg_pct)
                    rs_bonus = float(np.clip(rs_alpha * 1.5, -3.0, 5.0))
                    row = build_stock_trade_row(sym, df15, side, True, flow_tag, is_boosted, exp_r, wp, "15M MIS", rs_bonus)
                    if row:
                        intra_stk_cands.append((row["FlowBoost"], row["Score"], row))

        # --- B. 1D POSITIONAL SWING SCAN (Patched with Live Dhan OHLCV) ---
        df1d_raw = extract_ticker_df(raw_1d, tk)
        df1d = patch_daily_df_with_dhan_quote(get_closed_bar_slice(df1d_raw, is_intraday=False), dhan_q)
        if run_swing and len(df1d) >= 200:
            df1d = compute_daily_swing_indicators(df1d)
            last = df1d.iloc[-1]
            prev = df1d.iloc[-2]

            daily_chg = ((float(last["Close"]) - float(prev["Close"])) / float(prev["Close"])) * 100.0
            price_chg_pct = intraday_chg_map.get(sym, daily_chg)
            oi_chg_pct = oi_spurts_map.get(sym, 0.0)
            rvol = float(last.get("RVOL", 1.0))

            stk_5d_ret = ((float(last["Close"]) - float(df1d["Close"].iloc[-6])) / float(df1d["Close"].iloc[-6])) * 100.0
            rs_5d_diff = stk_5d_ret - nifty_5d_ret

            if float(last["Close"]) >= float(last["EMA21"]):
                side = "BUY"
                is_blocked, is_boosted, flow_tag = classify_hybrid_flow(daily_chg, oi_chg_pct, rvol, side, ob_ratio)
                # Relative Strength Guard: Avoid buying laggards underperforming NIFTY by >2.5% over 5 days
                _sig = bool(last["BUY_Signal"]) or bool(prev["BUY_Signal"])
                _t = TEL["sw_buy"]; _t["scanned"] += 1; _t["signal"] += int(_sig); _t["blocked"] += int(_sig and is_blocked); _t["no_boost"] += int(_sig and not is_blocked and not is_boosted); _t["laggard"] += int(_sig and not is_blocked and is_boosted and rs_5d_diff < -2.5)
                if _sig and (is_blocked or not is_boosted):
                    _shadow_eval(TEL["sw_buy"], sym, side, df1d, SWING_ATR_SL_MULT, SWING_RR, SWING_MAX_DAYS, False, SWING_MIN_SETUPS, SWING_MIN_EXP_R, "blocked" if is_blocked else "no_boost")
                if not is_blocked and is_boosted and rs_5d_diff >= -2.5 and (bool(last["BUY_Signal"]) or bool(prev["BUY_Signal"])):
                    tot, exp_r, wp, se = backtest_cash_signals(df1d, side, SWING_ATR_SL_MULT, SWING_RR, SWING_MAX_DAYS, False)
                    _few = tot < SWING_MIN_SETUPS; _ok = (not _few) and exp_r - BT_SE_K * se >= SWING_MIN_EXP_R
                    TEL["sw_buy"]["bt_few"] += int(_few); TEL["sw_buy"]["bt_fail"] += int((not _few) and not _ok); TEL["sw_buy"]["passed"] += int(_ok)
                    if (not _few) and not _ok:
                        TEL["sw_buy"]["near"].append((exp_r - BT_SE_K * se - SWING_MIN_EXP_R, sym, side, exp_r, se, tot))
                    if _ok:
                        rs_bonus = float(np.clip(rs_5d_diff * 0.8, -2.0, 5.0))
                        row = build_stock_trade_row(sym, df1d, side, False, flow_tag, is_boosted, exp_r, wp, "3-5D CNC", rs_bonus)
                        if row:
                            swing_buy_cands.append((row["FlowBoost"], row["Score"], row))
            else:
                side = "SHORT"
                is_blocked, is_boosted, flow_tag = classify_hybrid_flow(daily_chg, oi_chg_pct, rvol, side, ob_ratio)
                _sig = bool(last["SHORT_Signal"]) or bool(prev["SHORT_Signal"])
                _t = TEL["sw_short"]; _t["scanned"] += 1; _t["signal"] += int(_sig); _t["blocked"] += int(_sig and is_blocked); _t["no_boost"] += int(_sig and not is_blocked and not is_boosted); _t["laggard"] += int(_sig and not is_blocked and is_boosted and rs_5d_diff > 2.5)
                if _sig and (is_blocked or not is_boosted):
                    _shadow_eval(TEL["sw_short"], sym, side, df1d, SWING_ATR_SL_MULT, SWING_RR, SWING_MAX_DAYS, False, SWING_MIN_SETUPS, SWING_MIN_EXP_R, "blocked" if is_blocked else "no_boost")
                if not is_blocked and is_boosted and rs_5d_diff <= 2.5 and (bool(last["SHORT_Signal"]) or bool(prev["SHORT_Signal"])):
                    tot, exp_r, wp, se = backtest_cash_signals(df1d, side, SWING_ATR_SL_MULT, SWING_RR, SWING_MAX_DAYS, False)
                    _few = tot < SWING_MIN_SETUPS; _ok = (not _few) and exp_r - BT_SE_K * se >= SWING_MIN_EXP_R
                    TEL["sw_short"]["bt_few"] += int(_few); TEL["sw_short"]["bt_fail"] += int((not _few) and not _ok); TEL["sw_short"]["passed"] += int(_ok)
                    if (not _few) and not _ok:
                        TEL["sw_short"]["near"].append((exp_r - BT_SE_K * se - SWING_MIN_EXP_R, sym, side, exp_r, se, tot))
                    if _ok:
                        rs_bonus = float(np.clip(-rs_5d_diff * 0.8, -2.0, 5.0))
                        row = build_stock_trade_row(sym, df1d, side, False, flow_tag, is_boosted, exp_r, wp, "1D Watch", rs_bonus)
                        if row:
                            swing_short_cands.append((row["FlowBoost"], row["Score"], row))

    del raw_15m, raw_1d
    gc.collect()

    intra_stk_cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    swing_short_cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    swing_buy_cands.sort(key=lambda x: (x[0], x[1]), reverse=True)

    intra_rows    = [x[2] for x in intra_stk_cands[:TOP_N_PER_SECTION]]
    short_1d_rows = [x[2] for x in swing_short_cands[:TOP_N_PER_SECTION]]
    buy_1d_rows   = [x[2] for x in swing_buy_cands[:TOP_N_PER_SECTION]]

    msg = build_single_master_message(mood_summary, intra_rows, short_1d_rows, buy_1d_rows, now_str, bool(dhan_eq_quotes), market_open=market_open_now)
    tel_lines = _telemetry_text(TEL, skipped_idx, len(idx_badges), len(INDEX_UNIVERSE), {"intra": run_intraday, "sw_buy": run_swing, "sw_short": run_swing})
    print("SCAN STATS | " + " || ".join(tel_lines))
    tel_msg = "\n\n<b>📊 Scan stats</b>\n<code>" + html.escape("\n".join(tel_lines)) + "</code>"
    if len(msg) + len(tel_msg) < 3900:
        msg += tel_msg
        tel_msg = ""
    send_telegram_alert(msg)
    if tel_msg:
        send_telegram_alert(tel_msg)


if __name__ == "__main__":
    main()
