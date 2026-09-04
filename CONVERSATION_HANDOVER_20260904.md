# Conversation Handover — External Benchmark and Phase 2 Audit

Export date: 2026-09-04  
Repository: `hgsrao/zerodha-phase1`  
Handover branch: `codex/conversation-handover-20260904`  
Branch base commit: `18f9819e44474648fce2a97a4823e33a6fdd09b3`

## Purpose

This is the durable continuation record for the PC/laptop workflow. It is a structured handover of the conversation, decisions, tests, and verified findings. It is not presented as a word-for-word transcript because command output and repeated conversational acknowledgements would make that unreliable and difficult to use.

No broker order or live deployment was authorized or performed during this work. The activity was research, historical backtesting, dry-paper visualization, and engineering verification.

## Project direction agreed in the conversation

1. Use all 48 frozen NSE symbols and three years of historical data only after a five-symbol validation passes.
2. Preserve the original calibration directory while experiments run in isolated copies.
3. Run signals through the complete staged/gate pipeline and record entries, exits, costs, P&L, and gate outputs.
4. Keep engineering-test values separate from real calculated signal values.
5. Freeze safety thresholds and signal weights; calibration can recommend changes but cannot silently deploy them.
6. Prefer mature external repositories for commodity infrastructure instead of rebuilding backtest engines, portfolio accounting, order simulation, and analytics.
7. Keep custom code for the unique confidence signal, DCS/PID/gate policy, data/broker adapters, and operator interface.
8. Require a second independent engine to reproduce critical P&L before accepting a result.

## Signal configuration discussed

The current calculated confidence formula is:

`confidence = 0.35*momentum + 0.35*trend + 0.20*volume + 0.10*volatility`

The admission threshold is `0.55`. Engineering mode may use forced confidence `0.60` only for plumbing verification. Research mode must calculate confidence from market data and must reject forced values.

The configuration currently contains contradictions and is not yet the executable single source of truth.

## External-engine decision

Backtrader 1.9.78.123 was selected as a transparent independent Python execution/accounting reference. It is useful for verification but its pinned upstream release dates from 2023, so it should not become the only long-term production dependency.

QuantConnect LEAN is the recommended future primary event-driven platform. Vectorbt is recommended later for fast vectorized research sweeps. FreqAI/Freqtrade remains an ML benchmark with crypto-native assumptions that must be adapted explicitly. TradeMaster/FinRL-style PPO work remains a research benchmark, not production evidence.

External repository references:

- Backtrader: https://github.com/mementum/backtrader
- QuantConnect LEAN: https://github.com/QuantConnect/Lean
- vectorbt: https://github.com/polakowo/vectorbt
- Freqtrade/FreqAI: https://github.com/freqtrade/freqtrade

## Verified Backtrader five-symbol reference

Symbols: INFY, TCS, RELIANCE, SUNPHARMA, HDFCLIFE  
Starting capital: Rs 1,000,000

| Measure | Verified value |
|---|---:|
| Raw rows | 93,600 |
| Exact duplicate rows removed | 1,020 |
| Processed unique bars | 92,580 |
| Closed trades | 665 |
| Winning trades | 282 |
| Win rate | 42.4060% |
| Gross P&L before charges | approximately Rs 1,431.11 |
| Total charges | approximately Rs 2,195.00 |
| Net P&L | **Rs -763.89** |
| Return | **-0.07639%** |
| Maximum drawdown | 0.11098% |

Verification passed:

- Source file hashes matched the frozen-data manifest.
- Duplicate timestamps were exact duplicate values; conflicting duplicates fail closed.
- Processed timestamps were sorted and unique.
- Sampled vectorized confidence values matched the executable confidence class within `2.01e-14`.
- 665 gate approvals = 665 submitted buys = 665 fills = 665 closed trades.
- No zero-quantity trade occurred.
- No position crossed a trading date.
- Account equity reconciled with the complete trade ledger within floating-point noise.
- A full rerun produced a byte-identical result.

Interpretation: the signal had a small positive gross result, but the average edge was smaller than transaction charges. Overtrading converted the gross profit into a net loss.

## Initial in-house result audit

The original in-house result reported 129 trades and P&L of Rs -2,476.88. Independent inspection found:

- 70 of 129 records had quantity zero.
- Only 59 records were economic trades.
- 56 of those 59 economic trades crossed dates despite the MIS label.
- The economic winners were 37/59, or 62.71%; the reported 28.68% included zero-quantity records as losses.
- Closed-trade ledger P&L totalled approximately Rs +1,367.89.
- Reported account P&L was Rs -2,476.88.
- The unreconciled difference was approximately Rs -3,844.77.
- Entry charges were calculated on the requested 100 shares before gates reduced actual size to 1–5 shares.
- Entry charges were deducted from cash but omitted from closed-trade ledger P&L.
- Drawdown was multiplied by 100 before being passed to gates expecting a fraction.
- Lambda used position count/5 instead of gross exposure/equity.
- The gate state passed an empty open-position exposure list.
- The future next-bar open was accessed before the original decision flow completed.
- The frozen validator declared data valid despite duplicate timestamps.

## Audit of the later “776 trades — all fixed” claim

Commit `18f9819` claimed 776 valid trades, perfect accounting, no overnight positions, and only configuration differences versus Backtrader. A subsequent direct audit found that this conclusion was still incorrect.

What was genuinely improved:

- Drawdown now returns a fraction.
- Zero-size entries are rejected.
- Lambda uses marked position value/equity.
- Gate evaluation no longer directly receives the future next-bar open.
- Entry costs are based on gate-adjusted quantity rather than requested quantity.

What remains invalid in the corresponding 776-trade result:

| Check | Verified result |
|---|---:|
| Reported account P&L | Rs -1,348.20 |
| Sum of closed-trade ledger P&L | Rs -328.11 |
| Remaining accounting gap | **Rs -1,020.09** |
| Trades whose recorded entry/exit dates differ | **727 / 776** |
| JSON winners | 345, not the report's 346 |
| Actual maximum drawdown | approximately 0.1448%, not 0.000% |
| Existing deterministic gate suite | 0 passed, 36 failed |

Root causes still present:

1. The daily MIS routine waits until the next date, uses the preceding close price retrospectively, and records the exit with the next date's timestamp. This is not an executable market order.
2. A signal from the final bar can select the next trading day's open, insert that future-priced position immediately, and mark it against the earlier bar before its fill time.
3. Entry charges use current-bar evaluation price rather than actual next-bar fill price.
4. `PortfolioManager` deducts entry costs from cash but closed-trade `realized_pnl` and `costs` include only exit costs.
5. Open positions are supplied as `position_value`, while Gates 06 and 08 read `notional`; existing exposure therefore remains invisible to those gates.
6. Daily realized losses are accumulated as negative numbers, but Gate 03 compares against a positive loss threshold, so the halt does not trigger.
7. Date-boundary closures are not added correctly to the applicable daily-loss state.
8. The runner still swallows exceptions with broad `except` blocks.
9. Gate 13 duplicate protection is a placeholder that always passes.
10. Gate 15 and default signal timestamps use wall-clock time rather than historical event time. A long 48-symbol run can change behavior merely because five real minutes elapsed.
11. `end_date="2026-08-14"` is interpreted as midnight, excluding all August 14 intraday bars.
12. Duplicate rows remain inside indicator lookbacks even though the unified timestamp event set is unique.

The 111-trade difference is not explained solely by the unresolved parameter values. Both comparison runs used the same executable project gate code. Material differences also come from data deduplication, session handling, fill timing, fee valuation, slippage, end-date filtering, and event-vs-wall-clock behavior.

## Recommended canonical validation values

These are conservative engineering defaults for parity testing, not optimized trading parameters:

| Parameter | Recommended decision |
|---|---|
| Symbol concentration | 15% |
| Maximum gross exposure | 50% |
| Daily loss halt | 2% of start-of-day equity, represented as a positive loss magnitude |
| Slippage tolerance | 0.10% represented as fraction `0.001` |
| Trend calculation | SMA(9) for present parity; compare EMA only in a later preregistered study |
| Holdout | Remove the false current claim and establish a new prospective unseen holdout |
| Duplicate timestamps | Build frozen dataset V2: remove exact duplicates, fail on conflicting duplicates, publish new hashes |

## Canonical execution contract required before 48 symbols

1. Signals use completed data only through time `t`.
2. Gates use prices and state known at `t`.
3. An approved market entry fills at the next available bar open and remains pending until that event.
4. Fees use actual filled quantity and actual fill price on both sides.
5. Stop/target collision and gap behavior is documented and identical in both engines.
6. Drawdown and exposure use fractional units everywhere.
7. Zero-size orders fail closed.
8. MIS positions close through a session-calendar-aware order before the final available bar, including shortened sessions.
9. Every closed trade contains entry and exit charges; trade-ledger P&L equals equity change.
10. All 18 gate pass/fail unit tests must pass.
11. The five-symbol engines must match at the trade/event level before the 48-symbol run.

## Correct continuation order

1. Create frozen deduplicated dataset V2 and hashes without modifying the original frozen files.
2. Create one machine-loaded configuration source and remove hard-coded duplicates.
3. Repair session-aware pending entry and daily exit behavior.
4. Repair full round-trip trade accounting.
5. Repair open-position field names and positive daily-loss semantics.
6. Replace wall-clock calls with injected historical event time.
7. Repair and run all 18 deterministic gate pass/fail tests.
8. Rerun the five-symbol in-house test.
9. Compare individual signal, approval, fill, and exit events against the external reference.
10. Only after parity passes, run the 48-symbol universe.
11. Optimize signal parameters only after the execution contract is frozen.

## Git and workspace notes at export time

- The primary PC worktree was intentionally not cleaned, reset, or force-pushed.
- It contained tracked local modifications to `COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829.py`, `TIMESTAMP_ALIGNED_5SYMBOL_RESULTS.json`, and `timestamp_aligned_backtest.py` at export time.
- Those uncommitted changes are not included in this handover branch.
- The handover branch starts from committed state `18f9819` and adds only this document.
- The original calibration workspace was not changed to create this handover.
- A separate local Backtrader benchmark branch existed at `codex/nse-benchmark`, commit `3f9b570`; it was based on the public Backtrader repository and was not pushed to the upstream project.

## Laptop retrieval

From the existing laptop clone:

```powershell
Set-Location C:\Users\Dishan\ECS_Project
git fetch origin
git switch codex/conversation-handover-20260904
Get-Content .\CONVERSATION_HANDOVER_20260904.md
```

To return to the normal branch afterward:

```powershell
git switch master
```

