# Mr Alpha Autonomous Market Engine

This service is the continuously running engine behind the Mr Alpha Firm dashboard.

## What it does
- Reads live tick/bar data from a locally installed MetaTrader 5 terminal.
- Scans a configurable multi-symbol watchlist continuously.
- Uses M15 EMA 20/50/200, RSI, MACD, ATR, market structure and candle-pattern confirmation.
- Produces a 0-100 signal score and ignores setups below `MIN_SIGNAL_SCORE`.
- Automatically opens and manages **paper trades**. The executor is deliberately hard-coded to paper mode in this build and has no broker order-submission path.
- Automatically closes paper positions at their simulated SL/TP and records R-multiple results.
- Sends email notifications when paper trades open/close when SMTP is configured.
- Provides email verification and login endpoints.
- Stores a local SQLite trade/event journal.

## Run
1. Install MetaTrader 5 on the same Windows machine as this service and log into a demo account.
2. Copy `.env.example` to `.env` and fill the demo MT5 and SMTP settings locally.
3. `pip install -r requirements.txt`
4. `uvicorn main:app --host 0.0.0.0 --port 8000`
5. Open the GitHub Pages dashboard and set its API URL to this machine/server URL.

## Endpoints
- `GET /api/health`
- `GET /api/market`
- `GET /api/trades`
- `GET /api/events`
- `POST /api/auth/register`
- `GET /api/auth/verify`
- `POST /api/auth/login`
- `POST /api/engine/start`
- `POST /api/engine/stop`

## Latency
The engine records event timestamps, but it does not claim zero latency. Actual latency depends on broker/MT5 feed, host, network and email provider. Email is asynchronous relative to trading decisions.

## Safety
Do not commit credentials. Do not expose MT5 credentials to the browser. This repository version intentionally supports market-data monitoring and paper execution only.
