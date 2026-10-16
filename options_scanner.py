import os
import sys
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
# 🔐 CREDENTIALS & ENVIRONMENT VARIABLES (AUTO-READS FROM GITHUB SECRETS)
# ==============================================================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
EVENT_NAME         = os.environ.get("GITHUB_EVENT_NAME", "manual")

RISK_PER_TRADE        = 3000      # Max Rupee risk per option trade (₹)
MAX_CAPITAL_PER_TRADE = 45000     # Max Option Premium outlay per trade (₹)

# Intraday 15-Minute Scalping Parameters (Calibrated for 15m Option Momentum)
INTRA_RR_RATIO        = 1.6       # 1:1.6 Underlying RR (~1:2.0 Option Premium RR with Gamma)
INTRA_ATR_SL_MULT     = 1.0       # 1.0x 15m-ATR Stop Loss
INTRA_MAX_BARS        = 12        # 12 bars (3 hours) intraday window
INTRA_MIN_SETUPS      = 5         # Minimum historical setups in 60 days
INTRA_MIN_WIN_PROB    = 28.0      # Captures 28%-55% win-prob distribution, sorted highest first

# Daily Short-Term F&O Parameters (Calibrated for 3-5 Day F&O Momentum)
SWING_RR_RATIO        = 1.8       # 1:1.8 Underlying RR (~1:2.2 Option Premium RR with Gamma)
SWING_ATR_SL_MULT     = 1.0       # 1.0x Daily-ATR Stop Loss
SWING_MAX_DAYS        = 6         # 6 trading days max hold before Theta decay
SWING_MIN_SETUPS      = 6         # Minimum historical setups in 2 years
SWING_MIN_WIN_PROB    = 28.0      # Captures 28%-55% win-prob distribution, sorted highest first

RISK_FREE_RATE        = 0.068

# ==============================================================================
# 📋 2026 F&O UNIVERSE & REVISED LOT SIZES
# ==============================================================================
FNO_UNIVERSE = {
    "^NSEI":         {"name": "NIFTY",      "nse": "NIFTY",      "lot": 65,   "step": 50,  "is_idx": True},
    "^NSEBANK":      {"name": "BANKNIFTY",  "nse": "BANKNIFTY",  "lot": 30,   "step": 100, "is_idx": True},
    "^BSESN":        {"name": "SENSEX",     "nse": "SENSEX",     "lot": 20,   "step": 100, "is_idx": True},
    "AUROPHARMA.NS": {"name": "AUROPHARMA", "nse": "AUROPHARMA", "lot": 550,  "step": 20,  "is_idx": False},
    "MOTILALOFS.NS": {"name": "MOTILALOFS", "nse": "MOTILALOFS", "lot": 600,  "step": 20,  "is_idx": False},
    "ZYDUSLIFE.NS":  {"name": "ZYDUSLIFE",  "nse": "ZYDUSLIFE",  "lot": 900,  "step": 20,  "is_idx": False},
    "MANKIND.NS":    {"name": "MANKIND",    "nse": "MANKIND",    "lot": 225,  "step": 20,  "is_idx": False},
    "RBLBANK.NS":    {"name": "RBLBANK",    "nse": "RBLBANK",    "lot": 2500, "step": 5,   "is_idx": False},
    "KAYNES.NS":     {"name": "KAYNES",     "nse": "KAYNES",     "lot": 125,  "step": 50,  "is_idx": False},
    "CONCOR.NS":     {"name": "CONCOR",     "nse": "CONCOR",     "lot": 1000, "step": 10,  "is_idx": False},
    "VOLTAS.NS":     {"name": "VOLTAS",     "nse": "VOLTAS",     "lot": 375,  "step": 20,  "is_idx": False},
    "SOLARINDS.NS":  {"name": "SOLARINDS",  "nse": "SOLARINDS",  "lot": 75,   "step": 100, "is_idx": False},
    "TITAN.NS":      {"name": "TITAN",      "nse": "TITAN",      "lot": 175,  "step": 50,  "is_idx": False},
    "ABB.NS":        {"name": "ABB",        "nse": "ABB",        "lot": 125,  "step": 100, "is_idx": False},
    "RELIANCE.NS":   {"name": "RELIANCE",   "nse": "RELIANCE",   "lot": 500,  "step": 20,  "is_idx": False},
    "HDFCBANK.NS":   {"name": "HDFCBANK",   "nse": "HDFCBANK",   "lot": 550,  "step": 20,  "is_idx": False},
    "ICICIBANK.NS":  {"name": "ICICIBANK",  "nse": "ICICIBANK",  "lot": 700,  "step": 20,  "is_idx": False},
    "SBIN.NS":       {"name": "SBIN",       "nse": "SBIN",       "lot": 750,  "step": 10,  "is_idx": False},
    "TATAMOTORS.NS": {"name": "TATAMOTORS", "nse": "TATAMOTORS", "lot": 550,  "step": 10,  "is_idx": False},
    "BAJFINANCE.NS": {"name": "BAJFINANCE", "nse": "BAJFINANCE", "lot": 125,  "step": 100, "is_idx": False},
    "HAL.NS":        {"name": "HAL",        "nse": "HAL",        "lot": 150,  "step": 50,  "is_idx": False},
    "BEL.NS":        {"name": "BEL",        "nse": "BEL",        "lot": 2850, "step": 5,   "is_idx": False},
    "TRENT.NS":      {"name": "TRENT",      "nse": "TRENT",      "lot": 100,  "step": 100, "is_idx": False},
    "DIXON.NS":      {"name": "DIXON",      "nse": "DIXON",      "lot": 50,   "step": 200, "is_idx": False},
    "CAMS.NS":       {"name": "CAMS",       "nse": "CAMS",       "lot": 150,  "step": 50,  "is_idx": False},
    "NTPC.NS":       {"name": "NTPC",       "nse": "NTPC",       "lot": 1500, "step": 5,   "is_idx": False},
    "ADANIPORTS.NS": {"name": "ADANIPORTS", "nse": "ADANIPORTS", "lot": 400,  "step": 20,  "is_idx": False},
    "INDHOTEL.NS":   {"name": "INDHOTEL",   "nse": "INDHOTEL",   "lot": 1000, "step": 10,  "is_idx": False},
    "MOTHERSON.NS":  {"name": "MOTHERSON",  "nse": "MOTHERSON",  "lot": 4100, "step": 2.5, "is_idx": False},
    "NYKAA.NS":      {"name": "NYKAA",      "nse": "NYKAA",      "lot": 3125, "step": 5,   "is_idx": False},
    "JINDALSAW.NS":  {"name": "JINDALSAW",  "nse": "JINDALSAW",  "lot": 1500, "step": 5,   "is_idx": False},
    "PARADEEP.NS":   {"name": "PARADEEP",   "nse": "PARADEEP",   "lot": 4000, "step": 2.5, "is_idx": False},
    "SUPREMEIND.NS": {"name": "SUPREMEIND", "nse": "SUPREMEIND", "lot": 175,  "step": 50,  "is_idx": False},
    "GLAND.NS":      {"name": "GLAND",      "nse": "GLAND",      "lot": 250,  "step": 20,  "is_idx": False},
}

# ==============================================================================
# 🧹 MULTI-INDEX SAFE DATAFRAME EXTRACTOR
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

        for col in ['Close', 'High', 'Low']:
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

        return df.dropna(subset=['Close', 'High', 'Low']).copy()
    except Exception:
        return pd.DataFrame()

# ==============================================================================
# 🧮 BLACK-SCHOLES OPTION PRICER & GREEKS TRANSLATOR
# ==============================================================================
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

# ==============================================================================
# 📊 TECHNICAL INDICATORS (DAILY & 15-MINUTE ENGINES)
# ==============================================================================
def compute_daily_indicators(df):
    df = df.copy()
    df['EMA21']  = df['Close'].ewm(span=21, adjust=False).mean()
    df['EMA55']  = df['Close'].ewm(span=55, adjust=False).mean()
    df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()
    delta = df['Close'].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI'] = 100 - (100 / (1 + (gain / loss.replace(0, np.nan))))
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - df['Close'].shift()).abs(),
        (df['Low'] - df['Close'].shift()).abs()
    ], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    df['HV20'] = np.log(df['Close'] / df['Close'].shift(1)).rolling(20).std() * np.sqrt(252)

    df['CE_Signal'] = (
        (df['Close'] > df['EMA21']) &
        (df['RSI'] >= 50) & (df['RSI'] <= 75) &
        (df['MACD_Hist'] > df['MACD_Hist'].shift(1))
    )
    df['PE_Signal'] = (
        (df['Close'] < df['EMA21']) &
        (df['RSI'] >= 25) & (df['RSI'] <= 50) &
        (df['MACD_Hist'] < df['MACD_Hist'].shift(1))
    )
    return df

def compute_intraday_15m_indicators(df):
    df = df.copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert('Asia/Kolkata').tz_localize(None)
    tp = (df['High'] + df['Low'] + df['Close']) / 3.0
    vol_clean = df['Volume'].replace(0, 1)
    dates = pd.Series(df.index.date, index=df.index)
    df['VWAP'] = (tp * vol_clean).groupby(dates).cumsum() / vol_clean.groupby(dates).cumsum()
    df['EMA9']  = df['Close'].ewm(span=9, adjust=False).mean()
    df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()
    delta = df['Close'].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI'] = 100 - (100 / (1 + (gain / loss.replace(0, np.nan))))
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD_Hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - df['Close'].shift()).abs(),
        (df['Low'] - df['Close'].shift()).abs()
    ], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    df['HV20'] = np.log(df['Close'] / df['Close'].shift(1)).rolling(50).std() * np.sqrt(252 * 25)

    df['CE_Signal'] = (
        (df['Close'] > df['VWAP']) & (df['Close'] > df['EMA21']) &
        (df['RSI'] >= 52) & (df['RSI'] <= 76) &
        (df['MACD_Hist'] > df['MACD_Hist'].shift(1))
    )
    df['PE_Signal'] = (
        (df['Close'] < df['VWAP']) & (df['Close'] < df['EMA21']) &
        (df['RSI'] >= 24) & (df['RSI'] <= 48) &
        (df['MACD_Hist'] < df['MACD_Hist'].shift(1))
    )
    return df

# ==============================================================================
# ⏱️ NON-OVERLAPPING BACKTEST ENGINE (EXACT SETUP COUNT & WIN PROBABILITY)
# ==============================================================================
def backtest_signals(df, direction="CE", atr_mult=1.0, rr=1.8, max_bars=6, is_intraday=False):
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
        tgt   = entry + (risk * rr) if direction == "CE" else entry - (risk * rr)
        total += 1
        won = False
        exit_bar = i + 1
        for j in range(i + 1, min(i + 1 + max_bars, n)):
            exit_bar = j
            if is_intraday and dates[j] != dates[i]:
                break
            if direction == "CE":
                if lows[j] <= sl:
                    break
                if highs[j] >= tgt:
                    won = True
                    break
            else:
                if highs[j] >= sl:
                    break
                if lows[j] <= tgt:
                    won = True
                    break
        if won:
            wins += 1
        # Advance past current trade so consecutive bars aren't double-counted
        i = max(i + 1, exit_bar)
    win_prob = (wins / total * 100.0) if total > 0 else 0.0
    return wins, total, win_prob

def build_option_trade_row(meta, df, opt_type, is_live, wins, total, win_prob, india_vix, is_intraday):
    latest = df.iloc[-1]
    spot   = float(latest['Close'])
    atr    = float(latest['ATR'])
    hv20   = float(latest['HV20']) if not np.isnan(latest['HV20']) else 0.25
    step   = meta["step"]
    atm_strike = int(round(spot / step) * step)

    dte = get_dte(is_intraday=is_intraday)
    T_years = dte / 365.0
    iv = india_vix * 1.15 if meta["is_idx"] else max(hv20 * 1.08, india_vix * 1.40)

    opt_entry, delta, gamma = black_scholes_greeks(
        S=spot, K=atm_strike, T=T_years, r=RISK_FREE_RATE, sigma=iv, option_type=opt_type
    )

    atr_mult = INTRA_ATR_SL_MULT if is_intraday else SWING_ATR_SL_MULT
    rr_ratio = INTRA_RR_RATIO if is_intraday else SWING_RR_RATIO
    und_risk   = atr * atr_mult
    und_reward = und_risk * rr_ratio

    max_pct_sl = 0.32 if is_intraday else 0.38
    opt_risk   = min(opt_entry * max_pct_sl, max(opt_entry * 0.15, delta * und_risk - 0.5 * gamma * (und_risk ** 2)))
    opt_reward = (delta * und_reward) + (0.5 * gamma * (und_reward ** 2))

    opt_sl    = round(max(1.0, opt_entry - opt_risk), 2)
    opt_tgt   = round(opt_entry + opt_reward, 2)
    opt_entry = round(opt_entry, 2)

    lot_size     = meta["lot"]
    risk_per_lot = max((opt_entry - opt_sl) * lot_size, 1.0)
    cost_per_lot = max(opt_entry * lot_size, 1.0)
    num_lots     = max(1, min(max(1, int(RISK_PER_TRADE // risk_per_lot)), max(1, int(MAX_CAPITAL_PER_TRADE // cost_per_lot))))

    qty     = num_lots * lot_size
    capital = int(round(opt_entry * qty))

    return {
        "Stock": f"{meta['name'][:9]} {atm_strike}{opt_type}",
        "Entry": opt_entry,
        "Qty": qty,
        "Capital": f"₹{capital:,}",
        "Stop": opt_sl,
        "Target": opt_tgt,
        "Setups": f"{wins}/{total}",
        "Win Prob": f"{win_prob:.1f}%",
        "WinProbNum": win_prob,
        "Live": is_live
    }

def format_table(records, title):
    header = f"{'Stock':>16} {'Entry':>7} {'Qty':>5} {'Capital':>8} {'Stop':>7} {'Target':>8} {'Setups':>6} {'Win Prob':>8}"
    lines = [f"🔥 {title}", header]
    for r in records[:18]:
        lines.append(
            f"{r['Stock']:>16} {r['Entry']:>7.2f} {r['Qty']:>5d} "
            f"{r['Capital']:>8} {r['Stop']:>7.2f} {r['Target']:>8.2f} "
            f"{r['Setups']:>6} {r['Win Prob']:>8}"
        )
    return "\n".join(lines)

def send_telegram_alert(message_text):
    if not TELEGRAM_BOT_TOKEN or "YOUR_TELEGRAM" in TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or "YOUR_TELEGRAM" in TELEGRAM_CHAT_ID:
        print("❌ ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing in GitHub Repository Secrets!")
        sys.exit(1)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"<pre>{message_text}</pre>", "parse_mode": "HTML"}
    r = requests.post(url, data=payload, timeout=15)
    if r.status_code == 200:
        print("✅ Telegram notification delivered successfully!")
    else:
        print(f"❌ Telegram API rejected the message ({r.status_code}): {r.text}")
        sys.exit(1)

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

def main():
    ist = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(ist)
    current_minutes = now_ist.hour * 60 + now_ist.minute

    if EVENT_NAME == "schedule":
        if now_ist.weekday() >= 5 or current_minutes < (9 * 60 + 15) or current_minutes > (15 * 60 + 35):
            print(f"⏸️ Outside Indian Market Hours ({now_ist.strftime('%H:%M IST')}). Exiting.")
            return

    run_intraday = True
    run_swing = (EVENT_NAME != "schedule") or (current_minutes <= 9 * 60 + 45) or (current_minutes >= 14 * 60 + 45)

    tickers = list(FNO_UNIVERSE.keys())
    india_vix = get_live_vix()
    print(f"🕒 IST Time: {now_ist.strftime('%Y-%m-%d %H:%M')} | Event: {EVENT_NAME} | India VIX: {india_vix*100:.2f}%")

    messages_sent = 0

    if run_intraday:
        raw_15m = yf.download(tickers, period="60d", interval="15m", group_by="ticker", progress=False)
        intra_live, intra_watch = [], []
        for tk, meta in FNO_UNIVERSE.items():
            df15 = extract_ticker_df(raw_15m, tk)
            if len(df15) < 80:
                continue
            df15 = compute_intraday_15m_indicators(df15)
            last = df15.iloc[-1]
            opt_type = "CE" if float(last['Close']) >= float(last['VWAP']) else "PE"
            is_live  = bool(last['CE_Signal']) if opt_type == "CE" else bool(last['PE_Signal'])
            wins, tot, wp = backtest_signals(df15, opt_type, INTRA_ATR_SL_MULT, INTRA_RR_RATIO, INTRA_MAX_BARS, True)
            if tot >= INTRA_MIN_SETUPS and wp >= INTRA_MIN_WIN_PROB:
                row = build_option_trade_row(meta, df15, opt_type, is_live, wins, tot, wp, india_vix, True)
                (intra_live if is_live else intra_watch).append(row)

        intra_final = sorted(intra_live, key=lambda x: x["WinProbNum"], reverse=True) + \
                      sorted(intra_watch, key=lambda x: x["WinProbNum"], reverse=True)
        if (intra_live or EVENT_NAME != "schedule") and intra_final:
            msg = format_table(intra_final, f"15M INTRADAY SCALPS ({now_ist.strftime('%H:%M IST')})")
            print("\n" + msg)
            send_telegram_alert(msg)
            messages_sent += 1

    if run_swing:
        raw_1d = yf.download(tickers, period="2y", interval="1d", group_by="ticker", progress=False)
        swing_live, swing_watch = [], []
        for tk, meta in FNO_UNIVERSE.items():
            df1d = extract_ticker_df(raw_1d, tk)
            if len(df1d) < 100:
                continue
            df1d = compute_daily_indicators(df1d)
            last = df1d.iloc[-1]
            opt_type = "CE" if float(last['Close']) >= float(last['EMA21']) else "PE"
            is_live  = bool(last['CE_Signal']) if opt_type == "CE" else bool(last['PE_Signal'])
            wins, tot, wp = backtest_signals(df1d, opt_type, SWING_ATR_SL_MULT, SWING_RR_RATIO, SWING_MAX_DAYS, False)
            if tot >= SWING_MIN_SETUPS and wp >= SWING_MIN_WIN_PROB:
                row = build_option_trade_row(meta, df1d, opt_type, is_live, wins, tot, wp, india_vix, False)
                (swing_live if is_live else swing_watch).append(row)

        swing_final = sorted(swing_live, key=lambda x: x["WinProbNum"], reverse=True) + \
                      sorted(swing_watch, key=lambda x: x["WinProbNum"], reverse=True)
        if swing_final:
            msg = format_table(swing_final, f"3-5D POSITIONAL F&O ({now_ist.strftime('%d %b')})")
            print("\n" + msg)
            send_telegram_alert(msg)
            messages_sent += 1

    if messages_sent == 0 and EVENT_NAME != "schedule":
        send_telegram_alert("ℹ️ Scanner ran successfully, but no tickers returned data.")

if __name__ == "__main__":
    main()
