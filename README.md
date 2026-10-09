# Smart Money Crypto Signal Bot (Delta Exchange India)

A local Streamlit dashboard that scans multiple Delta Exchange perpetual markets using public market data. It generates **candidate signals only** and never places trades.

## Features
- Multi-market scanner (BTC, ETH, SOL, XRP, ADA examples)
- OHLCV candles, EMA20/EMA50, ATR
- Recent swing-high/swing-low liquidity sweep proxy
- Volume spike vs 20-candle average
- L2 order-book imbalance
- Funding rate and open interest when supported by the current CCXT/Delta API
- Entry reference, ATR/swing-based stop, 1.5R and 2R targets
- CSV export and optional Telegram alerts

## Important limitations
- This is a transparent heuristic, not a trained AI model and not a proven profitable strategy.
- Exchange public data can be incomplete, delayed, region-specific, or unavailable for some symbols.
- The order book is only a snapshot and displayed orders can be cancelled.
- The app does **not** ingest CoinGlass liquidation heatmap data. Its “sweep” is a price-action proxy based on recent candle extremes, not actual liquidation records.
- Delta perpetual symbols may differ by market/account region. If a symbol fails, copy the exact CCXT symbol printed by `exchange.load_markets()` or check Delta's current market list.
- Spot buying/selling pressure is not directly measured by this version; the volume metric is the selected perpetual contract's candle volume.
- Stop/target values are chart references, not guaranteed execution prices. Fees, slippage, contract multiplier, and funding affect actual P&L.
- No order execution, API keys, or exchange login are required.

## Install (Windows)
1. Install Python 3.11 or newer from https://www.python.org/downloads/
2. Extract this ZIP to a folder.
3. Open Command Prompt in that folder.
4. Create and activate a virtual environment:
   ```bat
   py -m venv .venv
   .venv\Scripts\activate
   ```
5. Install packages:
   ```bat
   pip install -r requirements.txt
   ```
6. Start:
   ```bat
   streamlit run app.py
   ```
7. Open the local address Streamlit prints (usually `http://localhost:8501`) in your browser.

## Install (Linux / macOS)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Telegram alerts (optional)
1. Create a Telegram bot via the official BotFather and obtain the bot token.
2. Find your chat ID and send the bot a message first.
3. Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
4. Install python-dotenv if you want automatic `.env` loading, then add `from dotenv import load_dotenv; load_dotenv()` near the top of `app.py`. Alternatively set environment variables in your terminal before running Streamlit.
5. Tick “Send Telegram alert” in the app. Alerts are sent only for new signal/candle combinations in the current app session.

## First-run checklist
- Start with BTC and ETH only.
- Use 15m timeframe.
- Keep minimum confluence at 3.
- Run in observation/paper mode for at least 2–4 weeks.
- Log every signal and compare it with fees/slippage before risking real money.
- Never use a “90% win rate” claim without robust out-of-sample backtesting.

## Public data documentation
- Delta Exchange API: https://docs.delta.exchange/
- Delta Exchange public market-data docs: https://mcp.delta.exchange/docs/tools/market-data
- CCXT Delta docs: https://docs.ccxt.com/docs/exchanges/delta
