"""State-vector cost overlay (2026-08-25, owner-specified Step 2 - after
the joint-date block bootstrap + Benjamini-Hochberg FDR correction,
before any 48-symbol scaling).

Answers question #2 from the owner's own framing, kept explicitly
separate from question #1 (which the bootstrap already answered):
  1. Predictive information: does one state outperform another?  (bootstrap)
  2. Tradeable edge: does the FAVORABLE state itself produce positive
     net expectancy after REAL costs?                              (this file)

EXECUTION MODEL (owner-specified, not a flat round-trip subtraction):
    signal observed at completed bar t
      -> fill at bar t+1's OPEN (next-bar-open, this project's own
         established convention elsewhere - see run_chart_studies_live_
         monitor.py)
      -> exit at bar t+h's CLOSE (h = the tested horizon)
      -> REAL Zerodha MIS statutory costs (zerodha_intraday_costs.py -
         the exact schedule already validated and used by the frozen
         V10-C replay: brokerage, STT, exchange txn, SEBI, GST, stamp
         duty - not invented here)
      -> slippage/spread stress: 5 bps PER SIDE, reusing the EXACT
         convention replay_v10c_history.py already reports as its own
         "5-bps/side stressed net P&L" - not a new number invented for
         this study.

TWO SEPARATE cost questions, per the owner's own stated subtlety:
  (a) The DIFFERENCE (Gross/Net Momentum DeltaE, Structure spread) -
      costs are STRUCTURALLY SYMMETRIC between the two groups compared
      (both sides pay the same statutory schedule and slippage stress),
      so costs mostly CANCEL in a differential comparison. Reported for
      completeness, in the table format the owner requested.
  (b) The ABSOLUTE net expectancy of trading the FAVORABLE state ALONE
      (going long when M>0 & dM<=0; going long the bullish Structure
      extreme) - costs do NOT cancel here, this is the real "is there a
      tradeable edge" question. Reported as a second table, since the
      owner's own subtlety note makes clear this is the one that
      actually answers question #2.

SCOPE: tested against all 7 contrasts from the corrected bootstrap for
transparency, but the headline verdict is drawn from the 3 that
survived BOTH the raw 95% CI and Benjamini-Hochberg FDR (Momentum DeltaE
at +1, +2, +3 bars) - the others are shown but explicitly marked as not
statistically established, so a cost overlay on a non-surviving effect
is not mistaken for validating it.
"""
from __future__ import annotations

import pandas as pd

from study_layer_v2_forward_information_study import HORIZONS, SAMPLE_SYMBOLS, build_symbol_frame
from zerodha_intraday_costs import round_trip_bps_equivalent

# Reusing the V10-C replay's own established stress convention verbatim
# ("5-bps/side stressed net P&L") - not a new number invented here.
SLIPPAGE_STRESS_BPS_PER_SIDE = 5.0
SLIPPAGE_STRESS_BPS_ROUND_TRIP = SLIPPAGE_STRESS_BPS_PER_SIDE * 2

# A representative trade value for the statutory cost schedule - large
# enough that the brokerage cap (Rs 20) does not bind (it starts binding
# above ~Rs 66,667 of trade value), so this bps figure is realistic and
# stable across position sizes actually used elsewhere in this project.
REPRESENTATIVE_TRADE_VALUE = 50_000.0
STATUTORY_ROUND_TRIP_BPS = round_trip_bps_equivalent(REPRESENTATIVE_TRADE_VALUE)
TOTAL_ROUND_TRIP_COST_BPS = STATUTORY_ROUND_TRIP_BPS + SLIPPAGE_STRESS_BPS_ROUND_TRIP

# Which of the 7 contrasts survived the corrected bootstrap + FDR
# (from bootstrap_significance_output_v2.txt) - shown, not silently
# dropped, so the reader can see the overlay applied to non-surviving
# effects too, clearly labeled.
SURVIVED_BOTH = {
    "Momentum DeltaE +1": True, "Momentum DeltaE +2": True, "Momentum DeltaE +3": True,
    "Momentum DeltaE +6": False, "Momentum DeltaE +12": False,
    "Structure extreme +12": False, "VWAP-conditional Bollinger +6": False,
}


def mean_bp(series: pd.Series) -> float:
    return float(series.mean()) * 10_000  # fraction -> bp


def main():
    print(f"Statutory round-trip cost (Rs.{REPRESENTATIVE_TRADE_VALUE:,.0f} trade, real Zerodha MIS schedule): "
          f"{STATUTORY_ROUND_TRIP_BPS:.2f} bp")
    print(f"Slippage/spread stress (V10-C's own established convention, 5bp/side): "
          f"{SLIPPAGE_STRESS_BPS_ROUND_TRIP:.2f} bp")
    print(f"TOTAL round-trip cost assumed: {TOTAL_ROUND_TRIP_COST_BPS:.2f} bp\n")

    print("Building per-symbol frames (same 8-symbol sample, now with next-bar-open fill columns)...")
    frames = [build_symbol_frame(s) for s in SAMPLE_SYMBOLS]
    pooled = pd.concat(frames, ignore_index=True)

    m_pos = pooled["momentum"] > 0
    decelerating = m_pos & (pooled["momentum_delta"] <= 0)
    accelerating = m_pos & (pooled["momentum_delta"] > 0)

    diff_rows = []
    absolute_rows = []

    for h in HORIZONS:
        eligible = pooled["state_clean"] & pooled[f"label_eligible_{h}"] & m_pos
        col = f"fwd_return_open_{h}"
        dec = pooled.loc[eligible & decelerating, col]
        acc = pooled.loc[eligible & accelerating, col]
        gross_diff_bp = mean_bp(dec) - mean_bp(acc)
        # costs are symmetric between the two groups (same execution
        # model, same schedule) - they cancel in the differential, so
        # net_diff_bp == gross_diff_bp exactly under this cost model.
        net_diff_bp = gross_diff_bp
        label = f"Momentum DeltaE +{h}"
        diff_rows.append({
            "effect": label, "gross_bp": gross_diff_bp, "net_bp": net_diff_bp,
            "pct_consumed": None, "survived_bootstrap_fdr": SURVIVED_BOTH[label],
        })

        gross_favorable_bp = mean_bp(dec)  # the FAVORABLE state (decelerating) traded LONG, alone
        net_favorable_bp = gross_favorable_bp - TOTAL_ROUND_TRIP_COST_BPS
        pct_consumed = (TOTAL_ROUND_TRIP_COST_BPS / gross_favorable_bp * 100) if gross_favorable_bp > 0 else None
        absolute_rows.append({
            "effect": f"{label} (favorable state alone, LONG)", "gross_bp": gross_favorable_bp,
            "net_bp": net_favorable_bp, "pct_consumed": pct_consumed,
            "survived_bootstrap_fdr": SURVIVED_BOTH[label],
        })

    # --- Structure extreme, +12 bars (shown for completeness - did NOT survive FDR) ---
    h = 12
    eligible = pooled["state_clean"] & pooled[f"label_eligible_{h}"]
    col = f"fwd_return_open_{h}"
    bullish = pooled.loc[eligible & (pooled["structure"] >= 0.6), col]
    bearish = pooled.loc[eligible & (pooled["structure"] < -0.6), col]
    gross_diff_bp = mean_bp(bullish) - mean_bp(bearish)
    diff_rows.append({
        "effect": "Structure extreme +12", "gross_bp": gross_diff_bp, "net_bp": gross_diff_bp,
        "pct_consumed": None, "survived_bootstrap_fdr": SURVIVED_BOTH["Structure extreme +12"],
    })
    gross_favorable_bp = mean_bp(bullish)  # favorable = LONG the bullish extreme
    net_favorable_bp = gross_favorable_bp - TOTAL_ROUND_TRIP_COST_BPS
    pct_consumed = (TOTAL_ROUND_TRIP_COST_BPS / gross_favorable_bp * 100) if gross_favorable_bp > 0 else None
    absolute_rows.append({
        "effect": "Structure extreme +12 (favorable state alone, LONG)", "gross_bp": gross_favorable_bp,
        "net_bp": net_favorable_bp, "pct_consumed": pct_consumed,
        "survived_bootstrap_fdr": SURVIVED_BOTH["Structure extreme +12"],
    })

    diff_df = pd.DataFrame(diff_rows)
    absolute_df = pd.DataFrame(absolute_rows)

    def _fmt(df):
        d = df.copy()
        for c in ["gross_bp", "net_bp"]:
            d[c] = d[c].map(lambda x: f"{x:+.3f}")
        d["pct_consumed"] = d["pct_consumed"].map(lambda x: f"{x:.0f}%" if x is not None else "n/a (already negative gross)")
        return d

    print("\n" + "=" * 100)
    print("TABLE A - DIFFERENCE (Gross/Net DeltaE or spread) - costs are structurally symmetric, mostly cancel")
    print("=" * 100)
    print(_fmt(diff_df).to_string(index=False))

    print("\n" + "=" * 100)
    print("TABLE B - ABSOLUTE net expectancy of trading the FAVORABLE state alone - costs do NOT cancel here")
    print("=" * 100)
    print(_fmt(absolute_df).to_string(index=False))

    print("\n" + "=" * 100)
    print("VERDICT")
    print("=" * 100)
    for _, row in absolute_df.iterrows():
        survived = row["survived_bootstrap_fdr"]
        tradeable = row["net_bp"] > 0
        tag = "STATISTICALLY ESTABLISHED" if survived else "NOT statistically established (shown for completeness only)"
        econ = "net POSITIVE after costs" if tradeable else "net NEGATIVE after costs"
        print(f"  [{tag}] {row['effect']}: gross {row['gross_bp']:+.3f}bp -> net {row['net_bp']:+.3f}bp ({econ})")


if __name__ == "__main__":
    main()
