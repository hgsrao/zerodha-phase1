"""Offline smoke/demo using only deterministic measurements (NO market orders)."""
from datetime import datetime, timedelta, timezone
from r6_avr_core import (
    Action, AVRConfig, BenchExciterPlant, CausalID,
    ExposureAVR, ExposureConfig, ExposureSample,
    SafetyPermit, UnitMaster, VoltageAVR, Vote,
)

BAY = "GTG2_TECH_TELECOM"
T0 = datetime(2026, 8, 6, 13, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))

def timestamp(bar):
    return (T0 + timedelta(minutes=bar)).isoformat()

print("=== PHYSICAL-EQUIVALENT AVR: INDEPENDENT EXCITER/VOLTAGE FEEDBACK ===")
controller = VoltageAVR(AVRConfig.experimental(BAY))
plant = BenchExciterPlant(BAY)
command = 1.0
for bar in range(1, 81):
    measured = plant.advance(
        bar_index=bar, timestamp=timestamp(bar), command=command,
        bus_voltage_pu=0.985, load_pu=0, dt=1)
    result = controller.step(measured, dt=1)
    command = result.excitation_command_pu
    if bar in (1, 5, 20, 80):
        print(f"bar={bar} voltage={measured.terminal_voltage_pu:.6f} "
              f"actual_field={measured.field_feedback_pu:.6f} "
              f"command={command:.6f} err={result.voltage_error:.6f}")

print("\n=== PAPER EXPOSURE AVR: FILLS AND ACKNOWLEDGED PENDING ===")
exposure = ExposureAVR(ExposureConfig(BAY, max_utilization=.20,
                                     target_utilization=.12))
ident = CausalID(100, timestamp(100), BAY, "INFY")
state = ExposureSample(identity=ident, bay_capital=1_000_000,
                       filled_notional=0, acknowledged_pending_entry_notional=0,
                       symbol_held_quantity=0, price=1000)
avr_vote = exposure.step(state)
gov_vote = Vote(ident, "GOVERNOR", Action.ENTRY, 100,
                "EXAMPLE_GOVERNOR_PERMIT_NOT_NATIVE_MARK_V")
intent = UnitMaster.decide(
    governor=gov_vote, avr=avr_vote,
    safety=SafetyPermit(True, max_total_quantity=100),
    held_quantity=0, requested_entry_quantity=50,
    market_price=1000, minimum_order_notional=15000)
print("AVR vote:", avr_vote.action.value, avr_vote.max_total_quantity,
      avr_vote.reason)
print("Unit master:", intent.action.value, intent.order_quantity,
      intent.reason)
print("DEMO ONLY: no order is submitted or filled.")
