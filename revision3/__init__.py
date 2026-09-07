"""Revision 3: Grid-Tied Trading Architecture

Transitions the trading engine from Island Mode (local symbol evaluation only)
to Grid-Tied Mode (synchronized with macro market conditions via electrical
grid synchronization principles).

Core components:
- MacroGridSynchronizer: Three-parameter synchronization (Voltage/Frequency/Phase)
- MasterProtectionRelay: ANSI-standard electromechanical protections
- Integration layer: Breaker logic between orchestrator and broker

Uses TA-Lib's Hilbert Transform for phase angle detection, treating market
cycles as sine waves with measurable phase relationships.
"""

__version__ = "3.0.0-alpha"
__author__ = "Zerodha ECS Project"

from revision3.macro_grid_synchronizer import (
    MacroGridSynchronizer,
    SynchronizerConfig,
    SynchronizerState,
)

__all__ = [
    "MacroGridSynchronizer",
    "SynchronizerConfig",
    "SynchronizerState",
]
