# Paper Execution Lifecycle — Path to Production

**Date:** August 30, 2026  
**Context:** MainTradingLoop is simulation only. This roadmap covers the gap to real execution.

---

## Current State: Simulation vs. Reality

### What MainTradingLoop Currently Does

✅ **Simulates:**
- Receives ECS signals (SPEED, VOLTAGE)
- Decides "entry threshold"
- Increments counter
- Logs result as 'SIMULATED'

❌ **Does NOT do:**
- Place any orders (real or paper)
- Track position state (open/closed/partial)
- Match fills against prices
- Handle slippage
- Reconcile with broker
- Recover from failures

---

## Roadmap: Simulation → Paper Execution → Real Execution

### Phase 1: Paper Execution Layer (Weeks 1-3)

**Goal:** Realistic order simulation with state machine

#### 1.1 Order State Machine

```python
# Define order lifecycle
class OrderState(Enum):
    PENDING = "pending"      # Submitted, not filled
    PARTIAL = "partial"      # Some filled
    FILLED = "filled"        # Complete
    REJECTED = "rejected"    # Broker said no
    CANCELLED = "cancelled"  # User cancelled
    ERROR = "error"          # System error

class Order:
    def __init__(self, symbol, quantity, price, order_type):
        self.order_id = str(uuid.uuid4())
        self.state = OrderState.PENDING
        self.submitted_price = price
        self.filled_price = None
        self.filled_quantity = 0
        self.fill_timestamp = None
        self.rejection_reason = None
```

#### 1.2 Paper Broker (Fake but Realistic)

```python
class PaperBroker:
    """
    Simulates broker without touching real APIs.
    Implements realistic fills, rejections, slippage.
    """

    def place_order(self, order: Order) -> bool:
        """
        Simulates order submission.
        - 95% acceptance rate
        - 5% rejection (margin, circuit breaker, etc.)
        """
        if random.random() < 0.95:
            order.state = OrderState.PENDING
            self.orders[order.order_id] = order
            return True
        else:
            order.state = OrderState.REJECTED
            order.rejection_reason = "Insufficient margin"
            return False

    def fill_order(self, order: Order, market_price: float):
        """
        Simulates matching order against market.
        - Fills if price touched
        - Applies realistic slippage (0-10 bps)
        """
        slippage = random.uniform(-0.001, 0.001)  # ±10 bps
        fill_price = market_price * (1 + slippage)
        
        order.filled_price = fill_price
        order.filled_quantity = order.quantity
        order.fill_timestamp = datetime.now()
        order.state = OrderState.FILLED
        
        return order.filled_price

    def cancel_order(self, order_id: str) -> bool:
        """Cancel pending order"""
        order = self.orders.get(order_id)
        if order and order.state == OrderState.PENDING:
            order.state = OrderState.CANCELLED
            return True
        return False
```

#### 1.3 Position Manager

```python
class PositionManager:
    """Track open positions, P&L, risk exposure"""

    def __init__(self):
        self.positions = {}  # symbol → Position
        self.cash = 500000   # ₹500k initial capital
        self.trades = []     # History

    def open_position(self, symbol, quantity, entry_price):
        """Open new position"""
        cost = quantity * entry_price
        if cost > self.cash:
            raise InsufficientCash()
        
        self.positions[symbol] = Position(
            symbol=symbol,
            quantity=quantity,
            entry_price=entry_price,
            entry_timestamp=datetime.now()
        )
        self.cash -= cost

    def close_position(self, symbol, exit_price):
        """Close position, realize P&L"""
        pos = self.positions[symbol]
        pnl = (exit_price - pos.entry_price) * pos.quantity
        
        self.cash += (pos.quantity * exit_price)
        
        trade_record = {
            'symbol': symbol,
            'entry': pos.entry_price,
            'exit': exit_price,
            'quantity': pos.quantity,
            'pnl': pnl,
            'duration': datetime.now() - pos.entry_timestamp
        }
        
        self.trades.append(trade_record)
        del self.positions[symbol]
        return pnl

    def get_portfolio_value(self, current_prices: Dict):
        """Calculate unrealized + realized P&L"""
        unrealized = sum(
            pos.quantity * (current_prices[symbol] - pos.entry_price)
            for symbol, pos in self.positions.items()
        )
        realized = sum(t['pnl'] for t in self.trades)
        return self.cash + unrealized + realized
```

### Phase 2: Broker Integration Adapter (Weeks 3-4)

**Goal:** Swap paper broker for real one without changing core logic

#### 2.1 Broker Interface (Abstract)

```python
class BrokerInterface(ABC):
    """All brokers implement this contract"""

    @abstractmethod
    def place_order(self, order: Order) -> str:
        """Submit order, return broker order ID"""
        pass

    @abstractmethod
    def get_order_status(self, broker_order_id: str) -> OrderState:
        """Check order status"""
        pass

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel order"""
        pass

    @abstractmethod
    def get_positions(self) -> Dict[str, Position]:
        """Get live positions"""
        pass

    @abstractmethod
    def get_portfolio_value(self) -> float:
        """Get current account equity"""
        pass
```

#### 2.2 Paper Implementation

```python
class PaperBrokerAdapter(BrokerInterface):
    """Implements paper trading"""
    def __init__(self):
        self.broker = PaperBroker()
    
    def place_order(self, order: Order) -> str:
        success = self.broker.place_order(order)
        return order.order_id if success else None
    # ... rest of interface
```

#### 2.3 Real Broker Implementation

```python
class ZerodhaBrokerAdapter(BrokerInterface):
    """Implements Zerodha integration"""
    def __init__(self, kite_client):
        self.kite = kite_client
    
    def place_order(self, order: Order) -> str:
        response = self.kite.place_order(
            variety="regular",
            exchange="NSE",
            tradingsymbol=order.symbol,
            transaction_type="BUY" if order.quantity > 0 else "SELL",
            quantity=abs(order.quantity),
            order_type="LIMIT",
            price=order.price
        )
        return response['order_id']
    # ... rest of interface
```

#### 2.4 Configuration Switch

```python
# config.py
PAPER_TRADING = True  # Set to False for real execution

if PAPER_TRADING:
    broker = PaperBrokerAdapter()
else:
    # ⚠️ REAL EXECUTION - REQUIRES APPROVAL GATE
    broker = ZerodhaBrokerAdapter(kite_client)
```

### Phase 3: Failure Recovery (Week 5)

**Goal:** Handle broken trades, stale state, crashes

#### 3.1 Order Reconciliation

```python
def reconcile_orders():
    """
    Compare paper state vs. broker.
    Detect: orphaned orders, stuck trades, missed fills.
    """
    
    # For each order in our ledger
    for our_order_id, our_state in local_orders.items():
        # Check with broker
        broker_state = broker.get_order_status(our_order_id)
        
        # Reconcile
        if our_state != broker_state:
            # Log discrepancy
            # Trigger alert
            # Take corrective action
            pass
```

#### 3.2 Restart Recovery

```python
class TradingSession:
    """Manages session state for recovery"""
    
    def __init__(self):
        self.session_file = "trading_session.json"
        self.checkpoint_interval = 60  # seconds
    
    def checkpoint(self):
        """Save state to disk every 60s"""
        state = {
            'positions': self.position_manager.positions,
            'orders': self.order_manager.orders,
            'cash': self.position_manager.cash,
            'timestamp': datetime.now().isoformat()
        }
        with open(self.session_file, 'w') as f:
            json.dump(state, f)
    
    def resume_from_checkpoint(self):
        """Restore from crash"""
        if not os.path.exists(self.session_file):
            return False
        
        with open(self.session_file) as f:
            state = json.load(f)
        
        # Restore state
        self.position_manager.restore(state['positions'])
        self.order_manager.restore(state['orders'])
        
        # Reconcile with broker
        self.reconcile_orders()
        
        return True
```

#### 3.3 Kill Switch

```python
class CircuitBreaker:
    """Emergency stop mechanism"""
    
    def __init__(self):
        self.triggers = {
            'daily_loss': -50000,      # ₹50k loss limit
            'max_drawdown': -0.05,      # 5% drawdown
            'portfolio_vol': 0.15,      # 15% volatility
            'manual_trigger': False
        }
    
    def check(self, portfolio_state):
        """Run checks every iteration"""
        if portfolio_state['daily_pnl'] < self.triggers['daily_loss']:
            self.trigger("Daily loss limit exceeded")
        
        if portfolio_state['drawdown'] < self.triggers['max_drawdown']:
            self.trigger("Max drawdown exceeded")
        
        # If triggered, close ALL positions
    
    def trigger(self, reason):
        """Emergency close all positions"""
        logger.critical(f"CIRCUIT BREAKER: {reason}")
        for symbol in self.positions:
            self.close_position_immediately(symbol)
        self.paused = True  # Stop taking new trades
```

---

## Governance: Paper → Real Gate

### Approval Checklist Before Real Capital

Before flipping `PAPER_TRADING = False`:

- [ ] **Parameter Validation**
  - [ ] Parameters optimized on out-of-sample data
  - [ ] Win rate ≥50% on validation set
  - [ ] Risk-reward analysis completed

- [ ] **System Testing**
  - [ ] Paper execution matches theory for 1+ week
  - [ ] Reconciliation working (broker vs. local)
  - [ ] Restart recovery tested (simulated crashes)
  - [ ] Circuit breaker tested (manual trigger works)

- [ ] **Risk Limits**
  - [ ] Daily loss limit set and monitored
  - [ ] Max drawdown trigger active
  - [ ] Position sizing limits enforced
  - [ ] Concentration limits verified

- [ ] **Monitoring**
  - [ ] Dashboard live (P&L, positions, fills)
  - [ ] Alert system working (Slack/email)
  - [ ] Kill switch ready (manual override)
  - [ ] Audit logs enabled

- [ ] **Authorization**
  - [ ] Owner review + sign-off
  - [ ] Second opinion (independent reviewer)
  - [ ] Capital allocation approved
  - [ ] Rollback plan documented

- [ ] **Insurance**
  - [ ] Broker account secured (not shared credentials)
  - [ ] Capital segregated (not in main account)
  - [ ] Insurance/protection in place
  - [ ] Lawyer reviewed (if applicable)

### Escalation

If deployed and performance degrades:

1. **<-10% drawdown:** Manual review (owner decision)
2. **<-20% drawdown:** Automatic 50% position reduction
3. **<-30% drawdown:** Automatic full exit (circuit breaker)

---

## Timeline Estimate

| Phase | Duration | Deliverable |
|-------|----------|-------------|
| 1: Paper Execution | 3 weeks | Order state machine + position manager + fills |
| 2: Broker Adapter | 1 week | Paper/real adapters, config switch |
| 3: Recovery | 1 week | Reconciliation, restart, kill switch |
| Testing | 1 week | 1 week paper trading validation |
| **Total** | **~6 weeks** | **Production-ready system** |

---

## What NOT to Do

❌ **Don't skip paper trading**
- Real execution involves real money and real mistakes
- Paper execution catches bugs with zero cost

❌ **Don't hardcode "PAPER_TRADING = True"**
- Configuration must be runtime-checkable
- Audit trail essential

❌ **Don't deploy without kill switch**
- Runaway algorithm can cost entire capital
- Kill switch must be physically accessible

❌ **Don't assume parameters work forever**
- Market regimes change
- Monthly re-validation needed
- Drift detection automated

---

## References

- [[live-trading-safety-constraints]] — Safety framework
- [[ecs-audit-findings-20260830]] — Finding #4 (MainTradingLoop is simulator)
- CRITICAL_AUDIT_RESPONSE_20260830.md — Remediation roadmap
- MainTradingLoop.py — Current simulator (starting point)
