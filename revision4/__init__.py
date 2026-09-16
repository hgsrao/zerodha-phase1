"""
REVISION 04: Proper calibration engine

Complete implementation with:
- Typed contracts (immutable events)
- 48-symbol dataset sealing (with hash validation)
- Real Revision 2 box adapters (not simplified stubs)
- Shared portfolio ledger (₹1 lakh pool)
- Paper broker (order state machine, bar-t+1 fills)
- Hard acceptance tests (all pass before calibration)
- 68-parameter canonical configuration

STATUS: In progress. Not approved for calibration until complete.
"""

from revision4.contracts import (
    Bar, MarketSnapshot, ForecastSignal, IDDecision, TradePlan,
    SizedProposal, OrderIntent, FillEvent, ExitEvent, Position,
    PortfolioSnapshot, DatasetSeal, EffectiveConfig, RunResult,
    OrderState, ExitReason, SignalType
)

from revision4.dataset_seal import DatasetValidator, WarmupLoader, seal_dataset
from revision4.portfolio import PortfolioLedger
from revision4.paper_broker import PaperBroker
from revision4.timestamp_orchestrator import (
    RankedOrderCandidate,
    TimestampOrchestrator,
    TimestampReplayResult,
)
from revision4.pipeline import PipelineAdapter

__all__ = [
    # Contracts
    "Bar", "MarketSnapshot", "ForecastSignal", "IDDecision",
    "TradePlan", "SizedProposal", "OrderIntent", "FillEvent",
    "ExitEvent", "Position", "PortfolioSnapshot", "DatasetSeal",
    "EffectiveConfig", "RunResult", "OrderState", "ExitReason",
    "SignalType",
    # Sealing
    "DatasetValidator", "WarmupLoader", "seal_dataset",
    # Portfolio
    "PortfolioLedger",
    # Broker
    "PaperBroker",
    "RankedOrderCandidate",
    "TimestampOrchestrator",
    "TimestampReplayResult",
    # Pipeline
    "PipelineAdapter",
]

__version__ = "0.4.0"
__status__ = "Prototype (not approved for production)"
