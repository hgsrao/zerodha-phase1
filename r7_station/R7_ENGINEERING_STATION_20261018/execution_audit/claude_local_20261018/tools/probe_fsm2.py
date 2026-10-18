from revision7_avr.r7_state import ControlConfig
from revision7_avr.r7_coordinated_controller import PulseFSM
cfg = ControlConfig('INFY','GTG2_TECH_TELECOM',0.02)
def seq(sigs):
    f = PulseFSM(cfg); return [(s, *f.step(s), f.emergency_age, f.active_age) for s in sigs]
print("A) fresh BUY then 2 emergency:", [r[1] for r in seq([0.3,-0.3,-0.3])])
print("B) fresh BUY, emergency, non-emergency strong buy, emergency:", [r[1] for r in seq([0.3,-0.3,0.3,-0.3])])
print("C) aged BUY (age>=hold_bars), then 2 emergency:", [(r[1], r[3]) for r in seq([0.3,0.3,0.3,-0.3,-0.3,-0.3])])
print("D) alternating after entry:", [r[1] for r in seq([0.3]+[(-0.35 if i%2==0 else 0.35) for i in range(8)])])
