"""Real fixture ledger comparison against an isolated source overlay."""
import sys,json,hashlib,pathlib,tempfile,shutil,subprocess
r=pathlib.Path.cwd(); d=r/'outputs/r5_remediation/continuation'
code='''import sys,pathlib,json,hashlib,tempfile
sys.path.insert(0,sys.argv[1]);sys.path.insert(1,sys.argv[2])
from test_r5_d01_orchestrator_run_integration import build_orchestrator
with tempfile.TemporaryDirectory() as tmp:
 orch,runtime,store,observed,titan,warmup=build_orchestrator(pathlib.Path(tmp))
 report=orch.run({'TITAN':titan},warmup=warmup)
 payload=json.dumps(report['trades'],sort_keys=True,separators=(',',':'),default=str)
 pathlib.Path(sys.argv[3]).write_text(json.dumps({'ledger_sha256':hashlib.sha256(payload.encode()).hexdigest(),'trades':report['trades'],'gross_pnl':report['gross_pnl'],'net_pnl':report['net_pnl']},indent=2,default=str))
'''
with tempfile.TemporaryDirectory(prefix='r5-parity-before-') as tmp:
 t=pathlib.Path(tmp)
 for p in r.iterdir():
  if p.name != 'revision2_external': (t/p.name).symlink_to(p,target_is_directory=p.is_dir())
 (t/'revision2_external').mkdir()
 for p in (r/'revision2_external').iterdir():
  if p.name=='__pycache__': continue
  if p.name=='orchestrator.py': shutil.copyfile(r/'outputs/r5_remediation/activities/F-ROOT-005/before_sources/revision2_external/orchestrator.py',t/'revision2_external'/p.name)
  else: (t/'revision2_external'/p.name).symlink_to(p,target_is_directory=p.is_dir())
 for label,root in [('before',t),('after',r)]:
  result=subprocess.run([sys.executable,'-c',code,str(root),str(r/'tests'),str(d/(label+'_ledger.json'))],cwd=tmp,capture_output=True,text=True)
  (d/(label+'_parity.log')).write_text(result.stdout+result.stderr)
  assert result.returncode==0,result.stderr
before=json.loads((d/'before_ledger.json').read_text());after=json.loads((d/'after_ledger.json').read_text())
assert before==after,(before,after)
(d/'ledger_parity.json').write_text(json.dumps({'scope':'D01 real TITAN one-session SELL replay; no full Block 1 claim','exact_ledger_parity':True,'before':before['ledger_sha256'],'after':after['ledger_sha256']},indent=2)+'\n')
print('Exact real-fixture ledger/P&L parity PASS',after['ledger_sha256'])
