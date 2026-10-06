"""Evidence helper; never modifies engine inputs or source files."""
import sys,json,hashlib,pathlib,subprocess,difflib
phase,activity,*files=sys.argv[1:]
r=pathlib.Path.cwd(); d=r/'outputs/r5_remediation/activities'/activity; d.mkdir(parents=True,exist_ok=True)
if phase=='before':
 assert not (d/'before.json').exists(), 'Existing evidence must not be overwritten'
 hashes={str(p.relative_to(r)):hashlib.sha256(p.read_bytes()).hexdigest() for tree in ('revision2_external','revision5','runtime') for p in (r/tree).rglob('*.py')}
 receipt={'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'status':subprocess.check_output(['git','status','--short'],text=True),'hashes':hashes,'files':files}
 for f in files:
  dest=d/'before_sources'/f; dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes((r/f).read_bytes())
 (d/'before.json').write_text(json.dumps(receipt,indent=2)+'\n')
else:
 before=json.loads((d/'before.json').read_text()); hashes={f:hashlib.sha256((r/f).read_bytes()).hexdigest() for f in before['hashes']}
 changed=[f for f in hashes if hashes[f]!=before['hashes'][f]]
 assert set(changed)<=set(before['files']),changed
 patch=''
 for f in before['files']:
  patch+=''.join(difflib.unified_diff((d/'before_sources'/f).read_text().splitlines(True),(r/f).read_text().splitlines(True),fromfile='before/'+f,tofile='after/'+f))
 (d/'isolated.patch').write_text(patch)
 (d/'after.json').write_text(json.dumps({'hashes':hashes,'changed_sources':changed},indent=2)+'\n')
print(activity,phase)
