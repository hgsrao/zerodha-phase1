# R5 black-box audit: what each box actually does, and where it breaks

Line numbers refer to `revision2_external/orchestrator.py` on `diagnostic/entry-and-loss-decomposition`
(it carries passive research hooks; the sealed engine has the same logic a few lines earlier).

Scope: the sealed V2 configuration that produced Stage A/B (`governor_authority="full"`,
`governor_position_control="legacy"`, `closed_loop_mode="active_paper"`), Stage-B winner
(trial 7) parameters. Every claim below cites the code. "Measured" numbers come from the Stage-B
gate funnel (30 sessions, report SHA `9714f51a…`) or the block-1 rejection anatomy (5 sessions);
anything not yet measured is marked **to verify**.

## Summary

The engine does not trade the way its design describes. Six findings explain most of the result:

1. **The PA signal mixes direction with non-directional inputs.** Half its weight (volatility
   score, volume confirmation) has no sign, yet it is summed into the signed signal that sets
   BUY/SELL. This is the leading suspect for the 95% SELL book and for confidence being
   anti-predictive.
2. **One number, six thresholds.** PA confidence is filtered by six cut-offs. The calibrated
   one (`entry_confidence_threshold`, searched over 0.02–0.35) never binds; two fixed ones (0.43
   implied by ID's risk/reward test, 0.55 in Gate12) decide entry.
3. **Exits are decided by a rank, not by price.** FSRN is a percentile rank of confidence
   against the last 20 minutes, so it is roughly uniform by construction. With the exit
   threshold at 0.25 the conviction gate fires with about 42% probability per bar regardless of
   price, and it ignores the minimum hold. It made 47 of 54 exits in block 1 and 889 of 889
   conviction exits in Stage A.
4. **The PID loops have no authority.** The MPC PID pair is clamped to ±0.1 (at most 10% size
   change). The exit PID is advisory under full governor authority. The governor's inner PID
   output is only used as an on/off trigger, and the conviction gate overrides it (V3 included).
5. **Correctness bugs:**
   - Gate12 rejects about a third of valid candidates by floating-point rounding.
   - The HMM regime filter is fed a gappy price series.
6. **Economics.** Stops and targets are set from the 1-minute ATR, so 1R is about the same size
   as round-trip cost. Friction was measured at about 0.54R per trade, against a gross forward
   edge of the filled trades of about +0.15R at 15 bars.

Fixing 3–5 is necessary but will not by itself create an edge; 1 and 6 decide whether there is
one.

## Box by box

### PA — `revision2_external/indicators_talib.py`
| # | Finding | Evidence | Effect |
|---|---|---|---|
| PA-1 | `raw_signal` sums momentum and VWAP deviation (signed) with `volatility_score` and `volume_confirmation`, which carry no direction, at equal weights of 0.25. The sign of the sum is the trade side. | `:177`, `:181`, `:191-197`, `:225` | Above-baseline volatility pushes toward SELL; above-average volume pushes toward BUY, whatever the price did. Measured: births are 50/50, but SELL runs last longer (14.1 vs 10.8 bars) and reach confidence more often (43% vs 34%), giving 68% SELL among confident bars. **To verify** the cause with the new `pa_direction_from_directional_terms` telemetry. |
| PA-2 | `baseline_vol` is fixed once per symbol per orchestrator run, from the first 60 bars it sees. | `calibrate()` `:57-83`; called once at `:145-148` | The volatility score's sign drifts over a 5-session block, adding a persistent side tilt. |
| PA-3 | Quality-band multipliers make confidence non-monotonic: green ×1.1 (raw ≥ 0.75), amber ×0.85 (0.5–0.75), neutral ×1 (0.25–0.5), red ×0.5. Raw 0.55 → 0.47 < raw 0.49. | `:209-220` | Thresholds downstream select a band artefact, not "stronger signal". |
| PA-4 | A persistence bonus (×1.15 when 4 of the last 5 raw values share the sign) inflates confidence late in a move. | `:203-207` | Rewards chasing. Measured: edge is about 0 at a run's first bar but −0.18 to −0.28R on confident bars (15-bar edge against controls). |
| PA-5 | `entry_confidence_threshold` × 0.2 sets PA's direction activation. | `:225` | The calibrated parameter matters only here (see ID-2). |

### Chart studies — `revision2_external/composite_study_signal.py`
| # | Finding | Evidence | Effect |
|---|---|---|---|
| CS-1 | One of four votes is a 5,3,3 stochastic %K/%D crossover on 1-minute bars, which flips every few bars; Bollinger-middle and VWAP crosses are also fast. | `:122-138` | The studies' `direction` flips often. When it opposes the position, FSRN becomes 0 (GOV-4), so trades exit after 1–3 bars. Measured median hold: 3.5 bars. |
| CS-2 | Hit grading counts an unchanged price as "down". | `:290-294` | Small SELL-ward weight bias (minor). |

### ID — `revision2_external/regime_id_box.py`
| # | Finding | Evidence | Effect |
|---|---|---|---|
| ID-1 | Four checks in order: HMM stressed, red band (raw ≤ 0.25), confidence ≥ threshold, slippage, then risk/reward. The risk/reward test is `4c / 2(1−c) ≥ 1.5`, i.e. **c ≥ 0.4286**. | `:216-234`; registry: `id_reward_gain` 4, `id_risk_gain` 2, `min_risk_reward_ratio` 1.5 | The risk/reward test is a hidden confidence floor. Measured block 1: red band (1.98×) and risk/reward (2.25×) produce the ID side skew; HMM 1.09×. |
| ID-2 | `entry_confidence_threshold` was searched over [0.02, 0.35], entirely below 0.4286. | protocol `search_space` | As an ID gate it never changes the approved set. Optuna tuned a parameter with no effect there. |
| ID-3 | The HMM appends a close only when `evaluate()` runs, i.e. only on eligible bars (not while the symbol is held, not outside the window). | `_current_regime` `:136-138`; called from orchestrator `:1633` (and the warm-up at `:1450`) | Returns span gaps of many bars (after every trade, across sessions). This inflates volatility, so the series reads "stressed" more often (**to verify**). |
| ID-4 | "Stressed" is the higher-variance state of a 2-state HMM fitted to recent data. | `_refit` | It is a relative label, so it removes a roughly fixed share of bars (measured 29–35% of inputs) whatever the absolute risk. |

### MPC / PID — `revision2_external/pid_controller.py`
| # | Finding | Evidence | Effect |
|---|---|---|---|
| MPC-1 | Both PIDs measure confidence against its own rolling mean; output limits are ±`pid_integral_max_clamp` = ±0.1. | `:85`, `:186-201` | Size multiplier ≥ 0.9, stop/target scale ≥ 0.9, price nudge ≤ 1 bp. The 0.3 / 0.5 floors can never bind. Near no-op. |
| MPC-2 | Neither PID sees price, position or P&L. | `:186-201` | Open loop with respect to the market. |
| MPC-3 | Target = max(profit_mult × 1.1, 1.5 × stop_mult) × ATR. For trial 7: 1.37 < 1.78. | `:182-185` | `profit_target_atr_mult` never binds, so a second calibrated parameter is dead. |
| MPC-4 | ATR is the **1-minute** ATR (`signal.volatility × close`). | orchestrator `:1687` | 1R is about the same size as round-trip cost (ECON-1). |

### Governor entry — `revision5/governor.py`, `revision5/governor_authority.py`
| # | Finding | Evidence | Effect |
|---|---|---|---|
| GOV-1 | Conviction FSRN = min(PA rank, studies rank). Each rank is the percentile of the current confidence among the last 20 bars, so it is close to uniform on [0, 1] by construction. | orchestrator `_observe_conviction` `:669-680`; `side_aligned_conviction` | A rank discards absolute signal strength. P(both ≥ 0.6) ≈ 0.16, which matches the measured governor pass rate of 18–20% (block 1, both sides). The entry hurdle behaves like a random 1-in-5 filter. |
| GOV-2 | Overspeed comparator: admit when signed z ≤ −base_z + feedback − droop (base_z ≈ −1.9). | `evaluate_entry_request` `:601-680`, `dynamic_z` `:1009-1046` | Reasonable. Measured: per decision the governor is side-neutral (18.3% vs 19.8%; six blocks 19.7% vs 20.7%). |
| GOV-3 | Outer loop: error = 0.30R target − mean of the last 6 realised R, per bay; output clipped to [−0.45, +0.15]. | `register_trade` `:319-348`, `dynamic_z` | With most trades losing, the error stays positive and the offset sits at its clip. A saturated loop is no longer controlling (**to verify** from telemetry). |

### Governor position control (hold / exit)
| # | Finding | Evidence | Effect |
|---|---|---|---|
| GOV-4 | After the inner loop returns HOLD, `position_decision` exits if `fsr_selected < 0.25` (usually FSRN). There is **no minimum-hold check** on this path. | `governor_authority.py:369` (`position_decision`) | Per-bar exit probability ≈ 1 − 0.75² ≈ 42% from ranks alone, plus 100% whenever PA or the studies flip direction. Measured: 47/54 exits in block 1, 889/889 in Stage A; TITAN trades held 1–2 bars. |
| GOV-5 | Legacy inner loop: `control_u` is computed but used only as a binary trigger (`control_u ≥ exit_control`); the trailing floor is MFE − gap, independent of the PID. | `evaluate_position_control` `:802-930` | The "PID" is a threshold detector. Nothing is continuously modulated. |
| GOV-6 | V3 `PositionControlV3` replaces only the inner loop; the same FSR exit follows it. | `position_decision`: `inner` then `fsr` | Sealed V3 is overridden the same way (consistent with the paired bridge: −0.81R on trial 7). |

### Exit PID and final controller — `continuous_exit_controller.py`, `final_execution_controller.py`
| # | Finding | Evidence | Effect |
|---|---|---|---|
| EX-1 | Under full governor authority the stop used is the governor's; the exit PID's stop and its saturation exits are logged as `ADVISORY_EXIT_NOT_ACTUATED`. | orchestrator `:1134-1142`, `:1320`, `:1344` | Computed every bar, never used. |
| EX-2 | The final execution controller's entry veto and the entry-quality hold are skipped when `_governor_full`. | orchestrator `:1891`, `:1902` | Inactive in the configuration that was calibrated. |

### Sizing — `revision2_external/position_sizing_pyportfolioopt.py`
| # | Finding | Evidence | Effect |
|---|---|---|---|
| SZ-1 | Long-only max-Sharpe weights (bounds 0–1) on trailing 15-minute returns scale every trade, shorts included (weight / equal weight, capped at 1). | `:67-133`, `:196-209` | Shorts are sized up in stocks with strong recent *upside*. Side-blind. |

### Risk and pre-submit gates — `revision2/boxes.py`, `gates_framework.py`
| # | Finding | Evidence | Effect |
|---|---|---|---|
| RG-1 | Post-sizing profit floor: target profit must be ≥ cost × (1 + 1.976). | `evaluate_post_sizing` `:568-579` | Likely the main cause of the sized → risk-checked drop (SELL 7,369 → 2,356). **To verify** with the anatomy `risk` section. |
| RG-2 | Gate12 confidence: `decision.confidence < min_signal_confidence` (0.55, safety contract). | `gates_framework.py:204` | The binding entry threshold. Measured block 1: 66 of 66 BUY and 284 of 307 SELL pre-submit rejections. |
| RG-3 | **Bug.** Gate12 checks `rr < 1.5` where rr is recomputed from prices of a plan whose target was set to exactly 1.5 × stop (MPC-3). Floating-point rounding gives 1.4999…; simulation over typical prices rejects 33%. | `gates_framework.py:206`; orchestrator `:2014` | Measured block 1: 23 of 76 SELLs that cleared the confidence check (30%). A rejection by rounding error. |

### Economics — `revision2/transaction_costs.py`
| # | Finding | Evidence | Effect |
|---|---|---|---|
| ECON-1 | Per leg: brokerage min(₹20, 0.03%) + 0.00345% + STT 0.025% on the sell leg; slippage 5 bps per leg (`mpc_base_slippage_fraction`). Round trip ≈ 15–20 bps. | `leg_cost`; registry | With 1R ≈ 1.2 × 1-minute ATR, costs are a large fraction of R. Measured friction ≈ 0.54R per trade; gross edge of filled trades ≈ +0.15R at 15 bars. Structural loss before any controller acts. |

## The fix, in order

Each step is a separate, reviewable change on a research branch (sealed V2/V3 untouched), measured
on the discovery data (Stage A/B) with the existing tools before the next step. Nothing here is
judged on discovery data alone: once a configuration is frozen it must be tested on fresh data.

**Step 0 — correctness bugs (no strategy change).**
- RG-3: compare with a tolerance (`rr < min_rr − 1e-9`), or carry the planned ratio instead of
  recomputing it from prices.
- ID-3: feed the HMM every bar's close (update every bar; only *read* it at decision time).
- GOV-4: respect `min_hold_bars` on the conviction exit (or remove that exit; see step 1).

Re-run the funnel and anatomy to show the change in counts.

**Step 1 — give exits to one controller, with price feedback.** Run the exit ablation on the
same entries (task #13), as exit-only shadow arms:
- A: as sealed;
- B: legacy inner loop without the FSR exit;
- C: V3 without the FSR exit;
- D: bracket only (stop / target / max hold);
- E: fixed holds of 15 bars, 30 bars, and to the session close.

Report per-trade ΔR with session-cluster CIs. Keep the conviction score, if at all, for
*sizing*, not as an exit trigger.

**Step 2 — fix the signal's direction.** Remove `volatility_score` and `volume_confirmation`
from the signed sum; use them only to scale confidence. Make confidence monotonic (drop the band
multipliers). Recalibrate the PA baseline per session, causally. Then re-run the funnel and the
entry-edge audit: the side mix and the forward edge against controls must both be re-measured.
Pass criterion, set before looking: edge vs controls > 0 with the CI lower bound > 0 at 15 bars.

**Step 3 — one confidence threshold, and make it the calibrated one.** Collapse the six
thresholds (direction activation, red band, ID threshold, ID risk/reward, Gate12, FSRN hurdle)
into one explicit entry threshold. Remove the dead parameters (`entry_confidence_threshold` as an
ID gate, `profit_target_atr_mult` under the 1.5 floor) from calibration, or make them bind.

**Step 4 — economics.** Move the stop/target horizon to a slower ATR (e.g. 15-minute) so that
round-trip cost is ≤ 0.1R, or trade far less often. Make sizing side-aware (or drop the long-only
max-Sharpe derate for shorts).

**What to expect.** Steps 0–1 stop the engine from cutting trades at random and let a controller
actually control. Whether the system then makes money depends on steps 2 and 4. The current
measurements (gross edge about +0.15R at 15 bars, about 0 by 30, against about 0.5R of costs)
say it will not without them.

## Telemetry added for verification

`orchestrator._decide("id", …)` now also records PA's four components, the directional and
undirected parts, and `pa_direction_from_directional_terms` (`agrees` or `flips_to_X`). The
anatomy run prints its counts by side. If PA-1 is the cause, a large share of SELL decisions
will show `flips_to_BUY` or `flips_to_FLAT`.
