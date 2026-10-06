# R5 source and authority audit

**Is the R5 system complete?.** No. Components, wiring, authority and durable recovery are not one proven end-to-end system. See Findings and Wiring.

**Primary source.** /home/srinivas/projects/zerodha-r5-governor-refactor

**Branch / committed base.** feature/engine-ab-handoff-lifecycle / 411712afaecaeb44a6899a12db94ac547fef0186

**Working-tree qualification.** Snapshot includes modified and untracked integration code. It is not identical to commit 411712a. Inventory hashes identify the precise reviewed bytes.

**Code coverage of this report.** 149 selected first-party files; 39340 source lines; 1114 function/method definitions. All selected lines appear in code sheets; all definitions have semantic review entries.

**Selection boundary.** R2/R3/R4/R5 engine families and selected execution dependencies, plus explicitly separate Nautilus and Claude comparisons. Not every repository script, test body, third-party library or data file.

**Test evidence.** 222 targeted unit/component tests passed; 111 warnings. This is not statement coverage, full multi-day replay, broker certification or evidence that every reviewed definition executes.

**V3 inclusion.** The active external runner uses SimplePIDModelPredictiveControlBox and ClosedLoopSupervisor. A name containing V3 or Revision3 does not mean the sealed V3 controller implementation is loaded. See Controllers and historical code sheets.

**Current execution.** Standard candidate runner constructs an offline paper orchestrator; combined_cycle_runtime and paper_journal are optional and are not supplied by that construction path.

**Overnight protection.** Paper contingent protection is simulated. Persisting a stop description does not create a broker-held protective order or prove overnight protection.

**How to read.** Code sheets: exact source block on the left, explanation beside it. Function Index gives inputs, outputs, state, authority and connections. Inventory gives file hashes and working-tree status. Historical/alternate sheets are comparisons, not claims of active R5 wiring.

**Review method.** Source reading by the primary reviewer and three delegated reviewers; independent Claude adversarial review. Static code findings are separated from tests and proposed checks. No engine changes made for this audit.

**Independent review limits.** Claude findings are hypotheses until checked against source. The reviewer received a source-only snapshot without test files; its claims that tests do not exist apply only to that supplied snapshot.

**Artifacts preserved.** No holdout data, frozen trading parameters or raw reference outputs were changed by report generation. Source snapshot and original JSON reviews are retained beside this workbook.

## Source-backed findings and solutions

### N01 — HIGH

Ordinary paper A positions omit product but combined-cycle reconcile requires explicit MIS. Adapter default MIS used by ensure_protection/snapshot does not populate get_position dict.

Evidence: `runtime/operating_mode.py:79-108; revision5/combined_cycle_runtime.py:47-58; revision2_external/paper_execution.py:57-67`.

Consequence: Optional combined-cycle first management bar rejects a normal broker fill unless fixture/caller injects product.222 passing tests do not demonstrate this path.

Required solution/check: Add product at fill lifecycle boundary and an actual orchestrator one-fill+nextbar fixture; no change made.

### N02 — HIGH

After CNC conversion and close,product remains CNC in reused symbol dict; new A/MIS fill contingent protection rejects.

Evidence: `runtime/operating_mode.py:87-108; revision2_external/paper_execution.py:38-43; revision2_external/paper_execution.py:64-67; revision5/combined_cycle_runtime.py:34-37`.

Consequence: Same-symbol B close then A re-entry cannot complete registration under frozen implementation.

Required solution/check: Test broker conversion→flat→refill with actual adapter; define new position product explicitly.

### N03 — HIGH

Combined-cycle direct restore only merges open ownership/trades/B receipts and trade sequence; fleet outer/inner governors,merit,ECS,drawdown,symbol cooldowns and completed-close receipts are not restored.

Evidence: `revision5/combined_cycle_runtime.py:61-83; revision5/paper_state_journal.py:98-138`.

Consequence: Restarted plant can differ from uninterrupted feedback and protection; journal replay is a separate fresh-engine mechanism.

Required solution/check: Design atomic portfolio/controller checkpoint with schema,source/config identity and coherent restore; verify interrupted vs uninterrupted equivalence.

### N04 — HIGH

Combined-cycle close stores old protection with CLOSED record but does not record postclose broker snapshot or completed P&L/feedback state.

Evidence: `revision5/combined_cycle_runtime.py:39-41; revision2_external/orchestrator.py:970-1018`.

Consequence: Most recent stored per-position broker snapshot may predate another position close; open-snapshot restore can revive stale broker positions or totals.

Required solution/check: Checkpoint complete portfolio and close receipts at same authoritative boundary; keep completed ledger and broker state coherent.

### N05 — HIGH

Already-terminal conversion resolve ignores any subsequent outcome without comparing status/product/qty; late conflicting ACK after TIMEOUT/REJECTED is silently ignored.

Evidence: `revision5/handoff_manager.py:76-117`.

Consequence: Adapter may own CNC while durable state says A/MIS; repeated receipt not proven idempotent under contradiction.

Required solution/check: Reject conflicting terminal receipt and require adapter reconciliation; test timeout/ACK and close/ACK races.

### N06 — HIGH

Conversion quantity only requires positive integer and ACK equals request,not authoritative stored trade quantity; store does not validate record/trade/protection coherence.

Evidence: `revision5/handoff_manager.py:55-73; revision5/combined_cycle_store.py:70-87`.

Consequence: Partial conversion or inconsistent protection payload can pass durable ownership transition until later runtime reconciliation.

Required solution/check: Require exact durable quantity/side/symbol/stop/target consistency before write/ACK; protect partial fills explicitly.

### N07 — HIGH

Authoritative close feedback receipt is in-process PENDING→DONE only; partial failure leaves PENDING and blocks all retry after one feedback branch may already update.

Evidence: `revision2_external/orchestrator.py:855-878; revision5/combined_cycle_runtime.py:39-41`.

Consequence: Exactly-once cross-restart feedback not implemented; interrupted merit/governor update can become permanently inconsistent.

Required solution/check: Persist per-consumer close receipts and transactional replay/reconciliation of unfinished feedback.

### N08 — MEDIUM

Merit dispatcher clamps raw targets before renormalization; normalized target and smoothed weights can exceed configured max_ceiling.

Evidence: `revision5/ccpp_unified_plant.py:122-206; revision5/plant_control.py:351-401`.

Consequence: Internal merit max_ceiling is not a hard final allocation bound. Downstream water-fill applies ceiling on its references.

Required solution/check: Label weight law correctly and audit capacity law separately; use bounded-simplex allocation if weights themselves require strict envelope.

### N09 — MEDIUM

Native bay direct admission is BUY-only: governor called without side and stop/target always below/above price; in-flight adverse check uses price<entry without side.

Evidence: `revision5/ccpp_unified_plant.py:888-1062; revision5/ccpp_protection_cubicles.py:867-898`.

Consequence: Direct native SELL path is not symmetric. External side-aware governor entry is separate and avoids treating this as universal source of observed bias.

Required solution/check: Declare native side scope and add side argument/adverse tests before direct SELL use.

### N10 — MEDIUM

Native bay direct position_control omits position_id,so same-bay calls share None inner PID state. External full governor path uses explicit trade ids.

Evidence: `revision5/ccpp_unified_plant.py:1065-1170; revision5/governor.py:648-906; revision2_external/orchestrator.py:709-808`.

Consequence: Concurrent direct native positions can contaminate path I/error/floor. Not claimed for external keyed path.

Required solution/check: Pass durable position identity through native API and test simultaneous same-bay positions.

### N11 — MEDIUM

HRSG allocation and physical startup/machine scans are implemented hooks but no external orchestrator invocation of balance/return history/machine scan/synchronization exists in snapshot.

Evidence: `revision5/ccpp_unified_plant.py:1850-1978; revision5/ccpp_unified_plant.py:2056-2246; revision2_external/orchestrator.py:364-375`.

Consequence: Object construction is partial integration; no evidence physical analogue loops or HRSG recovery actively alter external replay.

Required solution/check: Add mode-labelled executable call map; do not claim full plant hookup from imports/install alone.

### N12 — MEDIUM

Fleet dispatch chain has no fleet PID: ECS is immediate cut/fixed restoration,merit is closed-R score smoothing,sector dispatch water-fill and governor admission capacity cap.

Evidence: `revision5/plant_control.py:230-322; revision5/plant_control.py:347-401; revision5/ccpp_unified_plant.py:122-206; revision5/governor.py:239-266`.

Consequence: Capacity feedback exists through exposure subtraction but requested fleet e/I/D/u telemetry cannot describe these units as PID.

Required solution/check: Retain precise controller contracts; any genuine fleet PID requires explicit loading setpoint,measured exposure,antiwindup and actuator design.

### N13 — MEDIUM

Several standalone definite/differential/distance relay paths omit finite measurement/negative-time validation; NaN can bypass trip comparisons.

Evidence: `revision5/relay_coordination.py:273-376; revision5/relay_coordination.py:389-432; revision5/relay_coordination.py:455-516`.

Consequence: Invalid sensor data may yield NORMAL or distort timers in standalone analogue relays. Not proven loaded in trading loop.

Required solution/check: Validate sensor/time/settings and failclosed input-unavailable; test NaN,negative dt,duplicate zones.

### N14 — LOW

Engine B restore validates policy/time/highwater/count but trusts nested cached decision and session metadata schema.

Evidence: `revision5/engine_b_management.py:50-59; revision5/engine_b_management.py:79-85`.

Consequence: Malformed restart receipt can inject cached action/stop on duplicatebar or fail late rather than at restoration.

Required solution/check: Validate complete versioned per-position state including finite stop/R,decision enum and immutable risk/identity association.

### N15 — MEDIUM

Paper journal is replay audit not general live recovery: bind requires fresh empty offline engine and checkpoint recomputes sequence from cursorzero.

Evidence: `revision5/paper_state_journal.py:98-138; revision5/paper_state_journal.py:151-183`.

Consequence: Direct combined-cycle restored nonempty broker cannot bind this journal; no shared commit boundary exists.

Required solution/check: Document mutually different recovery modes and prove portfolio restart with one explicitly selected contract.

### N16 — LOW

Native engine A interlocks14:30 entry cutoff and15:15 squareoff intent differ external force-close config and lifecycle A clocks.

Evidence: `revision5/engine_state.py:42-43; revision5/engine_state.py:77-103; revision2_external/orchestrator.py:1112-1119`.

Consequence: Two clocks can diverge if native callbacks and external lifecycle assumed equivalent.

Required solution/check: Define one mode-specific clock owner and record effective values in runtime receipts.

### N17 — MEDIUM

Physical machine models installed on every bay are engineering analogues with seconds/Hz/pu,not market-entry PID simulation; gas startup hold branch does not mutate sequencing.

Evidence: `revision5/machine_archetypes.py:22-24; revision5/ccpp_unified_plant.py:768-885`.

Consequence: Presence of machine objects does not prove turbine-style loading control responds to market/broker feedback.

Required solution/check: Keep physical vs trading units separated in architecture and verify explicit adapters before claiming shared closed loop.

### SIG-001 — HIGH

Frozen studies implementation still treats exactly unchanged grading-horizon prices as a SHORT hit and LONG miss.

Evidence: `revision2_external/composite_study_signal.py:287-299`.

Consequence: Directional reward bias propagates into relative hit-rate weights, study conviction and governor percentile inputs. Prior isolated fix commit must not be assumed loaded here.

Required solution/check: Choose neutral 0.5 grading in a separately reviewed change; prove equal LONG/SHORT flat grades and replay without other parameter changes.

### SIG-002 — HIGH

V3 research controller is not the instantiated MPC; default orchestrator explicitly creates SimplePIDModelPredictiveControlBox.

Evidence: `revision2_external/closed_loop_v3.py:119-152; revision2_external/orchestrator.py:44-58; revision2_external/orchestrator.py:250-255`.

Consequence: Tester contracts and test receipts do not establish live/default deployment of a V3 PID trader.

Required solution/check: Maintain separate implementation/import/authority columns; integrate only with explicit mode and actuator contract.

### SIG-003 — HIGH

PA signed signal adds unsigned volatility and volume terms to signed momentum/VWAP; sign reversal does not negate the full formula.

Evidence: `revision2_external/indicators_talib.py:174-199`.

Consequence: Magnitude confidence and sign symmetry are different properties. Generated symmetric PA source is not what the frozen orchestrator imports.

Required solution/check: Treat symmetric generated module as experiment-only until import/hash evidence; mirror-transform fixtures must cover all components.

### SIG-004 — MEDIUM

ID applies synthetic confidence RR before MPC computes actual price geometry and independent Gate12 safety checks.

Evidence: `revision2_external/regime_id_box.py:207-234; revision2_external/pid_controller.py:179-188; revision2_external/orchestrator.py:407-411`.

Consequence: Multiple entry hurdles can remove most candidates despite a valid ATR stop/target; PA-only patch does not neutralize this synthetic RR.

Required solution/check: Record sequential reason accounting by side and describe interventions exactly; do not equate RR admission with physical target/stop RR.

### SIG-005 — MEDIUM

Configured pid_derivative_smoothing is only traced by active SimplePID MPC, not applied.

Evidence: `revision2_external/pid_controller.py:165-172; revision2_external/pid_controller.py:190-205`.

Consequence: Parameter-consumption receipts can overstate functional use. Single-step D may respond to discrete confidence/rank jumps.

Required solution/check: Mark trace-only parameter; add actual derivative filter only through isolated controller specification and tests.

### SIG-006 — HIGH

Continuous exit PID ratchets and saturation are advisory when governor_authority is full.

Evidence: `revision2_external/orchestrator.py:1141-1158; revision2_external/orchestrator.py:1328-1338`.

Consequence: A passing exit PID test cannot establish actual discretionary exit ownership. Governor decision and hard lifecycle/protection paths remain decisive.

Required solution/check: Test mode-specific actuator matrix and trace winning exit authority for each actual trade.

### SIG-007 — MEDIUM

Alternative grid exit prototype open_position supplies initial_stop_price to a dataclass lacking that field.

Evidence: `revision2_external/continuous_exit_controller_with_grid.py:37-51; revision2_external/continuous_exit_controller_with_grid.py:157-165`.

Consequence: Calling this prototype opening path raises TypeError. It is not default imported, so no current-default impact is inferred.

Required solution/check: Fix dataclass constructor consistency before any integration and test opening both sides.

### SIG-008 — MEDIUM

Grid prototype compares NIFTY to itself and its documented tightening multiplier increases stop distance.

Evidence: `revision2_external/continuous_exit_controller_with_grid.py:122-136; revision2_external/continuous_exit_controller_with_grid.py:138-155; revision2_external/continuous_exit_controller_with_grid.py:224-240`.

Consequence: Self-comparison cannot measure stock/grid synchronization; 1.5 widens candidate ATR stop distance while comments say tighten. Existing ratchet does not loosen stored stop but proposed tightening is weaker.

Required solution/check: Keep prototype disconnected; use actual stock series and settle multiplier sign/units with mirrored examples.

### SIG-009 — MEDIUM

Curve entry quality substitutes 1.0 for zero amplitude/frequency errors using Python truthiness.

Evidence: `revision2_external/curve_entry_pid_shadow.py:44-54`.

Consequence: Perfect amplitude/frequency matching gives zero quality and a positive PID derate even when synchronized is true; shadow timing telemetry contradicts range flag.

Required solution/check: Use explicit None handling; test exact equality and near-zero errors.

### SIG-010 — MEDIUM

Both-stop-and-target shadow outcome label says stop precedence but execution falls through to close.

Evidence: `revision2_external/study_entry_shadow.py:154-184`.

Consequence: Shadow costs/net feedback may be optimistic or inconsistent for ambiguous bars; does not modify production broker.

Required solution/check: Apply conservative stop fill or exclude ambiguous outcome consistently; cover gap and both-hit side cases.

### SIG-011 — MEDIUM

FinalExecutionController uses any absolute studies clamp as sustained low-confidence signal.

Evidence: `revision2_external/final_execution_controller.py:62-74; revision2_external/continuous_exit_controller.py:474-488`.

Consequence: Telemetry recommendation can claim sustained deterioration without sign or consecutive-bar evidence; actual saturation helper has a different contract.

Required solution/check: Use the actual positive saturation count/reason in the final recommendation and distinguish single clamp versus persistence.

### SIG-012 — MEDIUM

Entry PID changes market reference with the same sign for BUY and SELL rather than selecting a later entry bar.

Evidence: `revision2_external/pid_controller.py:190-230; revision2_external/orchestrator.py:1679-1683`.

Consequence: A lower reference price helps BUY and harms SELL relative to market, and timing_multiplier is a size derate, not order waiting. Paper adjustment is not a broker fill guarantee.

Required solution/check: Specify entry actuator units and side semantics; test price realism separately from entry confidence sizing.

### SIG-013 — MEDIUM

Frozen target fit does not enforce its provided seed timestamp cutoff inside the method.

Evidence: `revision2_external/dynamic_target_setpoint.py:32-51`.

Consequence: Stored seed_start/end labels alone do not prove causal training selection. Caller must supply pre-cutoff completed trades.

Required solution/check: Audit caller filtering and hash seed input; add explicit cutoff validation before later promotion.

### SIG-014 — MEDIUM

Intraday PnL shadow stops accepting outcomes after a halt/target lock and has no day reset method.

Evidence: `revision2_external/intraday_pnl_setpoint_shadow.py:32-50`.

Consequence: Outstanding research trade outcomes after lock can leave net PnL accounting stale; reusing instance across sessions preserves prior state.

Required solution/check: Separate accounting from admission latch and define explicit session reset before any production use.

### LEG-001 — HIGH

revision3 MasterControlSystem PID evaluator is explicitly a no-op returning no-exit even when PID is enabled.

Evidence: `revision3/master_control_system.py:253-288`.

Consequence: Cannot supply claimed three-layer continuous exit controller. This prototype is distinct from external R5 controllers.

Required solution/check: Mark prototype unavailable; define and integrate actuator contract before considering this path. No implementation changes in this audit.

### LEG-002 — HIGH

Master decision wrapper supplies a one-element stock array; macro Hilbert phase gate permits insufficient (<63) history.

Evidence: `revision3/master_control_system.py:339-345; revision3/macro_grid_synchronizer.py:151-155`.

Consequence: Phase synchronization can report successful without a measurable stock phase in this wrapper.

Required solution/check: Require independently timestamp-aligned causal stock/index windows and an explicit insufficient-history disposition.

### LEG-003 — HIGH

Revision3ProtectedSupervisor checks only preflight and substitutes healthy telemetry/zero exposure when absent; engine exceptions return {}.

Evidence: `revision3/integration_supervisor.py:118-173; revision3/portfolio_orchestrator.py:63-88`.

Consequence: Preflight wrapper does not provide continuous measured plant safety or authoritative close receipts. Empty report obscures fault provenance.

Required solution/check: Keep separate from real measured protection; require caller-sourced telemetry and structured failure receipts for any future use.

### LEG-004 — MEDIUM

Revision3SafetyPanel master_reset clears lockout without validating external safety invariants.

Evidence: `revision3/safety_panel.py:43-60`.

Consequence: Atomic lock is present, but reset policy claimed in docstring is not implemented.

Required solution/check: Define measured-reset preconditions and operator receipt before operational use.

### LEG-005 — MEDIUM

Legacy grid volatility denominator is calculated from entire loaded NIFTY history rather than the causal prefix.

Evidence: `revision2/grid_synchronization.py:61-101`.

Consequence: If future bars are loaded, voltage-frequency phase decisions include future-dependent normalization. This module is not instantiated by listed vanilla orchestrators.

Required solution/check: Use prefix-only baseline if reviving legacy grid; establish timestamp alignment and missing-data policy.

### LEG-006 — HIGH

Revision4 PipelineAdapter uses undefined entry_cost_pct/fixed_cost, creates BUY-only plans, and does not veto grid_sync=False.

Evidence: `revision4/pipeline.py:256-269; revision4/pipeline.py:303-311; revision4/pipeline.py:327-352`.

Consequence: Earlier ReplayEngine path can suppress failures as rejected candidates and cannot support symmetric end-to-end trading. Authoritative sealed validator uses different adapters.

Required solution/check: Label this earlier path non-authoritative and quarantine from system completeness claims; repair only under a separate approved scope.

### LEG-007 — HIGH

Revision4 replay_engine processes symbols serially and submits pending orders without any broker.try_fill_order call in the replay methods.

Evidence: `revision4/replay_engine.py:117-241; revision4/replay_engine.py:243-266`.

Consequence: Order counts do not establish completed fills/trades or shared chronological portfolio execution in this prototype.

Required solution/check: Use authoritative timestamp validator; mark old ReplayEngine unsuitable as acceptance evidence.

### LEG-008 — HIGH

ten_box_orchestrator_clean is a pass-only scaffold for most advertised boxes despite header claims.

Evidence: `revision4/ten_box_orchestrator_clean.py:151-193; revision4/ten_box_orchestrator_clean.py:306-333; revision4/ten_box_orchestrator_clean.py:419-458`.

Consequence: Defined interfaces and instantiated classes are not ten operational boxes. run_replay reports INCOMPLETE.

Required solution/check: Do not count scaffold boxes as delivered implementation; retain as interface documentation or retire explicitly.

### LEG-009 — MEDIUM

Revision4 box_adapters computes pid_info but does not apply entry_timing_multiplier to PositionManager sizing.

Evidence: `revision4/box_adapters.py:173-199; revision2/boxes.py:420-450`.

Consequence: MPC entry PID is not completely disconnected: reference price and geometry still change; sizing advice specifically has no actuator in this callback path.

Required solution/check: Document per-path actuator differences; separately approve an isolation fix if desired, without editing sealed baseline.

### LEG-010 — HIGH

Revision4 exit callback sets peak_equity and current_equity to the same snapshot mark.

Evidence: `revision4/box_adapters.py:313-337; revision4/box_adapters.py:375-390`.

Consequence: Its local drawdown/daily-loss liquidation calculations are always zero. Other presubmit gates/ledger safeguards exist, so this is not proof all protection is absent.

Required solution/check: Trace historical peak/day baseline into this specific exit protection contract in a future approved change.

### LEG-011 — MEDIUM

ProperGateEvaluator discards adjusted quantities returned by drawdown/lambda derating gates.

Evidence: `revision4/gates_proper.py:295-302`.

Consequence: Gate pass/fail is connected, but quantity reductions do not flow into actual order size through this wrapper.

Required solution/check: Record gate-adjusted quantity and propagate explicitly before reservation/submission if changing this sealed path is authorized.

### LEG-012 — MEDIUM

ProperGateEvaluator only sets slippage in SafetyGateConfig and assumes healthy feed/broker; duplication input is always False.

Evidence: `revision4/gates_proper.py:63-89; revision4/gates_proper.py:128-145; revision4/gates_proper.py:303-317`.

Consequence: Several gate thresholds come from gate dataclass defaults rather than effective registry config; historical placeholders do not prove live health.

Required solution/check: Expose exact policy sources and real runtime inputs; keep historical gate claims bounded to replay context.

### LEG-013 — MEDIUM

Revision4 ledger uses fixed 5 slots, 2x exposure and abs(daily_pnl)>2000 reconciliation threshold.

Evidence: `revision4/portfolio.py:59-77; revision4/portfolio.py:260-285`.

Consequence: Positive profits beyond2000 also fail the absolute daily-P&L check; parameter changes do not necessarily change all portfolio limits.

Required solution/check: Document immutable safety policy versus accidental literal; do not silently tune sealed constants.

### LEG-014 — MEDIUM

PortfolioSnapshot unrealized_pnl is equity minus cash minus starting_cash minus realized_pnl.

Evidence: `revision4/portfolio.py:291-310`.

Consequence: This is not the usual signed marked P&L relative to position entry; downstream reliance requires explicit reconciliation. Marked equity calculation itself is separate.

Required solution/check: Compare snapshot unrealized field to authoritative signed position marks; capture a defect fixture before any approved repair.

### LEG-015 — MEDIUM

DatasetValidator seal uses literal HEAD/v1_68_params/baseline provenance and fixed August2024 dates.

Evidence: `revision4/dataset_seal.py:74-88`.

Consequence: CSV hashes are actually computed, but those code/config labels do not identify immutable real blobs.

Required solution/check: Distinguish measured dataset identity from placeholder provenance; use resolved hashes in future manifests.

### LEG-016 — MEDIUM

CanonicalConfigBuilder.validate_config skips numeric bounds if either bound is zero.

Evidence: `revision4/canonical_config.py:99-108`.

Consequence: Zero-minimum parameter bounds may not be checked in this older helper; full canonical payload validation is separate.

Required solution/check: Use canonical validation rather than helper for production decisions; add zero-bound fixture if revisiting.

### LEG-017 — LOW

Calibration _trade_pnl uses trade.get(net_pnl, trade[pnl]); Python evaluates default eagerly.

Evidence: `revision2/calibration_supervisor.py:121-129`.

Consequence: Net-only trade dictionaries raise KeyError despite intended fallback semantics; current legacy trade dictionaries usually include both.

Required solution/check: Use explicit presence check in an approved helper repair; do not infer current reports fail without payload evidence.

### LEG-018 — MEDIUM

Older in-house single-symbol and portfolio orchestrators use wall-clock fallback on malformed timestamps and carry synthetic ID RR to final gates.

Evidence: `revision2/orchestrator.py:151-155; revision2/orchestrator.py:444-453; revision2/portfolio_orchestrator.py:515-529`.

Consequence: Malformed-time replay can lose determinism; gate RR may reflect confidence proxy rather than final target/stop geometry in these paths. External R5 has its own separate geometry gate.

Required solution/check: Require explicit invalid-time disposition and identify each path RR source before comparing gate evidence.

### LEG-019 — MEDIUM

Sealed V3 validator partitions by session and explicitly EOD-flattens remaining positions.

Evidence: `revision4/validate_48symbol_sealed.py:119-147; revision4/validate_48symbol_sealed.py:220-246`.

Consequence: This sealed V3 intraday contract deliberately has no overnight Engine B lifecycle; absence is design scope, not evidence of broken broker conversion.

Required solution/check: Preserve sealed reference; implement combined cycle as opt-in separately, never relabel this validator as an overnight plant.

### LEG-020 — LOW

Empirical entry-probability validator rejects signature buckets with fewer than20 historical outcomes.

Evidence: `revision4/entry_probability_validator.py:124-151`.

Consequence: An unseeded wrapper cannot bootstrap accepted trades; zero-loss claims are unsupported empirical thresholds.

Required solution/check: Label experimental, identify source/causal boundaries of seed outcomes, and avoid interpreting win fractions as guaranteed forecasts.

### LEG-021 — HIGH

ProperGateEvaluator._evaluate_gate returns Gate09 evaluate tuple directly, while evaluate_pre_submission expects decision.passed.

Evidence: `revision4/gates_proper.py:295-299; revision4/gates_proper.py:159-170; gates_framework.py:172-183`.

Consequence: When this gate is reached with frozen Gate09 implementation, tuple has no passed attribute and historical wrapper cannot complete that admission. Static cross-file contract mismatch, not a newly executed failure.

Required solution/check: Align Gate09 returned quantity/decision contract in an separately approved repair; preserve sealed artifact provenance.

### ROOT-001 — HIGH

After paper fill, observer/postfill/research/dynamics calls occur before open_trades authoritative record; only SupervisorySnapshotError is contained.

Evidence: `revision2_external/orchestrator.py:2010-2098`.

Consequence: Unexpected observer or research exception can leave real paper broker position with no orchestrator open/protection record; comment Bookkeeping below always runs is stronger than implementation.

Required solution/check: First record actual filled quantity/price/identity and protective lifecycle durably; run non-authority observers only afterward with explicit fault containment. Require focused fault injection, not performed in this static audit.

### ROOT-002 — HIGH

Default Step5 candidate worker passes neither combined_cycle_runtime nor paper_journal.

Evidence: `scripts/run_r5_step5_candidate.py:690-719; revision2_external/orchestrator.py:1062-1077; revision2_external/orchestrator.py:2117-2128`.

Consequence: New optional handoff/durability classes are not part of sealed Block1 worker execution. Default remains intraday/end-run flatten; capability existence is not integrated complete-system proof.

Required solution/check: Define separate explicit opted-in entrypoint and acceptance contract; preserve unchanged sealed reference worker.

### ROOT-003 — HIGH

External _system_state substitutes market age0, brokerconnectedTrue, offline0 and breakerFalse.

Evidence: `revision2_external/orchestrator.py:809-825`.

Consequence: Broker/stale-feed gates see synthetic healthy replay inputs; standalone live reconciliation adapter does not fill these fields automatically.

Required solution/check: Label paper inputs explicitly; require measured broker/feed/interlock handoff for any live-runtime path.

### ROOT-004 — HIGH

Close-feedback receipts are processlocal; PENDING is installed before dispatch update then local governor update, and any failure blocks all retries.

Evidence: `revision2_external/orchestrator.py:855-878`.

Consequence: Duplicate feedback prevented within process, but crash durability absent and partial dispatch/governor update is not atomic all-path exactly-once completion.

Required solution/check: Use durable close receipt/outbox with per-consumer acknowledgment and restart recovery; distinguish duplicate prevention from guaranteed delivery.

### ROOT-005 — MEDIUM

_execute_exit calls controller outcome telemetry before realized-R feedback and optional durable lifecycle close before open_trade deletion.

Evidence: `revision2_external/orchestrator.py:980-1028; revision2_external/orchestrator.py:1072-1077`.

Consequence: Telemetry failure can suppress feedback despite ledger close; optional persistence failure can interrupt deletion after broker flatten. Existing reordered bookkeeping improves normal research-failure consistency but is not complete crash-atomic broker/runtime close.

Required solution/check: Put authoritative close receipts/position removal before observer calls, transactionally journal consumer outcomes; use failclosed recovery when persistence fails.

### ROOT-006 — MEDIUM

Force-close _maybe_exit clock compares raw Timestamp.strftime while _in_trading_window normalizes exchange-local time.

Evidence: `revision2_external/orchestrator.py:1112-1118; revision2_external/orchestrator.py:2222-2259`.

Consequence: Potential inconsistent clock semantics for tz-aware UTC input. This static observation does not establish affected prior trade timestamps.

Required solution/check: Normalize every lifecycle clock to the same exchange-local helper and verify UTC/IST equivalence in a focused approved fixture.

### ROOT-007 — MEDIUM

Stock boundary sentinel is excluded from trading clock but generic end-of-run reconciliation reads last data row.

Evidence: `scripts/run_r5_step5_candidate.py:208-266; scripts/run_r5_step5_candidate.py:525-554; revision2_external/orchestrator.py:2117-2125`.

Consequence: Normally session exits leave no positions; if any remain, exit could use nextsession sentinel price rather than last target-session close.

Required solution/check: Assert no remaining intraday positions at block boundary or pass explicit target-end index to reconciler; never infer quarantine proof from clock alone.

### ROOT-008 — MEDIUM

Candidate identity guard allows selected tracked runner/protocol drift and uses git diff name-only; it does not audit all loaded module bytes/untracked code.

Evidence: `scripts/run_r5_step5_candidate.py:64-110`.

Consequence: Tracked ancestry guard useful but not comprehensive loaded-code attestation. New integration branch is intentionally outside sealedparent contract.

Required solution/check: Record loaded module hashes in explicit experimental entrypoint; preserve strictparent rule for sealed candidate evaluator.

### ROOT-009 — MEDIUM

Kite product conversion correlation_id is echoed but not persisted or queried before conversion write.

Evidence: `revision2_external/broker_adapter_kite.py:107-134`.

Consequence: Adapter verifies before/after quantities and avoids automatic write retry, but repeated caller invocation can convert again where sufficient source remains. Durable caller receipts remain necessary.

Required solution/check: Keep conversion behind durable request-state/idempotency manager and serialize reconciliation; never treat adapter return correlation as durable exactlyonce.

### ROOT-010 — MEDIUM

Standalone live broker reconciliation service is absent from frozen orchestrator imports/calls and default Step5 worker.

Evidence: `revision2_external/broker_reconciliation.py:18-83; revision2_external/orchestrator.py:245-269; scripts/run_r5_step5_candidate.py:648-731`.

Consequence: Read-only broker truth/protection capability exists but does not make historical engine a live reconciled operating system.

Required solution/check: Make explicit runtime admission consumer for receipt.new_risk_allowed and durable ownership records before enabling new risk; no live calls performed here.

### ROOT-011 — MEDIUM

simulate_trade_cycle returns submitted=True for nonSafeBrokerAdapter without invoking adapter submission.

Evidence: `runtime/operating_mode.py:422-455`.

Consequence: This validation helper receipt is not proof of actual broker order.

Required solution/check: Label as contract simulation only; do not use as executed trade evidence.

### ROOT-012 — LOW

ContractValidator.validate_order_payload accepts positive float/bool quantities, unlike ExecutionGate positiveinteger contract.

Evidence: `runtime/contract_validator.py:56-68; runtime/operating_mode.py:359-378`.

Consequence: Validator disagreement can pass a payload that actual submit rejects; broad full_cycle result is not stronger than execution gate.

Required solution/check: Use shared strict order contract in an approved repair; record validationlayer currently differing.

### ROOT-013 — MEDIUM

Alternate Nautilus strategy is long-only local Bollinger reversal, its gridPID uses local -z_score, and dynamicstop ignores u_effort.

Evidence: `alternate/nautilus_revision2_strategy.py:32-60; alternate/nautilus_revision2_strategy.py:215-297`.

Consequence: Alternate PID computation is partly advisory/non-actuating; does not represent NIFTY grid or sealed R5 engine migration.

Required solution/check: Label independent experimental strategy; trace true reference/measurement/actuator before reconnecting anything.

### ROOT-014 — MEDIUM

Alternate fleet exposure PID has no import/call from frozen external orchestrator or Step5 worker.

Evidence: `alternate/fleet_loading_controller.py:1-25; alternate/fleet_loading_controller.py:117-153; scripts/run_r5_step5_candidate.py:648-731`.

Consequence: Standalone boundedPID implemented, but fleetcontroller executor/macro reference/merit allocation/admission wiring absent.

Required solution/check: Keep opt-in implementation separate; define actualexposure and ECS/grid references plus one executor headroom application before claiming dispatchPID operational.

### ROOT-015 — LOW

Paper restore validates environment/passed flag/slippage and finite positions, but passed flag is not cryptographic authenticated snapshot identity.

Evidence: `revision2_external/paper_execution.py:107-134`.

Consequence: Paper simulation recovery capability exists; authenticity, controller-state parity and full fleetcheckpoint consistency require additional contracts.

Required solution/check: Use durable immutable source/config identity and globalcheckpoint journal; never describe simulated protection as exchange-hosted overnight coverage.

### ROOT-016 — INFO

Canonical names expanded to170targets/22safety with46in-house/122external eligible; legacy method names retain68/45.

Evidence: `canonical_parameter_registry.py:520-583`.

Consequence: Historical names/prose counts can mislead audit; actual enforceable registry counts and applicability must govern payload.

Required solution/check: Report runtime registry identity/counts explicitly and classify each parameter by consumer/actuator, without changing frozen registry.