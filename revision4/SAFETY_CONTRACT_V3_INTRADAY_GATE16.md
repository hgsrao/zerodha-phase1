# Safety Contract V3 — Intraday Gate16 Threshold

Approved change: the Gate16 post-fill intraday slippage tolerance is **0.15%**
(0.0015 as a fraction), replacing 0.10%.

Evidence basis: the sealed August 2024 diagnostic contained 5,917 intraday
candidate fills. The 0.10% threshold rejected 68 (1.15%); 0.15% would reject
23 (0.39%). This is an execution-policy change, not a profitability setting.

Unchanged controls:

- Cross-session orders remain prohibited by default.
- Gate16 remains post-fill and the existing quarantine/remediation lifecycle
  remains mandatory for any breach.
- A successful SUNPHARMA replay does not authorize 48-symbol validation,
  calibration, or live trading.

Contract identity:

- ID: ECS_REVISION_2_PARAMETER_SURFACE_V3
- SHA-256: 26755ba69e28a81142a424fcca1a8c1b1f51a377ef70ba46fa52a97f96f28d74
- Surface: 69 targets (47 calibratable, 22 fixed) plus 20 immutable safety
  values.
