# Full shared R5 adaptation — implemented, testing deferred

This extends the narrower 9ce42b1 closure. It is new engine code, not a claim that the previous 481 tests validate it. The user requested implementation before the full testing campaign. No pytest, backtest, calibration or real-data replay was run for this extension. Syntax, imports, immutable-registry identity, configuration construction and whole-image validation were checked. Production and calibration qualification remain open.

## Activation

The normal shared R5 `PAPER_APPLY` constructor selects `full_adaptation=True` when the new argument is omitted. It selects consume-mode runtime profiles, full governor authority, V3, measured voltage, bounded dispatcher, causal HRSG returns, active paper closed loop, final-exit authority, preserved Box6 fill levels and in-flight protection. It does not construct or enable a live broker. SHADOW/non-R5 defaults retain previous behavior. Use `full_adaptation=False` or the shared CLI `--r5-full-adaptation off` for the prior explicit research-mode selection. Auto mode is the normal paper-runner selection; no three separate implementations were created.

An actual supplied reactive-input provider selects required signed measured-input mode. Otherwise the full paper path selects the explicitly named `market_churn_proxy` mode. Its telemetry identifies a causal, nonnegative, unitless model signal; it is not physical measured MVAR. Missing required measurements still fail closed, and zero is never replaced by an `or` fallback.

## Implemented operating-image architecture

`revision5/full_adaptation.py` freezes configured bases and nine registered meta-policy settings for the run. Each completed plant bar builds a new operating image from normalized causal regime shares, volatility and tracking error. The modulation is an explicit bounded regime/error schedule, not an online Kalman estimator and not calibration. It does not recursively tune its own policy.

For each eligible key, a registered adaptation fraction and bounded pressure interpolate from its configured base toward an admissible registry-domain anchor. Hard-domain clipping, relational projections, discrete safety rounding and whole-image validation occur before consumer mutation. Coupled constraints attenuate the requested image using deterministic backtracking; the attenuation and constraint reasons are retained in telemetry. Integer quantization, hard bounds, a zero input or a coupled constraint can leave individual values unchanged. No claim is made that all keys change on every bar.

The original 312 keys have operating-image entries. Their values are handed to native governor/AVR/SEL/MiCOM/dispatcher/HRSG state-preserving configuration, and the final EffectiveConfig overlay refreshes study PIDs, Box6, continuous exits, closed-loop outcome confidence, ECS/grid controls, MarkV authority and the V3 object. Eight envelope keys remain explicit immutable safety/advisory guards; nine new meta-policy settings also remain fixed. These finite hard-policy boundaries are deliberately not described as adaptive.

## Gemini recommendations implemented with corrections

- Governor feedback gain, z-exit, ATR barrier, target multiplier and per-bay capital-weight operating counterparts now follow the image. The z-exit crossing remains side-aware `signed_z >= -z_exit`, armed using the stored entry-time z threshold, with protection/minimum-hold precedence; Gemini's reversed inequality was rejected.
- Schedule coefficients receive actual per-bar operating values rather than a promised but absent entropy modulator. Five governor clips also use registered signed trend relief, with a strictly positive integral divisor. Box6 Kp/Kd use signed confidence error in full mode; Ki still damps absolute transients.
- Capital weights are projected onto a bounded unit simplex using real bay tracking errors, combined with a separate realized-trade merit state and routed into the native dispatcher. Combining no longer recursively multiplies yesterday's adaptive allocation on unchanged bars. Registry and dispatcher caps both apply.
- HRSG balance retains already committed notionals and redistributes only residual capital. Allocated capital plus reserve equals the plant budget; impossible over-commitment raises rather than inventing capital. Bay and AVR allocation consumers receive that balance.
- Dispatcher EMA coefficients remain complementary; windows, sample counts, scores and downside terms are refreshed without clearing histories. HRSG recovery/correlation/pressure/lag settings and nine V3 fields are refreshed through existing state-preserving consumers. Coupled sample/window, leak and voltage-corridor constraints are validated.
- The original 12 advisory stop/target controls are now active parameter rows in full mode. Their current ATR/price/reward-risk constraints modify BUY/SELL plan geometry before position sizing and cost/safety checks. Quantity is calculated from the constrained risk distance, rather than widening a stop after sizing. Fill handling remains shared.
- The reactive proxy is bounded by registry policy. Limit excursions reduce the actual AVR lot-sizing risk channel proportionally, with a registered derating floor, while the existing signed limiter/protection behavior remains available. The source and units are explicit in telemetry.
- Original safety-contract ceilings remain frozen. Adaptive upper limits tighten downward; undervoltage/UEL/steam lower protections and cooldown durations tighten upward. Stop-count/exit-grace discrete safety thresholds round toward stricter operation. MarkV thresholds tighten upward; VIX/path tolerances and recovery steps tighten downward. Existing sector-budget floor projection and timeout-contract cap are retained.
- Replay journal identity now includes the full selection and authority modes. Checkpoints include the operating image, projection reasons and independent merit/allocation weights. Recovery still reconstructs from the beginning; equivalence is not yet tested for this revision.

## Registry and evidence

The original 12 advisor classifications were promoted only alongside the actual plan consumer. The two added gain-suggestion envelope keys remain advisory. There are now 329 rows in this controller audit surface: the old 320 plus 9 fixed meta-policy rows; the complete canonical registry has 478 targets. The actual registry fingerprint is recorded in FULL_ADAPTATION_STATUS.json.

The old protocol and acceptance receipts remain historical and bind their named older engine/hash. They do not certify this implementation. No new qualified seal or regression-PASS receipt has been fabricated. Full tests, decision/causality checks, risk/fill/ordering cases and checkpoint equivalence are intentionally pending the next testing phase.

Per-key coverage: FULL_ADAPTATION_COVERAGE.json. A row reports its configured role, operating schedule, concrete consumer and whether it is a hard fixed policy. This is implementation coverage, not per-key runtime acceptance evidence.
