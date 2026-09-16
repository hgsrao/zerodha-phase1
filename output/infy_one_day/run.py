import sys, json, os, dataclasses, collections, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pandas as pd
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision2_external.data_loader_arctic import ArcticMarketDataLoader
root=Path(__file__).resolve().parents[2]
out=Path(__file__).resolve().parent
manifest=json.loads((root/'revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json').read_text())
item=next(x for x in manifest['files'] if x['symbol']=='INFY')
source=Path(manifest['data_dir'])/item['filename']
raw=pd.read_csv(source)
ts=pd.to_datetime(raw['timestamp'])
day=ts.dt.date.max()
one=raw[ts.dt.date==day].copy()
one.to_csv(out/'INFY_input.csv',index=False)
loader=ArcticMarketDataLoader(str(out/'arctic'),str(out))
audit=loader.ingest_symbol('INFY','INFY_input.csv',force=True)
bars=loader.load_symbol('INFY')
# ArcticDB round-trip returns UTC instants without the original timezone.
loaded_ts=pd.to_datetime(bars['timestamp'])
if loaded_ts.dt.tz is None:
    loaded_ts=loaded_ts.dt.tz_localize('UTC')
bars['timestamp']=loaded_ts.dt.tz_convert('Asia/Kolkata')
expected=pd.to_datetime(one['timestamp']).reset_index(drop=True)
if expected.dt.tz is None: expected=expected.dt.tz_localize('Asia/Kolkata')
assert (bars['timestamp'].reset_index(drop=True)==expected).all(), 'Arctic timestamps differ from source'

engine=Revision2ExternalEngineOrchestrator(['INFY'],starting_equity=1_000_000)
events=[]
def clean(x):
    if dataclasses.is_dataclass(x): return clean(dataclasses.asdict(x))
    if isinstance(x,dict): return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)): return [clean(v) for v in x]
    if hasattr(x,'item'): return x.item()
    if isinstance(x,(str,int,float,bool)) or x is None:return x
    return str(x)
def wrap(obj,name,label):
    orig=getattr(obj,name)
    def call(*args,**kwargs):
        result=orig(*args,**kwargs)
        events.append({'stage':label,'result':clean(result)})
        return result
    setattr(obj,name,call)
for obj,name,label in [(engine.data_ingestion,'admit','2_admission'),(engine.pa,'evaluate','4_signal'),(engine.id_box,'evaluate','5_decision'),(engine.id_box,'_current_regime','5_regime'),(engine.mpc,'build_plan','6_plan'),(engine.safety_gates_target,'evaluate_pre_sizing','7_pre'),(engine.safety_gates_target,'evaluate_post_sizing','7_post'),(engine.position_manager,'size','8_size'),(engine.p01d,'create_order','9_order'),(engine.entry_decision_engine,'evaluate','7_gates'),(engine.broker,'place_order','10_fill')]:wrap(obj,name,label)
report=engine.run({'INFY':bars},warmup=60)
report['run_metadata']={'day':str(day),'first_bar':str(bars.timestamp.min()),'last_bar':str(bars.timestamp.max()),'input_rows':len(bars),'warmup_bars':60,'first_evaluation':str(bars.timestamp.iloc[60]),'starting_equity':1_000_000,'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'commit':'96917bc','pythonhashseed':os.environ.get('PYTHONHASHSEED'),'arctic_ingestion':audit,'open_positions_at_end':len(engine.open_trades),'stage_calls':dict(collections.Counter(e['stage'] for e in events))}
(out/'report.json').write_text(json.dumps(clean(report),indent=2))
(out/'box_trace.json').write_text(json.dumps(events,indent=2))
pd.DataFrame(report['trades']).to_csv(out/'trades.csv',index=False)
print(json.dumps({k:v for k,v in report.items() if k not in ['mtm_equity_curve']},indent=2,default=str))
print('ID reasons:',collections.Counter(e['result'][0]['reason'] for e in events if e['stage']=='5_decision'))
print('Regimes:',collections.Counter(e['result'] for e in events if e['stage']=='5_regime'))
