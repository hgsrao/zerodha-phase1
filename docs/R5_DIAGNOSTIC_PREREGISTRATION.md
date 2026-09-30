# R5 diagnostic pre-registration: Stage-C TRAIN

This file fixes the hypotheses, the statistics and the decision rules **before** any Stage-C
training-window result is read. The criteria were chosen after Stage A (15 sessions) and Stage B
(30 sessions) had been analysed; neither sample is reused as evidence here.

## Data
- **Source:** the Stage-C TRAIN export. It is produced by `scripts/diagnostics/export_stage_c_train.py`
  and verified before it is written:
  - the parameters are the frozen Stage-B winner (V2 Stage-A trial 7);
  - there are 453 TRAIN sessions in [2023-09-01, 2025-07-01);
  - no validation session appears.
- **Tool:** `scripts/diagnostics/r5_entry_edge_audit.py` at the commit that adds this file, or a
  later commit that changes only reporting. Uncertainty is a 95% session-cluster bootstrap with
  seed 20260930.
- **Out of bounds:** the validation window (2025-07-01 to 2026-01-01) is never read.

## Hypotheses and pass criteria
"CI" below means the 95% session-cluster confidence interval of the mean, in R. "2 bps" means
fills synthesized at 2 bps slippage per leg, with brokerage, exchange charges and STT recomputed
on those fills.

| id | question | statistic | passes only if |
|---|---|---|---|
| H1 (primary) | Is there a tradable session-direction edge? | Competitor: first executed entry per symbol-session, held to the close; mean net R at 2 bps | CI lower bound > 0 |
| H2 | Does entry timing beat random timing? | Edge versus cross-session, time-of-day-matched controls, at 15 bars | CI lower bound > 0 |
| H3 | Does a minimum on-time pay? | Fixed hold of 15, 30 and 60 bars; mean net R at 2 bps | CI lower bound > 0 at some horizon (report all three) |
| H4 | Can execution alone rescue the current policy? | Actual trades; mean net R at 0 bps | CI lower bound > 0 |
| H5 | Are the favourable tails direction rather than volatility? | Fixed-horizon ladders at 15 and 30 bars: trades versus controls, share ≥ +1R and share ≤ −1R | Favourable excess ≥ 5 points while adverse excess ≤ 1 point |

**Also reported (no pass/fail):**
- the BUY/SELL split, with each side's net and edge;
- the whole slippage sweep (0 to 5 bps);
- FSRN exit share and exit regret;
- the pulse-width table.

If BUY is under 20% of trades, that is flagged as a structural side asymmetry to investigate
before any redesign.

## Decisions
- **H1 passes:** build a one-pulse-per-symbol-session, hold-to-close policy as a new sealed
  experiment (V4). Judge it only on fresh out-of-sample data after it is frozen, and measure real
  execution cost (Kite fills) before any capital is committed.
- **H1 fails and H2 fails:** stop calibrating this entry stack. Exits (V3.1), gains and minimum
  on-time are not pursued. Research moves to new signal sources, reusing these diagnostics as the
  acceptance test.
- **H3 fails:** no minimum on-time / τ_min rule, whatever H1 shows.
- **H5 fails:** no right-tail selectivity study on these entries' direction.
- **Nothing passes:** Revision 5, as configured, has no demonstrable edge after costs on 453
  training sessions. That is recorded as the result.

H1 is the only primary test. H2 to H5 are secondary and are reported as such. No criterion,
threshold or statistic may be changed after the export has been read.
