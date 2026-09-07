# Mr Alpha Firm Autonomous Market Engine

A live-market, continuously scanning trading research system connected to MetaTrader 5.

## Current build
- Multi-symbol live MT5 market scanner
- EMA 20/50/200, RSI, MACD, ATR
- Candle pattern and market-structure confirmation
- 0-100 signal strength ranking
- Automatic paper-trade entry and management
- SL/TP and R-multiple journal
- Email trade-open and trade-close alerts
- Email verification/login flow
- Latency-aware timestamps
- GitHub Pages dashboard

**Execution mode is PAPER in this build. No live broker order endpoint is included.**

Backend setup is documented in `backend/README.md`.
