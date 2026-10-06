# Unified plant wiring and architecture audit

Audit date: 2026-10-05. Repository: hgsrao/zerodha-phase1, local branch diagnostic/block1-isolation-harness, HEAD f1c7d63. Read-only source audit after the study grading fix. No new tests, controller changes, parameter changes, runs, or live actions were performed for this audit.

## 1. Architectural verdict

The complete requested lifecycle—Grid → Fleet Dispatch PID → Engine A → ownership handoff → Engine B multi-day exit—is not executable in Revision2ExternalEngineOrchestrator today. Some portfolio feedback and governor loops are connected; the position lifecycle remains intraday. There is no dedicated fleet dispatch PID found in the current checkout to simply reconnect. Building that controller and building an A/B position lifecycle are separate engineering changes.

| Component | Executable implementation | Authority and missing connection |
|---|---|---|
| Grid | PlantControlChain grid synchronizer; orchestrator._governor_grid_return | Causal macro context and side-dependent governor entry limits. Not a fleet tracking PID. |
| Fleet demand | ECS supervisor in revision5/plant_control.py | State-dependent loading target, immediate reduction and bounded restoration. No PID integral. |
| Fleet allocation | DynamicBayLoadDispatcher and SectorDispatchController | Realized-R merit feedback, weight smoothing, capacity water-filling. SHADOW is observational; PAPER_APPLY adds bounded admission capacity. |
| Bay governors | revision5/governor.py and governor_authority.py | Actual realized-R outer feedback and position tracking inner loop. PR #7 gives inner HOLD priority over FSRN within minimum hold; mandatory protection still wins. |
| Native A/B operating modes | revision5/engine_state.py; CCPPUnifiedPlant mode-taking APIs | A entry cutoff, durable daily fill latch and square-off intents; B is exempt from those A rules. These APIs do not implement ownership transfer. |
| Native HRSG | revision5/hrsg.py; CCPPUnifiedPlant.rebalance_capital | Recovers/reallocates capital between gas/steam bays. It does not move tickets, convert products, or carry stops overnight. |
| External position lifecycle | orchestrator.open_trades, _maybe_exit, _execute_exit | No A/B owner field or transfer state machine. Time/session liquidation applies to every open trade. |
| Overnight protection | No complete external lifecycle path found | No B-specific persistent protection/restart/gap lifecycle linked to transferred positions. |

A steam turbine BAY (sector topology) and ENGINE_B (operating mode) are different dimensions. Allocation to a steam bay does not imply overnight ownership.

## 2. Dispatch: what actually closes the loop

Source anchors: revision5/ccpp_unified_plant.py:88–208; revision5/plant_control.py:338–395; revision2_external/orchestrator.py:323–369, 849–866; native on_trade_closed at ccpp_unified_plant.py:2398–2424.

DynamicBayLoadDispatcher retains up to 20 realized-R outcomes per bay. Before three outcomes its score is 1. Thereafter score = max(0.1, 1 + mean(R)/max(0.2, downside standard deviation)); the downside fallback is 0.5. Scores are normalized, targets bounded, renormalized, and weights updated as 0.85*previous + 0.15*target, followed by normalization. This is adaptive feedback, not e/I/D control. Renormalization means the raw merit weight ceiling is not itself a final physical cap; SectorDispatchController separately enforces allocation ceilings.

SectorDispatchController distributes ECS demand over available bays by deterministic water-filling and reports unallocated capacity. ECS reduces loading immediately and restores it at a bounded rate. These are working mechanisms, not evidence that risk has no feedback. The external close path sends realized R to local governor and merit allocator exactly once within a run. PAPER_APPLY shares the native dispatcher/governor objects; SHADOW does not actuate the plant admission cap. The governor grid comparator is a separate path and must not be confused with shadow dispatch authority.

A current-checkout search for DispatchController, FleetPID, dispatch_pid and fleet_pid found the merit dispatcher and sector allocator, not a dedicated fleet PID. Other PID classes exist in signal, entry, exit and governor modules; none establishes the requested fleet dispatch control law. This finding does not establish what may exist in unexamined historical branches or an external design document.

### Required design for a new fleet PID

First define one controlled quantity and one actuator. A candidate contract is actual gross exposure divided by its allowed budget as measurement, ECS demand as reference, and bounded total admission capacity as output. Capital velocity and drawdown have different units and should not be silently combined into this error. Existing drawdown/protection authority must remain independently dominant.

Specify e = reference - measured loading, elapsed event-time dt, bounded output, conditional integration/back-calculation during saturation, unavailable bays, market closure and missing telemetry, restoration limits, restart state and reset policy. Decide whether this replaces ECS loading dynamics or acts inside them. Preserve the merit allocator for relative bay allocation unless deliberately superseded. Consume actual confirmed fills/exits, including carried positions, once; do not integrate candidate counts as portfolio exposure. Publish reference, measurement, e/P/I/D, unclipped/clipped output, saturation and authority mode. No gains or tuning are selected by this audit.

## 3. Grid and end-to-end current execution

Sources: orchestrator.py:642–700; revision5/governor.py:554–630, 910–945; governor_authority.py:274–310.

The grid input is NIFTY deviation from its causal EMA, not simply a volatility penalty. For side sign s (+1 BUY, -1 SELL), the governor uses signed_z = s*z and signed_grid = s*grid_deviation. Adverse grid = max(0, -signed_grid). The droop penalty is min(adverse_grid/runtime_droop_r * runtime_grid_droop_gain, runtime_grid_droop_max). Feedback offset is bounded -0.25*last_control_u. dynamic_z = base_z + feedback_offset - droop_penalty. In the external trend_overspeed mode, the admission upper limit is dynamic_z - 2*base_z; signed_z must be <= that limit. Thus adverse macro drift tightens the relevant side's overspeed allowance. It is directional steering through admission elasticity, not a standalone buy/sell command or fleet PID.

Current trade path:

1. Causal data and grid context update; PA and chart studies evaluate completed-bar information.
2. ID constructs a candidate and geometry; governor comparator, conviction and minimum-value protection decide admission.
3. Sizing and any enabled plant capacity cap constrain quantity; final pre-submit safety and actual Gate12 evaluate the signal.
4. Paper broker executes a fill; orchestrator records a symbol-owned open trade and initializes protective/controller state.
5. Subsequent bars check mandatory exits and armed protection; the full governor owns discretionary HOLD/EXIT. Completed-bar updates become effective on subsequent bars.
6. Time/session exit closes through _execute_exit; realized R updates the governor and merit allocator.
7. There is no next step that transfers this trade to Engine B.

Admission passes are not fills. Controller existence is not proof of execution authority. A per-trade PID HOLD cannot override unconditional intraday liquidation.

## 4. Native A/B design versus external lifecycle

Sources: revision5/engine_state.py:1–106 and EngineStateStore; ccpp_unified_plant.py:2059 onward, on_trade_filled:2365–2392, build_engine_a_squareoff_intents:2426–2485; revision5/hrsg.py:1–21.

Native ENGINE_A rejects new entries from 14:30 IST, consumes a SQLite-backed one-confirmed-fill-per-bay/day latch, and produces square-off intents from 15:15. ENGINE_B is a valid mode and is exempt from A's cutoff/latch. Square-off intent construction checks position.engine_mode and does not itself place orders. These are operating interlocks and mode-aware APIs, not an A→B transfer algorithm. HRSG operates on bay allocation/protection/correlation rather than position ownership.

The external orchestrator does not use engine_mode to route the active trade lifecycle or call the native A-square-off intent builder. Its _maybe_exit at line 1070 checks configured force_close_time and executes a market exit at bar open. The session_last_bar branch at line 1254 exits at close with mis_session_close. Both occur independently of an A/B ownership transfer and prevent overnight carry. Their purpose in this implementation is the configured intraday replay lifecycle; source alone does not reveal the historical design rationale. The external configured force-close clock must not be conflated with the native 15:15 rule.

CostedPaperBrokerAdapter in revision2_external/paper_execution.py supports MARKET replay orders and cost booking. Its interface has no product conversion operation or conversion acknowledgement. Changing a dictionary owner field would therefore not establish MIS→CNC conversion or broker-backed protective continuity.

## 5. Proposed handoff blueprint (not implemented)

Qualification thresholds such as 15:15 IST and MFE >= 0.75R are examples from the request, not approved frozen policy or evidence of an existing algorithm. MFE alone is insufficient: a trade can have positive past excursion and current loss. Specify current R, structural alignment, executable product eligibility, available carry capital, remaining protection distance, market-data freshness and risk budgets. Choose a qualification deadline strictly before the adapter's mandatory liquidation/conversion deadline. Mode is independent of bay.

Required lifecycle: A_OPEN → TRANSFER_REQUESTED → B_OPEN, or TRANSFER_REJECTED → A liquidation. Persist a stable position ID, original fill/risk anchor, quantity, product, bay, owner, stop/target, controller state and policy version. Handoff preserves the ticket and original risk/P&L basis; it is not a fictitious close/reopen. Do not book realized R or dispatch close feedback at transfer.

At qualification, reserve carry capital and request adapter conversion where applicable. Keep A ownership and existing protection until broker/product and protective-order acknowledgements are reconciled. Only then commit B ownership atomically and idempotently. A timeout/rejection leaves A under its mandatory exit rules. Never bypass the force-close clock merely because transfer was requested.

Route mandatory session exits by confirmed owner/product; B carry may bypass A-only clocks, but not kill switch, portfolio drawdown, protective stops or broker reconciliation. Avoid two controllers independently placing exits. A consumed daily fill latch remains consumed after transfer.

B requires its own approved holding horizon, structural signal, ratcheting/exit policy and causal bar cadence. Admission limits must include overnight positions. Reconcile positions/products/protective orders at restart, preserve stop continuity across sessions, and define opening-gap execution and stale-data handling. A modeled stop is not a guaranteed overnight fill. Product eligibility must be checked by the adapter; overnight short carry cannot be assumed equivalent to delivery-equity long carry. Corporate actions and data/session discontinuities need explicit position accounting.

Required components: persistent lifecycle store and transfer receipts; conversion-capable adapter contract; exclusive owner-based exit router; B controller and market clock; cross-session protection and reconciliation; exposure/funding reservation; idempotent journal/feedback handling; end-of-replay carry valuation policy. These are missing execution contracts, not just a missing callback.

## 6. Schematic confirmation and later acceptance milestone

Current: Grid → ECS/merit dispatch → bay governor/cap → external intraday trade → forced/session close → realized-R feedback.

Target: Grid → defined fleet loading control → bay allocation → A fill → acknowledged ownership/product/protection transfer → B cross-session management → authoritative exit → exactly-once feedback.

Before implementation, confirm the controller reference/measurement/actuator, the handoff qualification and deadline, B policy, product eligibility and protective-order model. This audit intentionally does not select them. After that schematic is confirmed, the first acceptance demonstration should be one deterministic offline trace: A confirmed fill, qualifying completed bar, acknowledged transfer, session boundary with broker and stop reconciliation, overnight gap, then a Day 2/3 B exit. Include a rejected-transfer path when validation is subsequently authorized. No multi-block optimization is needed to establish wiring.

The existing results do not prove that absence of Engine B makes profitability impossible, that carry cures fees, or that the entire plant works as specified. Longer holding can increase adverse excursion and overnight risk. Current merit feedback is genuinely connected even though it is not a PID. The study grading fix is a correctness change; it does not complete plant integration. The 48-symbol PA+RR experiment and TITAN PA-only experiment remain separate evidence, and the retained final report describes pre-grading-fix behavior.

## Audit boundary

No controller code, tests, frozen protocols, master references, holdout quarantine, Stage-C, broker positions or live configuration were changed. This document records current source wiring and a proposed integration contract; it does not certify a complete multi-day trading system.
