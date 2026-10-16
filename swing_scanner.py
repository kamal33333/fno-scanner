import os
import re
import io
import sys
import math
import warnings
warnings.filterwarnings('ignore')

from datetime import datetime
import pytz
import requests
import yfinance as yf
import pandas as pd
import numpy as np

# ==============================================================================
# 🔐 CREDENTIALS & CONFIGURATION (READS SECURELY FROM GITHUB SECRETS)
# ==============================================================================
RAW_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
RAW_CHATID = os.environ.get("TELEGRAM_CHAT_ID", "").strip().strip('"').strip("'")

token_match = re.search(r'(\d+:[A-Za-z0-9_-]+)', RAW_TOKEN)
TELEGRAM_BOT_TOKEN = token_match.group(1) if token_match else RAW_TOKEN.lstrip("bot").strip('"').strip("'")
TELEGRAM_CHAT_ID   = RAW_CHATID

MAX_RISK_PER_TRADE = 1000     # 1% risk on a ₹1 Lakh account (₹)
MAX_CAPITAL_LIMIT  = 100000   # ₹1 Lakh total capital ceiling per stock (₹)
MIN_SAMPLE_SIZE    = 5        # Minimum historical setups required over 5 years
MIN_WIN_RATE       = 28.5     # Minimum Win Prob % (Beats 1:2.5 break-even of 28.57%)
RR_RATIO           = 2.5      # 1:2.5 Risk-to-Reward Ratio
T1_RATIO           = 1.25     # Book 50% quantity at +1.25R & trail SL to Entry

# ==============================================================================
# 📲 TELEGRAM NOTIFICATION SENDER (AUTO-SPLITS LARGE TABLES)
# ==============================================================================
def send_telegram_alert(header_html, table_lines):
    if not TELEGRAM_BOT_TOKEN or ":" not in TELEGRAM_BOT_TOKEN:
        print("❌ ERROR: Invalid TELEGRAM_BOT_TOKEN in GitHub Secrets!")
        sys.exit(1)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Send in chunks of 25 rows so Telegram's 4096-character limit is never exceeded
    chunk_size = 25
    header_row = table_lines[0] if table_lines else ""
    data_rows  = table_lines[1:] if len(table_lines) > 1 else []

    if not data_rows:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": header_html, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=15)
        return

    for idx in range(0, len(data_rows), chunk_size):
        chunk = [header_row] + data_rows[idx:idx + chunk_size]
        body  = "\n".join(chunk)
        prefix = (
            header_html + "\n\n"
            if idx == 0
            else f"<b>🎯 5Y SWING SETUPS (Cont. {idx+1}-{min(idx+chunk_size, len(data_rows))}):</b>\n"
        )
        text_msg = f"{prefix}<pre>{body}</pre>"

        r = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text_msg, "parse_mode": "HTML"},
            timeout=15
        )
        if r.status_code == 200:
            print(f"✅ Telegram batch {idx // chunk_size + 1} delivered successfully!")
        else:
            print(f"❌ Telegram API Error ({r.status_code}): {r.text}")
            sys.exit(1)

# ==============================================================================
# 📋 FETCH NIFTY 500 UNIVERSE FROM NSE ARCHIVES (WITH FALLBACK)
# ==============================================================================
def fetch_nifty500_tickers():
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            df = pd.read_csv(io.StringIO(response.text))
            tickers = [
                f"{str(sym).strip()}.NS"
                for sym in df['Symbol'].dropna().tolist()
                if "DUMMY" not in str(sym)
            ]
            if len(tickers) > 100:
                print(f"✅ Loaded {len(tickers)} Nifty 500 symbols from NSE Archive.")
                return tickers
    except Exception as e:
        print(f"⚠️ NSE Archive CSV fallback triggered: {e}")

    return [
        "SOLARINDS.NS", "CASTROLIND.NS", "CRISIL.NS", "PARADEEP.NS", "IDBI.NS",
        "WELCORP.NS", "GESHIP.NS", "ANANDRATHI.NS", "MTARTECH.NS", "HFCL.NS",
        "RADICO.NS", "GRAPHITE.NS", "PNB.NS", "CARTRADE.NS", "RKFORGE.NS",
        "DIVISLAB.NS", "JYOTICNC.NS", "CRAFTSMAN.NS", "NYKAA.NS", "ADANIPORTS.NS",
        "TEGA.NS", "WELSPUNLIV.NS", "IIFL.NS", "JSWINFRA.NS", "PERSISTENT.NS",
        "CUB.NS", "WHIRLPOOL.NS", "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS",
        "SBIN.NS", "AUROPHARMA.NS", "MOTILALOFS.NS", "ZYDUSLIFE.NS", "MANKIND.NS",
        "RBLBANK.NS", "KAYNES.NS", "CONCOR.NS", "VOLTAS.NS", "TITAN.NS", "ABB.NS",
        "TATASTEEL.NS", "BAJFINANCE.NS", "HAL.NS", "BEL.NS", "TRENT.NS", "DIXON.NS",
        "CAMS.NS", "NTPC.NS", "INDHOTEL.NS", "MOTHERSON.NS", "JINDALSAW.NS",
        "SUPREMEIND.NS", "GLAND.NS", "GABRIEL.NS", "LICHSGFIN.NS", "IPCALAB.NS", "PETRONET.NS"
    ]

# ==============================================================================
# 📈 NATIVE PANDAS TECHNICAL INDICATORS (MATCHES PANDAS_TA EXACTLY)
# ==============================================================================
def add_indicators(df):
    df = df.copy()
    df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()

    delta = df['Close'].diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    df['RSI'] = 100 - (100 / (1 + rs))
    return df.dropna(subset=['EMA_21', 'EMA_50', 'RSI', 'Close', 'Open', 'High', 'Low'])

# ==============================================================================
# ⏱️ 5-YEAR HISTORICAL BACKTESTER (EXACT PULLBACK STRATEGY)
# ==============================================================================
def calculate_historical_win_rate(df):
    """Backtests the past 5 years for a specific stock and returns (win_rate, wins, total)."""
    wins = 0
    losses = 0
    in_trade = False
    target = 0.0
    stop_loss = 0.0

    closes = df['Close'].values
    opens  = df['Open'].values
    highs  = df['High'].values
    lows   = df['Low'].values
    ema21  = df['EMA_21'].values
    ema50  = df['EMA_50'].values
    rsi    = df['RSI'].values

    for i in range(50, len(df) - 1):
        if not in_trade:
            uptrend       = ema21[i] > ema50[i]
            pullback      = 40.0 < rsi[i] < 60.0
            bullish_close = closes[i] > opens[i]

            if uptrend and pullback and bullish_close:
                entry     = closes[i]
                stop_loss = float(np.min(lows[max(0, i - 6):i + 1]))
                risk      = entry - stop_loss
                if risk > 0:
                    target   = entry + (risk * RR_RATIO)
                    in_trade = True
        else:
            if lows[i] <= stop_loss:
                losses  += 1
                in_trade = False
            elif highs[i] >= target:
                wins    += 1
                in_trade = False

    total = wins + losses
    if total == 0:
        return 0.0, 0, 0
    win_rate = round((wins / total) * 100.0, 1)
    return win_rate, wins, total

# ==============================================================================
# 🧹 MULTI-INDEX BATCH EXTRACTOR
# ==============================================================================
def extract_single_df(raw_batch, ticker):
    try:
        if isinstance(raw_batch.columns, pd.MultiIndex):
            lvl0 = raw_batch.columns.get_level_values(0)
            lvl1 = raw_batch.columns.get_level_values(1)
            if ticker in lvl0:
                df = raw_batch[ticker].copy()
            elif ticker in lvl1:
                df = raw_batch.xs(ticker, axis=1, level=1).copy()
            else:
                return pd.DataFrame()
        else:
            df = raw_batch.copy()

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        for col in ['Open', 'High', 'Low', 'Close']:
            if col not in df.columns:
                return pd.DataFrame()
            if isinstance(df[col], pd.DataFrame):
                df[col] = df[col].iloc[:, 0]

        return df.dropna(subset=['Open', 'High', 'Low', 'Close']).copy()
    except Exception:
        return pd.DataFrame()

# ==============================================================================
# 🚀 MAIN SCANNER & 5-YEAR BACKTEST PIPELINE
# ==============================================================================
def get_swing_signals(tickers):
    signals = []
    batch_size = 100

    for b in range(0, len(tickers), batch_size):
        chunk = tickers[b:b + batch_size]
        print(f"⏳ Scanning & 5Y Backtesting batch {b+1}-{min(b+batch_size, len(tickers))} of {len(tickers)}...")
        try:
            raw_batch = yf.download(
                chunk, period="5y", interval="1d",
                auto_adjust=True, group_by="ticker", progress=False, threads=True
            )
        except Exception:
            continue

        for ticker in chunk:
            try:
                df = extract_single_df(raw_batch, ticker)
                if df.empty or len(df) < 100:
                    continue

                df = add_indicators(df)
                if len(df) < 60:
                    continue

                latest = df.iloc[-1]
                uptrend       = float(latest['EMA_21']) > float(latest['EMA_50'])
                pullback      = 40.0 < float(latest['RSI']) < 60.0
                bullish_close = float(latest['Close']) > float(latest['Open'])

                if uptrend and pullback and bullish_close:
                    entry          = float(latest['Close'])
                    stop_loss      = float(df['Low'].tail(7).min())
                    risk_per_share = entry - stop_loss

                    if risk_per_share <= 0:
                        continue

                    win_rate, wins, total_trades = calculate_historical_win_rate(df)

                    if total_trades < MIN_SAMPLE_SIZE or win_rate < MIN_WIN_RATE:
                        continue

                    t1_price = entry + (risk_per_share * T1_RATIO)
                    target   = entry + (risk_per_share * RR_RATIO)
                    qty      = math.floor(MAX_RISK_PER_TRADE / risk_per_share)

                    if qty > 0:
                        capital_required = qty * entry
                        if capital_required <= MAX_CAPITAL_LIMIT:
                            signals.append({
                                "Stock": ticker.replace(".NS", "")[:10],
                                "Entry": round(entry, 2),
                                "Qty": int(qty),
                                "Capital": f"₹{int(capital_required):,}",
                                "Stop": round(stop_loss, 2),
                                "T1_50%": round(t1_price, 2),
                                "Target": round(target, 2),
                                "RR": f"1:{RR_RATIO}",
                                "Setups": f"{wins}/{total_trades}",
                                "Win Prob": f"{win_rate:.1f}%",
                                "Sort_Prob": win_rate
                            })
            except Exception:
                continue

    if signals:
        df_results = pd.DataFrame(signals)
        df_results = df_results.sort_values(by='Sort_Prob', ascending=False).drop(columns=['Sort_Prob'])
        return df_results

    return pd.DataFrame()

def format_monospace_lines(df_results):
    header = (
        f"{'Stock':>10} {'Entry':>8} {'Qty':>4} {'Capital':>8} "
        f"{'Stop':>8} {'T1_50%':>8} {'Target':>8} {'RR':>5} {'Setups':>6} {'Win Prob':>8}"
    )
    lines = [header]
    for _, r in df_results.iterrows():
        lines.append(
            f"{r['Stock']:>10} {r['Entry']:>8.2f} {r['Qty']:>4d} {r['Capital']:>8} "
            f"{r['Stop']:>8.2f} {r['T1_50%']:>8.2f} {r['Target']:>8.2f} "
            f"{r['RR']:>5} {r['Setups']:>6} {r['Win Prob']:>8}"
        )
    return lines

def main():
    ist = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(ist)
    print(f"🕒 IST Time: {now_ist.strftime('%Y-%m-%d %H:%M')} | Starting Nifty 500 5Y Swing Scan...")

    watchlist = fetch_nifty500_tickers()
    results   = get_swing_signals(watchlist)

    if not results.empty:
        table_lines = format_monospace_lines(results)
        print("\n🎯 HIGH PROBABILITY SETUPS (5-YEAR VERIFIED):")
        print("\n".join(table_lines))

        header_html = (
            f"🎯 <b>5Y VERIFIED SWING SETUPS ({now_ist.strftime('%d %b %H:%M IST')}):</b>\n"
            f"<i>Max Risk: ₹{MAX_RISK_PER_TRADE} | Book 50% @ T1 (1.25R) & Trail SL to Entry | Final TGT: 2.5R</i>"
        )
        send_telegram_alert(header_html, table_lines)
    else:
        print("\nNo setups met criteria today.")
        msg = "⚠️ <b>No valid swing setups today.</b>\nCapital preservation is the priority."
        send_telegram_alert(msg, [])

if __name__ == "__main__":
    main()
