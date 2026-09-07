# XAUUSD Sniper Risk Control Console

A polished GitHub Pages frontend for the trading-risk rules defined for this project.

## Rules
- Maximum account drawdown: 2%
- Profit target: 10% of starting balance
- Maximum scalping trades: 3
- A trade is a scalp when its duration is under 2 minutes.
- A trade is recorded as a DD event when floating/unrealized P&L becomes negative at any point before the trade closes, even if it later closes in profit.

## Safety
The hosted page is a demo/paper-mode interface. It does not place live trades or request broker passwords. A real backend/MT5 bridge should be deployed separately with secrets stored as environment variables.

## GitHub Pages
The site is a single static `index.html`, so it can be served directly by GitHub Pages.
