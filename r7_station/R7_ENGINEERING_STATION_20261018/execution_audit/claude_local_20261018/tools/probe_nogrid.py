import sys; sys.path.insert(0,'.')
from dataclasses import asdict
import pandas as pd
from revision7_avr import Revision7Engine
from revision7_avr.conftest import make_bar
from revision7_avr.r7_state import ControlConfig
e = Revision7Engine(['INFY'], r7_configs=[ControlConfig('INFY','GTG2_TECH_TELECOM',0.02)])
e._governor_grid_return = lambda ts: None          # grid context missing
bars = pd.DataFrame([{k:v for k,v in asdict(make_bar(i)).items() if k!='index'} for i in range(200)])
e.run({'INFY': bars}, warmup=60)
d = list(e.r7_decisions)
print("decisions", len(d), "eligible", sum(x['eligible'] for x in d), "non-HOLD pulses", sum(x['pulse']!='HOLD' for x in d),
      "dispatch_available any", any(x['inputs']['dispatch_available'] for x in d), "gate_counts", dict(e.r7_gate_counts))
