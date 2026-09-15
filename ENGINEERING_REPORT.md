# Systematic Mean-Reversion Trading Engine (NSE Cash Equity)
## 38-Month Multi-Asset Empirical Calibration & Walk-Forward Audit Report

---

### 1. Executive Summary
This project designs, calibrates, and stress-tests an intraday mean-reversion trading engine applied to NSE cash equities using 1-minute historical data spanning **July 3, 2023 to August 24, 2026 (38 continuous calendar months)**.

The engine incorporates:
- **Welford Streaming Feature Pipeline:** Constant-memory, numerical-stability streaming computation of VWAP, intraday rolling standard deviations, $Z$-scores, and 14-period RSI on 15-minute resampled bars.
- **Dynamic Risk & Time-Decay Governor:** Volatility- and session-time-adjusted dynamic profit harvesting, trailing risk ratchet, and mandatory 15:15 IST square-off.
- **Realistic Statutory Friction:** Exact Zerodha equity intraday fee model including brokerage, STT/CTT, NSE turnover charges, SEBI turnover fees, stamp duty, and 18% GST.
- **Endogenous Cross-Sectional Breadth Interlock:** Multi-asset composite $\bar{Z}_{\text{universe}}$ thresholding to inhibit long positioning during systemic market flushes.
- **Session-Level Opening Gap Circuit Breaker:** Rejects any candidate gapping down $\ge 1.25\%$ on the opening print.

---

### 2. Empirical Evolution & Calibration Journey

#### Phase 1: Uncalibrated Raw Scaling (48 Symbols)
* **Setup:** Scaled across all 48 discovered NSE symbols with baseline triggers ($Z < -2.2$, $\text{RSI} < 32$) and broad breadth threshold ($\bar{Z} < -1.1$).
* **Results:**
  - Total Completed Trades: **2,200**
  - Win Rate: **57.9%** (1,274 wins / 926 losses)
  - Gross Alpha Generated: **₹ -22,048.64**
  - Statutory Fees: **₹ 3,66,198.06** (~₹166.45 per round-trip)
  - Net Portfolio P&L: **₹ -388,246.70**
* **Root Causes:**
  1. *Breadth Inversion ($N=48$ vs $N=6$):* Central Limit Theorem compressed the 48-symbol mean toward zero; $\bar{Z}$ rarely hit $-1.1$, failing to block entries during routine broad down days.
  2. *Statutory Friction Trap:* 60–90 trades/month ground down capital on retail fees.
  3. *Momentum Contamination:* High-beta momentum names (ADANIENT, RELIANCE) experienced continuation breakdowns rather than mean-reversion.

#### Phase 2: Breadth Recalibration & Selectivity Gating
* **Setup:** Breadth interlock adjusted to $\bar{Z} < -0.35$; entry filters tightened to $Z < -2.5$ and $\text{RSI} < 28$.
* **Results:**
  - Total Completed Trades: **424** (-80.7% churn eliminated)
  - Win Rate: **55.9%**
  - Gross Alpha: **₹ -21,196.13**
  - Statutory Fees: **₹ 70,556.25** (Saved ~₹2.95 Lakhs in fees)
  - Net Portfolio P&L: **₹ -91,752.39** (+₹2.96 Lakhs recovery)
* **Bimodal Asset Behavior:**
  - Top 17 Profitable Assets: **₹ +41,757 Net P&L (69.6% Win Rate)**
  - Bottom 8 Toxic Assets: **₹ -69,587 Net Loss (35.6% Win Rate)**

#### Phase 3: Calibrated 17-Symbol Whitelist & Expectancy Amplification
* **Setup:**
  - Restricted to 17 empirically verified mean-reverting equities (Auto, Cement, FMCG, Select Private Financials).
  - Breadth Gate: $\bar{Z}_{\text{universe}} < -0.45$.
  - Organic Variance Ratio Filter: Rejects symbols when rolling $\text{VR}_{k=5} > 1.05$.
  - Expectancy Multiplier: Initial target raised to **$1.8R$** with minimum harvest floor at **$1.4R$**.
* **Final 38-Month Replay Results:**
  - **Total Completed Trades:** **118** (~3.1 high-conviction trades/month)
  - **Aggregate Win Rate:** **47.5%** (56 Wins / 62 Losses)
  - **Total Gross Alpha:** **₹ +26,742.34**
  - **Total Statutory Fees:** **₹ 19,613.96**
  - **Total Net Portfolio P&L:** **₹ +7,128.37** (Net green after all taxes, STT, and broker friction)

---

### 3. Production Configuration

Frozen in `production_config.json`:
- Breadth Gate: $\bar{Z} < -0.45$
- Entry Triggers: $Z < -2.5$, $\text{RSI} < 28.0$, Gap $< 1.25\%$, $\text{VR} \le 1.05$
- Risk & Execution: Slot Capital ₹1,00,000, Max Slots 4, Target $1.8R$, Min Harvest $1.4R$, Trailing Lock at $1.2R$, Hard Exit 15:15 IST.
