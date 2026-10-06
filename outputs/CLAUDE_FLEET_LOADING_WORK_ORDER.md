# Claude work order: fleet loading controller

Supervisor: Codex. Final integration, verification, commit and push: Codex only.
Workspace: `/home/srinivas/projects/zerodha-claude-fleet-loading`.
Starting commit: `411712afaecaeb44a6899a12db94ac547fef0186`.

Implement a standalone opt-in fleet exposure PID with transparent units and
telemetry. This is a prototype interface, not a trading-edge certification or
permission to change frozen parameters. Read the existing dispatch/grid code
for context, but do not modify it or the orchestrator.

Own ONLY these new files:

- `revision5/fleet_loading_controller.py`
- `tests/test_fleet_loading_controller.py`
- `outputs/CLAUDE_FLEET_LOADING_AUDIT.md`

Required interface:

`FleetLoadingPolicy(enabled=False, kp, ki, kd, integral_limit_pu_seconds, max_capacity_pu)`
with explicit conservative prototype defaults, validation, immutable dataclass.
`FleetLoadingController(policy).update(reference_pu, actual_exposure_pu, dt_seconds,
capacity_pu, protection_tripped=False)` returns immutable telemetry containing
error_pu, integral_pu_seconds, derivative_pu_per_second, p, i, d, raw_output_pu,
allowed_capacity_pu, saturated, protection_tripped. Define reference and feedback
as total gross exposure / equity, not trade R or conviction. Output is total
permitted gross exposure / equity; executor subtracts actual exposure exactly
once to obtain new-risk headroom. Base output on reference plus PID correction.
Clamp capacity to nonnegative minimum of configured maximum and supplied plant
capacity. Protective trip immediately yields zero new capacity; do not wind the
integrator while tripped. Disabled policy returns the supplied capacity unchanged
subject to finite/nonnegative validation, with no hidden PID actuation.

Use conditional integration or back-calculation anti-windup; prove recovery
after both upper and lower saturation. Do not impose BUY/SELL preference. Reject
NaN/infinity and nonpositive dt. Persist all state and policy identity through
JSON export_state/restore_state, strictly validated. No arbitrary tuning from
Block 1 outcomes. No real broker or account actions.

Tests must independently establish response to under/over-loading, saturation
and reversal recovery, protective trip/fault recovery, disabled behavior,
serialization continuity and invalid input rejection. Include a unit check for
dt scaling (seconds) and explicit output/headroom distinction.

Audit must contain exact math, units, files changed, compatibility risks, test
commands and expected assertions. You cannot execute tests in this delegated
profile: label tests NOT RUN and do not fabricate test results. Codex will run
them and inspect your diff independently. Do not commit or push. Do not touch
sealed V3, parameters, holdout, Stage-C, raw reference outputs or other files.
Return a concise receipt with implementation choices and unresolved questions.
