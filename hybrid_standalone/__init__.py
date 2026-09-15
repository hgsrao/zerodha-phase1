"""
Hybrid Standalone Engine Package
================================
Independent implementation of hybrid trend+mean reversion trading system.
No dependencies on R2 orchestrator.
"""

from .hybrid_engine import (
    HybridStandaloneEngine,
    EngineConfig,
    Signal,
    Trade,
    SignalFilter,
    TradeBuilder,
    ExecutionEngine,
)

__all__ = [
    'HybridStandaloneEngine',
    'EngineConfig',
    'Signal',
    'Trade',
    'SignalFilter',
    'TradeBuilder',
    'ExecutionEngine',
]
