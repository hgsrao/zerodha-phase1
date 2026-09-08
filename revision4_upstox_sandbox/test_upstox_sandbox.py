from upstox_sandbox_adapter import UpstoxSandboxClient
from revision4_engine import Revision4Orchestrator

if __name__ == "__main__":
    client = UpstoxSandboxClient(sandbox=True)
    symbols = ["INFY", "MARUTI", "TCS"]
    orchestrator = Revision4Orchestrator(
        symbols=symbols,
        initial_capital=100000.0,
        target_daily_pnl=400.0
    )
    orchestrator.execute_backtest_month(client)
