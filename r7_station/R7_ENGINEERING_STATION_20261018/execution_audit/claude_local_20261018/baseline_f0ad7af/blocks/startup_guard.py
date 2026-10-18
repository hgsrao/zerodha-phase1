"""
Startup Capability Lock - Prevents accidental live trading connection.
Mandatory authorization before any broker mutation capability is granted.
"""

import os
import logging
from enum import Enum
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


class CapabilityLevel(Enum):
    """Trading capability levels."""
    SANDBOX_ONLY = "sandbox"          # Simulated broker only
    PAPER_ONLY = "paper"              # Read-only Kite mirror
    LIVE_TRADING = "live"             # Mutation-capable (DANGEROUS)


@dataclass
class CapabilityConfig:
    """Capability authorization config."""
    level: CapabilityLevel
    required_env_var: str              # Must be set to proceed
    required_account_allowlist: list   # Only these accounts allowed
    operator_name: str                 # Who authorized this
    authorization_timestamp_ms: int
    require_manual_approval: bool      # Extra gate for LIVE


class StartupCapabilityLock:
    """
    Enforces capability restrictions at startup.

    This is the PRIMARY safety gate. All broker adapters must be
    created through this lock.
    """

    def __init__(self, config: CapabilityConfig):
        self.config = config
        self.validated = False

        self._validate_capability()

    def _validate_capability(self):
        """Validate all requirements before granting capability."""

        # ==================== LEVEL 1: ENVIRONMENT VALIDATION ====================
        env_value = os.environ.get(self.config.required_env_var)

        if not env_value:
            raise RuntimeError(
                f"\n{'='*70}\n"
                f"CAPABILITY LOCK ENGAGED\n"
                f"{'='*70}\n"
                f"Cannot proceed. Required environment variable not set:\n"
                f"  {self.config.required_env_var}\n\n"
                f"To enable {self.config.level.value} capability:\n"
                f"  export {self.config.required_env_var}={self.config.level.value}\n\n"
                f"⚠️  This is NOT production-ready code.\n"
                f"⚠️  Do not enable LIVE_TRADING without formal review.\n"
                f"{'='*70}\n"
            )

        # ==================== LEVEL 2: CAPABILITY MATCH ====================
        if env_value != self.config.level.value:
            raise RuntimeError(
                f"Capability mismatch. Environment says '{env_value}' "
                f"but config requires '{self.config.level.value}'"
            )

        # ==================== LEVEL 3: ACCOUNT ALLOWLIST ====================
        if self.config.required_account_allowlist:
            account_id = os.environ.get("KITE_ACCOUNT_ID")

            if not account_id:
                raise RuntimeError(
                    f"Account allowlist is set, but KITE_ACCOUNT_ID environment variable missing"
                )

            if account_id not in self.config.required_account_allowlist:
                raise RuntimeError(
                    f"Account '{account_id}' is NOT in allowlist: {self.config.required_account_allowlist}\n"
                    f"This is a safety measure. Contact the operator to add this account."
                )

        # ==================== LEVEL 4: LIVE TRADING EXTRA GATE ====================
        if self.config.level == CapabilityLevel.LIVE_TRADING:
            if self.config.require_manual_approval:
                manual_approval = os.environ.get("MANUAL_LIVE_APPROVAL")

                if manual_approval != "YES_I_UNDERSTAND_THE_RISKS":
                    raise RuntimeError(
                        f"\n{'='*70}\n"
                        f"LIVE TRADING REQUIRES MANUAL APPROVAL\n"
                        f"{'='*70}\n"
                        f"To enable live trading, you must explicitly acknowledge the risks:\n"
                        f"\n"
                        f"  export MANUAL_LIVE_APPROVAL=YES_I_UNDERSTAND_THE_RISKS\n"
                        f"\n"
                        f"This is NOT a copy-paste string. It is a deliberate, explicit\n"
                        f"acknowledgment that:\n"
                        f"  1. This code has known defects\n"
                        f"  2. Live trading can cause financial loss\n"
                        f"  3. You are solely responsible for this decision\n"
                        f"  4. This code is not production-certified\n"
                        f"{'='*70}\n"
                    )

            logger.critical(
                f"🔴 LIVE TRADING ENABLED FOR ACCOUNT {os.environ.get('KITE_ACCOUNT_ID')} "
                f"by {self.config.operator_name}"
            )

        self.validated = True
        logger.info(f"✓ Capability lock validated: {self.config.level.value}")

    def create_broker_adapter(self, kite_api_key: str, kite_access_token: str):
        """
        Create appropriate broker adapter based on capability level.

        This is the ONLY way to create a broker adapter.
        """

        if not self.validated:
            raise RuntimeError("Capability not validated. Fix environment and restart.")

        if self.config.level == CapabilityLevel.SANDBOX_ONLY:
            logger.info("Creating SANDBOX broker adapter (simulated)")
            from blocks.brokers.simulated_broker import SimulatedBrokerAdapter
            return SimulatedBrokerAdapter()

        elif self.config.level == CapabilityLevel.PAPER_ONLY:
            logger.info("Creating PAPER broker adapter (read-only Kite mirror)")
            from blocks.brokers.paper_broker import PaperBrokerAdapter
            return PaperBrokerAdapter(kite_api_key, kite_access_token)

        elif self.config.level == CapabilityLevel.LIVE_TRADING:
            logger.critical("⚠️  Creating LIVE broker adapter (MUTATION-CAPABLE)")
            from blocks.brokers.live_broker import LiveBrokerAdapter
            return LiveBrokerAdapter(kite_api_key, kite_access_token)

        else:
            raise ValueError(f"Unknown capability level: {self.config.level}")


# Convenience factory with safe defaults
def create_capability_lock(
    level: str = "sandbox",
    account_allowlist: Optional[list] = None,
) -> StartupCapabilityLock:
    """
    Create a capability lock with sensible defaults.

    Args:
        level: "sandbox" (default), "paper", or "live"
        account_allowlist: List of allowed account IDs (empty = all allowed)

    Returns:
        StartupCapabilityLock if all validations pass
    """

    level_enum = CapabilityLevel[level.upper()]

    config = CapabilityConfig(
        level=level_enum,
        required_env_var="ECS_CAPABILITY_LEVEL",
        required_account_allowlist=account_allowlist or [],
        operator_name=os.environ.get("USER", "unknown"),
        authorization_timestamp_ms=int(__import__('time').time() * 1000),
        require_manual_approval=(level_enum == CapabilityLevel.LIVE_TRADING)
    )

    return StartupCapabilityLock(config)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    # Default to SANDBOX
    try:
        lock = create_capability_lock(level="sandbox")
        print("✓ Capability lock created (SANDBOX mode)")
        print(f"  Level: {lock.config.level.value}")
        print(f"  Validated: {lock.validated}")
    except RuntimeError as e:
        print(f"✗ Lock failed: {e}")
        sys.exit(1)
