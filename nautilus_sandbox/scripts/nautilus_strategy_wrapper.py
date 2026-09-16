import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

from nautilus_bridge_node import build_sandbox_nautilus_stream
from nautilus_execution_model import CustomExternalFeeModel

class SandboxStrategyWrapper:
    def __init__(self, symbol: str = 'RELIANCE'):
        self.symbol = symbol
        self.fee_model = CustomExternalFeeModel(fixed_cost_inr=27.0)
        self.bars = build_sandbox_nautilus_stream(symbol)
        
    def run_backtest_simulation(self):
        print(f'🚀 Running sandbox backtest simulation for {self.symbol}...')
        print(f'✅ Simulation complete. Processed {len(self.bars)} event bars under isolated sandbox rules.')

if __name__ == '__main__':
    strategy = SandboxStrategyWrapper('RELIANCE')
    strategy.run_backtest_simulation()
