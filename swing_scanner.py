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

# Smart Credential Loader: Supports standard Dhan env vars used across options_scanner.py
DHAN_CLIENT_ID = (
    os.environ.get("DHAN_CLIENT_ID", "")
    or os.environ.get("DHAN_CLIENT", "")
    or os.environ.get("CLIENT_ID", "")
).strip().strip('"').strip("'")

DHAN_ACCESS_TOKEN = (
    os.environ.get("DHAN_ACCESS_TOKEN", "")
    or os.environ.get("DHAN_TOKEN", "")
    or os.environ.get("ACCESS_TOKEN", "")
).strip().strip('"').strip("'")

DHAN_CACHE_FILE = Path("/tmp/dhan_master_eq_fno_map_v3.json")

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

INTRA_RR               = 2.0
INTRA_T1_RR            = 1.0
INTRA_ATR_SL_MULT      = 1.1
INTRA_MAX_BARS         = 12
INTRA_MIN_SETUPS       = 8
INTRA_MIN_EXP_R        = 0.10     # Time-decayed Expectancy threshold (ExpR >= +0.10)

SWING_RR               = 2.5
SWING_T1_RR            = 1.25
SWING_ATR_SL_MULT      = 1.2
SWING_MAX_DAYS         = 8
SWING_MIN_SETUPS       = 8
SWING_MIN_EXP_R        = 0.10     # Time-decayed Expectancy threshold (ExpR >= +0.10)

INDEX_UNIVERSE = {
    "^NSEI":                "NIFTY",
    "^NSEBANK":             "BANKNIFTY",
    "NIFTY_FIN_SERVICE.NS": "FINNIFTY",
    "NIFTY_MID_SELECT.NS":  "MIDCPNIFTY",
}

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
# ⚡ 3. NATIVE DHAN HQ v2 ENGINE (NSE_EQ + NSE_FNO FUTURES OI & BASIS + 15M)
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
        self.eq_map = {}       # sym -> securityId (NSE_EQ)
        self.fut_map = {}      # sym -> securityId (Near-month NSE_FNO FUTSTK/FUTIDX)
        self.inv_eq_map = {}
        self.inv_fut_map = {}
        if self.enabled:
            self._load_or_build_scrip_map()

    def _load_or_build_scrip_map(self):
        ist = pytz.timezone("Asia/Kolkata")
        today_str = datetime.now(ist).strftime("%Y-%m-%d")
        try:
            if DHAN_CACHE_FILE.exists():
                with open(DHAN_CACHE_FILE, "r") as f:
                    cached = json.load(f)
                if cached.get("date") == today_str and cached.get("eq_map") and cached.get("fut_map"):
                    self.eq_map = {k: int(v) for k, v in cached["eq_map"].items()}
                    self.fut_map = {k: int(v) for k, v in cached["fut_map"].items()}
                    self.inv_eq_map = {int(v): k for k, v in self.eq_map.items()}
                    self.inv_fut_map = {int(v): k for k, v in self.fut_map.items()}
                    return

            url = "https://images.dhan.co/api-data/api-scrip-master.csv"
            df_scrip = pd.read_csv(
                url,
                usecols=[
                    "SEM_EXM_EXCH_ID",
                    "SEM_SEGMENT",
                    "SEM_SMST_SECURITY_ID",
                    "SEM_INSTRUMENT_NAME",
                    "SEM_TRADING_SYMBOL",
                    "SEM_CUSTOM_SYMBOL",
                    "SEM_EXPIRY_DATE",
                    "SEM_SERIES",
                ],
                low_memory=False,
            )

            # 1. Map Cash Equities (NSE_EQ)
            nse_eq = df_scrip[
                (df_scrip["SEM_EXM_EXCH_ID"] == "NSE")
                & (df_scrip["SEM_SEGMENT"] == "E")
                & (df_scrip["SEM_SERIES"] == "EQ")
            ]
            eq_map = {}
            for _, row in nse_eq.iterrows():
                raw_sym = str(row["SEM_TRADING_SYMBOL"]).strip().upper()
                clean_sym = raw_sym[:-3] if raw_sym.endswith("-EQ") else raw_sym
                eq_map[clean_sym] = int(row["SEM_SMST_SECURITY_ID"])

            # 2. Map Near-Month Stock & Index Futures (NSE_FNO -> FUTSTK & FUTIDX)
            nse_fut = df_scrip[
                (df_scrip["SEM_EXM_EXCH_ID"] == "NSE")
                & (df_scrip["SEM_SEGMENT"] == "D")
                & (df_scrip["SEM_INSTRUMENT_NAME"].isin(["FUTSTK", "FUTIDX"]))
            ].copy()

            nse_fut["EXP_DT"] = pd.to_datetime(nse_fut["SEM_EXPIRY_DATE"], errors="coerce")
            today_dt = pd.Timestamp(today_str)
            nse_fut = nse_fut[nse_fut["EXP_DT"] >= today_dt].sort_values("EXP_DT")

            fut_map = {}
            for _, row in nse_fut.iterrows():
                trd_sym = str(row["SEM_TRADING_SYMBOL"]).strip().upper()
                # Dhan Futures trading symbol format: e.g. RELIANCE-Oct2026-FUT or NIFTY-Oct2026-FUT
                base_sym = trd_sym.split("-")[0].strip()
                if base_sym and base_sym not in fut_map:
                    fut_map[base_sym] = int(row["SEM_SMST_SECURITY_ID"])

            if eq_map:
                self.eq_map = eq_map
                self.fut_map = fut_map
                self.inv_eq_map = {int(v): k for k, v in eq_map.items()}
                self.inv_fut_map = {int(v): k for k, v in fut_map.items()}
                with open(DHAN_CACHE_FILE, "w") as f:
                    json.dump({"date": today_str, "eq_map": eq_map, "fut_map": fut_map}, f)
                print(f"✅ Dhan Master Mapped: {len(eq_map)} Cash Equities & {len(fut_map)} Near-Month Futures.")
        except Exception as e:
            print(f"⚠️ Dhan Scrip Master load warning: {e}")

    def fetch_unified_market_snapshot(self, symbols: list) -> tuple[dict, dict, dict]:
        """
        Single-request batch call to Dhan /v2/marketfeed/quote fetching:
        - NSE_EQ: Spot LTP, OHLC, Volume, Official VWAP (average_price), Bid/Ask Orderbook Ratio
        -IDX_I: Index Spot LTP & OHLC
        - NSE_FNO: Near-Month Futures Live OI, Previous Close OI -> Exact OI Change % & Futures Basis %
        """
        eq_quotes = {}
        idx_quotes = {}
        fno_oi_map = {}
        if not self.enabled or not self.eq_map:
            return eq_quotes, idx_quotes, fno_oi_map

        eq_sec_ids = [self.eq_map[s] for s in symbols if s in self.eq_map]
        idx_sec_ids = list(DHAN_INDEX_SEC_IDS.values())
        all_underlyings = list(symbols) + list(DHAN_INDEX_SEC_IDS.keys())
        fut_sec_ids = [self.fut_map[s] for s in all_underlyings if s in self.fut_map]

        payload = {
            "NSE_EQ": eq_sec_ids,
            "IDX_I": idx_sec_ids,
            "NSE_FNO": fut_sec_ids,
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

                # 1. Parse NSE_EQ Cash Quotes
                for sec_str, q in data.get("NSE_EQ", {}).items():
                    sym = self.inv_eq_map.get(int(sec_str))
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
                        "basis_pct": 0.0,
                    }

                # 2. Parse IDX_I Spot Index Quotes
                inv_idx = {v: k for k, v in DHAN_INDEX_SEC_IDS.items()}
                for sec_str, q in data.get("IDX_I", {}).items():
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

                # 3. Parse NSE_FNO Near-Month Futures (Exact OI Build-up % & Futures Basis %)
                for sec_str, q in data.get("NSE_FNO", {}).items():
                    sym = self.inv_fut_map.get(int(sec_str))
                    if not sym:
                        continue
                    fut_ltp = float(q.get("last_price", 0.0) or 0.0)
                    curr_oi = float(q.get("oi", 0.0) or 0.0)
                    prev_oi = float(q.get("previous_close_oi", 0.0) or q.get("oi_day_low", 0.0) or 0.0)
                    if prev_oi > 0 and curr_oi > 0:
                        oi_chg_pct = ((curr_oi - prev_oi) / prev_oi) * 100.0
                        fno_oi_map[sym] = round(oi_chg_pct, 2)

                    if sym in eq_quotes and eq_quotes[sym]["ltp"] > 0 and fut_ltp > 0:
                        spot_p = eq_quotes[sym]["ltp"]
                        eq_quotes[sym]["basis_pct"] = round(((fut_ltp - spot_p) / spot_p) * 100.0, 2)

                print(
                    f"✅ Dhan Snapshot: {len(eq_quotes)} EQ | {len(idx_quotes)} Indices | "
                    f"{len(fno_oi_map)} Native F&O OI Feeds."
                )
            else:
                print(f"⚠️ Dhan Quote API status {resp.status_code}: {resp.text[:160]}")
        except Exception as e:
            print(f"⚠️ Dhan Unified Snapshot error: {e}")
        return eq_quotes, idx_quotes, fno_oi_map

    def fetch_intraday_15m_df(self, sym: str, days_back: int = 45) -> pd.DataFrame:
        """
        Pulls exact 15-minute institutional OHLCV bars from Dhan /v2/charts/intraday
        for shortlisted setups so Entry, ATR, VWAP, and RVOL have zero YFinance lag.
        """
        if not self.enabled or sym not in self.eq_map:
            return pd.DataFrame()
        sec_id = str(self.eq_map[sym])
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
# 🕸️ 4. FALLBACK NSE OI SCRAPER & INSTITUTIONAL FLOW + BASIS CLASSIFIER
# ==============================================================================
def fetch_nse_oi_spurts_fallback() -> dict:
    base_url = "https://www.nseindia.com"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://www.nseindia.com/market-data/oi-spurts",
        "X-Requested-With": "XMLHttpRequest",
    }
    oi_map = {}
    try:
        session = requests.Session()
        session.headers.update(headers)
        session.get(base_url, timeout=6)
        time.sleep(0.2)
        resp = session.get(f"{base_url}/api/live-analysis-oi-spurts-underlyings", timeout=8)
        if resp.status_code == 200:
            for item in resp.json().get("data", []):
                sym = str(item.get("symbol", "")).strip().upper()
                oi_pct = pd.to_numeric(item.get("avgInOI", 0.0), errors="coerce")
                if sym and not np.isnan(oi_pct):
                    oi_map[sym] = float(oi_pct)
    except Exception:
        pass
    return oi_map


def classify_hybrid_flow(
    price_chg_pct: float,
    oi_chg_pct: float,
    rvol: float,
    side: str,
    ob_ratio: float = 1.0,
    basis_pct: float = 0.0,
):
    # 1. Anti-Trap Futures Basis Shield (Blocks buying into heavy futures discount or shorting into strong premium)
    if side == "BUY" and basis_pct < -0.35:
        return True, False, "BS-DISC"
    if side == "SHORT" and basis_pct > 0.85:
        return True, False, "BS-PREM"

    # 2. Anti-Trap Order-Book Depth Shield
    if side == "BUY" and ob_ratio < 0.38:
        return True, False, "OB-ASK!"
    if side == "SHORT" and ob_ratio > 2.60:
        return True, False, "OB-BID!"

    # 3. Primary Derivatives OI Build-Up Classification (Dhan NSE_FNO Native OI)
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

    # 4. Cash Institutional RVOL Expansion
    if 1.35 <= rvol <= 9.0:
        if (side == "BUY" and price_chg_pct > 0) or (side == "SHORT" and price_chg_pct < 0):
            return False, True, f"VL{rvol:>4.1f}x"
        return False, False, f"VL{rvol:>4.1f}x"

    # 5. Live Dhan Order-Book Accumulation / Distribution Boost
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
        bar_close_time = last_ts + timedelta(minutes=15)
        if last_ts.date() == now_ist.date() and now_ist < bar_close_time:
            return df.iloc[:-1].copy()
    return df


def patch_daily_df_with_dhan_quote(df: pd.DataFrame, dhan_q: dict) -> pd.DataFrame:
    if df.empty or not dhan_q or dhan_q.get("ltp", 0) <= 0:
        return df
    df = df.copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)

    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    today_date = now_ist.date()

    mins_elapsed = max(15.0, min(375.0, (now_ist.hour * 60 + now_ist.minute) - (9 * 60 + 15)))
    vol_pace_mult = (375.0 / mins_elapsed) if (now_ist.weekday() < 5 and 555 <= (now_ist.hour * 60 + now_ist.minute) <= 930) else 1.0
    projected_vol = dhan_q["volume"] * min(vol_pace_mult, 3.5)

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

    bar_range = (h - l).replace(0, np.nan)
    close_pos = ((c - l) / bar_range).fillna(0.5)
    not_overextended = (h - l) <= (2.2 * df["ATR"])

    df["BUY_Signal"] = (
        (c >= df["EMA21"] * 0.995)
        & (df["EMA21"] >= df["EMA55"] * 0.99)
        & (c < df["BB_UP_38"])
        & (df["RSI"] >= 46)
        & (df["RSI"] <= 68)
        & (df["ADX"] >= 16)
        & not_overextended
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
        & not_overextended
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

    tod_groups = df["Volume"].groupby(df.index.time)
    tod_mean = tod_groups.transform(lambda x: x.rolling(10, min_periods=3).mean()).replace(0, np.nan)
    fallback_mean = df["Volume"].rolling(20).mean().replace(0, np.nan)
    df["RVOL"] = (df["Volume"] / tod_mean.fillna(fallback_mean)).fillna(1.0)

    bar_range = (h - l).replace(0, np.nan)
    close_pos = ((c - l) / bar_range).fillna(0.5)
    not_overextended = ((h - l) <= (2.2 * df["ATR"])) & ((c - df["TREND_ANCHOR"]).abs() <= (1.8 * df["ATR"]))

    df["BUY_Signal"] = (
        (c >= df["TREND_ANCHOR"])
        & (df["EMA9"] >= df["EMA21"] * 0.998)
        & (c < df["BB_UP_38"])
        & (df["RSI"] >= 48)
        & (df["RSI"] <= 72)
        & (df["ADX"] >= 16)
        & not_overextended
        & ((c > o) | (close_pos >= 0.55))
    )
    df["SHORT_Signal"] = (
        (c <= df["TREND_ANCHOR"])
        & (df["EMA9"] <= df["EMA21"] * 1.002)
        & (c > df["BB_LO_38"])
        & (df["RSI"] >= 28)
        & (df["RSI"] <= 52)
        & (df["ADX"] >= 16)
        & not_overextended
        & ((c < o) | (close_pos <= 0.45))
    )
    return df


def backtest_cash_signals(
    df: pd.DataFrame, side: str = "BUY", atr_mult: float = 1.2, rr_target: float = 2.5, max_bars: int = 8, is_intraday: bool = False
):
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
                    if abs(opens[j] - curr_sl) <= abs(tgt - opens[j]):
                        trade_r = (0.5 * t1_r_lock) if t1_reached else -1.0
                    else:
                        trade_r = 0.5 * t1_r_lock + 0.5 * full_r_lock
                    outcome_resolved = True
                    break
                elif hit_sl:
                    trade_r = (0.5 * t1_r_lock) if t1_reached else -1.0
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
                        trade_r = (0.5 * t1_r_lock) if t1_reached else -1.0
                    else:
                        trade_r = 0.5 * t1_r_lock + 0.5 * full_r_lock
                    outcome_resolved = True
                    break
                elif hit_sl:
                    trade_r = (0.5 * t1_r_lock) if t1_reached else -1.0
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
        return 0, -1.0, 0.0

    arr_r = np.array(trade_results, dtype=float)
    weights = np.exp(np.linspace(-1.0, 0.0, total_trades))
    weights /= weights.sum()

    expectancy = float(np.sum(arr_r * weights))
    win_pct = float(np.sum((arr_r > 0) * weights) * 100.0)
    return total_trades, expectancy, win_pct


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
    live_ltp: float = 0.0,
):
    latest = df.iloc[-1]
    raw_close = float(live_ltp) if live_ltp > 0 else float(latest["Close"])
    atr = float(latest["ATR"])
    if raw_close <= 10.0 or np.isnan(atr) or atr <= 0:
        return None

    # Anti-Chasing Pullback Entry: If trigger candle expanded >1.2x ATR, set limit entry on minor 0.12*ATR dip
    bar_span = float(latest["High"]) - float(latest["Low"])
    if bar_span > 1.2 * atr:
        entry = round(raw_close - (0.12 * atr) if side == "BUY" else raw_close + (0.12 * atr), 1)
    else:
        entry = round(raw_close, 1)

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


def build_single_master_message(mood_summary, intra_rows, short_1d_rows, buy_1d_rows, now_str, dhan_active: bool, fno_cnt: int):
    feed_badge = f"Dhan EQ+F&O ({fno_cnt} OI) ⚡" if dhan_active else "YF + NSE Feed"
    lines = [
        f"<b>🎯 MASTER STOCK SCANNER ({html.escape(feed_badge)})</b>",
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

    if top_three:
        lines.append("\n<b>🏆 TOP 3 DIRECT CASH TRADES TO TAKE RIGHT NOW:</b>")
        for idx, p in enumerate(top_three, 1):
            lines.append(
                f"{idx}. <b>{html.escape(p['Symbol'])} ({p['Side']} - {p['Horizon']})</b> "
                f"👉 Entry: <code>₹{p['Entry']:.1f}</code> | Qty: <code>{p['Qty']}</code> "
                f"| Cap: <code>₹{p['Capital']:,}</code> | Risk: <code>₹{p['RiskRs']:,}</code> "
                f"| SL: <code>{p['Stop']:.1f}</code> | T1: <code>{p['T1']:.1f}</code> | Tgt: <code>{p['Target']:.1f}</code> "
                f"| Flow: <code>{html.escape(p['Flow'].strip())}</code> | <b>Score: {p['Score']:.1f}</b>"
            )

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
def main():
    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    now_str = now_ist.strftime("%d-%b %I:%M %p IST")
    current_minutes = now_ist.hour * 60 + now_ist.minute

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
    dhan_eq_quotes, dhan_idx_quotes, oi_spurts_map = dhan.fetch_unified_market_snapshot(clean_syms)

    # Silent fallback to NSE scraper ONLY if Dhan F&O OI map came back empty
    if not oi_spurts_map:
        oi_spurts_map = fetch_nse_oi_spurts_fallback()

    all_tickers = list(INDEX_UNIVERSE.keys()) + STOCK_UNIVERSE
    raw_15m = yf.download(all_tickers, period="60d", interval="15m", group_by="ticker", progress=False)
    raw_1d  = yf.download(all_tickers, period="5y", interval="1d", group_by="ticker", progress=False)

    intraday_chg_map = {}
    idx_badges = []
    bear_count = 0
    nifty_5d_ret = 0.0

    # 1. TRUE MACRO REGIME CHECK (Fuses Dhan Live Index Quote + Native Futures OI + Daily EMA21)
    for tk, idx_name in INDEX_UNIVERSE.items():
        df15 = get_closed_bar_slice(extract_ticker_df(raw_15m, tk), is_intraday=True)
        df1d = patch_daily_df_with_dhan_quote(
            get_closed_bar_slice(extract_ticker_df(raw_1d, tk), is_intraday=False),
            dhan_idx_quotes.get(idx_name, {}),
        )
        if len(df1d) < 60:
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

        is_bearish = (price_chg_pct < 0 and oi_chg_pct >= 1.5) or (spot < float(last1d["EMA21"]))
        if is_bearish:
            bear_count += 1
            flow_lbl = f"SB {oi_chg_pct:+.0f}%" if (price_chg_pct < 0 and oi_chg_pct >= 1.0) else f"{price_chg_pct:+.1f}%"
            idx_badges.append(f"<code>{idx_name}: {spot:,.0f} (BEAR 🔴 | {flow_lbl})</code>")
        else:
            flow_lbl = f"LB {oi_chg_pct:+.0f}%" if (price_chg_pct >= 0 and oi_chg_pct >= 1.0) else f"{price_chg_pct:+.1f}%"
            idx_badges.append(f"<code>{idx_name}: {spot:,.0f} (BULL 🟢 | {flow_lbl})</code>")

    overall_mood = "BEARISH 🔴 (Focus on SHORTS / PUTS)" if bear_count >= 2 else "BULLISH 🟢 (Focus on BUYS / CALLS)"
    mood_lines = [f"<b>🧭 MARKET MOOD: {overall_mood}</b>"]
    if len(idx_badges) >= 2:
        mood_lines.append("• " + " | ".join(idx_badges[:2]))
    if len(idx_badges) >= 3:
        mood_lines.append("• " + " | ".join(idx_badges[2:]))
    mood_summary = "\n".join(mood_lines)

    # 2. SCAN STOCKS ON 15M (INTRADAY) & 1D (POSITIONAL SHORT & BUY)
    intra_stk_cands = []
    swing_short_cands = []
    swing_buy_cands = []
    nifty_intra_chg = intraday_chg_map.get("NIFTY", 0.0)

    for tk in STOCK_UNIVERSE:
        sym = tk.replace(".NS", "")
        dhan_q = dhan_eq_quotes.get(sym, {})
        ob_ratio = dhan_q.get("ob_ratio", 1.0)
        basis_pct = dhan_q.get("basis_pct", 0.0)
        dhan_vwap = dhan_q.get("vwap", 0.0)
        live_ltp = dhan_q.get("ltp", 0.0)

        # --- A. 15M INTRADAY SCAN ---
        df15_raw = extract_ticker_df(raw_15m, tk)
        df15 = get_closed_bar_slice(df15_raw, is_intraday=True)
        if len(df15) >= 80:
            df15 = compute_intraday_15m_indicators(df15, is_index=False, dhan_vwap=dhan_vwap)
            last = df15.iloc[-1]
            prev = df15.iloc[-2]

            if live_ltp > 0 and dhan_q.get("open", 0) > 0:
                price_chg_pct = ((live_ltp - dhan_q["open"]) / dhan_q["open"]) * 100.0
            else:
                today_bars = df15[df15.index.date == last.name.date()]
                day_open = float(today_bars["Open"].iloc[0]) if not today_bars.empty else float(last["Open"])
                price_chg_pct = ((float(last["Close"]) - day_open) / day_open) * 100.0

            intraday_chg_map[sym] = price_chg_pct
            oi_chg_pct = oi_spurts_map.get(sym, 0.0)
            rvol = float(last.get("RVOL", 1.0))

            spot_15 = live_ltp if live_ltp > 0 else float(last["Close"])
            side = "BUY" if spot_15 >= float(last["TREND_ANCHOR"]) else "SHORT"
            sig_col = "BUY_Signal" if side == "BUY" else "SHORT_Signal"
            is_blocked, is_boosted, flow_tag = classify_hybrid_flow(
                price_chg_pct, oi_chg_pct, rvol, side, ob_ratio, basis_pct
            )

            if not is_blocked and is_boosted and (bool(last[sig_col]) or bool(prev[sig_col])):
                dhan_15m = get_closed_bar_slice(dhan.fetch_intraday_15m_df(sym, days_back=45), is_intraday=True)
                if len(dhan_15m) >= 80:
                    df15 = compute_intraday_15m_indicators(dhan_15m, is_index=False, dhan_vwap=dhan_vwap)

                tot, exp_r, wp = backtest_cash_signals(df15, side, INTRA_ATR_SL_MULT, INTRA_RR, INTRA_MAX_BARS, True)
                if tot >= INTRA_MIN_SETUPS and exp_r >= INTRA_MIN_EXP_R:
                    rs_alpha = (price_chg_pct - nifty_intra_chg) if side == "BUY" else (nifty_intra_chg - price_chg_pct)
                    rs_bonus = float(np.clip(rs_alpha * 1.5, -3.0, 5.0))
                    row = build_stock_trade_row(
                        sym, df15, side, True, flow_tag, is_boosted, exp_r, wp, "15M MIS", rs_bonus, live_ltp
                    )
                    if row:
                        intra_stk_cands.append((row["FlowBoost"], row["Score"], row))

        # --- B. 1D POSITIONAL SWING SCAN (Patched with Live Dhan OHLCV + Futures Basis) ---
        df1d_raw = extract_ticker_df(raw_1d, tk)
        df1d = patch_daily_df_with_dhan_quote(get_closed_bar_slice(df1d_raw, is_intraday=False), dhan_q)
        if len(df1d) >= 200:
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
                is_blocked, is_boosted, flow_tag = classify_hybrid_flow(
                    price_chg_pct, oi_chg_pct, rvol, side, ob_ratio, basis_pct
                )
                if not is_blocked and is_boosted and rs_5d_diff >= -2.5 and (bool(last["BUY_Signal"]) or bool(prev["BUY_Signal"])):
                    tot, exp_r, wp = backtest_cash_signals(df1d, side, SWING_ATR_SL_MULT, SWING_RR, SWING_MAX_DAYS, False)
                    if tot >= SWING_MIN_SETUPS and exp_r >= SWING_MIN_EXP_R:
                        rs_bonus = float(np.clip(rs_5d_diff * 0.8, -2.0, 5.0))
                        row = build_stock_trade_row(
                            sym, df1d, side, False, flow_tag, is_boosted, exp_r, wp, "3-5D CNC", rs_bonus, live_ltp
                        )
                        if row:
                            swing_buy_cands.append((row["FlowBoost"], row["Score"], row))
            else:
                side = "SHORT"
                is_blocked, is_boosted, flow_tag = classify_hybrid_flow(
                    price_chg_pct, oi_chg_pct, rvol, side, ob_ratio, basis_pct
                )
                if not is_blocked and is_boosted and rs_5d_diff <= 2.5 and (bool(last["SHORT_Signal"]) or bool(prev["SHORT_Signal"])):
                    tot, exp_r, wp = backtest_cash_signals(df1d, side, SWING_ATR_SL_MULT, SWING_RR, SWING_MAX_DAYS, False)
                    if tot >= SWING_MIN_SETUPS and exp_r >= SWING_MIN_EXP_R:
                        rs_bonus = float(np.clip(-rs_5d_diff * 0.8, -2.0, 5.0))
                        row = build_stock_trade_row(
                            sym, df1d, side, False, flow_tag, is_boosted, exp_r, wp, "1D Watch", rs_bonus, live_ltp
                        )
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

    msg = build_single_master_message(
        mood_summary, intra_rows, short_1d_rows, buy_1d_rows, now_str, bool(dhan_eq_quotes), len(oi_spurts_map)
    )
    send_telegram_alert(msg)


if __name__ == "__main__":
    main()
