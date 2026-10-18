# Study Layer V2 — Research Thread Closing Record

**Date:** 2026-08-25
**Status:** CLOSED — successful negative result, now extended to a full sensor/pillar vetting sweep (§7)
**Scope of the NO-GO verdict:** a 5-minute-bar, transaction-cost-driven trading brain built directly on the 7D state vector OR any of the four candidate expert/pillar sensors (P1, P2, ORB, Map+Context) at their native evaluation horizons. Does NOT apply to: the state-vector engineering itself (preserved), the outer meta-controller (preserved, dormant), the hierarchical MPC→PI→P01D architecture design (preserved as a candidate, gated behind evidence), or any future strategy with actual gross edge that might reuse any of these.

---

## 1. What this thread was

Triggered by the owner's own architecture review of the live Chart Studies Monitor: a composite vote (e.g. "3 of 4 studies bullish → BUY") treats structurally different signal types — leading/projective (Ichimoku, if used correctly) and reactive/coincident (Bollinger, VWAP crosses) — as interchangeable ±1 votes, with no measure of conviction. Confirmed concretely: `chart_studies_indicators.ichimoku()` explicitly disables Ichimoku's classic forward (kijun-period) shift, making it a same-bar reactive check wearing a leading indicator's name.

The research question this thread was built to answer, stated precisely at the end: **does a causal, continuous, 7-dimensional state vector `x_t = [S, L_vwap, L_bb, M, ΔM, R, V]` contain measurable, economically tradeable information about what happens next — enough to justify building a controller (PID/PI) around it?**

## 2. Four discoveries (owner's framing, preserved verbatim)

**Discovery A — equal-weight voting was structurally unsound.** Confirmed empirically: same-bar Ichimoku's classification agrees with Bollinger 92% of the time and VWAP 91% of the time (8 symbols, 3 years) — it functions as a third "price vs. moving average" vote, not an independent read.

**Discovery B — correct continuous state representations contain more information than the discrete votes suggested.** The genuinely-projected Ichimoku read is measurably less redundant with Bollinger/VWAP (82–87% agreement) than the same-bar version — real evidence the projection injects independent information, even though it doesn't flip classifications outright in this sample.

**Discovery C — the momentum-acceleration intuition was wrong in this dataset, and it replicates.** Positive-but-*decelerating* momentum (M>0, ΔM≤0) outperformed positive-and-*accelerating* momentum (M>0, ΔM>0) at every tested horizon in the initial pass, and the effect **survives a corrected joint-date block bootstrap + Benjamini-Hochberg FDR correction at +1/+2/+3 bars (5/10/15 min)** on the 8-symbol development sample. **Frozen replication on the full 48-symbol universe (2,792,470 bar-observations, same 781 trading dates, identical method, no changes) confirms it holds generally, not as a development-sample artifact**: +0.313bp, +0.311bp, +0.285bp at +1/+2/+3 bars respectively (vs. +0.334bp, +0.370bp, +0.374bp on 8 symbols), all three still surviving FDR (p=0.0005, 0.0005, 0.0020). This directly falsifies the intuition that motivated the original D-term controller idea ("acceleration confirms conviction"). **ΔM is a market-state feature, not a PID derivative term — the two concepts are deliberately kept separate** (a PID D-term operates on the controller's own error/controlled-variable, a categorically different thing).

**Discovery D — the remaining information is too weak to overcome execution friction.** Even the statistically-established effects (+1/+2/+3 bar momentum ΔE) have gross favorable-state returns of **0.07–0.20bp**, against a real, project-validated round-trip cost of **~20.58bp** (10.58bp Zerodha MIS statutory schedule + 10bp slippage stress, reusing V10-C's own established convention) — cost consumes 2,500%–29,000% of the gross edge. Even removing the entire 10bp slippage stress component, the remaining ~10.58bp statutory-only cost still dwarfs the effect by ~50–150x. This is not a narrow miss.

## 3. Final status table

| Component | Status |
|---|---|
| Layer-2 causal state-vector engineering | **PASS / PRESERVE** |
| Momentum ΔE statistical phenomenon | **ESTABLISHED, REPLICATED** at +1/+2/+3 bars — 8-symbol sample and full 48-symbol universe (2.79M bars) both survive FDR, magnitudes match closely |
| Momentum economic edge | **FAIL** — 0.07–0.20bp gross vs ~20.58bp cost |
| Structure predictive evidence | **UNESTABLISHED** after correction (raw CI barely survived, fails FDR) |
| Bollinger incremental evidence | **UNESTABLISHED** (pooled-mean result did not survive block bootstrap) |
| 5-minute state → trading brain | **NO-GO** |
| Desired-exposure mapping | **DO NOT BUILD** |
| Inner PI/PID controller | **DO NOT BUILD YET** |
| META_CONTROL_EXPERIMENT_V1 (outer/meta controller) | **PRESERVE, dormant** |
| Further PID gain search | **STOP** |
| 48-symbol replication | Scientific archive only (§5) |

## 4. What is explicitly preserved, and why

**META_CONTROL_EXPERIMENT_V1** performed *better* through this process than the alpha layer. The frozen counterfactual analysis (`META_CONTROL_EXPERIMENT_V1_COUNTERFACTUAL_20260825.json`) shows all 36 tested gain configurations produced **positive net selectivity R** (+20.5R to +32.1R, median +29.3R) — the adaptive threshold systematically rejects more bad-R candidates than good-R ones. Reframed correctly: this is a **bad-trade suppression / strategy-health mechanism**, not an alpha generator, and remains a candidate reusable architecture piece for any future strategy that does possess real gross edge. Kept dormant, not deleted.

**The state-vector engineering itself** (`study_layer_v2_state_vector.py`, `study_layer_v2_indicators.py`) is sound by construction (16-item freeze gate, 362 passing tests, causality/scale-invariance/determinism all verified) and is preserved regardless of this specific economic-edge finding — the architecture correctly told us it doesn't work yet, rather than lying about it.

## 5. Frozen artifacts (this research thread, 2026-08-25)

| Artifact | Purpose |
|---|---|
| `study_layer_v2_indicators.py` + tests | Genuine Ichimoku projection, anti-lookahead suite |
| `study_layer_v2_state_vector.py` + tests | Layer 2 — 7D causal state vector, quality/continuity/schema contracts |
| `study_layer_v2_adaptive_threshold.py` | META_CONTROL_EXPERIMENT_V1 controller (frozen) |
| `backtest_adaptive_threshold_on_v10c.py` | Walk-forward backtest of the outer controller |
| `META_CONTROL_EXPERIMENT_V1_FROZEN_20260825.json` (+.sha256) | Frozen 36-config grid result (Rupee terms) |
| `meta_control_v1_counterfactual_analysis.py` | Counterfactual accept/reject R-attribution |
| `META_CONTROL_EXPERIMENT_V1_COUNTERFACTUAL_20260825.json` (+.sha256) | Frozen R-based selectivity result |
| `study_layer_v2_forward_information_study.py` | 8-symbol forward-outcome response surfaces |
| `study_layer_v2_bootstrap_significance.py` | Joint-date block bootstrap + BH-FDR |
| `study_layer_v2_cost_overlay.py` | Real Zerodha cost + slippage overlay |
| `study_layer_v2_momentum_replication_48symbol.py` | Frozen 48-symbol replication (no changes to method) |
| `STUDY_LAYER_V2_MOMENTUM_REPLICATION_48SYMBOL_FROZEN_20260825.json` (+.sha256) | Replication result — CONFIRMED, all 3 horizons survive FDR on 2.79M bars |
| `STUDY_LAYER_V2_REVIEW_TRACKER_20260825.xlsx` | Layer-2 freeze-gate checklist + review log |
| `PID_Controller_Architecture_Review_20260825_WITH_CLAUDE_COMMENTS.xlsx` | 20-point + 10-step architecture review, annotated |

## 6. What would need to change to reopen this thread

Not "more controller sophistication" — the gap is two orders of magnitude, no gain tuning closes that. Reopening would require one of:
- A genuinely different signal source with a materially larger gross edge (this state vector's job is done; it answered honestly).
- A structurally different cost regime (not applicable to MIS intraday at these position sizes).
- A longer holding-period strategy where 20bp of round-trip friction is a smaller fraction of the expected move (a different research thread entirely, not a parameter change on this one).

The outer meta-controller (§4) is the one piece of this thread ready to be picked back up immediately, whenever a strategy with real gross edge exists to apply it to.

---

## 7. Extension — the MPC/self-learning brain discussion, and the full pillar vetting sweep

After §1–6 closed the 7D-state-vector thread, a separate architecture discussion proposed layering MPC (model-predictive control) and self-learning input selection on top of the state vector, plus adding four "expert/pillar" sensors (P1 Trend, P2 Mean-Reversion, ORB, Map+Context) above the raw studies. The owner correctly redirected this: *"we were beginning to discuss the architecture faster than the evidence had earned it."* No amount of decision-layer sophistication (MPC, PID, self-learning, or any weighting scheme) can convert real-but-tiny information into a tradeable edge — a controller can extract information more efficiently, it cannot create information that isn't there. The MPC→PI→P01D hierarchical design and the self-learning walk-forward discipline discussed are architecturally sound and preserved as candidates, but placed on **HOLD** pending upstream evidence.

The resulting mandate: vet P1, P2, ORB, and Map+Context individually, at each one's own *native* evaluation horizon (not forced into the 5-min forward-return frame), with the same rigor as §1–6 — freeze the definition first, real execution model, real costs, joint-date block bootstrap, three-way verdict (ECONOMIC PASS / STATISTICAL-ONLY / FAIL).

### Result: all four fail.

| Candidate | Native evaluation | Source | Result |
|---|---|---|---|
| **P1 (Pillar I – Trend)** | Multi-day/trailing-stop lifecycle | Cited from existing, already-rigorous prior research (P02 Quant Lab, 2026-08-16/17) — not rebuilt | **FAIL** — combined Pillar I+II production portfolio, 445 real trades, avg trade return **−0.12%**, profit factor **1.00**. "NOT profitable net of realistic execution and friction — do not read this as a validated edge." (Resolved a stale-vs-current conflict between two prior artifacts via file timestamps before citing.) |
| **P2 (Pillar II – Mean Reversion)** | Native trade lifecycle / 2R framework | Same source as P1 (evaluated jointly in that research) | **FAIL** — same combined verdict as P1; isolated signal was already ~breakeven-to-negative before full risk-managed execution. |
| **ORB (Opening-Range Breakout)** | Intraday, same-session lifecycle | **Newly backtested for the first time ever** — `orb_shadow_observer.py` previously stated ORB "cannot honestly be tested" for lack of intraday data; the 1-min V10-C archive (acquired earlier this session) removed that obstacle | **FAIL** — 3,446 trades (8 symbols), gross R-multiple ≈ 0 (**−0.0089**, no raw breakout edge even before costs), net R-multiple **−0.2511** after real costs, 95% CI [−0.290, −0.217] excludes zero (p=0.0005). 69% of trades exit via forced EOD square-off rather than stop/target — price mostly chops after the breakout. Trade rule (stop = ORB low, target = 2R, 15:15 square-off) was a disclosed, non-optimized convention — no prior rule existed to freeze. |
| **Map + Context** | 5-min/intraday reclaim lifecycle | **Newly backtested** — reused the actual tested logic (`map_context_indicators.py`, 34/34 tests) and the live engine's real cost-netting dataclass unchanged, driven by a new historical-replay loop (the live engine's own polling class re-resamples its full accumulated history every poll — intractable over 3 years, so bar-feeding mechanics were rebuilt around the *same* strategy functions) | **FAIL** — 2,027 trades (8 symbols), net P&L **−Rs.42,895.56** total, mean **−Rs.21.16/trade**, 95% CI [−Rs.21.69, −Rs.20.61] excludes zero (p=0.0005). Win rate only 5.3%; 58.7% of trades exit via STOP. |

**A genuine mid-analysis catch, not silently smoothed over:** the first Map+Context bootstrap run produced a nonsensical mean R-multiple (~−2.85×10¹⁰) — traced to a real structural property, not a coding bug: `compute_stop_and_target`'s stop equals the reclaimed level itself with **no minimum-distance floor**, so a marginal reclaim (e.g. entry 908.45 vs. stop 908.44, one paisa apart) creates a near-zero-risk denominator that blows up the R-multiple ratio even though the rupee P&L per trade stays normal. **34.5% of all Map+Context trades have risk-per-share under 0.1% of entry price** — a real, previously-unknown fragility in the strategy's own stop rule, surfaced by this vetting, not fixed (freeze-first discipline: report, don't repair). The fix applied was to the *test statistic* (switched to raw net rupee P&L, matching what the live engine's own EOD statement already reports), not the strategy.

### Verdict

**Every technical-signal source examined across this entire research thread — the 7D state vector, Pillar I, Pillar II, ORB, and Map+Context — fails the same economic-cost gate.** Per the owner's own pre-committed decision rule: *"If all four fail the economic-cost gate, we do not combine five weak things and hope intelligence emerges. We go back upstream and search for genuinely new information."*

**MPC, self-learning input selection, hierarchical PI control, and the P1/P2/ORB/Map+Context sensor-fusion architecture all remain on HOLD.** Nothing here reopens that hold — none of the four candidates earned admission. The architecture designs discussed (hierarchical MPC→PI→P01D, provenance-declared expert sensors, walk-forward self-learning with challenger/incumbent gates) are preserved in full, unchanged, ready to be picked up the moment a signal source with real gross edge is found — reusing this exact vetting methodology to confirm it before any controller is built around it.

### Frozen artifacts (this extension)

| Artifact | Purpose |
|---|---|
| `orb_historical_replay.py` | First-ever ORB backtest — entry logic reused from `orb_shadow_observer.py`, trade-management rule newly specified and disclosed |
| `ORB_VETTING_RESULT_20260825.json` (+.sha256) | Frozen ORB result |
| `map_context_historical_replay.py` | First-ever Map+Context backtest — reuses `map_context_indicators.py` and `MapContextTrade` unchanged |
| `MAP_CONTEXT_VETTING_RESULT_20260825.json` (+.sha256) | Frozen Map+Context result (rupee P&L as the test statistic, R-multiple instability documented) |
| `P02_QUANT_LAB_20260816/P02_LAB_MASTER_REPORT.md`, `P02_PERFORMANCE_ANALYTICS.md` | Pre-existing, cited (not rebuilt) source of the P1/P2 verdict |

---

## 8. Extension — Pillar Information Decomposition V1 (the corrected research question)

The owner rejected §7's closing statement as **too broad**: "every currently tested standalone trading implementation failed" does not establish "every underlying pillar carries no information at all." Testing a pillar's *complete trading rule* (entry + native stop/target/exit) against a full round-trip cost conflates three different jobs a sensor could do — **ALPHA** (predicts a move large enough to justify initiating a trade), **EXECUTION** (small, reliable effect useful for timing a trade already authorized elsewhere — does not need to clear a full round trip because it doesn't create an extra one), **RISK/FILTER** (useful for avoiding adverse states even if "trade every occurrence" loses money), or **NO DEMONSTRATED INFORMATION**. The owner also identified that Map+Context's −₹21.16/trade result entangles three separable things (entry logic, stop geometry, exit logic) and only tests their sum, not the entry signal in isolation — and flagged the near-zero-stop-distance finding as a real, separate design defect (a structural invalidation level is not necessarily an appropriate risk-sizing denominator) worth its own review, independent of profitability.

**Built: `pillar_information_decomposition.py`** — strips each pillar's native exit entirely and asks the strategy-independent question, at the moment ORB's breakout or Map+Context's reaction-ENTRY fires: what does the subsequent price path actually look like? Forward return, MFE, MAE at multiple horizons, native stop distance recorded as descriptive only (never as an R-multiple denominator again). Same rigor: joint-date block bootstrap, Benjamini-Hochberg FDR, gap-aware eligibility.

**A real error caught and corrected before reporting it:** the first run classified large, FDR-significant MFE values as "CANDIDATE_ALPHA" — wrong. Testing MFE against zero in isolation is the wrong question; growing MFE/MAE with horizon is exactly what ordinary volatility produces regardless of any real signal. Rebuilt with the correct test — **asymmetry = MFE + MAE** (MAE signed negative, so this directly measures whether the favorable excursion exceeds the adverse one), bootstrapped per-event, not eyeballed from two separate point estimates.

### Result

**Every directional metric — `fwd_return` and `asymmetry`, every tested horizon, both pillars (3,075 ORB events, 11,936 Map+Context events, 8 symbols) — is NO_DEMONSTRATED_INFORMATION after FDR correction.** `P(favorable excursion reached before adverse)` is 53.4% (ORB) and 52.4% (Map+Context) — a coin flip. One metric (`asymmetry_2` for Map+Context, raw p=0.024) would have looked significant alone but fails once corrected for the 10-test family — a direct, concrete demonstration of why the correction matters, not a formality. MFE/MAE magnitudes are large (20–50bp) but symmetric — ordinary volatility, not edge.

**This is a stronger, more complete negative result than §7's, not a reversal of it**: it directly answers whether ORB's native exit was destroying real path information (no — the raw path itself is flat/symmetric) and isolates Map+Context's entry signal from its stop-geometry defect (the entry signal alone, independent of the flawed stop, still shows no directional information).

**What remains genuinely open, not concluded either way:** the RISK/FILTER role for both pillars — whether their presence usefully narrows tail outcomes or reduces drawdown on trades initiated by something else — was not tested here (that requires comparing outcomes *with* the condition against a baseline *without* it, a different test design). P1/P2 decomposition (separating their entry signal from risk-managed execution) is a natural next step, not yet built — it requires engaging the P02 lab's own codebase for signal-event extraction. The slow/cross-sectional alpha direction and the MPC-as-execution-overlay repositioning are unchanged from §7 — still open, still gated behind finding a real signal source first.

### Frozen artifacts (this extension)

| Artifact | Purpose |
|---|---|
| `pillar_information_decomposition.py` | Strategy-independent path decomposition, asymmetry-corrected classifier |
| `PILLAR_INFORMATION_DECOMPOSITION_V1_20260825.json` (+.sha256) | Frozen result — all directional metrics NO_DEMONSTRATED_INFORMATION after FDR |
