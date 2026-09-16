# Systematic Mean-Reversion Trading Engine (NSE Cash Equity)
## 38-Month Multi-Asset Empirical Calibration & Walk-Forward Audit Report

### Executive Summary
- Period: July 3, 2023 – August 24, 2026 (38 Months)
- Active Universe: 17 Whitelisted Equities (AXISBANK, BAJAJ-AUTO, BAJFINANCE, CIPLA, COALINDIA, EICHERMOT, ETERNAL, GRASIM, HDFCLIFE, HINDUNILVR, ITC, JSWSTEEL, M&M, MARUTI, NTPC, SBILIFE, ULTRACEMCO)
- Total Trades: 155
- Aggregate Win Rate: 60.6%
- Gross Alpha: ₹ +42,601.10
- Statutory Friction: ₹ 25,774.17
- Net Portfolio P&L: ₹ +16,826.93

### Strategy Parameters (Frozen in production_config.json)
- Breadth Gate: Universe mean Z-score < -0.45
- Entry Triggers: Z < -2.5, RSI(14) < 28.0
- Circuit Breakers: Opening gap <= -1.25%, Rolling Variance Ratio <= 1.05
- Risk & Targets: Slot capital ₹1,00,000, Target >= 1.8R, Minimum Harvest Floor >= 1.6R
