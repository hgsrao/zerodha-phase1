import sys,json,dataclasses,collections,hashlib
from pathlib import Path
import pandas as pd
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root))
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator

def clean(x):
    if dataclasses.is_dataclass(x): return {f.name:clean(getattr(x,f.name)) for f in dataclasses.fields(x)}
    if isinstance(x,dict): return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,(tuple,list)): return [clean(v) for v in x]
    if hasattr(x,'item'): return x.item()
    if x is None or isinstance(x,(str,int,float,bool)): return x
    return str(x)
for case in ['infy_first_month']:
    source=root/'output'/case/'INFY_input.csv'
    external=json.loads((root/'output/infy_first_seven_days/report.json').read_text())
    bars=pd.read_csv(source)
    bars.timestamp=pd.to_datetime(bars.timestamp)
    if bars.timestamp.dt.tz is None: bars.timestamp=bars.timestamp.dt.tz_localize('Asia/Kolkata')
    bars.timestamp=bars.timestamp.dt.tz_convert('Asia/Kolkata')
    out=Path(__file__).parent/case
    out.mkdir(exist_ok=True)
    engine=Revision2PortfolioOrchestrator(['INFY'],starting_equity=1_000_000)
    assert engine.config.config_hash==external['config_hash']
    assert engine.safety_contract.contract_hash==external['safety_contract_hash']
    events=[]
    def wrap(obj,name,label):
        orig=getattr(obj,name)
        def call(*args,**kwargs):
            result=orig(*args,**kwargs)
            events.append({'stage':label,'result':clean(result)})
            return result
        setattr(obj,name,call)
    for obj,name,label in [(engine.data_ingestion,'admit','2_admission'),(engine.l2_certifier,'certify','3_certify'),(engine.pa,'evaluate','4_signal'),(engine.id_box,'evaluate','5_decision'),(engine.mpc,'build_plan','6_plan'),(engine.safety_gates_target,'evaluate_pre_sizing','7_pre'),(engine.safety_gates_target,'evaluate_post_sizing','7_post'),(engine.position_manager,'size','8_size'),(engine.p01d,'create_order','9_order'),(engine.entry_decision_engine,'evaluate','7_gates'),(engine.broker,'place_order','10_fill')]: wrap(obj,name,label)
    report=engine.run({'INFY':bars},warmup=60)
    assert report['bars_processed']==len(bars)-61
    assert abs(report['gross_pnl']-sum(t['pnl'] for t in report['trades']))<1e-6
    assert report['completed_trades']==report['fills']==len(report['trades'])
    assert not engine.open_trades
    reasons=collections.Counter('confidence below threshold' if e['result'][0]['reason'].startswith('confidence ') else e['result'][0]['reason'] for e in events if e['stage']=='5_decision')
    mpc=collections.Counter(e['result'][1].get('reason','plan produced') for e in events if e['stage']=='6_plan')
    report['run_metadata']={'input':str(source),'input_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'first_bar':str(bars.timestamp.min()),'last_bar':str(bars.timestamp.max()),'rows':len(bars),'warmup':60,'starting_equity':1_000_000,'config_matches_external':True,'safety_contract_matches_external':True,'open_positions':len(engine.open_trades),'stage_calls':dict(collections.Counter(e['stage'] for e in events)),'id_reasons':dict(reasons),'mpc_reasons':dict(mpc)}
    (out/'report.json').write_text(json.dumps(clean(report),indent=2))
    (out/'box_trace.json').write_text(json.dumps(events,indent=2))
    pd.DataFrame(report['trades']).to_csv(out/'trades.csv',index=False)
    print(case,json.dumps({k:v for k,v in report.items() if k not in ['mtm_equity_curve','parameter_coverage']},default=str),flush=True)
