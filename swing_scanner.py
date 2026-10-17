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
# 1. PORTFOLIO & RISK MANAGEMENT CONFIGURATION
# ==============================================================================
IST = pytz.timezone("Asia/Kolkata")

# Watchlist Table Baseline Sizing
BASE_RISK_PER_TRADE = 1000.0     # Baseline ₹1,000 risk for full watchlist table

# ₹1,00,000 Portfolio Basket Allocator (Top 3-4 Stocks)
TOTAL_PORTFOLIO_CAPITAL = 100000.0  # Your total swing trading capital (₹1,00,000)
PORTFOLIO_BASKET_SLOTS = 4          # Allocate ₹1,00,000 across the Top 4 setups
MAX_CAPITAL_PER_STOCK = 35000.0     # Hard cap (₹35k) so 1 stock never eats ₹68k!
MAX_PORTFOLIO_RISK_PCT = 0.06       # Max total portfolio risk across all 4 stocks (6% = ₹6,000)

# Strategy & Backtest Rules
RR_STAGE_1 = 1.25                # T1 - Book 50% at +1.25R & Trail SL to Entry
RR_FINAL = 2.50                  # Target - Book remaining 50% at +2.5R (Locked 1:2.5 RR)
SWING_LOW_LOOKBACK = 7           # 7-candle lowest low Stop Loss
MIN_BACKTEST_TRADES = 5          # Minimum 5 trades in 5 years
MIN_WIN_PROBABILITY = 38.0       # Only show high-probability stocks (>= 38%)
TOP_N_WATCHLIST = 12             # Top 12 stocks in the main table
MAX_HOLD_DAYS = 20               # 20-day time stop in backtest

raw_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
token_match = re.search(r"(\d+:[A-Za-z0-9_-]+)", raw_token)
TELEGRAM_BOT_TOKEN = token_match.group(1) if token_match else ""
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")

# ==============================================================================
# 2. NIFTY 500 UNIVERSE FETCHER (WITH ETERNAL.NS UPDATED FALLBACK)
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
# 3. TECHNICAL INDICATORS (EMA PULLBACK + DHAN CHART BB55, RSI-SMA, CMF21)
# ==============================================================================
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"]

    # 1. Core EMA 21 & 50
    df["EMA_21"] = close.ewm(span=21, adjust=False).mean()
    df["EMA_50"] = close.ewm(span=50, adjust=False).mean()

    # 2. Wilder's RSI(14) & SMA(14) of RSI
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / 14.0, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / 14.0, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI_14"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)
    df["RSI_SMA_14"] = df["RSI_14"].rolling(window=14).mean()

    # 3. Dual Fibonacci Bollinger Bands: BB(38, 2.0) & BB(61, 2.0)
    df["BB_SMA_38"] = close.rolling(window=38).mean()
    bb_std_38 = close.rolling(window=38).std(ddof=0)
    df["BB_UPPER_38"] = df["BB_SMA_38"] + (2.0 * bb_std_38)

    df["BB_SMA_61"] = close.rolling(window=61).mean()
    bb_std_61 = close.rolling(window=61).std(ddof=0)
    df["BB_LOWER_61"] = df["BB_SMA_61"] - (2.0 * bb_std_61)

    # 4. Core Proven Strategy: EMA Pullback
    df["SIG_CORE"] = (
        (df["EMA_21"] > df["EMA_50"])
        & (df["RSI_14"] > 40.0)
        & (df["RSI_14"] < 60.0)
        & (df["Close"] > df["Open"])
    )

    # 5. Upgraded ⭐GRADE-A Bonus: BB(38/61) Value Ribbon Bounce + RSI > SMA(14)
    # - Uptrend structure: 38 SMA > 61 SMA
    # - Value Zone Pullback: Recent 3-day low tested near/inside the 38 SMA (within 1.5%), and Close held above 61 SMA
    # - Momentum turn: RSI(14) > RSI_SMA(14) and plenty of headroom below BB_UPPER_38
    recent_low_3d = low.rolling(window=3).min()
    df["SIG_DHAN_BONUS"] = (
        (df["BB_SMA_38"] > df["BB_SMA_61"])
        & (recent_low_3d <= df["BB_SMA_38"] * 1.015)
        & (df["Close"] >= df["BB_SMA_61"])
        & (df["Close"] < df["BB_UPPER_38"] * 0.98)
        & (df["RSI_14"] > df["RSI_SMA_14"])
    )

    return df

# ==============================================================================
# 4. 5-YEAR IN-MEMORY BACKTESTER
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
# 5. ₹1,00,000 PORTFOLIO ALLOCATOR (EQUALIZED RISK & LOCKED 1:2.5 RR)
# ==============================================================================
def build_100k_portfolio_plan(signals: list) -> tuple[list, dict]:
    """
    Selects up to PORTFOLIO_BASKET_SLOTS (4) top stocks and sizes them so:
    1. Total Capital deployed fits inside ₹1,00,000.
    2. No single stock exceeds ₹35,000 capital (prevents ₹68k single-stock concentration).
    3. Every stock has Target = +2.5R, guaranteeing Total Portfolio Profit = 2.5x Total SL!
    """
    basket = [s.copy() for s in signals[:PORTFOLIO_BASKET_SLOTS]]
    if not basket:
        return [], {}

    # Target equal risk per slot (e.g., ₹1,500 risk per stock = ₹6,000 max risk across 4 stocks),
    # then dynamically scale so total capital fits <= ₹1,00,000 and each stock <= ₹35,000.
    target_risk_per_stock = (TOTAL_PORTFOLIO_CAPITAL * MAX_PORTFOLIO_RISK_PCT) / len(basket)

    # First pass: size each stock by target_risk_per_stock, capped at MAX_CAPITAL_PER_STOCK
    for item in basket:
        risk_per_share = item["RiskPerShare"]
        qty_risk = math.floor(target_risk_per_stock / risk_per_share)
        qty_cap = math.floor(MAX_CAPITAL_PER_STOCK / item["Entry"])
        item["PortQty"] = max(1, min(qty_risk, qty_cap))
        item["PortCap"] = item["PortQty"] * item["Entry"]

    total_cap = sum(x["PortCap"] for x in basket)

    # Second pass: if total_cap > ₹1,00,000, scale down proportionally;
    # if total_cap < ₹90,000, scale up uncapped stocks up to MAX_CAPITAL_PER_STOCK
    if total_cap > TOTAL_PORTFOLIO_CAPITAL:
        scale = TOTAL_PORTFOLIO_CAPITAL / total_cap
        for item in basket:
            item["PortQty"] = max(1, math.floor(item["PortQty"] * scale))
    elif total_cap < TOTAL_PORTFOLIO_CAPITAL * 0.95:
        scale = TOTAL_PORTFOLIO_CAPITAL / total_cap
        for item in basket:
            scaled_qty = math.floor(item["PortQty"] * scale)
            max_qty_cap = math.floor(MAX_CAPITAL_PER_STOCK / item["Entry"])
            item["PortQty"] = max(1, min(scaled_qty, max_qty_cap))

    # Finalize exact Rupee numbers for each stock in the ₹1,00,000 basket
    tot_capital = 0
    tot_risk = 0.0
    tot_t1_profit = 0.0
    tot_target_profit = 0.0

    for item in basket:
        q = item["PortQty"]
        rps = item["RiskPerShare"]
        cap = round(q * item["Entry"], 0)
        sl_rupees = round(q * rps, 0)
        # Full 1:2.5 Target Profit = 2.5 * Risk
        target_profit_rupees = round(q * (RR_FINAL * rps), 0)
        # 2-Stage Blended Profit if 50% booked at +1.25R and 50% at +2.5R (+1.875R)
        t1_blended_rupees = round(q * (1.875 * rps), 0)

        item["PortCap"] = int(cap)
        item["PortRisk"] = int(sl_rupees)
        item["PortProfit25R"] = int(target_profit_rupees)
        item["PortProfitBlended"] = int(t1_blended_rupees)

        tot_capital += int(cap)
        tot_risk += sl_rupees
        tot_t1_profit += t1_blended_rupees
        tot_target_profit += target_profit_rupees

    summary = {
        "total_capital": int(tot_capital),
        "unused_cash": int(max(0, TOTAL_PORTFOLIO_CAPITAL - tot_capital)),
        "total_risk": int(round(tot_risk, 0)),
        "total_profit_25r": int(round(tot_target_profit, 0)),
        "total_profit_blended": int(round(tot_t1_profit, 0)),
    }
    return basket, summary


# ==============================================================================
# 6. MAIN SCANNER ENGINE
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
                math.floor(BASE_RISK_PER_TRADE / risk),
                math.floor(MAX_CAPITAL_PER_STOCK / entry_price),
            )
            if qty < 1:
                continue

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
                "RiskPerShare": risk,
            })

    # FIXED SORT: Strictly sort by highest 5-Year Win% first, using ⭐GRADE-A as tie-breaker!
    signals.sort(key=lambda x: (x["WinProb"], x["IsGradeA"]), reverse=True)
    return signals[:TOP_N_WATCHLIST], now_ist


# ==============================================================================
# 7. TELEGRAM OUTPUT (WATCHLIST + ₹1,00,000 PORTFOLIO ALLOCATION PLAN)
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

    basket, port_sum = build_100k_portfolio_plan(signals)

    lines = [
        f"<b>🎯 TOP {len(signals)} NIFTY 500 SWING SETUPS (SORTED BY 5Y WIN%)</b>",
        f"🕒 <code>{now_ist}</code> | Base Table Risk: <code>₹1,000/trade</code>\n",
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
    lines.append("</pre>\n")

    # Add the ₹1,00,000 Portfolio Allocation Plan for the Top 4 Stocks
    lines.append(f"<b>💼 ₹1,00,000 PORTFOLIO PLAN (BUY TOP {len(basket)} STOCKS — LOCKED 1:2.5 RR)</b>")
    lines.append("<i>Max ₹35,000/stock cap so no single stock hogs your ₹1L capital:</i>")
    lines.append("<pre>")
    p_hdr = f"{'Stock':<10}|{'BuyQty':>6}|{'Capital':>8}|{'SL Price':>8}|{'Target':>8}|{'Risk(₹)':>8}|{'Profit(₹)':>9}"
    lines.append(p_hdr)
    lines.append("-" * len(p_hdr))

    for b in basket:
        lines.append(
            f"{b['Stock'][:10]:<10}|"
            f"{b['PortQty']:>6d}|"
            f"{b['PortCap']:>8d}|"
            f"{b['SL']:>8.1f}|"
            f"{b['Target']:>8.1f}|"
            f"{-b['PortRisk']:>8d}|"
            f"{'+' + str(b['PortProfit25R']):>9}"
        )
    lines.append("-" * len(p_hdr))
    lines.append(
        f"{'TOTAL':<10}|"
        f"{'Top ' + str(len(basket)):>6}|"
        f"{port_sum['total_capital']:>8d}|"
        f"{'1:2.5 RR':>8}|"
        f"{'Locked':>8}|"
        f"{-port_sum['total_risk']:>8d}|"
        f"{'+' + str(port_sum['total_profit_25r']):>9}"
    )
    lines.append("</pre>")
    lines.append(
        f"📌 <b>Portfolio Summary:</b> Deploy <code>₹{port_sum['total_capital']:,}</code> "
        f"(Cash Reserve: <code>₹{port_sum['unused_cash']:,}</code>) | "
        f"Combined SL: <code>-₹{port_sum['total_risk']:,}</code> | "
        f"Full Target (2.5R): <code>+₹{port_sum['total_profit_25r']:,}</code> "
        f"<i>(or <code>+₹{port_sum['total_profit_blended']:,}</code> if booking 50% at T1)</i>\n"
        f"⚠️ <i>Tip: If your actual Buy Avg differs from Entry, set Target = BuyAvg + 2.5 × (BuyAvg - SL).</i>"
    )

    send_telegram("\n".join(lines))


if __name__ == "__main__":
    top_signals, ts = scan_market()
    publish(top_signals, ts)
