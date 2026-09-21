"""
Revision-5 Filtered Supervisory Bridge
======================================

Purpose
-------
Expose bounded/advisory outputs from the Revision-2 External Dynamic
Parameter Controller (DPC) to Revision 5 without allowing that controller
to mutate Revision-5 protection state or frozen certification controls.

Authority hierarchy
-------------------
1. DynamicParameterController may COMPUTE requests/advisories.
2. This bridge classifies and validates those requests.
3. Revision-5 components decide whether/how to consume them.
4. Hard protection and frozen certification invariants remain authoritative.

This module performs NO broker I/O and mutates NO Revision-5 plant object.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping, Tuple

from canonical_parameter_registry import (
    CanonicalParameterRegistry,
)
from revision2_external.dynamic_parameter_controller import (
    DynamicParameterController,
    MarketEnvironmentState,
)
from revision2_external.startup_validation import (
    validate_runtime_parameters,
    validate_safety_contract,
)
from revision2.boxes import DataIngestionBox
from revision2.contracts import EffectiveConfig


@dataclass(frozen=True)
class SupervisorySnapshot:
    """
    Immutable classification of one DPC evaluation.

    strategy_advisory:
        Strategy/planning values which may later be consumed through an
        explicit Revision-5 interface. Nothing here is auto-applied.

    execution_advisory:
        Execution/cost-model values. These must not be applied by the
        Central Plant DCS.

    blocked:
        DPC outputs explicitly denied from direct Revision-5 actuation.

    tier3_pid_schedule:
        Calculated only for audit/telemetry comparison. Revision-5
        governor gains remain frozen during certification.
    """

    strategy_advisory: Mapping[str, float | int]
    execution_advisory: Mapping[str, float]
    blocked: Mapping[str, str]

    tier3_pid_schedule: Tuple[
        float,
        float,
        float,
    ]


@dataclass(frozen=True)
class UpstreamAdmissionSnapshot:
    """Fail-closed BB01/BB02 result for an intent headed to R5 admission.

    This is deliberately a precondition only.  It neither selects a bay nor
    calls the plant, so Governor, AVR, and protection retain their authority.
    """

    admitted: bool
    reason: str
    config_hash: str | None
    consumed_parameters: Tuple[str, ...]


@dataclass(frozen=True)
class BB03CertificationSnapshot:
    """Information-only record of a successful external BB03 certification."""

    symbol: str
    input_rows: int
    output_rows: int
    exact_duplicates_removed: int
    authority: str = "INFORMATION_ONLY"


@dataclass(frozen=True)
class BB04AnalyticsSnapshot:
    """Information-only copy of BB04 analytics; it is not a trade decision."""

    symbol: str
    timestamp: str
    direction: int
    confidence: float
    momentum: float
    volatility: float
    vwap_deviation: float
    volume_confirmation: float
    exit_confidence: float
    quality_band: str
    authority: str = "INFORMATION_ONLY"


@dataclass(frozen=True)
class BB03BB04SupervisorySnapshot:
    """Typed upstream certification and analytics state exposed to R5."""

    certification: BB03CertificationSnapshot
    analytics: BB04AnalyticsSnapshot


@dataclass(frozen=True)
class BB05BB06SupervisorySnapshot:
    """External admission/plan telemetry only; never an R5 trade instruction."""

    symbol: str
    admission_approved: bool
    admission_reason: str
    confidence: float
    risk_reward_ratio: float
    proposed_side: str | None
    proposed_entry: float | None
    proposed_stop: float | None
    proposed_target: float | None
    minimum_hold_bars: int | None
    maximum_hold_bars: int | None
    authority: str = "INFORMATION_ONLY"


class Revision5SupervisoryBridge:
    """
    Fail-closed boundary between the legacy DPC and Revision 5.
    """

    def __init__(
        self,
        registry: CanonicalParameterRegistry | None = None,
    ):
        self.registry = (
            registry
            if registry is not None
            else CanonicalParameterRegistry()
        )

    @staticmethod
    def _finite_float(
        name: str,
        value: object,
    ) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{name} must be numeric"
            ) from exc

        if not isfinite(number):
            raise ValueError(
                f"{name} must be finite"
            )

        return number

    def evaluate_upstream_admission(
        self,
        *,
        symbol: str,
        runtime_parameters: Mapping[str, Any],
        safety_parameters: Mapping[str, Any],
    ) -> UpstreamAdmissionSnapshot:
        """Validate BB01 then apply BB02 before a harness calls R5 entry.

        BB01 owns complete registry-backed startup validation. BB02 owns the
        allow/deny universe decision. Both are upstream and cannot mutate the
        plant or substitute for its Governor and protection checks.
        """
        runtime_values = dict(runtime_parameters)
        safety_values = dict(safety_parameters)
        runtime_errors = validate_runtime_parameters(
            self.registry,
            runtime_values,
        )
        safety_errors = validate_safety_contract(
            self.registry,
            safety_values,
        )
        safety_errors += self.registry.validate_execution_payload(
            safety_values,
        )
        if runtime_errors or safety_errors:
            return UpstreamAdmissionSnapshot(
                admitted=False,
                reason=(
                    "BB01_STARTUP_REJECTED:"
                    + "; ".join(runtime_errors + safety_errors)
                ),
                config_hash=None,
                consumed_parameters=(),
            )

        config = EffectiveConfig.build(
            runtime_values,
            registry_hash=self.registry.FROZEN_IDENTITY_SHA256,
        )
        admitted, reason, trace = DataIngestionBox().admit(
            symbol,
            config,
        )
        return UpstreamAdmissionSnapshot(
            admitted=admitted,
            reason=("BB02_" + reason.upper().replace(" ", "_")),
            config_hash=config.config_hash,
            consumed_parameters=tuple(
                use.parameter for use in trace
            ),
        )

    def snapshot_bb03_bb04(
        self,
        *,
        certification_audit: Mapping[str, object],
        analytics_signal: object,
    ) -> BB03BB04SupervisorySnapshot:
        """Expose BB03/BB04 outputs as immutable supervisory information.

        The caller supplies only a successful BB03 audit and BB04 signal.
        This method neither evaluates an entry nor touches any R5 plant
        control, protection, or governor object.
        """
        required_audit = (
            "input_rows",
            "output_rows",
            "exact_duplicates_removed",
        )
        missing = [
            name for name in required_audit
            if name not in certification_audit
        ]
        if missing:
            raise ValueError(
                "BB03 audit missing: " + ", ".join(missing)
            )

        symbol = str(getattr(analytics_signal, "symbol"))
        direction = int(getattr(analytics_signal, "direction"))
        if direction not in (-1, 0, 1):
            raise ValueError("BB04 direction must be -1, 0, or 1")

        def finite_signal_field(name: str) -> float:
            return self._finite_float(
                "BB04 " + name,
                getattr(analytics_signal, name),
            )

        certification = BB03CertificationSnapshot(
            symbol=symbol,
            input_rows=int(certification_audit["input_rows"]),
            output_rows=int(certification_audit["output_rows"]),
            exact_duplicates_removed=int(
                certification_audit["exact_duplicates_removed"]
            ),
        )
        analytics = BB04AnalyticsSnapshot(
            symbol=symbol,
            timestamp=str(getattr(analytics_signal, "timestamp")),
            direction=direction,
            confidence=finite_signal_field("confidence"),
            momentum=finite_signal_field("momentum"),
            volatility=finite_signal_field("volatility"),
            vwap_deviation=finite_signal_field("vwap_deviation"),
            volume_confirmation=finite_signal_field(
                "volume_confirmation"
            ),
            exit_confidence=finite_signal_field("exit_confidence"),
            quality_band=str(getattr(analytics_signal, "quality_band")),
        )
        return BB03BB04SupervisorySnapshot(
            certification=certification,
            analytics=analytics,
        )

    def snapshot_bb05_bb06(self, *, symbol, decision, plan=None):
        """Copy existing external outputs; no plant, governor or execution call."""
        side = None if plan is None else plan.side
        if side not in (None, "BUY", "SELL"):
            raise ValueError("BB06 proposed side must be BUY or SELL")
        if plan is not None and not decision.approved:
            raise ValueError("BB06 plan requires approved external admission")
        def price(name):
            return None if plan is None else self._finite_float(
                "BB06 " + name, getattr(plan, name))
        return BB05BB06SupervisorySnapshot(
            symbol=str(symbol), admission_approved=bool(decision.approved),
            admission_reason=str(decision.reason),
            confidence=self._finite_float("BB05 confidence", decision.confidence),
            risk_reward_ratio=self._finite_float("BB05 reward/risk", decision.risk_reward_ratio),
            proposed_side=side, proposed_entry=price("entry_price"),
            proposed_stop=price("stop_price"), proposed_target=price("target_price"),
            minimum_hold_bars=None if plan is None else int(plan.minimum_hold_bars),
            maximum_hold_bars=None if plan is None else int(plan.maximum_hold_bars),
        )

    def evaluate(
        self,
        env: MarketEnvironmentState,
        *,
        base_kp: float,
        base_ki: float,
        base_kd: float,
        error_delta: float,
    ) -> SupervisorySnapshot:
        """
        Evaluate all three DPC tiers without mutating Revision-5 state.
        """
        kp = self._finite_float(
            "base_kp",
            base_kp,
        )

        ki = self._finite_float(
            "base_ki",
            base_ki,
        )

        kd = self._finite_float(
            "base_kd",
            base_kd,
        )

        error = self._finite_float(
            "error_delta",
            error_delta,
        )

        if kp < 0.0 or ki < 0.0 or kd < 0.0:
            raise ValueError(
                "base PID gains must be non-negative"
            )

        tier1 = (
            DynamicParameterController
            .get_tier1_vol_parameters(env)
        )

        tier2 = (
            DynamicParameterController
            .get_tier2_regime_parameters(env)
        )

        tier3 = (
            DynamicParameterController
            .get_tier3_pid_schedule(
                kp,
                ki,
                kd,
                error,
            )
        )

        strategy: dict[
            str,
            float | int,
        ] = {
            # Tier 1
            "min_atr_floor": self._finite_float(
                "min_atr_floor",
                tier1["min_atr_floor"],
            ),
            "atr_floor_mult": self._finite_float(
                "atr_floor_mult",
                tier1["atr_floor_mult"],
            ),

            # Tier 2
            # Advisory only. No direct DCS mutation.
            "min_hold_bars": int(
                tier2["min_hold_bars"]
            ),
            "max_hold_bars": int(
                tier2["max_hold_bars"]
            ),
        }

        execution = {
            # Execution-cost layer only.
            "dynamic_slippage": self._finite_float(
                "dynamic_slippage",
                tier1["dynamic_slippage"],
            ),
        }

        blocked: dict[str, str] = {}

        # ----------------------------------------------------------
        # Tier-2 entry threshold:
        # Accept only if it satisfies the CURRENT canonical surface.
        # ----------------------------------------------------------

        requested_entry_conf = self._finite_float(
            "entry_confidence_threshold",
            tier2[
                "entry_confidence_threshold"
            ],
        )

        validation_errors = (
            self.registry
            .validate_calibration_payload(
                {
                    "entry_confidence_threshold":
                        requested_entry_conf
                },
                engine="EXTERNAL",
            )
        )

        if validation_errors:
            blocked[
                "entry_confidence_threshold"
            ] = (
                "DPC request rejected by current "
                "CanonicalParameterRegistry: "
                + "; ".join(validation_errors)
            )
        else:
            strategy[
                "entry_confidence_threshold"
            ] = requested_entry_conf

        # ----------------------------------------------------------
        # Tier-2 bay cooldown:
        #
        # Revision 5 owns its machine-bay cooldown semantics.
        # The DPC may report its suggestion, but it cannot actuate it.
        # ----------------------------------------------------------

        requested_cooldown = int(
            tier2["bay_cooldown_bars"]
        )

        blocked[
            "bay_cooldown_bars"
        ] = (
            "DPC requested "
            f"{requested_cooldown} bars; "
            "Revision-5 machine-bay cooldown is "
            "a fixed certification/protection invariant"
        )

        # ----------------------------------------------------------
        # Tier-3:
        #
        # Compute for telemetry/parity auditing only.
        # Never mutate BAY_GOVERNOR_SPECS from this bridge.
        # ----------------------------------------------------------

        scheduled = tuple(
            self._finite_float(
                f"tier3_pid_schedule[{index}]",
                value,
            )
            for index, value
            in enumerate(tier3)
        )

        blocked[
            "tier3_pid_schedule"
        ] = (
            "Tier-3 schedule calculated for audit telemetry only; "
            "Revision-5 governor PID gains remain frozen during "
            "certification"
        )

        return SupervisorySnapshot(
            strategy_advisory=MappingProxyType(
                strategy
            ),
            execution_advisory=MappingProxyType(
                execution
            ),
            blocked=MappingProxyType(
                blocked
            ),
            tier3_pid_schedule=scheduled,
        )
