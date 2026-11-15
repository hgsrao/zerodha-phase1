import sys, json, warnings
warnings.filterwarnings("ignore")
from dataclasses import asdict
import pandas as pd
from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from revision2_external.grid_context import SealedGridContextProvider
from tests.test_r5_parameter_authority import registry_with
from revision7_avr import Revision7Engine
from revision7_avr.r7_state import ControlConfig, default_configs
from revision7_avr.conftest import make_bar
syms = ['INFY', 'TCS', 'SBIN', 'CIPLA']
times = pd.date_range("2026-08-09 00:00", "2026-08-13 15:30", freq="15min", tz="Asia/Kolkata")
close = 21000.0 + pd.Series(range(len(times))) * 5.0
prov = SealedGridContextProvider(pd.DataFrame({"timestamp": times, "close": close}),
                                 pd.DataFrame({"timestamp": times, "close": 15.0}), minimum_aligned_bars=3)
e = Revision7Engine(syms, r7_configs=default_configs(syms), registry=registry_with(),
                    real_plant_dcs=CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:"),
                    plant_control_mode="PAPER_APPLY", grid_context_provider=prov)
fr = {s: pd.DataFrame([{k: v for k, v in asdict(make_bar(i, close=100 + (j + 1) * 0.04 * i + (i % 7) * 0.03)).items() if k != 'index'}
                       for i in range(200)]) for j, s in enumerate(syms)}
r = e.run(fr, warmup=60)
print(json.dumps({"decision_count": e.r7_decision_count, "decision_digest": e.r7_decision_digest,
                  "counts": dict(sorted(e.r7_gate_counts.items())), "trades": len(r["trades"]),
                  "orders": r["orders_submitted"], "fills": r["fills"]}, sort_keys=True))
