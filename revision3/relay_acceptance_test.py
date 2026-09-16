import numpy as np
import pandas as pd
import quantstats as qs
from transitions import Machine

class MasterProtectionRelay:
    """
    Revision 3: Master Protection Relay Panel.
    Monitors portfolio physics and enforces a hard state lockout upon a fault.
    """
    states = ['CLOSED', 'OPEN_TRIPPED']
    
    def __init__(self, max_drawdown_limit: float = -0.15):
        self.max_drawdown_limit = max_drawdown_limit
        self.trip_reason = None
        
        # Initialize the Finite State Machine
        self.machine = Machine(model=self, states=MasterProtectionRelay.states, initial='CLOSED')
        
        # Define transitions
        self.machine.add_transition(trigger='trip', source='CLOSED', dest='OPEN_TRIPPED')
        self.machine.add_transition(trigger='reset', source='OPEN_TRIPPED', dest='CLOSED', after='_clear_fault')

    def _clear_fault(self):
        self.trip_reason = None

    def evaluate_telemetry(self, portfolio_returns: pd.Series) -> bool:
        # If already tripped, maintain lockout
        if self.state == 'OPEN_TRIPPED':
            print("❌ RELAY STATUS: OPEN_TRIPPED. Circuit is open. Execution locked.")
            return False

        # Calculate max drawdown using QuantStats
        current_dd = qs.stats.max_drawdown(portfolio_returns)
        print(f"📊 Evaluated Max Drawdown: {current_dd:.2%}")
        
        if current_dd < self.max_drawdown_limit:
            reason = f"ANSI 32 Reverse Power: Drawdown {current_dd:.2%} breached limit {self.max_drawdown_limit:.2%}"
            self._execute_trip(reason)
            return False
            
        print("✅ RELAY STATUS: CLOSED. Plant operating within safe limits.")
        return True

    def _execute_trip(self, reason: str):
        self.trip_reason = reason
        self.trip() # Triggers state machine change to OPEN_TRIPPED
        print(f"\n[!!!] MASTER PROTECTION RELAY TRIPPED [!!!]")
        print(f"Fault Code: {reason}")
        print(f"State Machine Transition: CLOSED ➔ OPEN_TRIPPED\n")

if __name__ == "__main__":
    print("=== REVISION 3 PROTECTION RELAY FACTORY TEST ===")
    relay = MasterProtectionRelay(max_drawdown_limit=-0.15)
    
    # 1. Test Normal Operations (Steady portfolio growth/minor fluctuations)
    print("\n--- Test Phase 1: Normal Operating Returns ---")
    np.random.seed(42)
    normal_returns = pd.Series(np.random.normal(0.001, 0.01, 60), index=pd.date_range(start="2026-01-01", periods=60))
    relay.evaluate_telemetry(normal_returns)
    print(f"Current Relay State: {relay.state}")
    
    # 2. Test Fault Injection (Sudden catastrophic 20% drawdown)
    print("\n--- Test Phase 2: Injecting Catastrophic Drawdown (Fault) ---")
    crash_returns = pd.Series([0.01, -0.05, -0.06, -0.08, -0.04], index=pd.date_range(start="2026-03-01", periods=5))
    relay.evaluate_telemetry(crash_returns)
    print(f"Current Relay State: {relay.state}")
    
    # 3. Test State Lockout (Attempting to trade while tripped)
    print("\n--- Test Phase 3: Verifying State Lockout Under Fault ---")
    relay.evaluate_telemetry(normal_returns)
    
    # 4. Test Manual Reset by Admin
    print("\n--- Test Phase 4: Manual Admin Reset ---")
    print(f"Executing manual reset...")
    relay.reset()
    print(f"Current Relay State after reset: {relay.state}")
    
    # 5. Verify normal operation resumes post-reset
    print("\n--- Test Phase 5: Post-Reset Operation ---")
    relay.evaluate_telemetry(normal_returns)
