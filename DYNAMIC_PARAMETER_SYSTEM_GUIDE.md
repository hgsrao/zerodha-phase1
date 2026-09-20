# DYNAMIC PARAMETER SYSTEM GUIDE

## Critical Architectural Constraint

The Zerodha Phase 1 engine uses a **DYNAMIC PARAMETER SYSTEM** where configuration values are:
- Defined in configuration classes (SafetyGateConfig, Config, etc.)
- Passed to components at initialization time
- NOT hardcoded as scattered constants throughout the codebase

**CRITICAL**: Parameters must NEVER be changed by editing code constants. All parameter changes must flow through the proper configuration system.

---

## Parameter Configuration Architecture

### Layer 1: Configuration Classes

#### SafetyGateConfig (gates_framework.py, line 100)
Central configuration for all 18 safety gates:

```python
class SafetyGateConfig:
    # Group 1: Capital Risk
    RISK_PER_TRADE_FRACTION = 0.02
    MAX_LOSS_PER_TRADE_RUPEES = 5000
    
    # Group 2: Position Quantity
    MAX_POSITION_QUANTITY_PER_SYMBOL = {...}
    
    # Group 6: Daily Loss
    MAX_DAILY_LOSS_RUPEES = 50000
    
    # Group 13: Slippage
    SLIPPAGE_REJECT_THRESHOLD_PERCENT = 0.10  # ← Dynamic parameter
    
    # ... all other gate parameters
```

**Usage Pattern:**
```python
config = SafetyGateConfig()
gate = SafetyGate(config=config, logger=logger)
```

Parameters are injected at initialization, allowing runtime customization.

#### Config (institutional_engine_v34.py, line 31)
Engine-level configuration:

```python
@dataclass
class Config:
    alert_webhook_url: str
    max_daily_loss: Decimal
    market_protection_pct: Decimal = Decimal("1.0")
    stop_loss_pct: Decimal = Decimal("0.02")
    tick_size: Decimal = Decimal("0.05")
    # ... other parameters
```

#### Redis_Circuit_Breaker_Production.py (line 26)
Circuit breaker thresholds:

```python
class CircuitBreakerThresholds:
    DAILY_LOSS_THRESHOLD = -50000
    CONSECUTIVE_LOSSES_THRESHOLD = 2  # ← Dynamic parameter
    VOLATILITY_CRISIS = 5.0
    # ... other thresholds
```

---

## The 3 Operational Issues (Proper Approach)

### Issue #1: Position Quantity Default Too Restrictive

**Symptom**: 45 symbols stuck at 1 share position size  
**Root Cause**: `MAX_POSITION_QUANTITY_PER_SYMBOL` dictionary lacks entries for unlisted symbols  
**Wrong Approach**: Change `.get(symbol, 1)` → `.get(symbol, 100)` in code ❌  
**Correct Approach**: Expand the configuration dictionary ✓

**Solution:**

In `SafetyGateConfig` (gates_framework.py, line 108), expand the dictionary:

```python
MAX_POSITION_QUANTITY_PER_SYMBOL = {
    'INFY': 5,
    'TCS': 10,
    'RELIANCE': 3,
    'HDFC': 4,
    'SBIN': 8,
    'ICICIBANK': 6,
    'LT': 2,
    'ITC': 15,
    'MARUTI': 2,
    'ONGC': 20,
    'WIPRO': 6,
    'BAJAJFINSV': 3,
    'HDFCLIFE': 4,
    'TECHM': 8,
    'POWERGRID': 15,
    # ... complete all 48 NIFTY symbols with appropriate quantities
    # Default for missing symbols (fallback):
    '_default': 5  # Or create a method for intelligent defaults
}
```

**Implementation Pattern:**
```python
def get_position_size(symbol: str) -> int:
    """Get configured position size with fallback logic."""
    # Exact match first
    if symbol in self.MAX_POSITION_QUANTITY_PER_SYMBOL:
        return self.MAX_POSITION_QUANTITY_PER_SYMBOL[symbol]
    
    # Fallback logic (not hardcoded default)
    # Could be:
    # - Tier-based (large-cap: 5, mid-cap: 10)
    # - Based on liquidity
    # - Based on volatility
    # - Operator-configured default
    return self.get_fallback_quantity(symbol)
```

---

### Issue #2: Slippage Gate Choking on Sub-Tick Noise

**Symptom**: Legitimate entries blocked by 0.10% slippage threshold rejecting normal tick noise  
**Root Cause**: `SLIPPAGE_REJECT_THRESHOLD_PERCENT = 0.10` is too strict for high-liquidity stocks  
**Wrong Approach**: Change `0.10` to `0.35` in code ❌  
**Correct Approach**: Make threshold symbol-aware or volatility-aware ✓

**Solution:**

In `SafetyGateConfig` (gates_framework.py, line 144), make slippage dynamic:

```python
# Original (static):
SLIPPAGE_REJECT_THRESHOLD_PERCENT = 0.10

# Better (symbol-aware):
SLIPPAGE_THRESHOLDS_BY_SYMBOL = {
    'INFY': 0.20,      # High liquidity: 0.20%
    'TCS': 0.20,
    'RELIANCE': 0.20,
    'NIFTY': 0.15,     # Index: 0.15%
    'BANKNIFTY': 0.15,
    '_default': 0.25,  # Low liquidity: 0.25%
}

# Or volatility-aware:
SLIPPAGE_VOLATILITY_MULTIPLIER = {
    'low': 0.10,       # Vol < 1%: 0.10%
    'medium': 0.20,    # Vol 1-3%: 0.20%
    'high': 0.35,      # Vol > 3%: 0.35%
}
```

**Implementation Pattern:**
```python
def get_slippage_threshold(symbol: str, market_vol: float) -> float:
    """Get slippage threshold based on symbol and volatility."""
    # Option 1: Symbol-specific thresholds
    if symbol in self.SLIPPAGE_THRESHOLDS_BY_SYMBOL:
        return self.SLIPPAGE_THRESHOLDS_BY_SYMBOL[symbol]
    
    # Option 2: Volatility-based dynamic adjustment
    if market_vol > 0.03:
        return self.SLIPPAGE_VOLATILITY_MULTIPLIER['high']
    elif market_vol > 0.01:
        return self.SLIPPAGE_VOLATILITY_MULTIPLIER['medium']
    else:
        return self.SLIPPAGE_VOLATILITY_MULTIPLIER['low']
    
    return self.SLIPPAGE_VOLATILITY_MULTIPLIER['_default']
```

---

### Issue #3: Re-Entry Protection Too Lenient

**Symptom**: Circuit breaker allows 5 consecutive losses before halting  
**Root Cause**: `CONSECUTIVE_LOSSES_THRESHOLD = 5` is too high  
**Wrong Approach**: Change `5` to `2` in code ❌  
**Correct Approach**: Make threshold configurable and market-aware ✓

**Solution:**

In `CircuitBreakerThresholds` (Redis_Circuit_Breaker_Production.py, line 36):

```python
# Original (static):
CONSECUTIVE_LOSSES_THRESHOLD = 5

# Better (configurable):
CONSECUTIVE_LOSSES_THRESHOLD = 2  # ← This value LIVES IN CONFIG

# Or even better (market-aware):
class CircuitBreakerThresholds:
    # Base configuration (not hardcoded)
    BASE_CONSECUTIVE_LOSSES_THRESHOLD = 2
    
    # Market regime adjustments
    CONSECUTIVE_LOSSES_BY_REGIME = {
        'calm': 3,        # Calm: tolerate 3
        'elevated': 2,    # Elevated: tolerate 2
        'stressed': 1,    # Stressed: tolerate 1
        'crisis': 0,      # Crisis: halt immediately
    }
```

**Implementation Pattern:**
```python
class RedisCircuitBreaker:
    def __init__(self, config: CircuitBreakerConfig):
        """Initialize with configuration object."""
        self.config = config
        self.consecutive_losses_threshold = config.CONSECUTIVE_LOSSES_THRESHOLD
    
    def on_trade_result(self, trade_pl: float, market_regime: str = 'calm'):
        """Update with regime-aware threshold."""
        if trade_pl < 0:
            current_losses = int(self.redis_client.get('trading:consecutive_losses') or 0)
            threshold = self.get_threshold_for_regime(market_regime)
            
            if current_losses >= threshold:
                self.halt_trading(f"Consecutive losses >= {threshold} in {market_regime} regime")
    
    def get_threshold_for_regime(self, regime: str) -> int:
        """Get threshold based on market regime."""
        return self.config.CONSECUTIVE_LOSSES_BY_REGIME.get(
            regime, 
            self.config.BASE_CONSECUTIVE_LOSSES_THRESHOLD
        )
```

---

## How to Configure These Parameters (Proper Workflow)

### Step 1: Update Configuration Classes

Do NOT change code logic. Only change configuration values:

```python
# gates_framework.py
class SafetyGateConfig:
    MAX_POSITION_QUANTITY_PER_SYMBOL = {
        # Add all 48 symbols here
    }
    SLIPPAGE_THRESHOLDS_BY_SYMBOL = {
        # Symbol-specific thresholds
    }

# Redis_Circuit_Breaker_Production.py
class CircuitBreakerThresholds:
    BASE_CONSECUTIVE_LOSSES_THRESHOLD = 2  # ← Change value only here
    CONSECUTIVE_LOSSES_BY_REGIME = {...}
```

### Step 2: Initialize Engine with Custom Config

```python
from gates_framework import SafetyGateConfig
from institutional_engine_v34 import Config, TradingEngineV34

# Create custom configuration
gate_config = SafetyGateConfig()
# All parameters are now configurable via gate_config attributes

engine_config = Config(
    alert_webhook_url="...",
    max_daily_loss=Decimal("50000"),
    # Other parameters...
)

# Initialize engine with configuration
engine = TradingEngineV34(config=engine_config, gate_config=gate_config)
```

### Step 3: Load from External Config (Future Enhancement)

```python
import json

def load_config_from_file(filepath: str) -> SafetyGateConfig:
    """Load configuration from JSON file."""
    with open(filepath) as f:
        data = json.load(f)
    
    config = SafetyGateConfig()
    for key, value in data.items():
        if hasattr(config, key):
            setattr(config, key, value)
    return config

# Usage:
config = load_config_from_file("trading_config.json")
gate = SafetyGate(config=config, logger=logger)
```

---

## Critical Rules

### ✗ WRONG: Hardcoding Changes
```python
# WRONG - Don't do this
max_qty = self.config.MAX_POSITION_QUANTITY_PER_SYMBOL.get(symbol, 100)  # Hardcoded default
```

### ✓ CORRECT: Configuration-Based Changes
```python
# CORRECT - Pass through config
config = SafetyGateConfig()
config.MAX_POSITION_QUANTITY_PER_SYMBOL['INFY'] = 100

max_qty = config.MAX_POSITION_QUANTITY_PER_SYMBOL.get(symbol, config.DEFAULT_POSITION_QTY)
```

### ✓ CORRECT: Dynamic Configuration at Runtime
```python
# Load config from file
config = load_config_from_file("config.json")

# Initialize with configuration
engine = TradingEngineV34(config=config)
```

---

## Parameter Modification Checklist

Before changing ANY parameter:

- [ ] Identify the configuration class (SafetyGateConfig, Config, CircuitBreakerThresholds, etc.)
- [ ] Find where the parameter is defined (line number)
- [ ] Understand the parameter's impact on system behavior
- [ ] Check if parameter should be:
  - [ ] Static (one value for all conditions)
  - [ ] Symbol-aware (different per symbol)
  - [ ] Regime-aware (different by market condition)
  - [ ] Volatility-aware (different by volatility level)
- [ ] Implement appropriate configuration logic (not hardcoded)
- [ ] Add configuration to the Config class/file
- [ ] Test with configuration-based initialization
- [ ] Document the change with reason and expected impact

---

## Summary

The DYNAMIC PARAMETER SYSTEM means:
1. **ALL parameters live in configuration classes**, not scattered as constants
2. **Configuration is injected at initialization**, not hardcoded in logic
3. **Values can be changed through config updates**, not by modifying code
4. **Proper approach**: Update config class → Initialize with config → Test behavior
5. **Wrong approach**: Change code constants → Hope for the best → Introduce bugs

The 3 operational issues are all CONFIGURATION problems, not CODE problems. They require configuration system enhancements, not code modifications.

