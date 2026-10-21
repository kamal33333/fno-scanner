import os
import re
import time
import html
import warnings
from datetime import datetime, date

warnings.filterwarnings("ignore")

import pytz
import requests
import yfinance as yf
import pandas as pd
import numpy as np

# ==============================================================================
# 🔐 1. CREDENTIALS & ENVIRONMENT VARIABLES (AUTO-SANITIZED)
# ==============================================================================
RAW_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
RAW_CHATID = os.environ.get("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "manual")

token_match = re.search(r"(\d+:[A-Za-z0-9_-]+)", RAW_TOKEN)
TELEGRAM_BOT_TOKEN = token_match.group(1) if token_match else RAW_TOKEN.lstrip("bot").strip('"').strip("'")
TELEGRAM_CHAT_ID   = RAW_CHATID

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
TOP_N_PER_SECTION      = 5        # Keep tables ultra-clean: Top 5 setups per section!
COST_SLIPPAGE_R        = 0.05     # 0.05R deduction per backtest trade

INTRA_RR               = 2.0
INTRA_T1_RR            = 1.0
INTRA_ATR_SL_MULT      = 1.0
INTRA_MAX_BARS         = 12
INTRA_MIN_SETUPS       = 10
INTRA_MIN_EXP_R        = 0.08     # Only show high-edge Intraday setups (ExpR >= +0.08)

SWING_RR               = 2.5
SWING_T1_RR            = 1.25
SWING_ATR_SL_MULT      = 1.2
SWING_MAX_DAYS         = 8
SWING_MIN_SETUPS       = 10
SWING_MIN_EXP_R        = 0.08     # Only show high-edge Positional setups (ExpR >= +0.08)

INDEX_UNIVERSE = {
    "^NSEI":                "NIFTY",
    "^NSEBANK":             "BANKNIFTY",
    "NIFTY_FIN_SERVICE.NS": "FINNIFTY",
    "NIFTY_MID_SELECT.NS":  "MIDCPNIFTY",
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
    "REDINGTON.NS", "CASTROLIND.NS", "CESC.NS", "KARURVYSYA.NS", "CITYUNIONBK.NS",
    "IDFCFIRSTB.NS", "MANAPPURAM.NS", "MUTHOOTFIN.NS", "CHOLAFIN.NS", "LICHSGFIN.NS",
    "CANBK.NS", "PNB.NS", "BANKBARODA.NS", "UNIONBANK.NS", "INDIANB.NS",
    "BEML.NS", "MAZDOCK.NS", "COCHINSHIP.NS", "BDL.NS", "DATA_PATTERNS.NS",
    "KPITTECH.NS", "TATAELXSI.NS", "CYIENT.NS", "ZENSARTECH.NS", "BSOFT.NS",
    "SONACOMS.NS", "ENDURANCE.NS", "EXIDEIND.NS", "AMARAJABAT.NS", "APOLLOTYRE.NS",
    "CEATLTD.NS", "JKTYRE.NS", "BALKRISIND.NS", "ASHOKLEY.NS", "ESCORTS.NS",
    "KEI.NS", "HAVELLS.NS", "BLUESTARCO.NS", "WHIRLPOOL.NS", "CUMMINSIND.NS",
    "THERMAX.NS", "BHEL.NS", "CGPOWER.NS", "SIEMENS.NS", "APARINDS.NS",
    "NATIONALUM.NS", "HINDALCO.NS", "VEDL.NS", "NMDC.NS", "JINDALSTEL.NS",
    "SAIL.NS", "JINDALSAW.NS", "WELCORP.NS", "APLAPOLLO.NS", "RATNAMANI.NS",
    "GNFC.NS", "GSFC.NS", "RCF.NS", "FACT.NS", "PARADEEP.NS",
    "RADICO.NS", "UBL.NS", "UNITDSPR.NS", "ITC.NS", "TATACONSUM.NS",
    "VARUNBEV.NS", "JUBLFOOD.NS", "DEVYANI.NS", "CHALET.NS", "EIHOTEL.NS",
    "LEMON_TREE.NS", "IRCTC.NS", "RVNL.NS", "IRCON.NS", "IRFC.NS",
    "REC.NS", "PFC.NS", "IREDA.NS", "SJVN.NS", "NHPC.NS",
    "TATAPOWER.NS", "JSWENERGY.NS", "TORNTPOWER.NS", "IGL.NS", "MGL.NS",
    "PETRONET.NS", "GAIL.NS", "ONGC.NS", "OIL.NS", "IOC.NS",
    "BPCL.NS", "HPCL.NS", "CHENNPETRO.NS", "MRPL.NS", "NATCOPHARM.NS",
    "LAURUSLABS.NS", "GRANULES.NS", "GLENMARK.NS", "BIOCON.NS", "LUPIN.NS",
    "ALKEM.NS", "TORNTPHARM.NS", "IPCALAB.NS", "AJANTPHARM.NS", "JBCHEPHARM.NS",
    "APOLLOHOSP.NS", "MAXHEALTH.NS", "FORTIS.NS", "NARAYANA.NS", "ASTERDM.NS",
    "METROPOLIS.NS", "LALPATHLAB.NS", "SYNGENE.NS", "DIVISLAB.NS", "CIPLA.NS",
    "DRREDDY.NS", "PRESTIGE.NS", "BRIGADE.NS", "SOBHA.NS", "GODREJPROP.NS",
    "PHOENIXLTD.NS", "MAHLIFE.NS", "NCC.NS", "KNRCON.NS", "PNCINFRA.NS",
]
STOCK_UNIVERSE = list(dict.fromkeys(STOCK_UNIVERSE))


# ==============================================================================
# 🕸️ 3. LIVE NSE OI SPURTS SCRAPER & HYBRID FLOW CLASSIFIER
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


def classify_hybrid_flow(price_chg_pct: float, oi_chg_pct: float, rvol: float, side: str):
    if abs(oi_chg_pct) >= 0.5:
        sign_str = f"{oi_chg_pct:+.0f}%"
        if price_chg_pct >= 0.0 and oi_chg_pct >= 2.5:
            if side == "SHORT":
                return True, False, f"LB{sign_str:>5}"
            return False, True, f"LB{sign_str:>5}"
        if price_chg_pct < 0.0 and oi_chg_pct >= 2.5:
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
        return False, False, f"OI{sign_str:>5}"

    if rvol >= 1.5:
        if (side == "BUY" and price_chg_pct > 0) or (side == "SHORT" and price_chg_pct < 0):
            return False, True, f"VL{rvol:>4.1f}x"
        return False, False, f"VL{rvol:>4.1f}x"

    return False, False, "   --  "


# ==============================================================================
# 📈 4. DATA SLICER, INDICATORS & BACKTEST ENGINE
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


def compute_daily_swing_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c = df["Close"]
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

    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - c.shift()).abs(), (df["Low"] - c.shift()).abs()],
        axis=1,
    ).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()

    vol_sma20 = df["Volume"].rolling(20).mean().replace(0, np.nan)
    df["RVOL"] = (df["Volume"] / vol_sma20).fillna(1.0)

    df["BUY_Signal"] = (
        (c >= df["EMA21"] * 0.995)
        & (df["EMA21"] >= df["EMA55"] * 0.99)
        & (c < df["BB_UP_38"])
        & (df["RSI"] >= 44)
        & (df["RSI"] <= 70)
    )
    df["SHORT_Signal"] = (
        (c <= df["EMA21"] * 1.005)
        & (c > df["BB_LO_38"])
        & (df["RSI"] >= 28)
        & (df["RSI"] <= 56)
    )
    return df


def compute_intraday_15m_indicators(df: pd.DataFrame, is_index: bool = False) -> pd.DataFrame:
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

    vol_sma20 = df["Volume"].rolling(20).mean().replace(0, np.nan)
    df["RVOL"] = (df["Volume"] / vol_sma20).fillna(1.0)

    df["BUY_Signal"] = (
        (c >= df["TREND_ANCHOR"])
        & (df["EMA9"] >= df["EMA21"] * 0.998)
        & (c < df["BB_UP_38"])
        & (df["RSI"] >= 46)
        & (df["RSI"] <= 74)
    )
    df["SHORT_Signal"] = (
        (c <= df["TREND_ANCHOR"])
        & (df["EMA9"] <= df["EMA21"] * 1.002)
        & (c > df["BB_LO_38"])
        & (df["RSI"] >= 26)
        & (df["RSI"] <= 54)
    )
    return df


def backtest_cash_signals(
    df: pd.DataFrame, side: str = "BUY", atr_mult: float = 1.2, rr_target: float = 2.5, max_bars: int = 8, is_intraday: bool = False
):
    sig_col = "BUY_Signal" if side == "BUY" else "SHORT_Signal"
    closes, highs, lows, atrs, sigs = (
        df["Close"].values,
        df["High"].values,
        df["Low"].values,
        df["ATR"].values,
        df[sig_col].values,
    )
    dates = df.index.date if is_intraday else None

    t1_r_lock = INTRA_T1_RR if is_intraday else SWING_T1_RR
    full_r_lock = rr_target

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
        if risk <= 0:
            i += 1
            continue

        sl = (entry - risk) if side == "BUY" else (entry + risk)
        t1 = (entry + risk * t1_r_lock) if side == "BUY" else (entry - risk * t1_r_lock)
        tgt = (entry + risk * full_r_lock) if side == "BUY" else (entry - risk * full_r_lock)

        total_trades += 1
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
                if lows[j] <= curr_sl:
                    trade_r = (0.5 * t1_r_lock) if t1_reached else -1.0
                    outcome_resolved = True
                    break
                if highs[j] >= tgt:
                    trade_r = 0.5 * t1_r_lock + 0.5 * full_r_lock
                    outcome_resolved = True
                    break
                if highs[j] >= t1:
                    t1_reached = True
            else:
                curr_sl = entry if t1_reached else sl
                if highs[j] >= curr_sl:
                    trade_r = (0.5 * t1_r_lock) if t1_reached else -1.0
                    outcome_resolved = True
                    break
                if lows[j] <= tgt:
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
        net_r_sum += net_r
        if net_r > 0:
            profitable_trades += 1
        i = max(i + 1, exit_bar)

    expectancy = (net_r_sum / total_trades) if total_trades > 0 else -1.0
    win_pct = (profitable_trades / total_trades * 100.0) if total_trades > 0 else 0.0
    return total_trades, expectancy, win_pct


def build_stock_trade_row(
    sym: str, df: pd.DataFrame, side: str, is_intraday: bool, flow_tag: str, is_boosted: bool, exp_r: float, win_pct: float, horizon_tag: str
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
    min_risk = entry * (0.008 if is_intraday else 0.015)
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
    qty_by_cap = int(MAX_CAPITAL_PER_TRADE // entry)
    qty = min(qty_by_risk, qty_by_cap)

    if qty < 1:
        return None

    capital = int(round(entry * qty))
    total_rupee_risk = int(round(actual_risk * qty))

    exp_rounded = round(exp_r, 2)
    win_rounded = round(win_pct, 1)
    score = round((exp_rounded * 100.0) + win_rounded, 1)

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
    }


# ==============================================================================
# 📱 5. UNIFIED SINGLE-MESSAGE FORMATTER (SIMPLE, CLEAN & ACTIONABLE)
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


def build_single_master_message(mood_summary, intra_rows, short_1d_rows, buy_1d_rows, now_str, oi_count: int):
    lines = [
        "<b>🎯 MASTER STOCK SCANNER (INTRADAY &amp; 3-5D SWING)</b>",
        f"🕒 <code>{html.escape(now_str)}</code> | Max Risk: <code>₹{RISK_PER_TRADE:,}</code> | Max Cap: <code>₹{MAX_CAPITAL_PER_TRADE:,}</code>\n",
        mood_summary,
    ]

    if intra_rows:
        lines.append(f"\n<b>⚡ 1. TOP {len(intra_rows)} INTRADAY 15M SCALPS (1:2.0 RR | MIS)</b>")
        lines.append(format_stock_table_block(intra_rows))

    if short_1d_rows:
        lines.append(f"\n<b>📉 2. TOP {len(short_1d_rows)} POSITIONAL SHORT BREAKDOWNS (1D | 1:2.5 RR)</b>")
        lines.append(format_stock_table_block(short_1d_rows))

    if buy_1d_rows:
        lines.append(f"\n<b>🚀 3. TOP {len(buy_1d_rows)} POSITIONAL CASH SWING BUYS (1D | GTT 1:2.5 RR)</b>")
        lines.append(format_stock_table_block(buy_1d_rows))

    # Master Top-3 Best Trades Across All Active Sections (Prioritizing FlowBoost == 1)
    all_cands = list(intra_rows) + list(short_1d_rows) + list(buy_1d_rows)
    boosted = [r for r in all_cands if r["FlowBoost"] == 1 and r["Expectancy"] >= 0.10]
    if not boosted:
        boosted = all_cands
    boosted.sort(key=lambda x: (x["FlowBoost"], x["Score"]), reverse=True)

    # Deduplicate by Symbol so the same stock doesn't take two spots in Top 3
    seen_syms = set()
    top_three = []
    for r in boosted:
        if r["Symbol"] not in seen_syms:
            seen_syms.add(r["Symbol"])
            top_three.append(r)
        if len(top_three) == 3:
            break

    if top_three:
        lines.append("\n<b>🏆 TOP 3 BEST TRADES TO TAKE RIGHT NOW:</b>")
        for idx, p in enumerate(top_three, 1):
            lines.append(
                f"{idx}. <b>{html.escape(p['Symbol'])} ({p['Side']} - {p['Horizon']})</b> "
                f"👉 Entry: <code>₹{p['Entry']:.1f}</code> | Qty: <code>{p['Qty']}</code> "
                f"| Cap: <code>₹{p['Capital']:,}</code> | Risk: <code>₹{p['RiskRs']:,}</code> "
                f"| SL: <code>{p['Stop']:.1f}</code> | T1: <code>{p['T1']:.1f}</code> | Tgt: <code>{p['Target']:.1f}</code> "
                f"| Flow: <code>{html.escape(p['Flow'].strip())}</code> | <b>Score: {p['Score']:.1f}</b>"
            )

    lines.append("\n💡 <i>SB=Short Build, LU=Long Unwind, LB=Long Build, VL=Volume Surge</i>")
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
# 🚀 6. MAIN EXECUTION ENGINE
# ==============================================================================
def main():
    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    now_str = now_ist.strftime("%d-%b %I:%M %p IST")
    current_minutes = now_ist.hour * 60 + now_ist.minute

    if EVENT_NAME == "schedule":
        if (
            now_ist.weekday() >= 5
            or now_ist.date() in NSE_HOLIDAYS_2026
            or current_minutes < (9 * 60 + 15)
            or current_minutes > (15 * 60 + 45)
        ):
            print(f"⏸️ Outside Market Hours or NSE Holiday ({now_str}). Exiting.")
            return

    oi_spurts_map = fetch_nse_oi_spurts_map()
    all_tickers = list(INDEX_UNIVERSE.keys()) + STOCK_UNIVERSE

    raw_15m = yf.download(all_tickers, period="60d", interval="15m", group_by="ticker", progress=False)
    raw_1d  = yf.download(all_tickers, period="5y", interval="1d", group_by="ticker", progress=False)

    intraday_chg_map = {}
    idx_badges = []
    bear_count = 0
    total_idx  = 0

    # 1. TRUE MACRO REGIME CHECK (Uses Daily EMA21 + Intraday Session Change + OI Flow!)
    for tk, idx_name in INDEX_UNIVERSE.items():
        df15 = get_closed_bar_slice(extract_ticker_df(raw_15m, tk), is_intraday=True)
        df1d = get_closed_bar_slice(extract_ticker_df(raw_1d, tk), is_intraday=False)
        if len(df15) < 40 or len(df1d) < 60:
            continue
        df1d = compute_daily_swing_indicators(df1d)
        last15 = df15.iloc[-1]
        last1d = df1d.iloc[-1]

        today_bars = df15[df15.index.date == last15.name.date()]
        day_open = float(today_bars["Open"].iloc[0]) if not today_bars.empty else float(last15["Open"])
        spot = float(last15["Close"])
        price_chg_pct = ((spot - day_open) / day_open) * 100.0
        intraday_chg_map[idx_name] = price_chg_pct
        oi_chg_pct = oi_spurts_map.get(idx_name, 0.0)

        # True Institutional Bias: If price < Day Open with OI build-up (SB) OR below Daily EMA21 -> BEAR!
        is_bearish = (price_chg_pct < 0 and oi_chg_pct >= 2.0) or (spot < float(last1d["EMA21"]))
        total_idx += 1
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
    if len(idx_badges) >= 4:
        mood_lines.append("• " + " | ".join(idx_badges[2:4]))
    mood_summary = "\n".join(mood_lines)

    # 2. SCAN STOCKS ON 15M (INTRADAY) & 1D (POSITIONAL SHORT & BUY)
    intra_stk_cands = []
    swing_short_cands = []
    swing_buy_cands = []

    for tk in STOCK_UNIVERSE:
        sym = tk.replace(".NS", "")
        df15_raw = extract_ticker_df(raw_15m, tk)
        df15 = get_closed_bar_slice(df15_raw, is_intraday=True)
        if len(df15) >= 80:
            df15 = compute_intraday_15m_indicators(df15, is_index=False)
            last = df15.iloc[-1]
            prev = df15.iloc[-2]

            today_bars = df15[df15.index.date == last.name.date()]
            day_open = float(today_bars["Open"].iloc[0]) if not today_bars.empty else float(last["Open"])
            price_chg_pct = ((float(last["Close"]) - day_open) / day_open) * 100.0
            intraday_chg_map[sym] = price_chg_pct
            oi_chg_pct = oi_spurts_map.get(sym, 0.0)
            rvol = float(last.get("RVOL", 1.0))

            side = "BUY" if float(last["Close"]) >= float(last["TREND_ANCHOR"]) else "SHORT"
            sig_col = "BUY_Signal" if side == "BUY" else "SHORT_Signal"
            is_blocked, is_boosted, flow_tag = classify_hybrid_flow(price_chg_pct, oi_chg_pct, rvol, side)

            # Only include Intraday setups backed by active OI/Volume Flow (is_boosted) or strong signal
            if not is_blocked and is_boosted and (bool(last[sig_col]) or bool(prev[sig_col]) or is_boosted):
                tot, exp_r, wp = backtest_cash_signals(df15, side, INTRA_ATR_SL_MULT, INTRA_RR, INTRA_MAX_BARS, True)
                if tot >= INTRA_MIN_SETUPS and exp_r >= INTRA_MIN_EXP_R:
                    row = build_stock_trade_row(sym, df15, side, True, flow_tag, is_boosted, exp_r, wp, "15M")
                    if row:
                        intra_stk_cands.append((row["FlowBoost"], row["Score"], row))

        df1d_raw = extract_ticker_df(raw_1d, tk)
        df1d = get_closed_bar_slice(df1d_raw, is_intraday=False)
        if len(df1d) >= 200:
            df1d = compute_daily_swing_indicators(df1d)
            last = df1d.iloc[-1]
            prev = df1d.iloc[-2]

            daily_chg = ((float(last["Close"]) - float(prev["Close"])) / float(prev["Close"])) * 100.0
            price_chg_pct = intraday_chg_map.get(sym, daily_chg)
            oi_chg_pct = oi_spurts_map.get(sym, 0.0)
            rvol = float(last.get("RVOL", 1.0))

            if float(last["Close"]) >= float(last["EMA21"]):
                side = "BUY"
                is_blocked, is_boosted, flow_tag = classify_hybrid_flow(price_chg_pct, oi_chg_pct, rvol, side)
                if not is_blocked and is_boosted and (bool(last["BUY_Signal"]) or bool(prev["BUY_Signal"]) or is_boosted):
                    tot, exp_r, wp = backtest_cash_signals(df1d, side, SWING_ATR_SL_MULT, SWING_RR, SWING_MAX_DAYS, False)
                    if tot >= SWING_MIN_SETUPS and exp_r >= SWING_MIN_EXP_R:
                        row = build_stock_trade_row(sym, df1d, side, False, flow_tag, is_boosted, exp_r, wp, "3-5D")
                        if row:
                            swing_buy_cands.append((row["FlowBoost"], row["Score"], row))
            else:
                side = "SHORT"
                is_blocked, is_boosted, flow_tag = classify_hybrid_flow(price_chg_pct, oi_chg_pct, rvol, side)
                if not is_blocked and is_boosted and (bool(last["SHORT_Signal"]) or bool(prev["SHORT_Signal"]) or is_boosted):
                    tot, exp_r, wp = backtest_cash_signals(df1d, side, SWING_ATR_SL_MULT, SWING_RR, SWING_MAX_DAYS, False)
                    if tot >= SWING_MIN_SETUPS and exp_r >= SWING_MIN_EXP_R:
                        row = build_stock_trade_row(sym, df1d, side, False, flow_tag, is_boosted, exp_r, wp, "3-5D")
                        if row:
                            swing_short_cands.append((row["FlowBoost"], row["Score"], row))

    intra_stk_cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    swing_short_cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    swing_buy_cands.sort(key=lambda x: (x[0], x[1]), reverse=True)

    intra_rows    = [x[2] for x in intra_stk_cands[:TOP_N_PER_SECTION]]
    short_1d_rows = [x[2] for x in swing_short_cands[:TOP_N_PER_SECTION]]
    buy_1d_rows   = [x[2] for x in swing_buy_cands[:TOP_N_PER_SECTION]]

    msg = build_single_master_message(mood_summary, intra_rows, short_1d_rows, buy_1d_rows, now_str, len(oi_spurts_map))
    send_telegram_alert(msg)


if __name__ == "__main__":
    main()
