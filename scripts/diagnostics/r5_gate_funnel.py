#!/usr/bin/env python3
"""R5 entry-gate funnel by side: where do BUYs disappear, and where does forward edge decay?

The sealed V2 engine (legacy position control, full governor authority) replays each block with
the given V2 parameters.  A passive observer (``orchestrator.gate_observer``) records every
candidate at each entry gate it passes, with its side:

    pa_directional -> pa_confident -> eligible -> id_approved -> mpc_plan -> candidate
    -> pre_sizing_ok -> governor_admitted -> final_admitted -> sized -> risk_checked
    -> gates_passed -> submitted -> filled

plus ``pa_birth``, the first bar of each run of same-side PA directional signals for a symbol.

For each gate and side: counts, and the signed forward move from that bar's close at 1/5/15/30/60
bars and the session close, in basis points, against controls (the same symbol and side, +/-30
minutes of the same time of day, on the block's other sessions).  Confidence intervals resample
whole sessions.  Reading down the table shows where BUYs are removed and where edge is lost.

At four choke points it also records every admit/reject decision with its exact reason and the
variables behind it (``orchestrator._decide``): ``id`` (eligible -> id_approved), ``governor``
(pre_sizing_ok -> governor_admitted), ``risk`` (sized -> risk_checked) and ``pre_submit``
(risk_checked -> gates_passed, the first failing EntryDecisionEngine gate).  For each point, side
and reason: bars, unique PA runs and forward edge of that population; and for each variable its
distribution among admitted and rejected candidates by side.  Raw decisions are kept in
``decisions.jsonl.gz`` so later questions need no replay.

The observer is passive; to prove it, the replay's trade ledger must hash identically to the
sealed V2 result for the same parameters (--v2-reference), block by block, or nothing is written.

    python scripts/diagnostics/r5_gate_funnel.py --stage B --label stage_b_trial_007 \\
        --params <trial_007 params>.json --v2-reference <finalist_1_trial_007_result>.json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import gzip
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OUTPUT_ROOT = ROOT / "outputs/diagnostics"
DEFAULT_PROTOCOL = ROOT / "revision5/step5_sealed_calibration_protocol_v2.json"
V2_SURFACE = ("entry_confidence_threshold", "profit_target_atr_mult", "stop_loss_atr_mult",
              "minimum_profit_margin_over_cost", "max_hold_bars")
STAGES = ("pa_directional", "pa_birth", "pa_confident", "eligible", "id_approved", "mpc_plan", "candidate",
          "pre_sizing_ok", "governor_admitted", "final_admitted", "sized", "risk_checked", "gates_passed",
          "submitted", "filled")
FUNNEL_HORIZONS = (1, 5, 15, 30, 60, 375)
# Counts are always exact.  Forward statistics for very large stages (raw PA signals fire on a
# large share of bars) use a fixed-seed random subsample of at most this many events per side.
MAX_MEASURED_PER_STAGE_SIDE = 20_000
SUBSAMPLE_SEED = 20260930
DECISION_POINTS = ("id", "governor", "risk", "pre_submit")
MAX_MEASURED_PER_REASON_SIDE = 5_000
DECISION_HORIZONS = (15, 30)


def _load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GateRecorder:
    """Passive: records (stage, symbol, timestamp, bar_idx, side, run_id); never read by the engine.

    A PA "run" is a stretch of consecutive bars on which PA is directional on the same side for a
    symbol; ``pa_birth`` marks its first bar.  Every later gate event is tagged with the run that is
    live for that symbol and side, so each stage can be counted in bars AND in unique runs."""

    def __init__(self):
        self.events = []
        self.decisions = []
        self._run = {}                                   # symbol -> (side, last_bar, run_id)

    def _live_run(self, symbol, side):
        current = self._run.get(symbol)
        return current[2] if current is not None and current[0] == side else None

    def on_decision(self, point, symbol, timestamp, bar_idx, side, passed, reason, features):
        self.decisions.append((point, symbol, timestamp, bar_idx, side, passed, reason,
                               self._live_run(symbol, side), dict(features)))

    def on_gate(self, stage, symbol, timestamp, bar_idx, side):
        if stage == "pa_directional":
            current = self._run.get(symbol)
            if current is None or current[0] != side or current[1] != bar_idx - 1:
                run_id = f"{symbol}|{bar_idx}|{side}"
                self.events.append(("pa_birth", symbol, timestamp, bar_idx, side, run_id))
            else:
                run_id = current[2]
            self._run[symbol] = (side, bar_idx, run_id)
        else:
            run_id = self._live_run(symbol, side)
        self.events.append((stage, symbol, timestamp, bar_idx, side, run_id))


PARITY_FIELDS = ("trade_id", "symbol", "side", "entry_timestamp", "entry_price", "quantity",
                 "exit_timestamp", "reason", "exit_price", "costs", "net_pnl")


def ledger_parity(observed: list, reference: list, tolerance: float = 1e-9) -> list:
    """Field-by-field comparison of the replay's trade ledger with the sealed reference."""
    problems = []
    if len(observed) != len(reference):
        problems.append(f"trade count {len(observed)} != {len(reference)}")
    ref = json.loads(json.dumps(reference, default=str))
    obs = json.loads(json.dumps(observed, default=str))
    for i, (a, b) in enumerate(zip(obs, ref)):
        for field in PARITY_FIELDS:
            x, y = a.get(field), b.get(field)
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                if abs(float(x) - float(y)) > tolerance:
                    problems.append(f"trade {i} {field}: {x} != {y}")
            elif str(x) != str(y):
                problems.append(f"trade {i} {field}: {x!r} != {y!r}")
    return problems[:20]


def ledger_sha256(trades: list) -> str:
    canonical = json.loads(json.dumps(trades, default=str))
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def validate_reference(reference: dict, protocol_sha: str, stage: str, params: dict, blocks: list) -> None:
    if reference.get("mode") != "CANDIDATE_EVALUATION":
        raise SystemExit(f"V2_REFERENCE_MODE: {reference.get('mode')!r}")
    if reference.get("protocol_sha256") != protocol_sha:
        raise SystemExit("V2_REFERENCE_PROTOCOL: the reference was not produced under this protocol")
    if reference.get("stage") != stage:
        raise SystemExit(f"V2_REFERENCE_STAGE: {reference.get('stage')!r} != {stage!r}")
    if reference.get("params") != params:
        raise SystemExit("V2_REFERENCE_PARAMS: the reference was produced with different parameters")
    have = {int(b["block"]): b for b in reference.get("blocks", [])}
    for block in blocks:
        ref = have.get(int(block["block"]))
        if ref is None or "trades" not in ref or list(ref["sessions"]) != list(block["sessions"]):
            raise SystemExit(f"V2_REFERENCE_BLOCK_MISMATCH: block {block['block']}")


def replay_block(worker, protocol, block, params):
    from canonical_parameter_registry import CanonicalParameterRegistry
    from revision2_external.grid_context import SealedGridContextProvider
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    from revision5.ccpp_unified_plant import CentralPlantMasterDCS

    frames, feeds, _ = worker.prepare_block(ROOT, protocol, block)
    registry = CanonicalParameterRegistry()
    registry.verify_frozen_identity()
    errors = registry.validate_calibration_payload(params, engine="EXTERNAL")
    if errors:
        raise SystemExit("invalid payload: " + "; ".join(errors))
    equity = float(protocol["block_execution_contract"]["starting_equity_per_block"])
    plant = CentralPlantMasterDCS(total_capital=equity, db_path=":memory:")
    # Identical to the V2 worker's execute_block.
    orch = Revision2ExternalEngineOrchestrator(
        sorted(frames), registry, calibration_overrides=params, starting_equity=equity,
        grid_context_provider=SealedGridContextProvider(feeds["NIFTY_50_15MIN"], feeds["INDIA_VIX_15MIN"]),
        real_plant_dcs=plant, plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
        telemetry_mode="compact", governor_authority="full", governor_position_control="legacy")
    recorder = GateRecorder()
    orch.gate_observer = recorder
    report = orch.run(frames, warmup=int(protocol["block_execution_contract"]["stock_warmup_bars_per_symbol"]))
    return frames, report, recorder


LADDER_R = (0.5, 1.0)


def _atr14(b) -> "np.ndarray":
    import numpy as np
    prev = np.concatenate([[b.close[0]], b.close[:-1]])
    tr = np.maximum(b.high - b.low, np.maximum(abs(b.high - prev), abs(b.low - prev)))
    csum = np.cumsum(tr)
    out = np.empty_like(tr)
    for i in range(len(tr)):                             # causal: bars up to and including i
        lo = max(0, i - 13)
        out[i] = (csum[i] - (csum[lo - 1] if lo > 0 else 0.0)) / (i - lo + 1)
    return out


def stratified(items: list, cap: int, rng) -> list:
    """At most ``cap`` items, allocated evenly across (block, session, symbol) strata."""
    if len(items) <= cap:
        return items
    strata = defaultdict(list)
    for it in items:
        strata[(it[0], str(it[2])[:10], it[1])].append(it)
    quota = max(1, cap // len(strata))
    out = []
    for key in sorted(strata, key=str):
        group = strata[key]
        if len(group) <= quota:
            out.extend(group)
        else:
            out.extend(group[i] for i in sorted(rng.choice(len(group), size=quota, replace=False)))
    return out


def _forward_rows(sample, bars_by_block, side, controls, stop_atr_mult, atr_cache, key_prefix):
    """Forward path and same-time other-session controls for each (block_i, symbol, timestamp)."""
    rows = []
    for block_i, symbol, timestamp in sample:
        b = bars_by_block[block_i].get(symbol)
        if b is None:
            continue
        idx = b.index_of(timestamp)
        if idx is None:
            continue
        key = (block_i, symbol)
        if key not in atr_cache:
            atr_cache[key] = _atr14(b)
        unit = stop_atr_mult * float(atr_cache[key][idx])
        if not unit > 0:
            continue
        sign = 1.0 if side == "BUY" else -1.0
        seed = int(hashlib.sha256(f"{key_prefix}|{symbol}|{timestamp}".encode()).hexdigest()[:8], 16)
        hits = {}
        fwd = b.forward(idx, float(b.close[idx]), sign, unit)
        ctl = b.controls(idx, sign, unit, controls, seed, hits=hits)
        rows.append((fwd, ctl, hits, str(timestamp)[:10]))
    return rows


def normalise_reason(reason: str) -> str:
    """'confidence 0.4120 below entry threshold 0.5500' -> 'confidence # below entry threshold #'."""
    return re.sub(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?(?![\w.])", "#", str(reason)).strip()


def _quantiles(values):
    import numpy as np
    v = np.asarray([float(x) for x in values if isinstance(x, (int, float)) and not isinstance(x, bool)
                    and math.isfinite(float(x))])
    if not len(v):
        return None
    p10, p50, p90 = np.percentile(v, [10, 50, 90])
    return {"n": int(len(v)), "mean": float(v.mean()), "p10": float(p10), "p50": float(p50), "p90": float(p90)}


def anatomy(decisions_by_block: list, bars_by_block: list, controls: int, stop_atr_mult: float,
            measure: bool = True) -> dict:
    """Per choke point and side: the outcome of every decision by exact (normalised) reason, in bars
    and unique PA runs, with the forward edge of each population; plus each recorded variable's
    distribution for admitted vs rejected candidates."""
    import numpy as np
    audit = _load("scripts/diagnostics/r5_entry_edge_audit.py", "r5_entry_edge_audit_for_anatomy")
    rng = np.random.default_rng(SUBSAMPLE_SEED)
    atr_cache = {}
    cells = defaultdict(list)                         # (point, side, reason) -> [(block_i, symbol, ts)]
    runs = defaultdict(set)
    features = defaultdict(lambda: defaultdict(list))  # (point, side, outcome) -> name -> values
    categories = defaultdict(lambda: defaultdict(Counter))
    for block_i, decisions in enumerate(decisions_by_block):
        for point, symbol, timestamp, _, side, passed, reason, run_id, feats in decisions:
            if side not in ("BUY", "SELL"):
                continue
            label = "PASS" if passed else normalise_reason(reason)
            cells[(point, side, label)].append((block_i, symbol, timestamp))
            if run_id is not None:
                runs[(point, side, label)].add((block_i, run_id))
            outcome = "admitted" if passed else "rejected"
            for name, value in feats.items():
                if isinstance(value, str) or isinstance(value, bool) or value is None:
                    categories[(point, side, outcome)][name][str(value)] += 1
                else:
                    features[(point, side, outcome)][name].append(value)
    out = {}
    for point in DECISION_POINTS:
        labels = sorted({k[2] for k in cells if k[0] == point}, key=lambda x: (x == "PASS", x))
        if not labels:
            continue
        sides = {}
        for side in ("BUY", "SELL"):
            total = sum(len(cells[(point, side, lab)]) for lab in labels)
            rows = {}
            for lab in labels:
                items = cells[(point, side, lab)]
                row = {"bars": len(items), "share": len(items) / total if total else None,
                       "runs": len(runs[(point, side, lab)])}
                if measure and items:
                    sample = stratified(items, MAX_MEASURED_PER_REASON_SIDE, rng)
                    fr = _forward_rows(sample, bars_by_block, side, controls, stop_atr_mult, atr_cache,
                                       f"{point}|{lab}")
                    row["measured"] = len(fr)
                    for h in DECISION_HORIZONS:
                        edges, cl = [], []
                        for fwd, ctl, _, day in fr:
                            f = fwd.get(h)
                            if f and ctl.get(h) is not None:
                                edges.append(f["r"] - ctl[h])
                                cl.append(day)
                        row[f"edge_{h}"] = audit._stats(edges, cl)
                rows[lab] = row
            dist = {}
            for outcome in ("admitted", "rejected"):
                dist[outcome] = {
                    "numeric": {n: _quantiles(v) for n, v in sorted(features[(point, side, outcome)].items())},
                    "categorical": {n: dict(c.most_common(12))
                                    for n, c in sorted(categories[(point, side, outcome)].items())},
                }
            sides[side] = {"total": total, "reasons": rows, "features": dist}
        out[point] = sides
    return out


def print_anatomy(anat: dict) -> None:
    def cell(stat):
        if not stat or not stat.get("n"):
            return f"{'n/a':>21}"
        lo, hi = stat["ci95"]
        return f"{stat['mean']:+.3f} [{lo:+.2f},{hi:+.2f}]"
    for point, sides in anat.items():
        b, s = sides["BUY"], sides["SELL"]
        print(f"\nREJECTION ANATOMY: {point}  (input BUY {b['total']}  SELL {s['total']}; "
              "first reason that fired; edge vs same-time controls, 15 bars)")
        print(f"{'reason':<46} {'BUY bars':>9} {'BUY%':>5} {'SELL bars':>10} {'SELL%':>6} {'BUY runs':>9}"
              f" {'SELL runs':>10}  {'BUY edge15':>21}  {'SELL edge15':>21}")
        for lab in sorted(set(b["reasons"]) | set(s["reasons"]), key=lambda x: (x == "PASS", x)):
            rb, rs = b["reasons"].get(lab, {}), s["reasons"].get(lab, {})
            pct = lambda r: "  n/a" if r.get("share") is None else f"{r['share']:5.0%}"
            print(f"{lab[:46]:<46} {rb.get('bars', 0):>9} {pct(rb):>5} {rs.get('bars', 0):>10} {pct(rs):>6}"
                  f" {rb.get('runs', 0):>9} {rs.get('runs', 0):>10}  {cell(rb.get('edge_15'))}  {cell(rs.get('edge_15'))}")
        print(f"  variables (median [p10, p90]):  {'BUY admitted':>26} {'BUY rejected':>26} {'SELL admitted':>26}"
              f" {'SELL rejected':>26}")
        names = sorted({n for side in (b, s) for o in ("admitted", "rejected") for n in side["features"][o]["numeric"]})
        for n in names:
            vals = []
            for side in (b, s):
                for o in ("admitted", "rejected"):
                    q = side["features"][o]["numeric"].get(n)
                    vals.append(f"{q['p50']:+.3g} [{q['p10']:+.3g},{q['p90']:+.3g}]" if q else "n/a")
            print(f"  {n[:30]:<30} " + " ".join(f"{v:>26}" for v in vals))
        cats = sorted({n for side in (b, s) for o in ("admitted", "rejected") for n in side["features"][o]["categorical"]})
        for n in cats:
            for side_name, side in (("BUY", b), ("SELL", s)):
                for o in ("admitted", "rejected"):
                    c = side["features"][o]["categorical"].get(n)
                    if c:
                        print(f"  {n} {side_name} {o}: " + ", ".join(f"{k}={v}" for k, v in list(c.items())[:6]))


def analyse(events_by_block: list, bars_by_block: list, controls: int, stop_atr_mult: float) -> dict:
    """Per gate and side: bar counts, unique PA runs, survival, SELL share, and forward R (R ~
    stop_loss_atr_mult x ATR14 at the event bar) vs controls, with session-cluster CIs."""
    import numpy as np
    audit = _load("scripts/diagnostics/r5_entry_edge_audit.py", "r5_entry_edge_audit_for_funnel")
    counts, runs = Counter(), defaultdict(set)
    grouped = defaultdict(list)
    for block_i, events in enumerate(events_by_block):
        for stage, symbol, timestamp, _, side, run_id in events:
            counts[(stage, side)] += 1
            if run_id is not None:
                runs[(stage, side)].add((block_i, run_id))
            grouped[(stage, side)].append((block_i, symbol, timestamp))
    rng = np.random.default_rng(SUBSAMPLE_SEED)
    atr_cache = {}
    measured = {}
    for (stage, side), items in grouped.items():
        if side not in ("BUY", "SELL"):
            continue
        sample = stratified(items, MAX_MEASURED_PER_STAGE_SIDE, rng)
        fwd_rows = []
        for block_i, symbol, timestamp in sample:
            b = bars_by_block[block_i].get(symbol)
            if b is None:
                continue
            idx = b.index_of(timestamp)
            if idx is None:
                continue
            key = (block_i, symbol)
            if key not in atr_cache:
                atr_cache[key] = _atr14(b)
            unit = stop_atr_mult * float(atr_cache[key][idx])
            if not unit > 0:
                continue
            sign = 1.0 if side == "BUY" else -1.0
            seed = int(hashlib.sha256(f"{stage}|{symbol}|{timestamp}".encode()).hexdigest()[:8], 16)
            hits = {}
            fwd = b.forward(idx, float(b.close[idx]), sign, unit)
            ctl = b.controls(idx, sign, unit, controls, seed, hits=hits)
            fwd_rows.append((fwd, ctl, hits, str(timestamp)[:10]))
        per_h = {}
        for h in FUNNEL_HORIZONS:
            vals, cl, edges, ecl = [], [], [], []
            fav, adv, cfav, cadv = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
            for fwd, ctl, hits, day in fwd_rows:
                f = fwd.get(h)
                if not f:
                    continue
                vals.append(f["r"])
                cl.append(day)
                if ctl.get(h) is not None:
                    edges.append(f["r"] - ctl[h])
                    ecl.append(day)
                for x in LADDER_R:
                    fav[x].append(f["mfe_r"] >= x)
                    adv[x].append(f["mae_r"] <= -x)
                    if hits.get(h):
                        cfav[x].append(hits[h].get(f">={x:.2f}R"))
                        cadv[x].append(hits[h].get(f"<=-{x:.2f}R"))
            mean = lambda v: float(np.mean([y for y in v if y is not None])) if any(y is not None for y in v) else None
            per_h[str(h)] = {
                "fwd_r": audit._stats(vals, cl), "edge_r": audit._stats(edges, ecl),
                "ladder": {f"{x:g}R": {"fav": mean(fav[x]), "fav_ctl": mean(cfav[x]),
                                       "adv": mean(adv[x]), "adv_ctl": mean(cadv[x])} for x in LADDER_R}}
        measured[(stage, side)] = {"measured": len(fwd_rows), "horizons": per_h}

    table, previous = {}, None
    birth_runs = {side: len(runs[("pa_birth", side)]) for side in ("BUY", "SELL")}
    for stage in STAGES:
        entry = {}
        for side in ("BUY", "SELL"):
            n_runs = len(runs[(stage, side)])
            entry[side] = {
                "bars": counts.get((stage, side), 0), "runs": n_runs,
                "step_survival_runs": (n_runs / len(runs[(previous, side)])
                                       if previous and runs[(previous, side)] else None),
                "cumulative_from_birth_runs": n_runs / birth_runs[side] if birth_runs[side] else None,
                **measured.get((stage, side), {"measured": 0, "horizons": {}}),
            }
        total = entry["BUY"]["bars"] + entry["SELL"]["bars"]
        entry["sell_share"] = entry["SELL"]["bars"] / total if total else None
        prev_share = table[previous]["sell_share"] if previous else None
        entry["sell_share_added"] = (entry["sell_share"] - prev_share
                                     if entry["sell_share"] is not None and prev_share is not None else None)
        table[stage] = entry
        if stage != "pa_directional":                      # survival chains from pa_birth onwards
            previous = stage
    return table


def print_table(table: dict) -> None:
    pct = lambda x: "  n/a" if x is None else f"{x:5.0%}"
    sgn = lambda x: "   n/a" if x is None else f"{x * 100:+5.1f}"
    print("\nGATE FUNNEL (bars = decisions on a bar; runs = unique PA signal runs; survival on runs)")
    print(f"{'gate':<18} {'BUY bars':>9} {'SELL bars':>10} {'SELL%':>6} {'dSELL%':>7} {'BUY runs':>9} {'SELL runs':>10}"
          f" {'BUY step':>9} {'SELL step':>10} {'BUY cum':>8} {'SELL cum':>9}")
    for stage, e in table.items():
        b, s = e["BUY"], e["SELL"]
        print(f"{stage:<18} {b['bars']:>9} {s['bars']:>10} {pct(e['sell_share']):>6} {sgn(e['sell_share_added']):>7}"
              f" {b['runs']:>9} {s['runs']:>10} {pct(b['step_survival_runs']):>9} {pct(s['step_survival_runs']):>10}"
              f" {pct(b['cumulative_from_birth_runs']):>8} {pct(s['cumulative_from_birth_runs']):>9}")

    def cell(stat):
        if not stat or not stat.get("n"):
            return "        n/a         "
        lo, hi = stat["ci95"]
        return f"{stat['mean']:+.3f} [{lo:+.2f},{hi:+.2f}]"
    print("\nFORWARD R BY GATE (R ~ stop_loss_atr_mult x ATR14; edge vs same-time controls on other sessions)")
    print(f"{'gate':<18} {'side':<4} {'n':>6}  {'fwd 15b':>21}  {'edge 15b':>21}  {'edge 30b':>21}  {'edge EOD':>21}"
          f"  +1R15 t/c   -1R15 t/c")
    for stage, e in table.items():
        for side in ("BUY", "SELL"):
            h = e[side].get("horizons") or {}
            if not h:
                continue
            lad = h["15"]["ladder"]["1R"]
            f = lambda v: "n/a" if v is None else f"{v:.0%}"
            print(f"{stage:<18} {side:<4} {e[side]['measured']:>6}  {cell(h['15']['fwd_r'])}  {cell(h['15']['edge_r'])}"
                  f"  {cell(h['30']['edge_r'])}  {cell(h['375']['edge_r'])}"
                  f"  {f(lad['fav'])}/{f(lad['fav_ctl'])}   {f(lad['adv'])}/{f(lad['adv_ctl'])}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--stage", choices=["A", "B"], required=True)
    parser.add_argument("--block", type=int, action="append", help="block number (repeatable); default all")
    parser.add_argument("--params", type=Path, required=True, help="V2 trading parameters (5 keys)")
    parser.add_argument("--v2-reference", type=Path, required=True,
                        help="sealed V2 candidate result for the same stage and params (neutrality check)")
    parser.add_argument("--label", required=True)
    parser.add_argument("--controls", type=int, default=10)
    args = parser.parse_args(argv)

    if any("r5_step6" in str(p) for p in (args.params, args.v2_reference, args.protocol)):
        parser.error("holdout quarantine: Step-6 verification state may not be read")
    out_dir = (OUTPUT_ROOT / f"gate_funnel_{args.label}").resolve()
    if OUTPUT_ROOT.resolve() not in out_dir.parents:
        parser.error("output must stay under outputs/diagnostics/")
    protocol_sha = hashlib.sha256(args.protocol.read_bytes()).hexdigest()
    protocol = json.loads(args.protocol.read_text())
    params = json.loads(args.params.read_text())
    if set(params) != set(V2_SURFACE):
        parser.error(f"--params must contain exactly {sorted(V2_SURFACE)}")
    blocks = [b for b in protocol["sampling_plan"]["stage_a" if args.stage == "A" else "stage_b"]
              if not args.block or int(b["block"]) in args.block]
    reference = json.loads(args.v2_reference.read_text())
    validate_reference(reference, protocol_sha, args.stage, params, blocks)
    worker = _load("scripts/run_r5_step5_candidate.py", "r5_step5_worker")
    audit = _load("scripts/diagnostics/r5_entry_edge_audit.py", "r5_entry_edge_audit_bars")

    events_by_block, bars_by_block, decisions_by_block, neutrality = [], [], [], []
    for block in blocks:
        started = time.time()
        print(f"block {block['block']} {block['sessions'][0]}..{block['sessions'][-1]} ...", flush=True)
        frames, report, recorder = replay_block(worker, protocol, block, params)
        events = recorder.events
        ref = next(b for b in reference["blocks"] if int(b["block"]) == int(block["block"]))
        observed, expected = ledger_sha256(report["trades"]), ledger_sha256(ref["trades"])
        problems = ledger_parity(report["trades"], ref["trades"])
        if observed != expected or problems:
            raise SystemExit(f"OBSERVER_NOT_NEUTRAL block {block['block']}: ledger {observed} vs reference {expected}; "
                             f"{problems}")
        neutrality.append({"block": int(block["block"]), "trades": len(report["trades"]), "ledger_sha256": observed,
                           "fields_checked": list(PARITY_FIELDS)})
        bars_by_block.append({s: audit.SymbolBars(f) for s, f in frames.items()})
        events_by_block.append(events)
        decisions_by_block.append(recorder.decisions)
        c = Counter((e[0], e[4]) for e in events)  # (stage, side)
        print(f"  events {len(events)}  pa_directional BUY/SELL {c[('pa_directional', 'BUY')]}/{c[('pa_directional', 'SELL')]}"
              f"  filled BUY/SELL {c[('filled', 'BUY')]}/{c[('filled', 'SELL')]}  decisions {len(recorder.decisions)}"
              f"  ({time.time() - started:.0f}s)", flush=True)
    table = analyse(events_by_block, bars_by_block, args.controls, float(params["stop_loss_atr_mult"]))
    anat = anatomy(decisions_by_block, bars_by_block, args.controls, float(params["stop_loss_atr_mult"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_dir / "decisions.jsonl.gz", "wt") as fh:
        for block, decisions in zip(blocks, decisions_by_block):
            for point, symbol, timestamp, bar_idx, side, passed, reason, run_id, feats in decisions:
                fh.write(json.dumps({"block": int(block["block"]), "point": point, "symbol": symbol,
                                     "timestamp": timestamp, "bar_idx": bar_idx, "side": side, "passed": passed,
                                     "reason": reason, "run_id": run_id, **feats}, default=str) + "\n")
    document = audit._sanitize({"label": args.label, "stage": args.stage, "params": params,
                                "protocol_sha256": protocol_sha, "neutrality": neutrality,
                                "sessions": sum(len(b["sessions"]) for b in blocks), "funnel": table,
                                "anatomy": anat})
    (out_dir / "report.json").write_text(json.dumps(document, indent=2, allow_nan=False, default=str))
    print_table(table)
    print_anatomy(anat)
    print(f"\nwrote {out_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
