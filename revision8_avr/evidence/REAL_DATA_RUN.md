# Real-data replays (run on the machine that holds the frozen dataset)

The frozen 48-symbol NSE dataset is not in the cloud container (all 48 files missing there), so these
runs have to be made locally. `LIVE_TRADING_ENABLED` must be `False`; the runner refuses anything else.

```bash
git fetch origin claude/funny-gauss-u3eq3t
git checkout claude/funny-gauss-u3eq3t
export LIVE_TRADING_ENABLED=False
DATA=/home/srinivas/projects/zerodha-phase1/data/frozen_48_1min_20230703_20260824
GRID=local_workspace/records/grid-manifest-local.json          # the runner's default

# R7 baseline, then R8 off / shadow / paper: full session (390 bars), all 48 symbols, both engines
for E in B C; do
  python revision7_avr/run_r7_replay.py $E r7 --bars 390 --data-dir "$DATA" --grid-manifest "$GRID" --out runs/r7_$E
  for M in off shadow paper; do
    python revision8_avr/run_r8_replay.py $E r8 --bars 390 --adaptation $M \
        --data-dir "$DATA" --grid-manifest "$GRID" --out runs/r8_${E}_$M
  done
  python revision8_avr/compare_shadow_paper.py runs/r8_${E}_shadow runs/r8_${E}_paper --md runs/attribution_$E.md
done
```

What to check in the outputs:

| Check | Where | Expected |
|---|---|---|
| R8 `off` = R7 | `summary.json` trades, pulses | identical (decision digest may differ only by MarketAVR telemetry rounding) |
| R8 `shadow` = `off` | `r8.decision_digest` | identical |
| Formula parity | `r8.adaptation.parity_mismatches` | 0 (a mismatch stops a paper run) |
| Tighten-only | `r8.adaptation.applied` | every limit at or below the contract |
| SELL / short gate | `r8.pulses`, decision reasons | SELL pulses on four-way BEAR; `..._SHORT_BLOCKED_BY_POLICY` when shorts are off |
| P&L attribution | `runs/attribution_{B,C}.md` | every shadow -> paper difference names the limit in force |
