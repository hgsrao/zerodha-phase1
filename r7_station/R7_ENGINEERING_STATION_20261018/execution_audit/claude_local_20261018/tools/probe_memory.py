import sys
from dataclasses import asdict
import pandas as pd
sys.path.insert(0, '.')
from revision7_avr import Revision7Engine
from revision7_avr.conftest import make_bar
from revision7_avr.r7_state import ControlConfig
cfg = ControlConfig('INFY', 'GTG2_TECH_TELECOM', 0.02)
e = Revision7Engine(['INFY'], r7_configs=[cfg], r7_decision_memory=5)
e._governor_grid_return = lambda ts: 0.002
bars = pd.DataFrame([{k: v for k, v in asdict(make_bar(i)).items() if k != 'index'} for i in range(390)])
e.run({'INFY': bars}, warmup=60)
ev = [x for x in e.controller_telemetry if x.get('event_type') == 'R7_COMPLETED_BAR']
print("telemetry_mode=", e.telemetry_mode, "| r7_decision_count=", e.r7_decision_count, "| r7_decisions window=", len(e.r7_decisions),
      "| R7_COMPLETED_BAR events retained in native controller_telemetry=", len(ev))
