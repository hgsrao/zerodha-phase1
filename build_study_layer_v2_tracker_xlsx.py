"""Builds STUDY_LAYER_V2_REVIEW_TRACKER_20260825.xlsx - the living
architecture-review tracker requested by the owner ("make an excel sheet
of this with all the points, and let us see how it grows as we
proceed"). Three sheets:
  1. Layer2_Freeze_Gate - the owner's own 16-item freeze checklist,
     status as of today.
  2. Review_Log - point-by-point log of both review rounds today,
     meant to be APPENDED TO in future sessions, not replaced.
  3. Meta_Controller_Experiment - the frozen META_CONTROL_EXPERIMENT_V1
     summary.

Run: python build_study_layer_v2_tracker_xlsx.py
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).parent
OUT_PATH = ROOT / "STUDY_LAYER_V2_REVIEW_TRACKER_20260825.xlsx"

HEADER_FILL = PatternFill(start_color="2B3A67", end_color="2B3A67", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)
DONE_FILL = PatternFill(start_color="D6EFD6", end_color="D6EFD6", fill_type="solid")
PARTIAL_FILL = PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid")
TITLE_FONT = Font(bold=True, size=13)
WRAP = Alignment(wrap_text=True, vertical="top")


def _style_sheet(ws, col_widths, header_row=1):
    for col_idx, width in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    for cell in ws[header_row]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1).coordinate
    ws.row_dimensions[header_row].height = 30


def build_freeze_gate(wb):
    ws = wb.active
    ws.title = "Layer2_Freeze_Gate"
    ws["A1"] = "Study Layer V2 - Layer 2 (State Vector) Freeze Gate"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A1:E1")

    headers = ["#", "Requirement", "Status", "Evidence / Reference", "Notes"]
    ws.append([])
    ws.append(headers)
    header_row = 3

    rows = [
        (1, "Seven dimensions remain separate (S, L_vwap, L_bb, M, dM, R, V) - never fused into one scalar",
         "DONE", "study_layer_v2_state_vector.py module docstring; STATE_VECTOR_FIELDS",
         "Owner's own correction: 7 dimensions, not 6 - kept as-is, no forced aesthetic reduction."),
        (2, "Minimal Ichimoku structure - one defensible measurement, not 4 sub-signals combined",
         "DONE", "structure_distance_atr_raw() / structure_score()",
         "Tenkan/Kijun relationship, cloud orientation, cloud thickness documented as candidate extensions, not added."),
        (3, "VWAP and Bollinger location kept separate (redundancy measured empirically, not assumed)",
         "DONE", "location_vwap_score(), location_bb_score()",
         "Motivated directly by the 92% Ichimoku/Bollinger empirical agreement finding earlier today."),
        (4, "Raw values retained before every lossy (bounding) transform",
         "DONE", "structure_distance_atr_raw, vwap_distance_atr_raw, bb_z_raw, volatility_raw columns",
         "NOT applied mechanically everywhere - momentum_delta has no raw/normalized pair (not a lossy transform of a raw quantity)."),
        (5, "Raw SMI retained + momentum delta kept UNSMOOTHED",
         "DONE", "smi_raw column; momentum_delta() = 1-bar diff, no EMA/slope",
         "Smoothing choice (EMA vs 3-bar slope) deliberately deferred to an empirical Layer-3 question."),
        (6, "Regime is ONE defined, falsifiable context quantity - not a hidden sub-vote system",
         "DONE", "regime_score() - single SMA(20)-SMA(50)/ATR formula on the index",
         "Explicit contract documented; no RSI/ADX/breadth folded in."),
        (7, "ATR/price (raw) AND its causal trailing percentile both retained, as two DIFFERENT questions",
         "DONE", "atr_price_ratio(), volatility_percentile()",
         "Raw = comparable in % terms across symbols; percentile = symbol-relative abnormality. Corrected wording after owner's round-2 note."),
        (8, "NaN internally + explicit readiness AND status at the interface",
         "DONE", "<field>_ready (bool), <field>_status (READY/INSUFFICIENT_HISTORY) columns",
         "Status enum reserves MISSING_BAR/STALE_CONTEXT/TIMESTAMP_MISMATCH/INVALID_INPUT for when continuity + regime-join are wired in - not fabricated today."),
        (9, "Direct prefix-equivalence causality test, randomized across many cut points",
         "DONE", "test_no_lookahead_state_vector.py::test_prefix_equivalence_full_dataset_vs_exact_prefix",
         "~40 randomized cut points (seeded, reproducible), not the original 5 fixed CHECK_INDICES."),
        (10, "Exact price-scale invariance (metamorphic test): x10, x0.1, x40, x100 - OHLC scaled consistently, volume untouched",
         "DONE", "test_study_layer_v2_state_vector.py::test_dimensionless_states_are_exactly_scale_invariant",
         "Strictly stronger than the original 'neither saturates' heuristic test (kept alongside)."),
        (11, "Session-aware continuity validator (session_id, not weekday inference; no fixed bar-count assumption)",
         "PARTIAL", "validate_bar_continuity()",
         "Detects duplicates/non-monotonic/intra-session gaps without assuming a fixed bar count per session. KNOWN GAP: no authoritative NSE trading-calendar input yet (holidays, Muhurat-style special sessions) - documented explicitly in the function docstring, not silently claimed solved."),
        (12, "Causality + freshness timestamp alignment, 3-way status (ALIGNED / STALE / FUTURE_CONTEXT)",
         "DONE", "assert_temporal_alignment()",
         "Strict default max_staleness='0min' per owner's preference for a 5-min engine. Not yet wired into any live join (Regime join doesn't exist yet) - contract exists ahead of the join."),
        (13, "Deterministic replay: identical input -> numerically identical output; fails closed on unsorted input",
         "DONE", "compute_state_vector() raises ValueError on unsorted timestamps; test_..._is_deterministic_across_repeated_calls",
         "New criterion added in owner's round-2 review."),
        (14, "State schema / config identity stamped on every output",
         "DONE", "state_schema_version='STUDY_STATE_V2_1', config_hash columns",
         "New criterion added in owner's round-2 review. Prevents silently comparing state across code/config versions."),
        (15, "No Layer-3 'trade quality' semantics injected into Layer 2 (Location != Direction != Quality)",
         "DONE", "Docstrings throughout; monotonicity tests check the PHYSICAL quantity, never 'quality'",
         "Layer 3 is free to later learn a non-monotonic quality function on top - not built here."),
        (16, "No voting, no weights, no entry/exit decision logic anywhere in this file",
         "DONE", "Whole module - compute_state_vector() returns only descriptive state, never a decision",
         "Confirmed by construction: no BUY/SELL/threshold/entry logic exists in study_layer_v2_state_vector.py."),
    ]
    for r in rows:
        ws.append(list(r))

    for row in ws.iter_rows(min_row=header_row + 1, max_row=header_row + len(rows), min_col=1, max_col=5):
        status_cell = row[2]
        fill = DONE_FILL if status_cell.value == "DONE" else PARTIAL_FILL if status_cell.value == "PARTIAL" else None
        for cell in row:
            cell.alignment = WRAP
            if fill:
                status_cell.fill = fill

    _style_sheet(ws, col_widths=[4, 42, 10, 42, 55], header_row=header_row)
    ws.append([])
    ws.append(["", "Next gate (per owner, explicit): once all above are DONE, STOP modifying Layer 2.", "", "", ""])
    ws.append(["", "Move to the empirical response-surface experiment: x_t -> forward return/MFE/MAE/R at t+1,2,3,6,12.", "", "", ""])
    ws.append(["", "Does the state representation contain actionable information at all? Answer that BEFORE any controller.", "", "", ""])


def build_review_log(wb):
    ws = wb.create_sheet("Review_Log")
    ws["A1"] = "Study Layer V2 - Review Log (append future rounds below, do not overwrite)"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A1:F1")
    ws.append([])
    headers = ["Date", "Round", "Point", "Owner's Point (summary)", "Response / Action Taken", "Reference"]
    ws.append(headers)
    header_row = 3

    r1 = "2026-08-25"
    round1 = [
        (r1, 1, "1", "State vector, not composite score - 7 dims not 6", "Agreed, already compliant; kept STATE_VECTOR_FIELDS + regime separate = 7", "study_layer_v2_state_vector.py"),
        (r1, 1, "2", "Ichimoku Structure - minimal, don't overbuild (avoid recreating voting inside one dimension)", "Agreed, already compliant - single cloud-distance measurement only", "structure_distance_atr_raw()"),
        (r1, 1, "3", "VWAP/BB kept separate; Location != Direction", "Agreed, already compliant; adopted owner's exact 'Location is Layer 2, quality is Layer 3' framing", "location_vwap_score(), location_bb_score()"),
        (r1, 1, "4", "Retain raw Bollinger z alongside tanh (2 sigma vs 3 sigma distinction shouldn't be pre-compressed)", "Real gap - implemented raw+normalized pair", "bb_z_raw()"),
        (r1, 1, "5", "SMI + unsmoothed momentum delta", "Agreed, already compliant", "momentum_score(), momentum_delta()"),
        (r1, 1, "6", "Regime - audit the source, must be one falsifiable quantity not a hidden sub-vote", "Agreed, already compliant; tightened explicit contract wording", "regime_score()"),
        (r1, 1, "7", "Retain raw ATR/price alongside percentile (percentile can decay toward 0.5 in sustained high-vol regime)", "Real gap - implemented raw+percentile pair", "atr_price_ratio(), volatility_percentile()"),
        (r1, 1, "8", "NaN internally, explicit readiness at interface (fillna(0) landmine)", "Real gap - added <field>_ready booleans", "compute_state_vector()"),
        (r1, 1, "9", "Add prefix-equivalence causality test", "Added explicit full-dataset-vs-exact-prefix test (5 fixed points initially)", "test_no_lookahead_state_vector.py"),
        (r1, 1, "10", "Add exact scale-invariance metamorphic test (x10 / x0.1)", "Added - stronger than the median-comparison heuristic test", "test_dimensionless_states_are_exactly_scale_invariant"),
        (r1, 1, "11", "Session/continuity contract - ADD BEFORE FREEZE", "Real gap, not previously scoped - built validate_bar_continuity()", "validate_bar_continuity()"),
        (r1, 1, "12", "Cross-input timestamp alignment contract", "Real gap - built assert_temporal_alignment() first-cut", "assert_temporal_alignment()"),
        (r1, 1, "13", "Raw + normalized traceability as a general principle", "Implemented across all applicable dimensions", "compute_state_vector() output columns"),
        (r1, 1, "14", "Monotonicity: Layer 2 monotonic in physical quantity, Layer 3 free to be non-monotonic in quality", "Agreed, already compliant in substance; tightened docstring language", "module docstring criterion 5"),
        (r1, 1, "15-17", "Inner PI, P01D anti-windup, D-term all correctly deferred", "Agreed, no action - documented as Layer 3/4 requirements for later", "n/a (deferred)"),
        (r1, 1, "18", "Freeze the adaptive-threshold controller as an outer-loop artifact, do not let it touch Layer-2 states", "Agreed - added META_CONTROL_EXPERIMENT_V1 freeze header; confirmed no import of state_vector module", "study_layer_v2_adaptive_threshold.py"),
    ]
    round2 = [
        (r1, 2, "1", "ATR/price correction: raw ratio IS % -comparable across symbols; the real distinction is symbol-relative abnormality, not comparability", "Docstrings corrected throughout (section header + function docstring)", "atr_price_ratio()"),
        (r1, 2, "2", "Readiness should be a STATUS enum (INSUFFICIENT_HISTORY / MISSING_BAR / STALE_CONTEXT / TIMESTAMP_MISMATCH / INVALID_INPUT), not only boolean", "Implemented <field>_status; only READY/INSUFFICIENT_HISTORY reachable today, others reserved and documented (not fabricated)", "compute_state_vector(), READY/INSUFFICIENT_HISTORY constants"),
        (r1, 2, "3", "Session continuity must distinguish invalid gap from legitimate session boundary; no authoritative exchange calendar yet", "Docstring tightened to state the exact current definition (calendar-date session inference) and the explicit known limitation (no holiday/Muhurat calendar)", "validate_bar_continuity() docstring"),
        (r1, 2, "4", "Temporal alignment needs precise inequality + 3 outcomes (ALIGNED/STALE/FUTURE_CONTEXT), strict default for a 5-min engine", "Rewrote to return status enum; changed default max_staleness 10min -> 0min (exact-match preference)", "assert_temporal_alignment()"),
        (r1, 2, "5", "Raw+normalized: don't force a pair where none is needed (e.g. momentum_delta)", "Confirmed already compliant - no manufactured pair added for momentum_delta; documented explicitly in criterion 12", "module docstring criterion 12"),
        (r1, 2, "6a", "Prefix-equivalence should be randomized across MANY cut points, not a handful", "Expanded to ~40 seeded-random cut points", "RANDOM_PREFIX_CHECKPOINTS"),
        (r1, 2, "6b", "Scale invariance: also test x100; scale OHLC consistently; don't scale volume", "Added scale=100.0 to the parametrized test; already scaled OHLC together and left volume untouched", "test_dimensionless_states_are_exactly_scale_invariant"),
        (r1, 2, "13(new)", "New criterion: deterministic replay - identical input -> identical output, no hidden state/ordering dependence", "Implemented: raises ValueError on unsorted input (fail closed); added repeatability test", "compute_state_vector()"),
        (r1, 2, "14(new)", "New criterion: schema/config version identity, to prevent comparing state across silently-different code/config", "Implemented STATE_SCHEMA_VERSION + config_hash columns", "compute_state_vector()"),
        (r1, 2, "meta-1", "Freeze the WHOLE meta-controller experiment (data hash, grid, results, conclusion), not just the source file", "Built _write_frozen_manifest() - META_CONTROL_EXPERIMENT_V1_FROZEN_20260825.json + .sha256 sidecar", "backtest_adaptive_threshold_on_v10c.py"),
        (r1, 2, "meta-2", "'All 36 beat baseline' (broad plateau) is more credible than 'best was -Rs.98' (possible selection bias)", "Adopted framing explicitly in the frozen manifest's own conclusion field - narrow claim only, no promoted 'winning' config", "META_CONTROL_EXPERIMENT_V1_FROZEN_20260825.json:conclusion"),
    ]
    for row in round1 + round2:
        ws.append(list(row))
    for row in ws.iter_rows(min_row=header_row + 1, max_row=header_row + len(round1) + len(round2), min_col=1, max_col=6):
        for cell in row:
            cell.alignment = WRAP
    _style_sheet(ws, col_widths=[11, 7, 8, 55, 60, 40], header_row=header_row)


def build_meta_controller_sheet(wb):
    ws = wb.create_sheet("Meta_Controller_Experiment")
    ws["A1"] = "META_CONTROL_EXPERIMENT_V1 - Frozen Summary"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A1:B1")
    ws.append([])
    rows = [
        ("Experiment ID", "META_CONTROL_EXPERIMENT_V1"),
        ("Status", "FROZEN 2026-08-25"),
        ("Manifest file", "META_CONTROL_EXPERIMENT_V1_FROZEN_20260825.json (+ .sha256 sidecar)"),
        ("Input", "P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825 v10c_history_trades.csv (64 trades)"),
        ("Input sha256", "adc45ff51032d3313150cdbe1c74f195171ad51b70386800b73a7a32bb11487c"),
        ("Process variable", "trailing hit-rate over last `window` closed, actually-taken trades"),
        ("Setpoint", "target_hit_rate = 0.5"),
        ("Actuator", "entry_score threshold (rejected trades never fed back into the controller)"),
        ("Anti-windup rule", "integral hard-clamped to +-5.0 each update"),
        ("Baseline net P&L", "-Rs.5,292.97"),
        ("Baseline win rate", "12.50%"),
        ("Grid size", "36 gain combinations (kp in {0,2,5}, ki in {0,1,3}, kd in {0,2}, window in {5,10})"),
        ("Fraction beating baseline", "36 / 36 (100%)"),
        ("Fraction net profitable", "0 / 36 (0%)"),
        ("Best net P&L in grid", "~ -Rs.98 (kp=2.0, ki=1.0, kd=0.0, window=10)"),
        ("Worst net P&L in grid", "~ -Rs.2,914 (the static no-op control, kp=ki=kd=0)"),
        ("Conclusion (verbatim from manifest)",
         "Every tested adaptive configuration improved the V10-C loss relative to the stated static baseline, "
         "but none established positive net expectancy. The broad, unanimous direction of improvement across the "
         "entire tested parameter region is the credible finding - not any single best-performing gain "
         "combination, which remains vulnerable to selection bias with 36 configs tried."),
        ("Candidate future role", "Outer/meta adapter (slow loop on realized strategy quality) in a future two-loop "
                                   "architecture - never a replacement for the inner state-vector-driven loop"),
    ]
    for r in rows:
        ws.append(list(r))
    for row in ws.iter_rows(min_row=3, max_row=2 + len(rows), min_col=1, max_col=2):
        for cell in row:
            cell.alignment = WRAP
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 95


def main():
    wb = Workbook()
    build_freeze_gate(wb)
    build_review_log(wb)
    build_meta_controller_sheet(wb)
    wb.save(OUT_PATH)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
