# R7 57-requirement acceptance matrix

Status counts: {'PASS': 55, 'BLOCKED': 2}. Gate C complete: False.

| ID | Intent | Source | Status | Recommendation | Residual risk |
|---|---|---|---|---|---|
| M01 | AVR and governor not bypassed; one owner per bar | `revision7_avr/r7_coordinated_controller.py:80` | PASS | ACCEPT (unit + replay evidence) | none material |
| M02 | Liquidity feature not constant | `revision7_avr/r7_features.py:47` | PASS | ACCEPT (unit + replay evidence) | relative-volume window and /2 scaling uncalibrated |
| M03 | Every completed bar sampled, not candidate-only | `revision7_avr/r7_engine.py:289` | PASS | ACCEPT (unit + replay evidence) | none material |
| M04 | Exposure in symbol-budget pu on both sides | `revision7_avr/r7_engine.py:94` | PASS | ACCEPT (unit + replay evidence) | budgets are research inputs |
| M05 | Side-aware admission; explicit short policy | `revision7_avr/r7_engine.py:339` | PASS | ACCEPT (unit + replay evidence) | all Gate B entries were shorts (native strategy) |
| M06 | Droop, slew and hold configuration actually used | `revision7_avr/r7_state.py:25` | PASS | ACCEPT (unit + replay evidence) | none material |
| M07 | True closed-loop governor | `revision7_avr/r7_market_governor.py:30` | PASS | ACCEPT (unit + replay evidence) | saturation rate (see PID evidence) |
| M08 | Governor checkpoint/restore | `revision7_avr/r7_coordinated_controller.py:117` | PASS | ACCEPT (unit + replay evidence) | none material |
| M09 | Market AVR closed loop (not open-loop) | `revision7_avr/r7_market_avr.py:7` | PASS | ACCEPT (unit + replay evidence) | market AVR has no Q-V droop; 8% AVR droop is not part of the R7 market AVR |
| M10 | No bay-level state contamination | `revision7_avr/r7_engine.py:54` | PASS | ACCEPT (unit + replay evidence) | none material |
| M11 | No pulse reversal deadlock | `revision7_avr/r7_coordinated_controller.py:12` | PASS | ACCEPT WITH RESIDUAL RISK | replay shows many reversals within 3 bars (possible hunting) |
| M12 | Pulse age bounded and correctly counted | `revision7_avr/r7_coordinated_controller.py:51` | PASS | ACCEPT (unit + replay evidence) | behaviour change vs baseline (off-by-one fixed) |
| M13 | Hysteresis | `revision7_avr/r7_state.py:53` | PASS | ACCEPT WITH RESIDUAL RISK | thresholds uncalibrated |
| M14 | Same-bar idempotency | `revision7_avr/r7_coordinated_controller.py:75` | PASS | ACCEPT (unit + replay evidence) | none material |
| M15 | No silent unmapped symbols | `revision7_avr/r7_engine.py:48` | PASS | ACCEPT (unit + replay evidence) | none material |
| M16 | Causal per-symbol feature pipeline | `revision7_avr/r7_features.py:8` | PASS | ACCEPT (unit + replay evidence) | none material |
| M17 | Studies and grid direction bounded signed inputs | `revision7_avr/r7_engine.py:191` | PASS | ACCEPT (unit + replay evidence) | 0.01 scale provisional |
| M18 | Setpoint and error units consistent | `revision7_avr/r7_allocator.py:104` | PASS | ACCEPT (unit + replay evidence) | none material |
| M19 | Native safety and execution authority | `revision7_avr/r7_engine.py:356` | PASS | ACCEPT (unit + replay evidence) | none material |
| M20 | Five-bay topology isolation | `revision7_avr/r7_state.py:89` | PASS | ACCEPT (unit + replay evidence) | none material |
| M21 | Engine B/C rejection telemetry | `revision7_avr/r7_engine.py:350` | PASS | ACCEPT (unit + replay evidence) | none material |
| M22 | Warmup before first candidate | `revision7_avr/r7_engine.py:303` | PASS | ACCEPT (unit + replay evidence) | none material |
| M23 | Unimpeded native protective exits | `revision7_avr/r7_engine.py:390` | PASS | ACCEPT (unit + replay evidence) | none material |
| M24 | Orchestrator checkpoint schema | `revision7_avr/r7_engine.py:496` | BLOCKED | NOT ACCEPTED: native journal integration missing | R7 snapshot is not wired into the native paper journal; engine-level restart not tested |
| M25 | Verification suite | `revision7_avr/test_*.py` | BLOCKED | PENDING GATE C | Gate C 20-session results PENDING |
| G01 | P_ref / E_ref three-source reference | `revision7_avr/r7_coordinated_controller.py:92` | PASS | ACCEPT (unit + replay evidence) | weights fixed by operator |
| G02 | Market feedback = common portfolio utilization | `revision7_avr/r7_allocator.py:25` | PASS | ACCEPT (unit + replay evidence) | none material |
| G03 | Settable droop R + demand-aware sharing | `revision7_avr/r7_allocator.py:73` | PASS | ACCEPT WITH RESIDUAL RISK | oversubscription never reached in real replay |
| G04 | P_actual signed, unclamped | `revision7_avr/r7_engine.py:99` | PASS | ACCEPT (unit + replay evidence) | none material |
| G05 | Tracking error | `revision7_avr/r7_market_governor.py:30` | PASS | ACCEPT (unit + replay evidence) | none material |
| G06 | Proportional | `revision7_avr/r7_market_governor.py:31` | PASS | ACCEPT (unit + replay evidence) | none material |
| G07 | Integral + anti-windup | `revision7_avr/r7_market_governor.py:39` | PASS | ACCEPT WITH RESIDUAL RISK | integral reaches its 0.5 limit in replay; rejected-order interaction tested only via pending reservation |
| G08 | Derivative on measurement | `revision7_avr/r7_market_governor.py:32` | PASS | ACCEPT (unit + replay evidence) | D negligible in replay |
| G09 | Slew | `revision7_avr/r7_market_governor.py:47` | PASS | ACCEPT (unit + replay evidence) | none material |
| G10 | Response lag | `revision7_avr/r7_market_governor.py:59` | PASS | ACCEPT (unit + replay evidence) | none material |
| G11 | Rotor inertia kept physical (R6) | `revision6_avr/r6_turbine_governor.py:179` | PASS | ACCEPT (unit + replay evidence) | R6 units are not coupled to trading decisions (by design) |
| A12 | AVR participation vote | `revision7_avr/r7_market_avr.py:13` | PASS | ACCEPT (unit + replay evidence) | not an 8% Q-V droop; participation score only |
| A13 | Price-volume trend | `revision7_avr/r7_features.py:57` | PASS | ACCEPT (unit + replay evidence) | 0.01 scale provisional |
| A14 | Candle pressure | `revision7_avr/r7_features.py:51` | PASS | ACCEPT (unit + replay evidence) | none material |
| A15 | Relative volume | `revision7_avr/r7_features.py:40` | PASS | ACCEPT (unit + replay evidence) | none material |
| A16 | Market quality | `revision7_avr/r7_market_avr.py:18` | PASS | ACCEPT (unit + replay evidence) | weights uncalibrated |
| A17 | Studies input | `revision7_avr/r7_engine.py:301` | PASS | ACCEPT (unit + replay evidence) | none material |
| A18 | Grid direction input | `revision7_avr/r7_engine.py:266` | PASS | ACCEPT (unit + replay evidence) | none material |
| A19 | Eligibility / permissive | `revision7_avr/r7_coordinated_controller.py:95` | PASS | ACCEPT (unit + replay evidence) | none material |
| A20 | Control dt | `revision7_avr/r7_features.py:31` | PASS | ACCEPT (unit + replay evidence) | none material |
| A21 | Approved (droop-allocated) target | `revision7_avr/r7_allocator.py:104` | PASS | ACCEPT (unit + replay evidence) | none material |
| A22 | Applied governor output | `revision7_avr/r7_market_governor.py:59` | PASS | ACCEPT (unit + replay evidence) | saturation rate (see PID evidence) |
| A23 | Coordinated pulse | `revision7_avr/r7_coordinated_controller.py:110` | PASS | ACCEPT WITH RESIDUAL RISK | possible hunting in replay |
| P1 | Q-V reactive droop (settable; defaults 7.5% GTG / 10% steam unchanged) | `revision6_avr/r6_avr_core.py:275` | PASS | ACCEPT (unit + replay evidence) | 8% not commissioned; no change authorized |
| P2 | Cascaded outer voltage / inner field PI | `revision6_avr/r6_avr_core.py:221` | PASS | ACCEPT (unit + replay evidence) | physical model only |
| P3 | Positional PI with back-calculation anti-windup | `revision6_avr/r6_avr_core.py:75` | PASS | ACCEPT (unit + replay evidence) | none material |
| P4 | Field limits and slew | `revision6_avr/r6_avr_core.py:124` | PASS | ACCEPT (unit + replay evidence) | none material |
| P5 | OEL/UEL reactive limiting on final actuator | `revision6_avr/r6_avr_core.py:284` | PASS | ACCEPT (unit + replay evidence) | none material |
| P6 | ANSI 27/59/Q trips latch; bumpless operator reset | `revision6_avr/r6_avr_core.py:258` | PASS | ACCEPT (unit + replay evidence) | none material |
| P7 | No faked market MVAR; measurements fail closed | `revision6_avr/r6_avr_core.py:194` | PASS | ACCEPT (unit + replay evidence) | none material |
| P8 | Causal identity / anti-replay | `revision6_avr/r6_avr_core.py:162` | PASS | ACCEPT (unit + replay evidence) | none material |
| P9 | Checkpoint/restore with config hash; restart equivalence | `revision6_avr/r6_avr_core.py:376` | PASS | ACCEPT (unit + replay evidence) | R6 physical AVR is not used by the R7 trading path or native replay |
