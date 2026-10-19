#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
expiry_study.py  -  does buying index options with a FAR expiry beat near expiry?
================================================================================
Companion to options_scannerv2.py (must sit in the same folder; nothing in v2 is modified).

For every historical swing signal on each index it:
  1. buys an ATM option that has exactly D calendar days to expiry (D = 7, 10, 15, 22, 30, 45 ...)
  2. builds the SAME option ladder as v2 (stop clamp 18-28% of premium, T1 = 1.25R, target = 2.5R)
  3. REPRICES the option every bar with Black-Scholes along the real price path
     (so theta decay, gamma and the time-to-expiry effect are all inside the P&L)
  4. applies the v2 exit rules: 50% booked at T1 then break-even stop, gap-aware stop fills,
     mark-to-market at timeout, forced exit 1 day before expiry
  5. deducts v2's cost model (brokerage, STT, exchange, GST, stamp, slippage)
  6. compares against RANDOM entries (same direction rule) with a permutation p-value

Usage
  python expiry_study.py                                   # NIFTY + BANKNIFTY, 5y, default expiries
  python expiry_study.py --symbols NIFTY,BANKNIFTY,SENSEX --dtes 7,15,30,45 --max-bars 8
  python expiry_study.py --csv study.csv
  python expiry_study.py --synthetic                       # offline pipeline check on fake data

Honest limitations (read before trusting a number)
  * IV is held constant during each trade (VIX x 1.15 at entry). Real IV moves: it usually rises when
    the index falls (helps puts, hurts calls) and mean-reverts after spikes. Skew is ignored.
  * Daily bars: the path inside a bar is unknown. The full day's decay is applied to every price in
    the bar and the adverse side is evaluated first (conservative).
  * Prices are theoretical Black-Scholes values, not traded quotes: real spreads on far-dated contracts
    are usually wider than on near-dated ones, so far-expiry results here are somewhat flattering.
  * ATM only, one lot, no margin/overnight-gap-limit effects, in-sample (no walk-forward).
  * The p-values across expiries are NOT independent (same signals), so don't "shop" the best one.
"""
from __future__ import annotations

import argparse
import math
import sys
import zlib
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import options_scannerv2 as S

NAME2TK = {m["name"]: tk for tk, m in S.FNO_UNIVERSE.items() if m["is_idx"]}
NAME2TK.update({m["nse"]: tk for tk, m in S.FNO_UNIVERSE.items() if m["is_idx"]})


# ------------------------------------------------------------------------------
# fast Black-Scholes price (the study reprices ~10^5 times)
# ------------------------------------------------------------------------------
def _cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def bs_price(spot: float, k: float, t: float, sigma: float, opt: str) -> float:
    t = max(t, 1.0 / 365.0)
    sq = math.sqrt(t)
    d1 = (math.log(spot / k) + (S.RISK_FREE_RATE + 0.5 * sigma * sigma) * t) / (sigma * sq)
    d2 = d1 - sigma * sq
    disc = math.exp(-S.RISK_FREE_RATE * t)
    if opt == "CE":
        return spot * _cdf(d1) - k * disc * _cdf(d2)
    return k * disc * _cdf(-d2) - spot * _cdf(-d1)


# ------------------------------------------------------------------------------
# one option trade, repriced bar by bar
# ------------------------------------------------------------------------------
def sim_option_trade(i, sgn, opt, strike, iv, dte, lad, o, h, l, c, dates, max_bars):
    """Returns (R, exit_index, bars_held). R is in units of the option's rupee risk (entry - stop)."""
    entry, risk = lad.entry, lad.risk
    sl, t1, tg = lad.sl, lad.t1, lad.tgt
    r1, rt = (t1 - entry) / risk, (tg - entry) / risk
    frac = S.T1_BOOK_FRAC
    t1_hit = False
    last_j, last_val = i, entry
    end = min(i + max_bars, len(c) - 1)
    for j in range(i + 1, end + 1):
        elapsed = (dates[j] - dates[i]).days
        rem_days = dte - elapsed
        T = rem_days / 365.0
        adv_u = l[j] if sgn > 0 else h[j]
        fav_u = h[j] if sgn > 0 else l[j]
        p_open = bs_price(o[j], strike, T, iv, opt)
        p_adv = bs_price(adv_u, strike, T, iv, opt)
        p_fav = bs_price(fav_u, strike, T, iv, opt)
        p_close = bs_price(c[j], strike, T, iv, opt)
        last_j, last_val = j, p_close
        stop = entry if t1_hit else sl
        if min(p_open, p_adv) <= stop:
            fill = p_open if p_open <= stop else stop            # gap through the stop fills at the open
            rem = (fill - entry) / risk
            return (frac * r1 + (1 - frac) * rem if t1_hit else rem), j, j - i
        if p_fav >= tg:
            return frac * r1 + (1 - frac) * rt, j, j - i
        if not t1_hit and p_fav >= t1:
            t1_hit = True
            if p_adv <= entry:                                    # same bar also tagged break-even: assume worst
                return frac * r1, j, j - i
        if rem_days <= 1:                                         # never hold into the last day
            break
    mtm = (last_val - entry) / risk
    return (frac * r1 + (1 - frac) * mtm if t1_hit else mtm), last_j, last_j - i


# ------------------------------------------------------------------------------
# study for one symbol at one expiry
# ------------------------------------------------------------------------------
def study_one(df: pd.DataFrame, meta: dict, iv_series: np.ndarray, dte: int, max_bars: int,
              iters: int, seed: int) -> dict:
    o, h, l, c = (df[x].values.astype(float) for x in ("Open", "High", "Low", "Close"))
    atr = df["ATR"].values.astype(float)
    dates = [ts.date() for ts in df.index]
    ce_dir = (df["Close"].values >= df["EMA21"].values)                 # v2's direction rule
    sig = np.where(ce_dir, df["CE_Signal"].values, df["PE_Signal"].values).astype(bool)
    step, lot = float(meta["step"]), int(meta["lot"])
    n = len(df)
    R = np.full(n, np.nan)
    X = np.zeros(n, dtype=int)
    H = np.zeros(n)
    for i in range(60, n - max_bars - 2):
        if not (atr[i] > 0) or not np.isfinite(iv_series[i]):
            continue
        opt = "CE" if ce_dir[i] else "PE"
        sgn = 1 if opt == "CE" else -1
        strike = round(c[i] / step) * step
        p0, delta, gamma, _ = S.black_scholes(c[i], strike, dte / 365.0, S.RISK_FREE_RATE, iv_series[i], opt)
        lad = S.build_ladder(p0, delta, gamma, atr[i], S.SWING)
        r, x, hd = sim_option_trade(i, sgn, opt, strike, iv_series[i], dte, lad, o, h, l, c, dates, max_bars)
        cost_r = S.round_trip_cost(lad.entry, lot, S.SLIPPAGE_PCT) / (lad.risk * lot)
        R[i], X[i], H[i] = r - cost_r, x, hd                              # NET of costs
    valid = R[~np.isnan(R)]
    out = {"dte": dte, "n": 0}
    if len(valid) == 0:
        return out
    rs, held, last_exit = [], [], -1
    for i in np.flatnonzero(sig & ~np.isnan(R)):                          # non-overlapping signal trades
        if i <= last_exit:
            continue
        rs.append(R[i])
        held.append(H[i])
        last_exit = X[i]
    base = float(valid.mean())
    out.update(base=base)
    if not rs:
        return out
    arr = np.asarray(rs)
    m = len(arr)
    rng = np.random.default_rng(seed)
    means = valid[rng.integers(0, len(valid), size=(iters, m))].mean(axis=1)
    cum = np.cumsum(arr)
    out.update(
        n=m, hit=float((arr > 0).mean()), exp=float(arr.mean()), total=float(arr.sum()),
        dd=float((np.maximum.accumulate(cum) - cum).max()), hold=float(np.mean(held)),
        p=float((np.sum(means >= arr.mean()) + 1) / (iters + 1)),
        se=float(arr.std(ddof=1) / math.sqrt(m)) if m > 1 else float("nan"),
    )
    # --- today's feasibility at this expiry (latest bar, latest IV)
    i = n - 1
    opt = "CE" if ce_dir[i] else "PE"
    strike = round(c[i] / step) * step
    iv = iv_series[i] if np.isfinite(iv_series[i]) else 0.155
    p0, delta, gamma, _ = S.black_scholes(c[i], strike, dte / 365.0, S.RISK_FREE_RATE, iv, opt)
    lad = S.build_ladder(p0, delta, gamma, atr[i], S.SWING)
    cost_lot = S.round_trip_cost(lad.entry, lot, S.SLIPPAGE_PCT)
    risk_lot = lad.risk * lot + cost_lot
    out.update(prem=lad.entry, per_lot=lad.entry * lot, risk_lot=risk_lot,
               lots=int(S.RISK_PER_TRADE // risk_lot), fid=lad.fidelity)
    return out


# ------------------------------------------------------------------------------
# data
# ------------------------------------------------------------------------------
def load_vix(index: pd.DatetimeIndex, fixed_iv: float) -> np.ndarray:
    try:
        v = S.download(["^INDIAVIX"], "6y", "1d")
        v = S.extract_ticker_df(v, "^INDIAVIX")
        if v.empty:
            raise ValueError("empty")
        v = S.to_ist_naive(v)["Close"] / 100.0 * 1.15
        v.index = v.index.normalize()
        iv = v.reindex(index.normalize(), method="ffill").values.astype(float)
        iv = np.where(np.isfinite(iv), iv, fixed_iv)
        return np.clip(iv, 0.12, 0.9)
    except Exception as e:
        print(f"  (India VIX unavailable: {e}; using fixed IV {fixed_iv:.3f})")
        return np.full(len(index), fixed_iv)


def load_symbol(tk: str, years: int) -> pd.DataFrame:
    raw = S.download([tk], f"{years}y", "1d")
    df = S.extract_ticker_df(raw, tk)
    if len(df) < 300:
        return pd.DataFrame()
    df = S.drop_partial_daily(S.to_ist_naive(df), pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None).to_pydatetime())
    return S.compute_daily_indicators(df)


def synthetic_symbol(seed: int, n: int = 1250) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0004, 0.009, n)
    close = 20000 * np.exp(np.cumsum(r))
    open_ = np.r_[close[0], close[:-1]]
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n)))
    df = pd.DataFrame({"Open": open_, "High": hi, "Low": lo, "Close": close, "Volume": 1e6},
                      index=pd.bdate_range(start="2021-10-01", periods=n))
    return S.compute_daily_indicators(df)


# ------------------------------------------------------------------------------
# reporting
# ------------------------------------------------------------------------------
HDR = (f"{'DTE':>4} {'n':>4} {'hit%':>5} {'net E[R]':>9} {'+/-se':>6} {'random':>7} {'edge':>6} {'p':>5} "
       f"{'hold':>5} {'totalR':>7} {'maxDD':>6} | {'prem':>7} {'Rs/lot':>8} {'risk/lot':>9} {'lots':>5} {'fid':>5}")


def fmt(r: dict) -> str:
    if r.get("n", 0) == 0 or "exp" not in r:
        return f"{r['dte']:>4}   (no trades)"
    return (f"{r['dte']:>4} {r['n']:>4} {r['hit']*100:>4.0f}% {r['exp']:>+9.3f} {r['se']:>6.3f} {r['base']:>+7.3f} "
            f"{r['exp']-r['base']:>+6.3f} {r['p']:>5.2f} {r['hold']:>5.1f} {r['total']:>+7.1f} {r['dd']:>6.1f} | "
            f"{r['prem']:>7.1f} {r['per_lot']:>8,.0f} {r['risk_lot']:>9,.0f} {r['lots']:>5d} {r['fid']:>5.2f}")


def verdict(sym: str, rows: List[dict]) -> List[str]:
    rows = [r for r in rows if r.get("n", 0) > 0 and "exp" in r]
    if not rows:
        return []
    best = max(rows, key=lambda r: r["exp"])
    out = []
    if best["exp"] <= 0:
        out.append(f"  -> {sym}: NO expiry has positive net expectancy. Far expiry reduces the loss "
                   f"(best: {best['dte']}d at {best['exp']:+.3f}R) but does not create a profit.")
    else:
        sig = best["exp"] - 2 * best["se"] > 0
        out.append(f"  -> {sym}: best net expectancy {best['exp']:+.3f}R at {best['dte']}d "
                   f"({'clears' if sig else 'does NOT clear'} 2 standard errors; p vs random = {best['p']:.2f}).")
    near = min(rows, key=lambda r: r["dte"])
    far = [r for r in rows if r["dte"] >= 15]
    if far:
        f = max(far, key=lambda r: r["exp"])
        out.append(f"     near ({near['dte']}d) {near['exp']:+.3f}R  vs  best far (>=15d, {f['dte']}d) {f['exp']:+.3f}R "
                   f"=> far expiry changes net R/trade by {f['exp'] - near['exp']:+.3f}.")
    if best.get("lots", 0) < 1:
        out.append(f"     NOTE: one lot risks Rs {best['risk_lot']:,.0f} at {best['dte']}d > your Rs {S.RISK_PER_TRADE:,.0f} limit "
                   f"- raise RISK_PER_TRADE (or accept a wider stop) to trade it.")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Expiry study for index option buying (uses options_scannerv2 logic)")
    ap.add_argument("--symbols", default="NIFTY,BANKNIFTY", help="comma list of index names: NIFTY,BANKNIFTY,SENSEX,FINNIFTY,MIDCPNIFTY")
    ap.add_argument("--dtes", default="7,10,15,22,30,45")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--max-bars", type=int, default=S.SWING.max_bars, help="max holding days (v2 swing default)")
    ap.add_argument("--iv", type=float, default=0.155, help="fallback IV if India VIX download fails")
    ap.add_argument("--iters", type=int, default=2000, help="permutation iterations")
    ap.add_argument("--csv", default="", help="optional path to save all rows")
    ap.add_argument("--synthetic", action="store_true", help="offline pipeline check on random data (not a result!)")
    a = ap.parse_args()

    dtes = [int(x) for x in a.dtes.split(",") if x.strip()]
    all_rows = []
    print(f"Expiry study | max hold {a.max_bars} bars | risk limit Rs {S.RISK_PER_TRADE:,.0f} | costs: v2 model")
    for name in [s.strip().upper() for s in a.symbols.split(",") if s.strip()]:
        tk = NAME2TK.get(name)
        if not tk:
            print(f"\n{name}: unknown index (use one of {sorted(set(m['name'] for m in S.FNO_UNIVERSE.values() if m['is_idx']))})")
            continue
        meta = S.FNO_UNIVERSE[tk]
        df = synthetic_symbol(zlib.crc32(name.encode())) if a.synthetic else load_symbol(tk, a.years)
        if df.empty:
            print(f"\n{name}: no data downloaded")
            continue
        iv = np.full(len(df), a.iv) if a.synthetic else load_vix(df.index, a.iv)
        print(f"\n=== {name}  ({len(df)} daily bars, {df.index[0].date()} -> {df.index[-1].date()}) "
              f"{'[SYNTHETIC DATA]' if a.synthetic else ''}")
        print(HDR)
        rows = []
        for d in dtes:
            r = study_one(df, meta, iv, d, a.max_bars, a.iters, zlib.crc32(f"{name}{d}".encode()))
            r["symbol"] = name
            rows.append(r)
            print(fmt(r))
        for line in verdict(name, rows):
            print(line)
        all_rows += rows

    if a.csv and all_rows:
        pd.DataFrame(all_rows).to_csv(a.csv, index=False)
        print(f"\nSaved {len(all_rows)} rows to {a.csv}")
    print("\nColumns: net E[R] = mean R per trade AFTER theta (via repricing) and costs | random = same-direction "
          "random entries | edge = signal minus random | p = chance random entries match the signal | "
          "hold = avg bars | maxDD in R | right block = what one lot costs/risks TODAY at that expiry.")


if __name__ == "__main__":
    main()
