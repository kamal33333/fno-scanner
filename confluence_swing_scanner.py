import os
import re
import io
import math
import time
import requests
import warnings
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime
import pytz

warnings.filterwarnings("ignore")

# ==============================================================================
# 1. CONFIGURATION & RISK MANAGEMENT PARAMETERS
# ==============================================================================
IST = pytz.timezone("Asia/Kolkata")

MAX_RISK_PER_TRADE = 1000.0      # Maximum Rupee risk per trade (₹1,000)
MAX_CAPITAL_LIMIT = 100000.0     # Maximum Capital allocation per trade (₹1,00,000)
RR_STAGE_1 = 1.25                # T1 - 50% Profit Booking at +1.25R
RR_FINAL = 2.50                  # Final Target at +2.5R
SWING_LOW_LOOKBACK = 7           # 7-candle lowest low for Stop Loss
MIN_BACKTEST_TRADES = 5          # Minimum trades in 5 years to qualify
MIN_WIN_PROBABILITY = 28.5       # Minimum Win Prob (%) to beat 1:2.5 break-even (28.57%)
MAX_HOLD_DAYS = 20               # Time stop in trading days

# Clean Telegram Secrets
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
    "AMARAJABAT.NS", "APOLLOTYRE.NS", "MRF.NS", "BALKRISIND.NS", "CEATLTD.NS",
    "VOLTAS.NS", "BLUESTARCO.NS", "CROMPTON.NS", "WHIRLPOOL.NS", "VGUARD.NS",
    "PAGEIND.NS", "ABFRL.NS", "MANYAVAR.NS", "BATAINDIA.NS", "RELAXO.NS",
    "JUBLFOOD.NS", "DEVYANI.NS", "WESTLIFE.NS", "VBL.NS", "RADICO.NS",
    "UNITDSPR.NS", "UBL.NS", "INDIANB.NS", "UNIONBANK.NS", "IDFCFIRSTB.NS",
    "FEDERALBNK.NS", "BANDHANBNK.NS", "AUBANK.NS", "RBLBANK.NS", "KARURVYSYA.NS",
    "MUTHOOTFIN.NS", "MANAPPURAM.NS", "LICHSGFIN.NS", "M&MFIN.NS", "POONAWALLA.NS",
    "SUNDARMFIN.NS", "ABCAPITAL.NS", "LTF.NS", "HUDCO.NS", "IREDA.NS",
    "TATAPOWER.NS", "JSWENERGY.NS", "NHPC.NS", "SJVN.NS", "CESC.NS",
    "TORNTPOWER.NS", "IGL.NS", "MGL.NS", "GUJGASLTD.NS", "PETRONET.NS",
    "DEEPAKNTR.NS", "SRF.NS", "PIIND.NS", "UPL.NS", "COROMANDEL.NS",
    "TATACHEM.NS", "AARTIIND.NS", "ATUL.NS", "NAVINFLUOR.NS", "CLEAN.NS",
    "OBEROIRLTY.NS", "GODREJPROP.NS", "PRESTIGE.NS", "BRIGADE.NS", "PHOENIXLTD.NS",
    "ASTRAL.NS", "SUPREMEIND.NS", "KEI.NS", "CONCOR.NS", "DELHIVERY.NS",
    "IRCTC.NS", "GMRINFRA.NS", "RVNL.NS", "IRCON.NS", "NBCC.NS",
    "NCC.NS", "KPITTECH.NS", "TATAELXSI.NS", "LTTS.NS", "CYIENT.NS",
    "ZENSARTECH.NS", "BSOFT.NS", "SONATSOFTW.NS", "FSL.NS", "MPHASIS.NS"
]

def get_nifty500_tickers():
    """Fetches dynamic Nifty 500 list from NSE Archives with safe fallback."""
    url = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
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
                # Ensure ETERNAL.NS replaces legacy ZOMATO.NS if needed
                symbols = ["ETERNAL.NS" if s == "ZOMATO.NS" else s for s in symbols]
                if len(symbols) >= 100:
                    print(f"[INFO] Loaded {len(symbols)} Nifty 500 symbols from NSE.")
                    return list(dict.fromkeys(symbols))
    except Exception as e:
        print(f"[WARN] NSE CSV fetch failed ({e}). Using curated fallback universe.")
    return list(dict.fromkeys(FALLBACK_NIFTY_200))


# ==============================================================================
# 3. PURE PANDAS / NUMPY TECHNICAL INDICATORS (EXACT DHAN CHART FORMULAS)
# ==============================================================================
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes:
    - EMA(21), EMA(50)
    - Wilder's RSI(14) and SMA(14) of RSI
    - Fibonacci Bollinger Bands: Basis = SMA(55), StdDev = 1.618 & 2.618
    - Chaikin Money Flow: CMF(21)
    """
    df = df.copy()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"]

    # 1. EMAs (for Strategy A: EMA Pullback)
    df["EMA_21"] = close.ewm(span=21, adjust=False).mean()
    df["EMA_50"] = close.ewm(span=50, adjust=False).mean()

    # 2. Wilder's RSI(14) + 14 SMA of RSI (for Both Strategies)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / 14.0, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / 14.0, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI_14"] = 100.0 - (100.0 / (1.0 + rs))
    df["RSI_14"] = df["RSI_14"].fillna(50.0)
    df["RSI_SMA_14"] = df["RSI_14"].rolling(window=14).mean()

    # 3. Fibonacci Bollinger Bands: Length 55, Basis SMA, Multipliers 1.618 & 2.618
    df["BB_SMA_55"] = close.rolling(window=55).mean()
    bb_std_55 = close.rolling(window=55).std(ddof=0)
    df["BB_UPPER_1618"] = df["BB_SMA_55"] + (1.618 * bb_std_55)
    df["BB_LOWER_1618"] = df["BB_SMA_55"] - (1.618 * bb_std_55)
    df["BB_UPPER_2618"] = df["BB_SMA_55"] + (2.618 * bb_std_55)
    df["BB_LOWER_2618"] = df["BB_SMA_55"] - (2.618 * bb_std_55)

    # 4. Chaikin Money Flow: CMF(21)
    hl_range = (high - low).replace(0, np.nan)
    mf_multiplier = ((close - low) - (high - close)) / hl_range
    mf_multiplier = mf_multiplier.fillna(0.0)
    mf_volume = mf_multiplier * vol
    vol_sum_21 = vol.rolling(window=21).sum().replace(0, np.nan)
    df["CMF_21"] = (mf_volume.rolling(window=21).sum() / vol_sum_21).fillna(0.0)

    # 5. Signal Boolean Series for Vectorized Speed + Exact Backtest Matching
    # Strategy A: Classic EMA_21 > EMA_50 + RSI(40..60) + Green Candle
    df["SIG_EMA"] = (
        (df["EMA_21"] > df["EMA_50"])
        & (df["RSI_14"] > 40.0)
        & (df["RSI_14"] < 60.0)
        & (df["Close"] > df["Open"])
    )

    # Strategy B: Dhan Chart Setup (BB 55 1.618/2.618 + RSI > RSI_SMA_14 + CMF_21 > 0)
    # Checks that price is above the 55 SMA support, below the 1.618 upper band (not overextended),
    # pulled back near the value zone recently, RSI(14) is in 40..65 & above its 14-SMA, CMF(21) > 0, and Green Candle
    recent_low_5d = low.rolling(window=5).min()
    near_value_zone = (recent_low_5d <= df["BB_SMA_55"] * 1.035) | (
        recent_low_5d <= df["EMA_21"] * 1.02
    )
    df["SIG_BB_CMF"] = (
        (df["Close"] > df["BB_SMA_55"])
        & (df["Close"] < df["BB_UPPER_1618"])
        & near_value_zone
        & (df["RSI_14"] >= 40.0)
        & (df["RSI_14"] <= 65.0)
        & (df["RSI_14"] > df["RSI_SMA_14"])
        & (df["CMF_21"] > 0.0)
        & (df["Close"] > df["Open"])
    )

    # Strategy C: Grade-A Confluence (Both Strategy A and Strategy B active on the same bar)
    df["SIG_CONFLUENCE"] = df["SIG_EMA"] & df["SIG_BB_CMF"]

    return df


# ==============================================================================
# 4. 5-YEAR IN-MEMORY BACKTESTER (NO LOOK-AHEAD BIAS)
# ==============================================================================
def run_5y_backtest(df: pd.DataFrame, signal_col: str) -> dict:
    """
    Runs a realistic state-machine backtest over 5 years of daily bars for `signal_col`.
    - Entry: Close of signal candle `i`
    - Stop Loss: Lowest low of candles `i-6` to `i` (7-candle swing low)
    - Target: Entry + 2.5 * Risk (`+2.5R`)
    - Checks Low <= Stop before High >= Target on every subsequent bar.
    """
    closes = df["Close"].values
    highs = df["High"].values
    lows = df["Low"].values
    signals = df[signal_col].values
    n = len(df)

    wins = 0
    losses = 0
    total_trades = 0
    in_trade = False
    entry_price = 0.0
    stop_loss = 0.0
    target_price = 0.0
    entry_idx = 0

    # Start at index 60 so BB(55) and EMA(50) are warmed up
    for i in range(60, n - 1):
        if not in_trade:
            if signals[i]:
                swing_low = float(np.min(lows[i - SWING_LOW_LOOKBACK + 1 : i + 1]))
                curr_close = float(closes[i])
                risk = curr_close - swing_low

                # Guard against zero/micro risk or abnormally wide >12% stops
                if risk <= (curr_close * 0.003) or risk > (curr_close * 0.12):
                    continue

                in_trade = True
                entry_price = curr_close
                stop_loss = swing_low
                target_price = entry_price + (RR_FINAL * risk)
                entry_idx = i
        else:
            # Evaluate active trade on bar `i` (Conservative: Check SL first)
            if lows[i] <= stop_loss:
                losses += 1
                total_trades += 1
                in_trade = False
            elif highs[i] >= target_price:
                wins += 1
                total_trades += 1
                in_trade = False
            elif (i - entry_idx) >= MAX_HOLD_DAYS:
                # Time-stop exit: count as win if exited above +1R, else loss
                if closes[i] >= (entry_price + (entry_price - stop_loss)):
                    wins += 1
                else:
                    losses += 1
                total_trades += 1
                in_trade = False

    win_prob = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
    # Mathematical expectancy in R-multiples at 1:2.5 RR
    expectancy_r = (
        ((win_prob / 100.0) * RR_FINAL) - ((1.0 - (win_prob / 100.0)) * 1.0)
        if total_trades > 0
        else 0.0
    )

    return {
        "trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_prob": round(win_prob, 1),
        "expectancy_r": round(expectancy_r, 2),
    }


# ==============================================================================
# 5. MULTI-INDEX DATAFRAME EXTRACTOR FOR YFINANCE
# ==============================================================================
def extract_ticker_df(batch_data: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Safely extracts a single ticker DataFrame from yfinance batch download."""
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

        sub = sub[required].dropna(subset=["Open", "High", "Low", "Close"])
        return sub
    except Exception:
        return pd.DataFrame()


# ==============================================================================
# 6. MAIN SCANNER & 5-YEAR STRATEGY COMPARISON ENGINE
# ==============================================================================
def scan_market():
    now_ist = datetime.now(IST).strftime("%d-%b-%Y %I:%M %p IST")
    print(f"==================================================================")
    print(f"🚀 STARTING CONFLUENCE SWING SCANNER AT {now_ist}")
    print(f"==================================================================")

    tickers = get_nifty500_tickers()
    chunk_size = 100

    live_signals = []

    # Global 5-Year Strategy Comparison Aggregators across all Nifty 500 stocks
    global_stats = {
        "EMA_Pullback": {"trades": 0, "wins": 0},
        "BB55_CMF": {"trades": 0, "wins": 0},
        "GRADE_A_CONFLUENCE": {"trades": 0, "wins": 0},
    }

    for idx in range(0, len(tickers), chunk_size):
        chunk = tickers[idx : idx + chunk_size]
        print(f"[INFO] Downloading 5Y data for chunk {idx // chunk_size + 1} ({len(chunk)} stocks)...")
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
        except Exception as e:
            print(f"[ERROR] Batch download failed: {e}")
            continue

        for ticker in chunk:
            df = extract_ticker_df(batch_data, ticker)
            if df.empty or len(df) < 100:
                continue

            df = compute_indicators(df)

            # Run 5-Year Backtest for all 3 modes on every stock to compare probabilities
            bt_ema = run_5y_backtest(df, "SIG_EMA")
            bt_bb = run_5y_backtest(df, "SIG_BB_CMF")
            bt_conf = run_5y_backtest(df, "SIG_CONFLUENCE")

            global_stats["EMA_Pullback"]["trades"] += bt_ema["trades"]
            global_stats["EMA_Pullback"]["wins"] += bt_ema["wins"]

            global_stats["BB55_CMF"]["trades"] += bt_bb["trades"]
            global_stats["BB55_CMF"]["wins"] += bt_bb["wins"]

            global_stats["GRADE_A_CONFLUENCE"]["trades"] += bt_conf["trades"]
            global_stats["GRADE_A_CONFLUENCE"]["wins"] += bt_conf["wins"]

            # Check Latest Daily Candle for Live Trade Signals
            last_row = df.iloc[-1]
            sig_ema_today = bool(last_row["SIG_EMA"])
            sig_bb_today = bool(last_row["SIG_BB_CMF"])

            if not (sig_ema_today or sig_bb_today):
                continue

            # Determine Setup Tag and select the corresponding 5Y Backtest stats
            if sig_ema_today and sig_bb_today:
                setup_tag = "⭐GRADE-A"
                # If Grade-A Confluence has >= 5 historical trades on this single stock, use its exact win rate;
                # otherwise use the higher-confidence blend of BB55_CMF & EMA_Pullback on this stock.
                if bt_conf["trades"] >= MIN_BACKTEST_TRADES:
                    chosen_bt = bt_conf
                else:
                    comb_trades = bt_bb["trades"] + bt_ema["trades"]
                    comb_wins = bt_bb["wins"] + bt_ema["wins"]
                    comb_wp = round((comb_wins / comb_trades * 100.0), 1) if comb_trades > 0 else 0.0
                    chosen_bt = {"trades": max(bt_bb["trades"], bt_ema["trades"]), "win_prob": comb_wp}
            elif sig_bb_today:
                setup_tag = "BB55+CMF"
                chosen_bt = bt_bb
            else:
                setup_tag = "EMA_Pull"
                chosen_bt = bt_ema

            # Filter by Minimum Historical Trades and Break-Even Win Probability (28.5%)
            if chosen_bt["trades"] < MIN_BACKTEST_TRADES or chosen_bt["win_prob"] < MIN_WIN_PROBABILITY:
                continue

            # Calculate Entry, 7-Day Lowest Low Stop Loss, T1 (+1.25R), Target (+2.5R)
            entry_price = float(last_row["Close"])
            swing_low_7d = float(df["Low"].tail(SWING_LOW_LOOKBACK).min())
            risk_per_share = entry_price - swing_low_7d

            if risk_per_share <= (entry_price * 0.003) or risk_per_share > (entry_price * 0.12):
                continue

            # Position Sizing capped by ₹1,000 Risk and ₹1,00,000 Max Capital
            qty_by_risk = math.floor(MAX_RISK_PER_TRADE / risk_per_share)
            qty_by_cap = math.floor(MAX_CAPITAL_LIMIT / entry_price)
            qty = min(qty_by_risk, qty_by_cap)

            if qty < 1:
                continue

            capital_req = round(qty * entry_price, 0)
            t1_price = round(entry_price + (RR_STAGE_1 * risk_per_share), 2)
            target_price = round(entry_price + (RR_FINAL * risk_per_share), 2)

            clean_symbol = ticker.replace(".NS", "")
            live_signals.append({
                "Stock": clean_symbol,
                "Entry": round(entry_price, 2),
                "Qty": int(qty),
                "Capital": int(capital_req),
                "StopLoss": round(swing_low_7d, 2),
                "T1_50": t1_price,
                "Target": target_price,
                "RR": "1:2.5",
                "Setups": setup_tag,
                "WinProb": chosen_bt["win_prob"],
                "Priority": 1 if setup_tag == "⭐GRADE-A" else (2 if setup_tag == "BB55+CMF" else 3),
            })

    # Sort first by Win Probability descending (and prioritize ⭐GRADE-A ties)
    live_signals.sort(key=lambda x: (x["WinProb"], -x["Priority"]), reverse=True)
    return live_signals, global_stats, now_ist


# ==============================================================================
# 7. TELEGRAM FORMATTING & CHUNKED DELIVERY
# ==============================================================================
def format_strategy_comparison(global_stats: dict) -> str:
    """Builds the 5-Year Nifty 500 Strategy Comparison header block."""
    lines = []
    lines.append("<b>📊 5-YEAR NIFTY 500 BACKTEST COMPARISON (1:2.5 RR)</b>")
    lines.append("<pre>")
    lines.append(f"{'Strategy Mode':<18} | {'Trades':>7} | {'Win Prob':>8} | {'Expectancy':>10}")
    lines.append("-" * 52)

    best_mode = ""
    best_wp = -1.0

    label_map = {
        "EMA_Pullback": "1. EMA_Pull (Old)",
        "BB55_CMF": "2. BB55+CMF (New)",
        "GRADE_A_CONFLUENCE": "3. ⭐GRADE-A Both",
    }

    for key, label in label_map.items():
        tr = global_stats[key]["trades"]
        wi = global_stats[key]["wins"]
        wp = (wi / tr * 100.0) if tr > 0 else 0.0
        exp_r = ((wp / 100.0) * RR_FINAL) - ((1.0 - (wp / 100.0)) * 1.0) if tr > 0 else 0.0
        if wp > best_wp and tr > 0:
            best_wp = wp
            best_mode = label
        lines.append(f"{label:<18} | {tr:>7d} | {wp:>7.1f}% | {exp_r:>+8.2f}R")

    lines.append("</pre>")
    lines.append(f"🏆 <b>5Y Highest Probability Edge:</b> <code>{best_mode} ({best_wp:.1f}%)</code>\n")
    return "\n".join(lines)


def send_telegram_message(text: str):
    """Sends HTML-formatted message to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram credentials not set. Printing output to console only.")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code != 200:
            print(f"[ERROR] Telegram API error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"[ERROR] Failed to send Telegram message: {e}")


def publish_results(live_signals: list, global_stats: dict, now_ist: str):
    comparison_block = format_strategy_comparison(global_stats)

    if not live_signals:
        msg = (
            f"<b>🎯 NIFTY 500 CONFLUENCE SWING SCANNER</b>\n"
            f"🕒 <code>{now_ist}</code>\n\n"
            f"{comparison_block}\n"
            f"ℹ️ <i>No stocks triggered qualifying setups today with Win Prob &gt;= {MIN_WIN_PROBABILITY}%.</i>"
        )
        send_telegram_message(msg)
        return

    # Chunk table into batches of 22 rows to stay well under Telegram's 4096-char limit
    batch_size = 22
    total_batches = math.ceil(len(live_signals) / batch_size)

    for b_idx in range(total_batches):
        batch = live_signals[b_idx * batch_size : (b_idx + 1) * batch_size]
        header_Lines = [
            f"<b>🎯 NIFTY 500 CONFLUENCE SWING SCANNER ({b_idx + 1}/{total_batches})</b>",
            f"🕒 <code>{now_ist}</code> | Risk/Trade: <code>₹{int(MAX_RISK_PER_TRADE)}</code>\n",
        ]
        if b_idx == 0:
            header_Lines.append(comparison_block)

        table_lines = ["<pre>"]
        # Exact 10 Columns matching your screenshot layout
        col_hdr = (
            f"{'Stock':<10}|{'Entry':>7}|{'Qty':>4}|{'Capital':>7}|"
            f"{'SL':>7}|{'T1-50%':>7}|{'Target':>7}|{'R:R':>5}|{'Setups':<9}|{'Win%':>5}"
        )
        table_lines.append(col_hdr)
        table_lines.append("-" * len(col_hdr))

        for row in batch:
            line = (
                f"{row['Stock'][:10]:<10}|"
                f"{row['Entry']:>7.1f}|"
                f"{row['Qty']:>4d}|"
                f"{row['Capital']:>7d}|"
                f"{row['StopLoss']:>7.1f}|"
                f"{row['T1_50']:>7.1f}|"
                f"{row['Target']:>7.1f}|"
                f"{row['RR']:>5}|"
                f"{row['Setups']:<9}|"
                f"{row['WinProb']:>4.1f}%"
            )
            table_lines.append(line)

        table_lines.append("</pre>")
        table_lines.append(
            "💡 <i>Setups: ⭐GRADE-A = Both EMA &amp; BB55+CMF | BB55+CMF = Dhan Chart Setup | EMA_Pull = Classic 21/50 EMA</i>"
        )

        full_msg = "\n".join(header_Lines + table_lines)
        send_telegram_message(full_msg)
        time.sleep(1)


if __name__ == "__main__":
    signals, stats, timestamp = scan_market()
    publish_results(signals, stats, timestamp)
