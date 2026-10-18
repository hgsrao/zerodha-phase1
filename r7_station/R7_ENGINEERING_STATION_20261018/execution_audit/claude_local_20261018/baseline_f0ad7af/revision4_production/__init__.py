"""Revision 04: Production intraday strategy with 10-box architecture"""

from .strategy import Revision04Strategy, TradeSignal
from .backtest import Revision04Backtest, Trade
from .boxes import (
    DataInputBox,
    PABox,
    ChartStudiesBox,
    EntryValidatorBox,
    RiskManagerBox,
    GridSyncBox,
    Position,
    PositionManagerBox,
    ExitDecisionBox,
    MPCBox,
    PerformanceTrackerBox,
)
from .orchestrator import Revision04Orchestrator

__all__ = [
    "Revision04Strategy",
    "TradeSignal",
    "Revision04Backtest",
    "Trade",
    "Revision04Orchestrator",
    # Boxes
    "DataInputBox",
    "PABox",
    "ChartStudiesBox",
    "EntryValidatorBox",
    "RiskManagerBox",
    "GridSyncBox",
    "PositionManagerBox",
    "Position",
    "ExitDecisionBox",
    "MPCBox",
    "PerformanceTrackerBox",
]
