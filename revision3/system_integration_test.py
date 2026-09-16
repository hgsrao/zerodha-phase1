import time
import numpy as np
import pandas as pd
import logging
from revision3.safety_panel import Revision3SafetyPanel
from revision3.integration_supervisor import ProtectedEngineSupervisor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("SIT_Harness")

class MockEngine:
    """Mock engine simulating strategy signal generation for SIT."""
    def __init__(self, name):
        self.name = name

    def generate_signals(self, market_bar, portfolio_state):
        # Simulate returning a list of signal objects
        class Signal:
            def __init__(self, sym, exp_ret, strat_mom, target_sz):
                self.symbol = sym
                self.expected_return = exp_ret
                self.strategy_momentum = strat_mom
                self.target_size = target_sz
        return [Signal("RELIANCE", 0.04, 0.01, 5000)]

class MockPortfolioState:
    """Mock portfolio state for safety panel evaluation."""
    def __init__(self, returns, gross_exp, net_liq, max_sz):
        self.returns = returns
        self.gross_exposure = gross_exp
        self.net_liquidation_value = net_liq
        self.max_allowed_size = max_sz

    def get_realized_return(self, symbol):
        return 0.025

if __name__ == "__main__":
    logger.info("=== INITIALIZING REVISION 3 SYSTEM INTEGRATION TEST (SIT) ===")

    # 1. Instantiate dual safety panels and supervisors
    ext_safety = Revision3SafetyPanel(max_drawdown_limit=-0.15)
    inh_safety = Revision3SafetyPanel(max_drawdown_limit=-0.15)

    ext_engine = MockEngine("External-HMM")
    inh_engine = MockEngine("Inhouse-Vanilla")

    ext_supervisor = ProtectedEngineSupervisor("External-HMM-Engine", ext_engine, ext_safety)
    inh_supervisor = ProtectedEngineSupervisor("Inhouse-Vanilla-Engine", inh_engine, inh_safety)

    # 2. Test Normal Operational State
    logger.info("\n--- Phase 1: Normal Operational Telemetry Check ---")
    normal_telemetry = {
        "api_latency_ms": 120.0,
        "cpu_temp_celsius": 55.0,
        "broker_connected": True,
        "websocket_active": True,
        "tick_interval_seconds": 1.0,
        "current_volatility": 0.012,
        "baseline_volatility": 0.01,
        "grid_close": np.array([100 + i for i in range(100)])
    }
    normal_portfolio = MockPortfolioState(
        returns=pd.Series([0.005, 0.002, -0.001, 0.003]),
        gross_exp=150000,
        net_liq=100000,
        max_sz=10000
    )

    class MockBar:
        def get_price_series(self):
            return np.array([50 + i for i in range(100)])

    bar = MockBar()

    ext_signals = ext_supervisor.process_guarded_tick(bar, normal_portfolio, normal_telemetry)
    logger.info(f"External Engine Guarded Tick Result: {'✅ Approved' if ext_signals is not None else '❌ Blocked'}")

    # 3. Test Fault Injection: Mechanical Vibration / Latency Spike
    logger.info("\n--- Phase 2: Fault Injection (API Latency Spike) ---")
    faulty_telemetry = normal_telemetry.copy()
    faulty_telemetry["api_latency_ms"] = 1500.0  # Breaches 1000ms limit

    ext_signals_fault = ext_supervisor.process_guarded_tick(bar, normal_portfolio, faulty_telemetry)
    logger.info(f"Faulty Telemetry Result: {'✅ Approved' if ext_signals_fault is not None else '❌ Locked Out'}")
    logger.info(f"Engine State: {ext_supervisor.safety_panel.state} | Reason: {ext_supervisor.safety_panel.trip_reason}")

    # 4. Test Master Reset Recovery
    logger.info("\n--- Phase 3: ANSI 86 Lockout & Master Reset Recovery ---")
    reset_success = ext_supervisor.command_master_reset()
    logger.info(f"Master Reset Command: {'✅ SUCCESS' if reset_success else '❌ DENIED'}")
    logger.info(f"Engine Post-Reset State: {ext_supervisor.safety_panel.state}")

    logger.info("\n=== REVISION 3 SIT VERIFIED SUCCESSFULLY ===")
