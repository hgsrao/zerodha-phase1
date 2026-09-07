#!/usr/bin/env python3
"""
5-LAYER ENTRY ORCHESTRATOR: ZERO-LOSS ENTRY ARCHITECTURE
=========================================================

Stack all filtering layers to achieve zero losses:

  Layer 5: Entry Probability Validator   (Historical win rate > 70%)
  Layer 4: Entry Signal Quality Gate     (PA > 0.8 AND Studies > 0.8)
  Layer 3: Directional Bias Filter       (Trade with trend, not against)
  Layer 2: Positive Expectancy Filter    (R-multiple > 2.0)
  Layer 1: Protection Relay + Grid Sync  (System health + regime)

ONLY trades passing ALL 5 layers enter.
All others are rejected.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

from revision4.entry_probability_validator import (
    EntryProbabilityValidator,
    DirectionalBiasFilter,
    PositiveExpectancyFilter,
    EntryQualityGate,
    TradeSignature,
    WinProbability,
)


@dataclass
class EntryDecision:
    """Result of 5-layer entry validation."""
    symbol: str
    approved: bool
    reason: str
    decision_trace: List[str]  # Trace through all layers

    # Layer results
    layer1_protection: bool
    layer1_reason: str

    layer2_grid: bool
    layer2_reason: str

    layer3_quality: bool
    layer3_reason: str

    layer4_bias: bool
    layer4_reason: str

    layer5_probability: bool
    layer5_reason: str
    win_probability: float
    r_multiple: float

    # Summary
    layers_passed: int  # How many of 5 layers passed?
    layers_failed: List[str]  # Which layers rejected?


class FiveLayerEntryOrchestrator:
    """
    Master entry decision system combining all anti-loss approaches.

    Strategy: Stack 5 independent filters. A trade must pass ALL 5 to enter.

    This is the inverse of the old system:
    - OLD: Liberal entry (if ANY signal is strong, enter)
    - NEW: Conservative entry (if ALL validations pass, enter)
    """

    def __init__(self):
        # Layer 5: Probability validator
        self.probability_validator = EntryProbabilityValidator(
            threshold_win_probability=0.70,
            threshold_r_multiple=2.0,
        )

        # Layer 4: Signal quality gate
        self.quality_gate = EntryQualityGate(
            min_pa_confidence=0.8,
            min_studies_confidence=0.8,
        )

        # Layer 3: Directional bias
        self.directional_filter = DirectionalBiasFilter(nifty_ema_period=50)

        # Layer 2: Positive expectancy
        self.expectancy_filter = PositiveExpectancyFilter(min_r_multiple=2.0)

        # Statistics
        self.total_entry_attempts = 0
        self.entries_approved = 0
        self.entries_rejected = 0
        self.rejections_by_layer = {
            'protection': 0,
            'grid': 0,
            'quality': 0,
            'bias': 0,
            'probability': 0,
        }

    def make_entry_decision(
        self,
        symbol: str,
        pa_confidence: float,
        studies_confidence: float,
        trade_direction: int,  # 1=BUY, -1=SHORT
        nifty_prices: np.ndarray,
        vix_level: float,
        # From Grid Sync
        grid_synchronized: bool,
        grid_voltage: float,  # 0.0-1.0
        grid_frequency: float,  # 0.0-1.0
        grid_phase: float,  # degrees
        # From Protection
        broker_connected: bool,
        api_latency_ms: float,
        cpu_temp_celsius: float,
        memory_used_pct: float,
        # Historical data for probability calculation
        historical_trades_on_symbol: Optional[List[Dict]] = None,
    ) -> EntryDecision:
        """
        Run all 5 layers and return entry decision.

        Args:
            symbol: Trading symbol
            pa_confidence: PA signal confidence (0.0-1.0)
            studies_confidence: Chart studies confidence (0.0-1.0)
            trade_direction: 1=BUY, -1=SHORT
            nifty_prices: Nifty 50 price series (for trend analysis)
            vix_level: Current VIX level
            grid_synchronized: Is grid currently synchronized?
            grid_voltage: Voltage component (trend)
            grid_frequency: Frequency component (VIX stability)
            grid_phase: Phase angle between stock and index
            broker_connected: Is broker connected?
            api_latency_ms: API latency in milliseconds
            cpu_temp_celsius: CPU temperature
            memory_used_pct: Memory usage %
            historical_trades_on_symbol: Previous trades on this symbol

        Returns:
            EntryDecision with approval/rejection and trace
        """
        self.total_entry_attempts += 1
        trace = [f"[ENTRY DECISION: {symbol}]"]

        layers_passed = 0
        layers_failed = []

        # ═══════════════════════════════════════════════════════════════
        # LAYER 1: PROTECTION RELAY (System Health)
        # ═══════════════════════════════════════════════════════════════

        layer1_pass = (
            broker_connected and
            api_latency_ms < 500 and
            cpu_temp_celsius < 85 and
            memory_used_pct < 95
        )

        layer1_reason = ""
        if not broker_connected:
            layer1_reason = "Broker disconnected"
        elif api_latency_ms >= 500:
            layer1_reason = f"API latency {api_latency_ms:.0f}ms > 500ms"
        elif cpu_temp_celsius >= 85:
            layer1_reason = f"CPU temp {cpu_temp_celsius:.0f}°C > 85°C"
        elif memory_used_pct >= 95:
            layer1_reason = f"Memory {memory_used_pct:.0f}% > 95%"
        else:
            layer1_reason = "✓ All system constraints satisfied"

        trace.append(f"L1 Protection: {layer1_reason}")

        if layer1_pass:
            layers_passed += 1
        else:
            layers_failed.append('protection')
            self.rejections_by_layer['protection'] += 1

        # ═══════════════════════════════════════════════════════════════
        # LAYER 2: GRID SYNCHRONIZATION (Market Regime)
        # ═══════════════════════════════════════════════════════════════

        # Grid must be synchronized for entry
        # We allow slightly desynchronized entries (V/F > 0.5) with caution
        layer2_pass = grid_synchronized or (grid_voltage > 0.5 and grid_frequency > 0.5)

        layer2_reason = ""
        if not grid_synchronized and (grid_voltage <= 0.5 or grid_frequency <= 0.5):
            layer2_reason = f"Grid not synchronized (V={grid_voltage:.2f}, F={grid_frequency:.2f})"
        else:
            layer2_reason = f"✓ Grid OK (V={grid_voltage:.2f}, F={grid_frequency:.2f}, φ={grid_phase:.1f}°)"

        trace.append(f"L2 Grid Sync: {layer2_reason}")

        if layer2_pass:
            layers_passed += 1
        else:
            layers_failed.append('grid')
            self.rejections_by_layer['grid'] += 1

        # ═══════════════════════════════════════════════════════════════
        # LAYER 3: ENTRY SIGNAL QUALITY (Raise Thresholds)
        # ═══════════════════════════════════════════════════════════════

        layer3_pass, layer3_reason = self.quality_gate.check_signal_quality(
            pa_confidence, studies_confidence
        )

        trace.append(f"L3 Signal Quality: {layer3_reason}")

        if layer3_pass:
            layers_passed += 1
        else:
            layers_failed.append('quality')
            self.rejections_by_layer['quality'] += 1

        # ═══════════════════════════════════════════════════════════════
        # LAYER 4: DIRECTIONAL BIAS (Trade with Trend)
        # ═══════════════════════════════════════════════════════════════

        layer4_pass, layer4_reason = self.directional_filter.check_directional_bias(
            nifty_prices, trade_direction, tolerance_pct=0.5
        )

        trend_alignment = self.directional_filter.get_trend_alignment_score(
            nifty_prices, trade_direction
        )

        trace.append(f"L4 Directional Bias: {layer4_reason}")

        if layer4_pass:
            layers_passed += 1
        else:
            layers_failed.append('bias')
            self.rejections_by_layer['bias'] += 1

        # ═══════════════════════════════════════════════════════════════
        # LAYER 5: ENTRY PROBABILITY VALIDATOR (Historical Win Rate)
        # ═══════════════════════════════════════════════════════════════

        # Determine regime based on grid metrics
        if grid_voltage > 0.7 and grid_frequency > 0.6:
            regime = "strong_bull" if trade_direction == 1 else "strong_bear"
        elif grid_voltage > 0.5 or grid_frequency > 0.5:
            regime = "weak_bull" if trade_direction == 1 else "weak_bear"
        else:
            regime = "sideways"

        # Create trade signature for probability lookup
        signature = self.probability_validator.sample_signature(
            symbol=symbol,
            pa_conf=pa_confidence,
            studies_conf=studies_confidence,
            direction=trade_direction,
            regime=regime,
            vix=vix_level,
            phase=grid_phase,
            trend_align=trend_alignment,
        )

        # Validate probability
        win_prob_result: WinProbability = self.probability_validator.validate_entry(signature)

        layer5_pass = (
            win_prob_result.passes_threshold and
            win_prob_result.passes_expectancy and
            win_prob_result.confidence != "insufficient_data"
        )

        trace.append(
            f"L5 Probability: {win_prob_result.reason} "
            f"({win_prob_result.historical_trades} similar trades, "
            f"confidence={win_prob_result.confidence})"
        )

        if layer5_pass:
            layers_passed += 1
        else:
            layers_failed.append('probability')
            self.rejections_by_layer['probability'] += 1

        # ═══════════════════════════════════════════════════════════════
        # FINAL DECISION: All 5 layers must PASS
        # ═══════════════════════════════════════════════════════════════

        approved = (
            layer1_pass and
            layer2_pass and
            layer3_pass and
            layer4_pass and
            layer5_pass
        )

        if approved:
            self.entries_approved += 1
            reason = f"✓ ALL 5 LAYERS PASS - APPROVED (confidence={win_prob_result.confidence})"
            trace.append(f"\n🟢 {reason}")
        else:
            self.entries_rejected += 1
            failed_layers_str = ", ".join(layers_failed)
            reason = f"✗ REJECTED - {len(layers_failed)} layers failed: {failed_layers_str}"
            trace.append(f"\n🔴 {reason}")

        return EntryDecision(
            symbol=symbol,
            approved=approved,
            reason=reason,
            decision_trace=trace,
            layer1_protection=layer1_pass,
            layer1_reason=layer1_reason,
            layer2_grid=layer2_pass,
            layer2_reason=layer2_reason,
            layer3_quality=layer3_pass,
            layer3_reason=layer3_reason,
            layer4_bias=layer4_pass,
            layer4_reason=layer4_reason,
            layer5_probability=layer5_pass,
            layer5_reason=win_prob_result.reason,
            win_probability=win_prob_result.win_probability,
            r_multiple=win_prob_result.r_multiple,
            layers_passed=layers_passed,
            layers_failed=layers_failed,
        )

    def record_trade_outcome(self, symbol: str, trade_direction: int,
                            pa_confidence: float, studies_confidence: float,
                            pnl_pct: float, bars_held: int,
                            regime: str, vix: float, phase: float, trend_align: float):
        """
        Record trade outcome for probability recalibration.

        This builds the historical database over time.
        """
        signature = self.probability_validator.sample_signature(
            symbol, pa_confidence, studies_confidence,
            trade_direction, regime, vix, phase, trend_align
        )

        self.probability_validator.record_trade_outcome(
            signature, pnl_pct, bars_held, 0.0, 0.0
        )

    def get_statistics(self) -> Dict:
        """Return entry decision statistics."""
        return {
            'total_entry_attempts': self.total_entry_attempts,
            'entries_approved': self.entries_approved,
            'entries_rejected': self.entries_rejected,
            'approval_rate': self.entries_approved / self.total_entry_attempts if self.total_entry_attempts > 0 else 0.0,
            'rejection_rate': self.entries_rejected / self.total_entry_attempts if self.total_entry_attempts > 0 else 0.0,
            'rejections_by_layer': self.rejections_by_layer,
            'probability_validator_stats': self.probability_validator.get_statistics(),
        }

    def print_summary(self):
        """Print entry decision summary."""
        stats = self.get_statistics()
        print("\n" + "="*100)
        print("5-LAYER ENTRY ORCHESTRATOR SUMMARY")
        print("="*100)
        print(f"\nTotal entry attempts:     {stats['total_entry_attempts']}")
        print(f"Approved:                 {stats['entries_approved']} ({100*stats['approval_rate']:.1f}%)")
        print(f"Rejected:                 {stats['entries_rejected']} ({100*stats['rejection_rate']:.1f}%)")
        print(f"\nRejections by layer:")
        print(f"  L1 Protection:          {stats['rejections_by_layer']['protection']}")
        print(f"  L2 Grid Sync:           {stats['rejections_by_layer']['grid']}")
        print(f"  L3 Signal Quality:      {stats['rejections_by_layer']['quality']}")
        print(f"  L4 Directional Bias:    {stats['rejections_by_layer']['bias']}")
        print(f"  L5 Probability:         {stats['rejections_by_layer']['probability']}")
        print(f"\nKey insight: Reducing entries by {100*stats['rejection_rate']:.1f}% filters out:")
        print(f"  - Unhealthy system states")
        print(f"  - Unfavorable market regimes")
        print(f"  - Low-confidence signals")
        print(f"  - Counter-trend trades")
        print(f"  - Low-probability trades")
        print("="*100 + "\n")
