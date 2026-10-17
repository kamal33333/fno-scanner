import os
import re
import io
import math
import requests
import warnings
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime
import pytz

warnings.filterwarnings("ignore")

# ==============================================================================
# 1. SIMPLE CONFIGURATION & RISK RULES
# ==============================================================================
IST = pytz.timezone("Asia/Kolkata")

MAX_RISK_PER_TRADE = 1000.0      # ₹1,000 fixed risk per trade
MAX_CAPITAL_LIMIT = 100000.0     # ₹1,00,000 max capital per trade
RR_STAGE_1 = 1.25                # T1 - Book 50% at +1.25R & Trail SL to Entry
RR_FINAL = 2.50                  # Target - Book remaining 50% at +2.5R
SWING_LOW_LOOKBACK = 7           # 7-candle lowest low Stop Loss
MIN_BACKTEST_TRADES = 5          # Minimum 5 trades in last 5 years
MIN_WIN_PROBABILITY = 38.0       # Only show high-probability stocks (>= 38%)
TOP_N_RESULTS = 15               # Show only the Top 15 best setups in 1 message
MAX_HOLD_DAYS = 20               # 20-day time stop

raw_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
token_match = re.search(r"(\d+:[A-Za-z0-9_-]+)", raw_token)
TELEGRAM_BOT_TOKEN = token_match.group(1) if token_match else ""
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")

# ==============================================================================
# 2. NIFTY 500 UNIVERSE FETCHER
# ==============================================================================
FALLBACK_NIFTY_200 = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "BHARTIARTL.NS",
    "INFY.NS", "SBIN.NS", "ITC.NS", "HINDUNILVR.NS", "LT.NS",
    "BAJFINANCE.NS", "HCLTECH.NS", "MARUTI.NS", "SUNPHARMA.NS", "ADANIENT.NS",
    "KOTAKBANK.NS", "TITAN.NS", "ONGC.NS", "TATAMOTORS.NS", "NTPC.NS",
    "AXISBANK.NS", "DMART.NS", "ADANIPORTS.NS", "ULTRACEMCO.NS", "ASIANPAINT.NS",
    "COALINDIA.NS", "BAJAJFINSV.NS", "BAJAJ-AUTO.NS", "POWERGRID.NS", "NESTLEIND.NS",
    "M&M.NS", "IOC.NS", "TATASTEEL.NS", "JSWSTEEL.NS", "GRASIM.NS",
    "LTIM.NS", "TECHM.NS", "WIPRO.NS", "SBILIFE.NS", "HDFCLIFE.NS",
    "DRREDDY.NS", "CIPLA.NS", "BRITANNIA.NS", "INDUSINDBK.NS", "EICHERMOT.NS",
    "DIVISLAB.NS", "HINDALCO.NS", "BPCL.NS", "TATACONSUM.NS", "HEROMOTOCO.NS",
    "APOLLOHOSP.NS", "ETERNAL.NS", "BEL.NS", "HAL.NS", "TRENT.NS",
    "SIEMENS.NS", "DLF.NS", "PIDILITIND.NS", "INDIGO.NS", "BANKBARODA.NS",
    "PNB.NS", "GAIL.NS", "CHOLAFIN.NS", "AMBUJACEM.NS", "ABB.NS",
    "TVSMOTOR.NS", "VEDL.NS", "HAVELLS.NS", "GODREJCP.NS", "SHRIRAMFIN.NS",
    "DABUR.NS", "ICICIPRULI.NS", "SHREECEM.NS", "MARICO.NS", "CANBK.NS",
    "REC.NS", "PFC.NS", "IRFC.NS", "BHEL.NS", "CGPOWER.NS",
    "POLYCAB.NS", "CUMMINSIND.NS", "PERSISTENT.NS", "COFORGE.NS", "DIXON.NS",
    "MCX.NS", "ANGELONE.NS", "BSE.NS", "CDSL.NS", "KALYANKJIL.NS",
    "LUPIN.NS", "AUROPHARMA.NS", "TORNTPHARM.NS", "ALKEM.NS", "MAXHEALTH.NS",
    "FORTIS.NS", "SYNGENE.NS", "LAURUSLABS.NS", "GRANULES.NS", "NATCOPHARM.NS",
    "ASHOKLEY.NS", "BHARATFORG.NS", "MOTHERSON.NS", "SONACOMS.NS", "EXIDEIND.NS",
    "APOLLOTYRE.NS", "MRF.NS", "BALKRISIND.NS", "CEATLTD.NS", "VOLTAS.NS",
    "BLUESTARCO.NS", "CROMPTON.NS", "VGUARD.NS", "PAGEIND.NS", "ABFRL.NS",
    "JUBLFOOD.NS", "DEVYANI.NS", "VBL.NS", "RADICO.NS", "UNITDSPR.NS",
    "INDIANB.NS", "UNIONBANK.NS", "IDFCFIRSTB.NS", "FEDERALBNK.NS", "AUBANK.NS",
    "KARURVYSYA.NS", "MUTHOOTFIN.NS", "MANAPPURAM.NS", "LICHSGFIN.NS", "M&MFIN.NS",
    "POONAWALLA.NS", "SUNDARMFIN.NS", "ABCAPITAL.NS", "LTF.NS", "HUDCO.NS",
    "IREDA.NS", "TATAPOWER.NS", "JSWENERGY.NS", "NHPC.NS", "SJVN.NS",
    "TORNTPOWER.NS", "IGL.NS", "MGL.NS", "PETRONET.NS", "DEEPAKNTR.NS",
    "SRF.NS", "PIIND.NS", "UPL.NS", "COROMANDEL.NS", "TATACHEM.NS",
    "OBEROIRLTY.NS", "GODREJPROP.NS", "PRESTIGE.NS", "BRIGADE.NS", "PHOENIXLTD.NS",
    "ASTRAL.NS", "SUPREMEIND.NS", "KEI.NS", "CONCOR.NS", "DELHIVERY.NS",
    "IRCTC.NS", "GMRINFRA.NS", "RVNL.NS", "IRCON.NS", "NBCC.NS",
    "KPITTECH.NS", "TATAELXSI.NS", "LTTS.NS", "CYIENT.NS", "MPHASIS.NS"
]

def get_nifty500_tickers():
    url = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            df = pd.read_csv(io.StringIO(resp.text))
            if "Symbol" in df.columns:
                symbols = [
                    f"{str(sym).strip()}.NS"
                    for sym in df["Symbol"].dropna().unique()
                    if "DUMMY" not in str(sym).upper()
                ]
                symbols = ["ETERNAL.NS" if s == "ZOMATO.NS" else s for s in symbols]
                if len(symbols) >= 100:
                    return list(dict.fromkeys(symbols))
    except Exception:
        pass
    return list(dict.fromkeys(FALLBACK_NIFTY_200))


# ==============================================================================
# 3. TECHNICAL INDICATORS (EMA + DHAN CHART BB55, RSI-SMA, CMF21)
# ==============================================================================
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"]

    # EMA 21 & 50
    df["EMA_21"] = close.ewm(span=21, adjust=False).mean()
    df["EMA_50"] = close.ewm(span=50, adjust=False).mean()

    # Wilder's RSI(14) & SMA(14) of RSI
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / 14.0, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / 14.0, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI_14"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)
    df["RSI_SMA_14"] = df["RSI_14"].rolling(window=14).mean()

    # Bollinger Bands (55 SMA, 1.618 StdDev)
    df["BB_SMA_55"] = close.rolling(window=55).mean()
    bb_std_55 = close.rolling(window=55).std(ddof=0)
    df["BB_UPPER_1618"] = df["BB_SMA_55"] + (1.618 * bb_std_55)

    # Chaikin Money Flow (21)
    hl_range = (high - low).replace(0, np.nan)
    mf_mult = (((close - low) - (high - close)) / hl_range).fillna(0.0)
    vol_sum_21 = vol.rolling(window=21).sum().replace(0, np.nan)
    df["CMF_21"] = ((mf_mult * vol).rolling(window=21).sum() / vol_sum_21).fillna(0.0)

    # Core Proven Strategy: EMA Pullback (Winner of the 5Y Backtest)
    df["SIG_CORE"] = (
        (df["EMA_21"] > df["EMA_50"])
        & (df["RSI_14"] > 40.0)
        & (df["RSI_14"] < 60.0)
        & (df["Close"] > df["Open"])
    )

    # Dhan Chart Confirmation Bonus (BB55 + RSI > SMA14 + CMF > 0)
    df["SIG_DHAN_BONUS"] = (
        (df["Close"] > df["BB_SMA_55"])
        & (df["Close"] < df["BB_UPPER_1618"])
        & (df["RSI_14"] > df["RSI_SMA_14"])
        & (df["CMF_21"] > 0.0)
    )

    return df


# ==============================================================================
# 4. 5-YEAR BACKTESTER (Evaluates Full +2.5R Target Win Probability)
# ==============================================================================
def run_5y_backtest(df: pd.DataFrame) -> dict:
    closes = df["Close"].values
    highs = df["High"].values
    lows = df["Low"].values
    signals = df["SIG_CORE"].values
    n = len(df)

    wins = 0
    total_trades = 0
    in_trade = False
    entry_price = 0.0
    stop_loss = 0.0
    target_price = 0.0
    entry_idx = 0

    for i in range(60, n - 1):
        if not in_trade:
            if signals[i]:
                swing_low = float(np.min(lows[i - SWING_LOW_LOOKBACK + 1 : i + 1]))
                curr_close = float(closes[i])
                risk = curr_close - swing_low
                if risk <= (curr_close * 0.003) or risk > (curr_close * 0.12):
                    continue
                in_trade = True
                entry_price = curr_close
                stop_loss = swing_low
                target_price = entry_price + (RR_FINAL * risk)
                entry_idx = i
        else:
            if lows[i] <= stop_loss:
                total_trades += 1
                in_trade = False
            elif highs[i] >= target_price:
                wins += 1
                total_trades += 1
                in_trade = False
            elif (i - entry_idx) >= MAX_HOLD_DAYS:
                if closes[i] >= (entry_price + (entry_price - stop_loss)):
                    wins += 1
                total_trades += 1
                in_trade = False

    win_prob = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
    return {"trades": total_trades, "win_prob": round(win_prob, 1)}


def extract_ticker_df(batch_data: pd.DataFrame, ticker: str) -> pd.DataFrame:
    try:
        if isinstance(batch_data.columns, pd.MultiIndex):
            if ticker in batch_data.columns.get_level_values(0):
                sub = batch_data[ticker].copy()
            elif ticker in batch_data.columns.get_level_values(1):
                sub = batch_data.xs(ticker, axis=1, level=1).copy()
            else:
                return pd.DataFrame()
        else:
            sub = batch_data.copy()
        required = ["Open", "High", "Low", "Close", "Volume"]
        if not all(col in sub.columns for col in required):
            return pd.DataFrame()
        return sub[required].dropna(subset=["Open", "High", "Low", "Close"])
    except Exception:
        return pd.DataFrame()


# ==============================================================================
# 5. MAIN SCANNER ENGINE
# ==============================================================================
def scan_market():
    now_ist = datetime.now(IST).strftime("%d-%b-%Y %I:%M %p IST")
    tickers = get_nifty500_tickers()
    chunk_size = 100
    signals = []

    for idx in range(0, len(tickers), chunk_size):
        chunk = tickers[idx : idx + chunk_size]
        try:
            batch_data = yf.download(
                tickers=chunk,
                period="5y",
                interval="1d",
                auto_adjust=True,
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception:
            continue

        for ticker in chunk:
            df = extract_ticker_df(batch_data, ticker)
            if df.empty or len(df) < 100:
                continue

            df = compute_indicators(df)
            last_row = df.iloc[-1]

            # Must trigger the proven Core Pullback setup today
            if not bool(last_row["SIG_CORE"]):
                continue

            bt = run_5y_backtest(df)
            if bt["trades"] < MIN_BACKTEST_TRADES or bt["win_prob"] < MIN_WIN_PROBABILITY:
                continue

            entry_price = float(last_row["Close"])
            swing_low = float(df["Low"].tail(SWING_LOW_LOOKBACK).min())
            risk = entry_price - swing_low
            if risk <= (entry_price * 0.003) or risk > (entry_price * 0.12):
                continue

            qty = min(
                math.floor(MAX_RISK_PER_TRADE / risk),
                math.floor(MAX_CAPITAL_LIMIT / entry_price),
            )
            if qty < 1:
                continue

            # Simple 2-tier Tag: ⭐GRADE-A (Both Agree) vs Pullback (Core)
            is_grade_a = bool(last_row["SIG_DHAN_BONUS"])
            setup_tag = "⭐GRADE-A" if is_grade_a else "Pullback"

            signals.append({
                "Stock": ticker.replace(".NS", ""),
                "Entry": round(entry_price, 1),
                "Qty": int(qty),
                "Capital": int(round(qty * entry_price, 0)),
                "SL": round(swing_low, 1),
                "T1": round(entry_price + (RR_STAGE_1 * risk), 1),
                "Target": round(entry_price + (RR_FINAL * risk), 1),
                "RR": "1:2.5",
                "Setup": setup_tag,
                "WinProb": bt["win_prob"],
                "IsGradeA": 1 if is_grade_a else 0,
            })

    # Sort: ⭐GRADE-A setups first, then by highest 5-Year Win%
    signals.sort(key=lambda x: (x["IsGradeA"], x["WinProb"]), reverse=True)
    return signals[:TOP_N_RESULTS], now_ist


# ==============================================================================
# 6. CLEAN, SINGLE-MESSAGE TELEGRAM OUTPUT
# ==============================================================================
def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )


def publish(signals: list, now_ist: str):
    if not signals:
        send_telegram(
            f"<b>🎯 TOP NIFTY 500 SWING SETUPS</b>\n"
            f"🕒 <code>{now_ist}</code>\n\n"
            f"No high-probability setups (&gt;= {MIN_WIN_PROBABILITY}%) found today."
        )
        return

    lines = [
        f"<b>🎯 TOP {len(signals)} NIFTY 500 SWING SETUPS</b>",
        f"🕒 <code>{now_ist}</code> | Max Risk: <code>₹1,000/trade</code>\n",
        "<pre>",
        f"{'Stock':<10}|{'Entry':>7}|{'Qty':>4}|{'Capital':>7}|{'SL':>7}|{'T1-50%':>7}|{'Target':>7}|{'R:R':>5}|{'Setups':<9}|{'Win%':>5}",
        "-" * 77,
    ]

    for s in signals:
        lines.append(
            f"{s['Stock'][:10]:<10}|"
            f"{s['Entry']:>7.1f}|"
            f"{s['Qty']:>4d}|"
            f"{s['Capital']:>7d}|"
            f"{s['SL']:>7.1f}|"
            f"{s['T1']:>7.1f}|"
            f"{s['Target']:>7.1f}|"
            f"{s['RR']:>5}|"
            f"{s['Setup']:<9}|"
            f"{s['WinProb']:>4.1f}%"
        )

    lines.append("</pre>")
    lines.append(
        "✅ <b>⭐GRADE-A</b> = EMA Pullback + BB(55) + RSI&gt;SMA + CMF&gt;0 all agree\n"
        "✅ <b>Pullback</b> = Proven 21/50 EMA Pullback (5Y Winner)"
    )

    send_telegram("\n".join(lines))


if __name__ == "__main__":
    top_signals, ts = scan_market()
    publish(top_signals, ts)
