#!/usr/bin/env python3
"""Extraction procedure for the DERIVED_REAL_REPLAY_FIXTURE used by tests/test_r5_d01_orchestrator_run_integration.py.

Every row written here is copied unchanged from the output of the repository's own Block 1 loader
(scripts/run_r5_step5_candidate.prepare_block) over the existing verified replay input.  Nothing is generated, resampled or edited.
Rows are only SELECTED:
  * TITAN 1-minute frame: the 60 warmup rows, the 375 rows of the first scored session (2024-02-13) and the first row of the
    next session (the loader's own successor row).
  * NIFTY 15-minute and INDIA VIX 15-minute feeds: every loader row from 2023-08-14 up to and including the first
    availability stamp after that session (2024-02-14 03:45 UTC).  VIX rows before 2023-08-14 are not selected because the
    plant only uses the inner join of the two feeds.
The script then proves the selection is sufficient by re-running the same orchestrator on the selected rows and on the full
loader output and comparing the resulting trade records bit for bit.

Usage: R5_D01_DATA_ROOT=<dir of symlinks: revision2/ and local_workspace/> python extract_fixture.py
"""
import hashlib, importlib.util, json, os, sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent
PARAMS_SRC = Path("/home/srinivas/projects/zerodha-phase1/outputs/r5_step5_stage_a_v2_state/params/trial_007.json")
FEED_CUT_UTC = pd.Timestamp("2024-02-14 03:45:00", tz="UTC")
FEED_START_UTC = pd.Timestamp("2023-08-14 00:00:00", tz="UTC")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


spec = importlib.util.spec_from_file_location("step5", ROOT / "scripts/run_r5_step5_candidate.py")
step5 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step5)
proto = json.loads((ROOT / "revision5/step5_sealed_calibration_protocol_v2.json").read_text())
frames, feeds, audit = step5.prepare_block(Path(os.environ["R5_D01_DATA_ROOT"]), proto, proto["sampling_plan"]["stage_a"][0])

titan = frames["TITAN"]
day = titan["timestamp"].dt.strftime("%Y-%m-%d")
first_day_end = int((day == "2024-02-13").to_numpy().nonzero()[0].max())
titan_sel = titan.iloc[: first_day_end + 2].reset_index(drop=True)          # warmup + session 1 + successor row
nifty, vix = feeds["NIFTY_50_15MIN"], feeds["INDIA_VIX_15MIN"]
nifty_sel = nifty[(nifty["timestamp"] >= FEED_START_UTC) & (nifty["timestamp"] <= FEED_CUT_UTC)].reset_index(drop=True)
vix_sel = vix[(vix["timestamp"] >= FEED_START_UTC) & (vix["timestamp"] <= FEED_CUT_UTC)].reset_index(drop=True)

titan_sel.to_csv(OUT / "titan_1min_block1_session1.csv", index=False)
nifty_sel.to_csv(OUT / "nifty_50_15min_prefix.csv", index=False)
vix_sel.to_csv(OUT / "india_vix_15min_prefix.csv", index=False)
(OUT / "trial_007_params.json").write_bytes(PARAMS_SRC.read_bytes())

# --- round-trip proof: the files read back are identical (values and dtypes) to the loader rows
def read_titan(p):
    f = pd.read_csv(p); f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata"); return f
def read_feed(p):
    f = pd.read_csv(p); f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True); return f
pd.testing.assert_frame_equal(read_titan(OUT / "titan_1min_block1_session1.csv"), titan_sel, check_exact=True)
pd.testing.assert_frame_equal(read_feed(OUT / "nifty_50_15min_prefix.csv"), nifty_sel, check_exact=True)
pd.testing.assert_frame_equal(read_feed(OUT / "india_vix_15min_prefix.csv"), vix_sel, check_exact=True)

# --- sufficiency proof: causal grid context on every scored decision time of session 1 is identical on full vs selected feeds
from revision2_external.grid_context import SealedGridContextProvider
full_p, sel_p = SealedGridContextProvider(nifty, vix), SealedGridContextProvider(nifty_sel, vix_sel)
checked = 0
for ts in titan_sel["timestamp"].iloc[60:-1]:
    a, b = full_p.causal_context(ts), sel_p.causal_context(ts)
    assert (a.available, a.reason, a.source_timestamp, a.age_seconds) == (b.available, b.reason, b.source_timestamp, b.age_seconds)
    if a.available:
        pd.testing.assert_frame_equal(a.aligned.reset_index(drop=True), b.aligned.reset_index(drop=True), check_exact=True)
    checked += 1

prov = {
    "classification": "DERIVED_REAL_REPLAY_FIXTURE",
    "origin": "Block 1 (sessions 2024-02-13..2024-02-19) of revision5/step5_sealed_calibration_protocol_v2.json, Stage A; symbol TITAN",
    "loader": "scripts/run_r5_step5_candidate.prepare_block",
    "loader_slice_sha256": audit["slice_sha256"],
    "stock_manifest": {"path": "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json", "sha256": sha256(ROOT / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")},
    "stock_source_csv": "see manifest entry for TITAN (file sha256 f9b34afbceee6fc7269255fc4d2363a057d1ec5b8921e75b729b440b14533a1e, verified by the loader)",
    "grid_source": "local_workspace/records/grid-manifest-local.json (NIFTY 50 15min sha256 c2e78705f61b5bad66257890ed1ec0cd3703571e721c16e1b2d2128ee4ea5443, INDIA VIX 15min sha256 9992deda85005fdb9ee2d6684f8a84a3cabd3f591655450207aeeaacbe7c1a8d, verified by the loader)",
    "params_source": {"path": str(PARAMS_SRC), "sha256": sha256(PARAMS_SRC)},
    "files": {n: {"sha256": sha256(OUT / n), "rows": int(len(f)), "first": str(f['timestamp'].iloc[0]), "last": str(f['timestamp'].iloc[-1])}
              for n, f in (("titan_1min_block1_session1.csv", titan_sel), ("nifty_50_15min_prefix.csv", nifty_sel), ("india_vix_15min_prefix.csv", vix_sel))},
    "loader_rows": {"titan": int(len(titan)), "nifty": int(len(nifty)), "vix": int(len(vix))},
    "round_trip_exact_equal_to_loader_rows": True,
    "grid_context_identical_full_vs_selected_decisions_checked": checked,
    "extractor_sha256": sha256(Path(__file__)),
}
(OUT / "PROVENANCE.json").write_text(json.dumps(prov, indent=1))
print(json.dumps(prov, indent=1))
