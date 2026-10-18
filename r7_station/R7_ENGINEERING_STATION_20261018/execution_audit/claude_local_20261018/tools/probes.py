import random, json, copy, itertools, math
from dataclasses import asdict
from revision7_avr.r7_state import ControlConfig, Bar, Context, default_configs, DROOP_LIMITS
from revision7_avr.r7_coordinated_controller import PulseFSM, SymbolController
from revision7_avr.r7_allocator import allocate_headroom, approve_target, increase_demand_pu, Utilization
from revision7_avr.test_r7_pulse_fsm import *  # noqa (ensures importable)
R = {}
def rec(k, ok, note=""): R[k] = {"ok": bool(ok), "note": note}; print(("PASS " if ok else "FAIL ")+k, note)
cfg = ControlConfig('INFY', 'GTG2_TECH_TELECOM', 0.02)
# P1 FSM: random-walk fuzz, never BUY->SELL directly without >=2 consecutive opposing emergency samples
random.seed(7); viol = 0; flips = 0
for trial in range(300):
    f = PulseFSM(cfg); hist = []
    for i in range(200):
        s = random.choice([-0.4, -0.3, -0.1, 0, 0.1, 0.3, 0.4]) if random.random() < .6 else random.uniform(-1, 1)
        prev = f.pulse; p, _ = f.step(s); hist.append((s, prev, p))
        if prev in ('BUY','SELL') and p in ('BUY','SELL') and p != prev:
            flips += 1
            # require two consecutive emergency samples
            e = (lambda x: x <= -cfg.emergency) if prev == 'BUY' else (lambda x: x >= cfg.emergency)
            if not (len(hist) >= 2 and e(hist[-1][0]) and e(hist[-2][0])): viol += 1
rec("fsm_reversal_needs_two_consecutive_emergency", viol == 0 and flips > 0, f"flips={flips} violations={viol}")
# P2 FSM: ineligible resets emergency_age; emergency counter reset by non-emergency sample
f = PulseFSM(cfg); f.step(0.3); f.step(-0.3); f.step(0.0, eligible=False); 
rec("fsm_ineligible_resets_emergency_age", f.emergency_age == 0 and f.pulse == 'HOLD')
f = PulseFSM(cfg); f.step(0.3); f.step(-0.3); f.step(0.3); p = f.step(-0.3)[0]
rec("fsm_non_emergency_sample_breaks_streak", p == 'BUY', f"pulse={p}")
# P3 allocator invariants fuzz
random.seed(11); bad = []
for t in range(3000):
    n = random.randint(1, 12); syms = [f"S{i}" for i in range(n)]
    d = {s: random.choice([0, random.uniform(0, 100)]) for s in syms}; w = {s: random.uniform(.01, 5) for s in syms}
    H = random.uniform(0, 300) * random.choice([0, 1, 1])
    a, case = allocate_headroom(d, w, H)
    tot = sum(a.values())
    if tot > H + 1e-6 or any(a[s] > d[s] + 1e-9 or a[s] < -1e-12 for s in syms): bad.append(('bound', t))
    if case == 'FITS' and any(abs(a[s]-d[s]) > 1e-9 for s in syms): bad.append(('fits', t))
    if case == 'OVERSUBSCRIBED' and abs(tot - H) > 1e-6: bad.append(('exhaust', t, tot, H))
    items = list(d.items()); random.shuffle(items)
    a2, _ = allocate_headroom(dict(items), w, H)
    if any(abs(a[s]-a2[s]) > 1e-9 for s in syms): bad.append(('order', t))
rec("allocator_bounds_fits_exhaust_order_independence", not bad, f"3000 cases, violations={bad[:5]}")
# P4 approve_target properties
bad = 0
for _ in range(5000):
    r = random.uniform(-1, 1); a = random.uniform(-2, 2); cap = random.choice([None, 0.0, random.uniform(0, 1)])
    t = approve_target(r, a, cap)
    if r > 0 and t < 0 or r < 0 and t > 0: bad += 1
    if r != 0 and a * r < 0 and t != 0: bad += 1
    if cap is not None and r*a >= 0 and abs(t) > abs(a)*(1 if abs(a)*(1 if r*a>0 else 0) else 0) + cap + 1e-9 and abs(t) > abs(r) + 1e-9: bad += 1
rec("approve_target_sign_flat_on_opposition_cap", bad == 0, f"violations={bad}")
# P5 utilization fail-closed validation
for args in [(-1, 0, 1), (0, -1, 1), (0, 0, 0), (float('nan'), 0, 1)]:
    try: Utilization(*args); rec(f"util_reject_{args}", False)
    except ValueError: rec(f"util_reject_{args}", True)
u = Utilization(150, 0, 100); rec("util_not_clamped", u.actual == 1.5 and u.headroom_notional == 0)
# P6 causality: decision at bar k unchanged by future bars
def mkbar(i, c): 
    from conftest import make_bar
    return make_bar(i, 100 + (i % 7), c)
import importlib; mk = importlib.import_module('revision7_avr.conftest').make_bar
def run(n, seed):
    random.seed(seed); c = SymbolController(cfg); out = []
    for i in range(n):
        b = mk(i, volume=100 + random.random()*50, close=100 + math.sin(i/5)*2 + i*0.01)
        out.append(c.step(b, Context(0.0, 0.5*math.sin(i/9), 0.3)))
    return out
a, b = run(80, 1), run(120, 1)
rec("causality_prefix_invariant", all(json.dumps(asdict(x), sort_keys=True) == json.dumps(asdict(y), sort_keys=True) for x, y in zip(a, b[:80])))
# P7 idempotency: same bar same input returns same; conflicting raises
c = SymbolController(cfg); b0 = mk(0); ctx = Context(0.0, .5, .3); r1 = c.step(b0, ctx); r2 = c.step(b0, ctx)
rec("idempotent_same_bar", r1 is r2 and c.steps == 1)
try: c.step(b0, Context(0.1, .5, .3)); rec("conflicting_same_bar_rejected", False)
except ValueError: rec("conflicting_same_bar_rejected", True)
try: c.step(mk(0, 101), ctx); rec("conflicting_ohlcv_rejected", False)
except ValueError: rec("conflicting_ohlcv_rejected", True)
# P8 checkpoint/restore: determinism + atomic failure + legacy payload + corrupt rejection
ca = SymbolController(cfg)
for i in range(40): ca.step(mk(i, 100+(i%5)*3), Context(0.0, .6, .4))
snap = json.loads(json.dumps(ca.snapshot())); cb = SymbolController(cfg); cb.restore(snap)
fa = [asdict(ca.step(mk(i, 100+(i%4)), Context(0.1, -.5, .2))) for i in range(40, 70)]
fb = [asdict(cb.step(mk(i, 100+(i%4)), Context(0.1, -.5, .2))) for i in range(40, 70)]
rec("restore_reproduces_future", fa == fb)
legacy = copy.deepcopy(snap); legacy['pulse'].pop('emergency_age', None)
cl = SymbolController(cfg); 
try: cl.restore(legacy); rec("restore_accepts_pre_emergency_age_checkpoint", cl.fsm.emergency_age == 0)
except Exception as e: rec("restore_accepts_pre_emergency_age_checkpoint", False, repr(e))
for name, mut in [("nan", lambda s: s['governor'].__setitem__('integral', float('nan'))),
                  ("neg_emergency_age", lambda s: s['pulse'].__setitem__('emergency_age', -1)),
                  ("float_emergency_age", lambda s: s['pulse'].__setitem__('emergency_age', 1.5)),
                  ("bad_pulse", lambda s: s['pulse'].__setitem__('pulse', 'X')),
                  ("schema", lambda s: s.__setitem__('schema', 1))]:
    s2 = copy.deepcopy(snap); mut(s2); tgt = SymbolController(cfg); before = json.dumps(tgt.snapshot(), sort_keys=True)
    try: tgt.restore(s2); ok = False
    except Exception: ok = json.dumps(tgt.snapshot(), sort_keys=True) == before
    rec(f"restore_rejects_atomically_{name}", ok)
# huge emergency_age accepted? (upper bound not enforced)
s2 = copy.deepcopy(snap); s2['pulse']['emergency_age'] = 10**9; t = SymbolController(cfg)
try: t.restore(s2); rec("restore_bounds_emergency_age_upper", False, "accepted emergency_age=1e9 (no upper bound; harmless: only >=2 matters)")
except Exception: rec("restore_bounds_emergency_age_upper", True)
# droop limits
for r in (0.019, 0.101): 
    try: ControlConfig('INFY','GTG2_TECH_TELECOM',0.02,droop_r=r); rec(f"droop_reject_{r}", False)
    except ValueError: rec(f"droop_reject_{r}", True)
json.dump(R, open(__import__('sys').argv[1], 'w'), indent=1)
