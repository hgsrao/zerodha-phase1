# Project review — 15 November 2026

Scope: the whole trading project as it stands on GitHub (`hgsrao/zerodha-phase1`, 44 branches, 417 commits since late August, roughly 100,000 lines of Python), read at branch `claude/r7-governor-adaptation`. Sources are the code, the test runs of 5 November, and the project's own result documents. Anything not re-checked in this review is marked as such.

## 1. Summary

The project has built a safe, well-tested trading *machine*. It has not yet shown a trading *edge*.

- **What is strong:** data integrity, safety controls, test coverage, research discipline, and three independent execution engines.
- **What is missing:** evidence that the intraday signal makes money after costs. Every intraday P&L result recorded so far is negative or too small to mean anything.
- **What is under way:** the first full 3-year backtest of the engine as it stands, with pass/fail rules fixed in advance. Its result decides the next step.
- **One overlooked asset:** the daily momentum strategy researched in August (V11–V17) is the only line of work in the repository with a positive result that survived realistic costs. It was set aside when the project moved to intraday.

## 2. What has been built, module by module

| Module | What it does | State | Main gap |
|---|---|---|---|
| **Frozen dataset** (`revision2/DATASET_MANIFEST…`, `engine_c/data_contract.py`) | 48 NSE stocks, 1-minute bars, Jul 2023–Aug 2026, sealed by SHA-256; every row audited before use | Strong. Verified byte-identical on 6 Nov. | Uses today's index members (survivorship bias). The Nifty grid feed covers only 14 Aug 2023–13 Aug 2026. |
| **Entry signal** (`revision2_external/indicators_talib.py`, "PA box") | Weighted score of momentum, VWAP deviation, volatility and 2-bar confirmation, with about 30 tunable settings | Works as designed | **No demonstrated edge.** See §3. |
| **Regime and studies** (`regime_hmm.py`, `composite_study_signal.py`) | Market-state labels and chart-study votes | Works | The project's own closure (25 Aug) found this sensor family "has not earned authority to predict direction". |
| **Planner and exits** (`pid_controller.py`, exit controllers) | Sets stop, target and holding time | Works | The 62-trade INFY study found 79% of trades stopped out in the entry bar; stops are too tight for 1-minute noise. |
| **Sizing and safety** (`position_sizing_pyportfolioopt.py`, `gates_framework.py`, P01D authorization) | Position size, 18 safety gates, signed authorization | Strong | None found. |
| **R5 plant and governor** (`revision5/`, 14,000 lines) | Power-plant-style supervisory control of exposure per sector "bay" | 1,137 tests pass | Value unproven. The one before/after comparison on file (`RESULTS.md`) shows net P&L slightly worse with it. 9 tests fail (all pre-existing). |
| **R6 AVR** (`revision6_avr/`) | Voltage-regulator-style participation control | 121 tests pass | Value unproven. |
| **R7 governor** (`revision7_avr/`) | Exposure governor, demand-aware capital allocation, pulse FSM; optional adaptation added 5 Nov | 330 tests pass | Value unproven. Never run on real data beyond one day. |
| **Engines A, B, C** | Same strategy on three executors: in-house, Nautilus, Backtrader | B: 21 pass. C: 50 pass. | Engine A's golden-master reference fails in 17 scenarios, so the main regression check is broken. |
| **Live-trading guard** (`runtime/live_trading_guard.py`) | Refuses to start unless `LIVE_TRADING_ENABLED` is exactly `False` | Strong (72 tests) | None. |
| **Broker bridge** (on `master`: Kite client, kill switch, terminator, daily accounting) | The path to real orders | Exists with its own tests | Not re-tested in this review. Not connected to the R5–R7 engine. |
| **Research protocol** (pre-registration, sealed hold-outs, closure records) | Stops a failed idea from being re-tuned until it "passes" | Strong, and rare in retail projects | Was not applied to the control layers R5–R7 themselves. |
| **Daily momentum research** (`BRAIN_RESEARCH_SPEC_V11…V17`) | Buy the strongest stocks, hold for days (delivery, not intraday) | **Positive result** | Abandoned before paper trading. See §4. |

Test totals on 5 November: about 1,660 passed, 27 failed. All 27 failures also occur on the code as it was before this month's changes.

## 3. Where the project is lacking

**3.1 No proven edge (the central gap).** The repository's own evidence on the intraday signal:

- 15 Sep: root-cause analysis titled "why system is unprofitable"; 41% win rate, net loss.
- INFY 62-trade trace: every trade lost.
- 18 Oct one-day, 48-symbol replay: one trade per engine, both lost; costs of about ₹29 were as large as the gross move.
- `ENTRY_TIMING_RESEARCH.md`: "a momentum-confirmation engine … It has no dedicated reversal or pullback alpha."
- Sensor-fusion closure: the indicators describe the market but do not predict it.

**3.2 Effort went to control before signal.** About 23,000 lines (R5, R6, R7) manage *how much* to hold. They cannot create profit; they can only shape risk around a signal that already has an edge. The order should have been: prove the signal, then add control.

**3.3 The engine is too slow to test.** Measured speed is about 15–18 symbol-bars per second per core. Three years of data needs about 9 core-days. This is why the 3-year and 6-month runs were abandoned in October, and why profitability was never measured.

**3.4 Too many adjustable settings.** The signal alone has about 30 settings, and the registry holds several hundred. With three years of data, a tuned backtest can look good by chance. Any result on this data is partly in-sample.

**3.5 Costs are large relative to the trades.** At about ₹29 per round trip on small positions, a 1-minute strategy needs a large move per trade just to break even.

**3.6 Repository sprawl.** Seven generations of code (revision 2 to 7) live side by side, with 44 branches, 151 documents at the top level and a 178 MB archive committed into the source tree. That archive causes 3 of the failing tests. The working branch named in the handover (`r7-governor-corrections`) was never pushed, so the workstation and GitHub have differed.

**3.7 No automatic checks.** GitHub runs no tests on pushes or pull requests. Broken references (the golden master) stayed broken unnoticed.

## 4. The overlooked asset: daily momentum (V11–V17)

`BRAIN_RESEARCH_SPEC_V14_REALISTIC_COSTS.md` records a walk-forward test over 2018–2026 (17 periods, 19 stocks) with real Zerodha delivery costs:

- 11 of 17 periods profitable;
- mean return +0.083% per day;
- probability the result is zero or negative: 0.06% (bootstrap).

The document's own caveats still apply: it used today's index members (survivorship bias), few independent periods, and it was never paper-traded. It is not proof. But it is the only positive, cost-adjusted result in the project, and it suits the stated goal better than intraday: fewer trades, lower costs, no need to beat fast institutions.

## 5. What needs to be done

**Step 1 — Finish the 3-year backtest (running on the workstation).** The rules were fixed before the run: at least 30 trades; net profit after costs; at least 60% of quarters positive; positive average per trade; no window with more than 10% drawdown; last four quarters positive in total.

**Step 2 — Act on the verdict.**

- **FAIL (the likely outcome on the evidence in §3.1):** stop intraday development. Do not add layers or re-tune. Keep the safety, data and test infrastructure; they are reusable.
- **PASS:** it is still partly in-sample. Paper-trade live for 4–8 weeks with no real money before anything else.
- **INCONCLUSIVE (too few trades):** the engine is too restrictive to judge; treat as FAIL for the purpose of risking money.

**Step 3 — Re-open daily momentum as the candidate strategy (about 2–3 weeks).**

1. Re-run V14 on data up to today, to see whether it held up after August.
2. Fix the survivorship bias: use the index members as they were at each date.
3. Fix pass/fail rules in advance, as was done for the backtest.
4. If it passes: paper-trade for 2–3 months, then start with a small, capped amount and a hard stop-loss on the capital.

**Step 4 — Engineering clean-up (about 1 week, can run in parallel).**

- Repair or formally retire the Engine A golden master; fix the 9 failing R5 tests.
- Turn on automatic tests for pull requests.
- Move the 178 MB archive out of the source tree; delete merged branches; keep one source of truth on GitHub.
- Profile the engine; a 10× speed-up would turn a 9-day test into under a day.
- Decide the two open pull requests (#16 and #17).

**Step 5 — Set limits before spending more.** Write down a date and a budget of hours after which the project stops if no strategy has passed paper trading. Keep core savings outside the project regardless of any result.

## 6. What this review did not check

- The state of the backtest now running; its results are on the workstation and have not been shared yet.
- The broker bridge and live-order path on `master`.
- Whether the V11–V17 numbers reproduce; they are quoted from the project's own documents.
- Corporate-action adjustment of the frozen data.
