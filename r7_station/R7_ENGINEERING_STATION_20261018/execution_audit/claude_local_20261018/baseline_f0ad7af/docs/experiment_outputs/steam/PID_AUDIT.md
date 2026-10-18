# Auxiliary PID audit — no controller changes

Scope: the Step-5 external replay orchestrator and its native R5 plant, plus explicitly
identified legacy and shadow alternatives in revision2, revision2_external and revision5.
This is source inspection and isolated diagnostic probing, not a claim that every historical
script elsewhere in the repository participates in the current engine. The runtime probe is
`scripts/r5_pid_audit.py`; its JSON includes exact constructor paths/lines and library location.

## Findings

1. **Continuous actuators already exist outside the governor.** Studies PID outputs change
   vote weights. MPC PIDs change price/timing/stop-related plan quantities. The exit controller
   changes ATR trailing distances. They are not merely random binary hazards.
2. **Anti-windup and derivative handling are not uniform.** Library PIDs clamp their weighted
   integral term and output and differentiate the measurement; manual governor/AVR loops clamp
   an unweighted error accumulator but leave the intermediate PID sum unclamped. Their actuator
   commands are bounded downstream. Legacy BoundedPID averages error differences; library
   derivatives are single-step and unsmoothed. Rolling signal means are not derivative filters.
3. **A binary summary is mislabeled as sustained.** FinalExecutionController.exit_decision
   checks the single current `studies_clamped` boolean and emits
   `STUDIES_PID_SUSTAINED_LOW_CONFIDENCE`. That boolean is based on absolute output magnitude,
   so it also loses the sign distinguishing below-baseline from above-baseline confidence.
   This is a diagnostic/action-label concern. In the inspected orchestrator `final_exit` is
   logged; the actual separate saturation rule uses consecutive positive-saturation counts.
4. **Merit allocation has a normalization-bound issue.** DynamicBayLoadDispatcher clamps raw
   weights, normalizes them, then smooths/normalizes again. The final merit weights need not
   respect the original per-bay clamp. A reproducible isolated probe produces 0.522388 against
   a 0.35 ceiling after 100 favorable outcomes. The separate SectorDispatchController uses
   capped water-filling, so this does not by itself prove a breached actual dispatch ceiling.
5. **The double-gamma correction is already present.** progress() owns the exponent; expected_r
   and lower_bound_r consume it linearly. No additional duplicated gamma was found in this
   reference pipeline. This does not establish every exponent in unrelated research modules
   is redundant or erroneous.

None of these findings was patched, per the audit-only scope.

## Controller/consumer inventory

| Owner and source | Feedback and actuator | Bounds / anti-windup | Derivative / filtering | Binary consumers and authority |
|---|---|---|---|---|
| CompositeStudySignal, `revision2_external/composite_study_signal.py:260` and `:318` | Per-study delayed hit rate vs cross-study baseline; negative PID output raises the study's weight | Library output and weighted integral bounded by studies_pid_output_clamp; raw weight clamped 0.05–0.60 | Derivative on measurement, initial D=0; no explicit derivative smoothing | Composite direction and conviction subsequently meet entry/FSRN gates; the PID itself continuously adjusts weights |
| SimplePIDModelPredictiveControlBox, `revision2_external/pid_controller.py:85`, `:196` | Per-symbol confidence vs causal rolling mean; continuous plan modifiers | Library integral/output ±pid_integral_max_clamp; timing and tightness have downstream floors | Derivative on measurement, dt=1; pid_derivative_smoothing is consumed for bookkeeping but does not filter this library controller | Upstream ID admission and downstream safety/governor can still reject a candidate; this does not remove continuous plan actuation |
| ContinuousExitController, `revision2_external/continuous_exit_controller.py:241`, `:375`, `:474` | Separate PA and studies confidence tracks; min tightness changes ATR-based stop distance | Both PIDs integral/output ±clamp, tightness bounded, stop one-way | Derivative on measurement, dt=1, no derivative filter | Positive saturation for saturation_exit_bars yields a discrete exit. Under full governor authority these exits and this stop are advisory; governor stop owns actuation. Non-full active-paper saturation exits use existing close execution |
| FinalExecutionController, `revision2_external/final_execution_controller.py:56` | Combines path, stop movement, studies_clamped | No PID state; consumes bounded outputs | Not a derivative controller | Single-bar absolute saturation is labeled sustained; final_exit telemetry is not the separate persistent-exit implementation |
| BayTurbineClosedLoopGovernor outer, `revision5/governor.py:268` | Mean realized R -> PID -> dynamic_z feedback offset -> admission ceiling | Error integral ±runtime_integral_clamp; raw u not explicitly clamped; offset is clamped at runtime_dynamic_offset_min/max | Error difference from last_error=0; first-sample and setpoint kicks possible; no D filter | Entry comparator is discrete; a measured feedback-driven binary actuator still constitutes feedback |
| BayTurbineClosedLoopGovernor inner, `revision5/governor.py:647` | Reference-minus-measured R; position-policy mode modulates trailing gap after MFE activation | Error integral clamped; raw u unbounded, applied u clipped [0,pid_u_max], gap bounded, floor monotonic | Error difference, initial previous error zero; no D filter | Legacy mode uses error-envelope AND u gate; experimental mode uses the floor. Safety/target/max-hold rules remain discrete |
| BayExcitationAVR, `revision5/ccpp_protection_cubicles.py:598` | Bus voltage or MVAR error -> delta excitation -> loading/lot size | Error integral ±runtime_integral_clamp; raw delta_u unbounded; excitation command clamped excitation_min/max plus OEL/UEL | Error difference; initial kick and mode/setpoint discontinuity possible; no D filter | Undervoltage/overvoltage trips are protection, not PID hazard exits. PID delta is accumulated into excitation; positional-vs-incremental formulation remains a separate design question |
| DynamicBayLoadDispatcher, `revision5/ccpp_unified_plant.py:88` | Rolling realized R/downside score -> smoothed capital merit weights | Raw clamp then normalization can violate merit-weight bounds; no integral state | Not a PID; 0.85/0.15 smoothing | Availability and protection independently block admissions |
| ECSPlantSupervisor, `revision5/plant_control.py:223` | Grid/protection demand reference; exposure headroom used by admission caps | Demand bounded [0,1], restoration rate-limited; no integral state | Not a PID | Trips/no available bays force zero demand; supporting risk derate acts in sizing rather than being subtracted twice |
| SectorDispatchController, `revision5/plant_control.py:338` | ECS demand, merit weights, bay availability -> per-bay references | Capped water-filling; infeasible demand explicitly reported | Not a PID | Unavailable bays receive zero; exposure/admission caps are downstream constraints |
| Portfolio risk / entry quality, `revision2_external/closed_loop_control.py:430`, `closed_loop_v2.py:49` | Exposure soft/hard budget derate; Bayesian completed-outcome probability | Bounded derates, no PID accumulator | Not PIDs | Entry quality can return a discrete approval; do not invent PID defects in a probability gate |
| ContinuousExitControllerWithGrid, `revision2_external/continuous_exit_controller_with_grid.py:100` | Alternative grid-aware trailing controller | Library PID integral/output limits plus tightness bounds | Measurement derivative, no explicit filter | Not imported by the inspected Step-5 orchestrator; do not attribute its behavior to this replay |
| BoundedPID / legacy MPC, `revision2/boxes.py:36` | Rolling error window and smoothed error differences -> adjustment | Window sum clamped; weighted output itself is not clamped inside BoundedPID | Moving-average D, first sample relative to zero | Legacy alternative, not the external orchestrator's SimplePID MPC |
| CurveEntryPidShadow, `revision2_external/curve_entry_pid_shadow.py:28` | Smooth phase/amplitude/frequency quality -> suggested derate | Library weighted integral/output [0,0.75] | Measurement derivative, no D filter | Separate synchronized boolean; shadow workflow, not governor execution authority |
| StudyEntryShadowLedger, `revision2_external/study_entry_shadow.py:73` | Completed shadow outcome probability vs cost-aware required probability -> suggested derate | Library weighted integral/output [0,0.5] | Measurement derivative, no D filter | Shadow-only; no order/size/stop authority in the main replay |

The studies weight bounds above describe raw weights. The normalized effective share of one
study can exceed 0.60 (e.g. 0.60/(0.60+3*0.05)=0.80). Do not interpret MAX_WEIGHT as a bound
on normalized share without a separate constrained-normalization algorithm.

## Numerical checks and interpretation

The installed simple_pid source explicitly clamps `_integral = clamp(_integral, output_limits)`
and final output. It stores the Ki-weighted integral contribution, not a separately exposed
raw sum of errors. A source comment in pid_controller.py describing unbounded accumulation
is inaccurate for this installed version. A test with 100 same-signed errors holds the weighted
integral at 0.5 under ±0.5 limits. First-sample D and a setpoint-only-change D are both zero.
These bounds prevent unbounded controller state; they are not evidence of back-calculation
against every downstream actuator's saturation. No controller is being declared tuned/stable
from these local checks.

## Topology and scope retained

All five canonical bay mappings continue through SYMBOL_TO_BAY, the native bay governor,
protection snapshot and plant admission interfaces. This task only resolves a per-bay policy
at replay construction. It does not alter trips, cooldowns, dispatch, runtime gain schedules,
AVR behavior or droop. Existing droop remains GTG 0.04, CSTG 0.055 and BPSTG 0.075.

The supplied five profiles have identical values. They are user-specified experimental inputs,
not independently calibrated sector-volatility/inertia estimates. Existing different bay gains
still produce different PID trajectories. No auxiliary hardening proposed earlier in the chat
was applied after the user narrowed that work to an audit.

Runtime parameter schedulers (`dynamic_parameter_controller.py`, `revision5/dynamic_parameters.py`) calculate gain profiles from environment/stress rather than integrating a PID error. Replay begin_bar still lacks the scheduled environment; FIXED gain telemetry should not be described as demonstrated adaptive tuning.
