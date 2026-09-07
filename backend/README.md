# Live MT5 Market Bridge

This backend reads live XAUUSD market data from a locally installed MetaTrader 5 terminal and exposes `/api/live` for the GitHub Pages dashboard.

It is market-data-only. There is deliberately no order execution endpoint.

Run on the Windows machine where MT5 is installed:

```bash
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

For a demo account, create a `.env` from `.env.example` and set the demo login/server values. The MT5 Python integration can retrieve bars and ticks from the terminal; the dashboard polls this bridge every 2 seconds.