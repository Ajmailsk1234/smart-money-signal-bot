import os, time, math
from datetime import datetime, timezone
import pandas as pd
import streamlit as st
import ccxt
import requests

st.set_page_config(page_title="Smart Money Signal Bot", page_icon="📊", layout="wide")

DEFAULT_SYMBOLS = "BTC/USD:USD,ETH/USD:USD,SOL/USD:USD,XRP/USD:USD,ADA/USD:USD"
TIMEFRAME = "15m"

def env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")

@st.cache_resource
def get_exchange():
    ex = ccxt.delta({
        "enableRateLimit": True,
        "timeout": 20000,
        "urls": {"api": {
            "public": "https://api.india.delta.exchange",
            "private": "https://api.india.delta.exchange",
        }},
    })
    ex.load_markets()
    return ex

def safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default

def get_symbols(ex, requested):
    available = ex.markets
    out = []
    for s in requested:
        if s in available:
            m = available[s]
            if m.get("swap") or m.get("contract"):
                out.append(s)
            continue
        # Try matching Delta raw symbol or base/quote among live swaps.
        matches = []
        for sym, m in available.items():
            if (m.get("swap") or m.get("contract")) and (
                sym.upper() == s.upper() or m.get("id", "").upper() == s.upper()
            ):
                matches.append(sym)
        if matches:
            out.append(matches[0])
    return list(dict.fromkeys(out))

def as_num(v, default=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default

def calc_indicators(df):
    df = df.copy()
    df["ema20"] = df.close.ewm(span=20, adjust=False).mean()
    df["ema50"] = df.close.ewm(span=50, adjust=False).mean()
    prev_close = df.close.shift(1)
    tr = pd.concat([
        df.high - df.low,
        (df.high - prev_close).abs(),
        (df.low - prev_close).abs()
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["vol_sma"] = df.volume.rolling(20).mean()
    return df

def orderbook_imbalance(book, depth=20):
    bids = (book or {}).get("bids") or []
    asks = (book or {}).get("asks") or []
    b = sum(as_num(x[1], 0) or 0 for x in bids[:depth] if len(x) > 1)
    a = sum(as_num(x[1], 0) or 0 for x in asks[:depth] if len(x) > 1)
    total = b + a
    return ((b - a) / total) if total else None

def scan_symbol(ex, symbol, timeframe="15m", candle_limit=120):
    ohlcv = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=candle_limit)
    if not ohlcv or len(ohlcv) < 60:
        raise ValueError("Not enough candles")
    df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
    df = calc_indicators(df)
    # Exclude the currently forming candle for more stable signals.
    d = df.iloc[-2]
    prev = df.iloc[-3]
    prior = df.iloc[-22:-2]
    price = float(d.close)
    atr = as_num(d.atr)
    if not atr or atr <= 0:
        raise ValueError("ATR unavailable")

    # A liquidity sweep proxy: wick breaks prior 20-candle extreme then closes back inside.
    prior_high = float(prior.high.max())
    prior_low = float(prior.low.min())
    sweep_high = float(d.high > prior_high and d.close < prior_high)
    sweep_low = float(d.low < prior_low and d.close > prior_low)

    vol_ratio = None
    if as_num(d.vol_sma, 0) and d.vol_sma > 0:
        vol_ratio = float(d.volume / d.vol_sma)

    book = safe_call(lambda: ex.fetch_order_book(symbol, limit=20), {})
    imbalance = orderbook_imbalance(book)

    funding = None
    if ex.has.get("fetchFundingRate"):
        fr = safe_call(lambda: ex.fetch_funding_rate(symbol), {})
        funding = as_num((fr or {}).get("fundingRate"))
    oi = None
    if ex.has.get("fetchOpenInterest"):
        oi_data = safe_call(lambda: ex.fetch_open_interest(symbol), {})
        oi = as_num((oi_data or {}).get("openInterest"))
        if oi is None:
            oi = as_num((oi_data or {}).get("openInterestAmount"))

    trend_up = d.close > d.ema20 > d.ema50
    trend_down = d.close < d.ema20 < d.ema50
    # Use confluence, not a single indicator. OI is shown as context only (not a trend trigger).
    long_score = sum([
        bool(trend_up),
        bool(sweep_low),
        bool(vol_ratio is not None and vol_ratio >= 1.25),
        bool(imbalance is not None and imbalance >= 0.12),
        bool(funding is not None and funding < 0),
    ])
    short_score = sum([
        bool(trend_down),
        bool(sweep_high),
        bool(vol_ratio is not None and vol_ratio >= 1.25),
        bool(imbalance is not None and imbalance <= -0.12),
        bool(funding is not None and funding > 0.0005),
    ])

    signal = "WAIT"
    reason = []
    entry = sl = tp1 = tp2 = None
    side = None
    if long_score >= 3 and long_score > short_score and (trend_up or sweep_low):
        signal, side = "LONG SETUP", "LONG"
        entry = price
        sl = min(float(d.low), entry - 1.2 * atr)
        risk = entry - sl
        tp1, tp2 = entry + 1.5*risk, entry + 2.0*risk
        if sweep_low: reason.append("prior-low liquidity sweep/reclaim")
        if trend_up: reason.append("EMA trend bullish")
        if vol_ratio is not None and vol_ratio >= 1.25: reason.append("volume above 20-candle average")
        if imbalance is not None and imbalance >= 0.12: reason.append("bid-side book imbalance")
        if funding is not None and funding < 0: reason.append("negative funding context")
    elif short_score >= 3 and short_score > long_score and (trend_down or sweep_high):
        signal, side = "SHORT SETUP", "SHORT"
        entry = price
        sl = max(float(d.high), entry + 1.2 * atr)
        risk = sl - entry
        tp1, tp2 = entry - 1.5*risk, entry - 2.0*risk
        if sweep_high: reason.append("prior-high liquidity sweep/rejection")
        if trend_down: reason.append("EMA trend bearish")
        if vol_ratio is not None and vol_ratio >= 1.25: reason.append("volume above 20-candle average")
        if imbalance is not None and imbalance <= -0.12: reason.append("ask-side book imbalance")
        if funding is not None and funding > 0.0005: reason.append("positive funding context")
    else:
        reason.append("confluence threshold not met; wait for confirmation")

    market = ex.market(symbol)
    return {
        "symbol": symbol,
        "signal": signal,
        "price": price,
        "entry": entry,
        "stop_loss": sl,
        "target_1": tp1,
        "target_2": tp2,
        "long_score": long_score,
        "short_score": short_score,
        "trend": "UP" if trend_up else ("DOWN" if trend_down else "MIXED"),
        "sweep": "LOW SWEEP" if sweep_low else ("HIGH SWEEP" if sweep_high else "NONE"),
        "volume_ratio": vol_ratio,
        "book_imbalance": imbalance,
        "funding_rate": funding,
        "open_interest": oi,
        "atr": atr,
        "closed_candle_utc": datetime.fromtimestamp(int(d.timestamp)/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "reason": "; ".join(reason),
        "contract_type": "perpetual/contract",
    }

def send_telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False, "Telegram credentials not configured"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=10,
        )
        r.raise_for_status()
        return True, "sent"
    except Exception as e:
        return False, str(e)

def fmt(v, digits=4):
    if v is None or pd.isna(v):
        return "—"
    return f"{v:.{digits}f}"

st.title("📊 Smart Money Crypto Signal Bot")
st.caption("Delta Exchange India public data • signals only • no order execution • educational tool")

with st.sidebar:
    st.header("Settings")
    symbols_text = st.text_area("Perpetual symbols (CCXT format)", DEFAULT_SYMBOLS, height=100)
    timeframe = st.selectbox("Signal timeframe", ["5m","15m","30m","1h","4h"], index=1)
    refresh = st.selectbox("Refresh interval", ["Manual", "30 seconds", "60 seconds", "5 minutes"], index=0)
    min_score = st.slider("Minimum confluence score", 3, 5, 3)
    st.markdown("---")
    st.markdown("**Risk rules**")
    risk_pct = st.slider("Risk per trade (% of account)", 0.25, 1.0, 0.5, 0.25)
    account_size = st.number_input("Account size (INR, optional sizing)", min_value=0.0, value=0.0, step=1000.0)
    telegram_on = st.checkbox("Send Telegram alert for new setup", value=False)
    st.caption("Set Telegram credentials in .env before enabling alerts.")

if refresh != "Manual":
    seconds = {"30 seconds":30, "60 seconds":60, "5 minutes":300}[refresh]
    st.info(f"Auto-refresh is set to {refresh}. Use your browser refresh or install streamlit-autorefresh for continuous refresh.")

if st.button("🔍 Scan markets", type="primary"):
    st.session_state["scan_now"] = True

if st.session_state.get("scan_now", False):
    try:
        with st.spinner("Fetching public market data…"):
            ex = get_exchange()
            requested = [x.strip() for x in symbols_text.replace("\n", ",").split(",") if x.strip()]
            symbols = get_symbols(ex, requested)
            if not symbols:
                st.error("No symbols matched. Open Delta Exchange markets and copy the exact CCXT perpetual symbol, e.g. BTC/USD:USD.")
                st.stop()
            results, errors = [], []
            for symbol in symbols:
                try:
                    results.append(scan_symbol(ex, symbol, timeframe))
                    time.sleep(max(0.15, ex.rateLimit / 1000))
                except Exception as e:
                    errors.append(f"{symbol}: {e}")
            if results:
                df = pd.DataFrame(results)
                # Display summary first.
                cols = st.columns(4)
                cols[0].metric("Markets scanned", len(results))
                cols[1].metric("Long setups", int((df.signal == "LONG SETUP").sum()))
                cols[2].metric("Short setups", int((df.signal == "SHORT SETUP").sum()))
                cols[3].metric("Wait", int((df.signal == "WAIT").sum()))
                st.subheader("Signal board")
                display = df[["symbol","signal","price","trend","sweep","long_score","short_score","volume_ratio","book_imbalance","funding_rate","open_interest"]].copy()
                st.dataframe(display, use_container_width=True, hide_index=True)
                st.subheader("Entry / SL / Targets")
                for r in results:
                    if r["signal"] == "WAIT":
                        continue
                    st.markdown(f"### {r['symbol']} — {r['signal']}")
                    a,b,c,d = st.columns(4)
                    a.metric("Entry reference", fmt(r["entry"], 6))
                    b.metric("Stop-loss", fmt(r["stop_loss"], 6))
                    c.metric("Target 1 (1.5R)", fmt(r["target_1"], 6))
                    d.metric("Target 2 (2R)", fmt(r["target_2"], 6))
                    st.caption(f"Closed candle: {r['closed_candle_utc']} | Reasons: {r['reason']}")
                    if account_size > 0 and r["entry"] and r["stop_loss"]:
                        risk_cash = account_size * risk_pct / 100
                        distance_pct = abs(r["entry"] - r["stop_loss"]) / r["entry"]
                        approx_notional = risk_cash / distance_pct if distance_pct > 0 else 0
                        st.write(f"Approx. position notional by risk rule: ₹{approx_notional:,.2f} (before fees/slippage; verify contract sizing on Delta).")
                    if telegram_on:
                        key = f"alerted_{r['symbol']}_{r['signal']}_{r['closed_candle_utc']}"
                        if not st.session_state.get(key, False):
                            msg = (f"{r['symbol']} {r['signal']} ({timeframe})\n"
                                   f"Price/entry ref: {fmt(r['entry'], 6)}\nSL: {fmt(r['stop_loss'], 6)}\n"
                                   f"TP1: {fmt(r['target_1'], 6)} | TP2: {fmt(r['target_2'], 6)}\n"
                                   f"Reason: {r['reason']}\nSignal only — confirm manually.")
                            ok, msg_status = send_telegram(msg)
                            if ok:
                                st.session_state[key] = True
                            else:
                                st.warning(f"Telegram alert not sent for {r['symbol']}: {msg_status}")
                st.download_button("Download scan as CSV", df.to_csv(index=False).encode("utf-8"), "signals.csv", "text/csv")
                st.caption("Open interest/funding may be unavailable for some contracts. Liquidation levels here are approximated from recent candle swing highs/lows; this app does NOT receive CoinGlass liquidation heatmap data unless separately integrated.")
            if errors:
                with st.expander(f"Markets with data errors ({len(errors)})"):
                    st.write("\n".join(errors))
    except Exception as e:
        st.error(f"Could not initialize Delta public market data: {e}")
        st.info("Check internet access, region/API availability, and the current CCXT Delta market symbol format.")
else:
    st.info("Click **Scan markets** to fetch public data and calculate candidate setups.")
    st.markdown("""
    **Signal logic (transparent, not machine-learning magic):**
    - EMA20/EMA50 trend
    - prior 20-candle high/low sweep and reclaim
    - volume spike vs 20-candle average
    - top-20 order-book imbalance
    - funding-rate context where supported
    - ATR-based stop and 1.5R / 2R targets

    A signal requires at least 3 of 5 confluence checks. This is a heuristic and must be backtested; it is not a proven profitable strategy.
    """)
    st.warning("Do not treat this as financial advice. No exchange orders are placed. Test in paper mode first; crypto perpetuals can lose the full margin quickly.")
