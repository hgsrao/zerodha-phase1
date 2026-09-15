from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Optional, List, Dict, Any
import math

IST = ZoneInfo("Asia/Kolkata")
EXCHANGE = "NSE"

@dataclass(frozen=True)
class Config:
    alert_webhook_url: str
    max_daily_loss: Decimal
    market_protection_pct: Decimal = Decimal("1.0")
    holidays: frozenset[date] = field(default_factory=frozenset)
    trial_capital: Decimal = Decimal("20000")
    universe: List[str] = field(default_factory=lambda: [
        "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", 
        "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK", "LT", "M&M",
        "AXISBANK", "WIPRO", "HCLTECH", "SUNPHARMA"
    ])

@dataclass
class TradeContext:
    symbol: str
    entry_tag: str
    target_qty: int          # Total intended position quantity (2 * tranche_qty)
    tranche_qty: int         # Size per tranche
    filled_qty: int = 0
    pending_qty: int = 0
    entry_order_id: Optional[str] = None
    order_status: str = "PENDING"  # PENDING, PARTIAL, COMPLETE, REJECTED, CANCELLED
    executed_tranches: int = 0
    booked_pnl: Decimal = Decimal("0")
    avg_entry_price: Decimal = Decimal("0")

@dataclass
class BotState:
    trading_day: str
    status: str = "STARTUP"  # STARTUP, RECONCILING, FLAT, QUANT_SCAN, CANDIDATE_VALIDATION, OBI_VALIDATION, RISK_GATE, ENTRY_SUBMIT, ENTRY_PENDING, PARTIAL_POSITION, PROTECTION, MANAGING, EXIT, EXIT_PENDING, RECONCILIATION_HALT
    realised_net_pnl: str = "0"
    unrealised_mtm: str = "0"
    active_trade: Optional[TradeContext] = None

class QuantitativeEngineCore:
    """Hardened Quantitative Core with Fail-Closed Data Validation and Z-Score Normalization."""
    
    @staticmethod
    def validate_and_extract_quote(symbol: str, raw_quote: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Pillar 3 (Previous): Fail-closed data validation. Rejects malformed, missing, or negative quotes."""
        if not raw_quote:
            return None
            
        ltp = raw_quote.get("last_price")
        net_change = raw_quote.get("net_change")
        ohlc = raw_quote.get("ohlc")
        
        if ltp is None or net_change is None or ohlc is None:
            return None
            
        try:
            ltp_dec = float(ltp)
            change_dec = float(net_change)
            high = float(ohlc.get("high", 0))
            low = float(ohlc.get("low", 0))
        except (TypeError, ValueError):
            return None
            
        if ltp_dec <= 0 or not math.isfinite(ltp_dec):
            return None
        if high <= 0 or low <= 0 or high < low or not math.isfinite(high) or not math.isfinite(low):
            return None
            
        return {
            "last_price": ltp_dec,
            "net_change": change_dec,
            "high": high,
            "low": low,
            "daily_range": abs(high - low)
        }

    @staticmethod
    def calculate_cross_sectional_scores(quotes_dict: Dict[str, Dict[str, Any]], universe: List[str]) -> Dict[str, float]:
        validated_data: Dict[str, Dict[str, float]] = {}
        
        for symbol in universe:
            q = quotes_dict.get(f"NSE:{symbol}", {})
            clean_q = QuantitativeEngineCore.validate_and_extract_quote(symbol, q)
            if clean_q:
                validated_data[symbol] = clean_q

        if not validated_data:
            return {}

        raw_momentum = {s: d["net_change"] for s, d in validated_data.items()}
        raw_stability = {s: 1.0 / ((d["daily_range"] / d["last_price"]) + 1e-6) for s, d in validated_data.items()}

        def z_score_normalize(data: Dict[str, float]) -> Dict[str, float]:
            if not data:
                return {}
            values = list(data.values())
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            std = math.sqrt(variance) if variance > 0 else 1.0
            return {k: (v - mean) / std for k, v in data.items()}

        norm_momentum = z_score_normalize(raw_momentum)
        norm_stability = z_score_normalize(raw_stability)

        final_scores = {}
        for symbol in validated_data.keys():
            m = norm_momentum.get(symbol, 0.0)
            s = norm_stability.get(symbol, 0.0)
            final_scores[symbol] = (m * 0.6) + (s * 0.4)

        return final_scores

    @staticmethod
    def calculate_obi(depth_data: Dict[str, Any]) -> float:
        buy_qty = sum(l.get("quantity", 0) for l in depth_data.get("buy", []))
        sell_qty = sum(l.get("quantity", 0) for l in depth_data.get("sell", []))
        total = buy_qty + sell_qty
        if total == 0:
            return 0.0
        return float(buy_qty - sell_qty) / float(total)


class TradingEngineV33Hardened:
    """Execution Safety Release: True Reconciliation Matrix, Partial Fills, and Live MTM Circuit Breaker."""
    
    def __init__(self, broker, clock, sleeper, store, audit, alert, lock, terminator, cfg: Config):
        self.broker = broker
        self.clock = clock
        self.sleeper = sleeper
        self.store = store
        self.audit = audit
        self.alert = alert
        self.lock_provider = lock
        self.terminator = terminator
        self.cfg = cfg
        
        self._lock_ref = self.lock_provider.acquire()
        self.state = self.store.load(self.clock.now().date())

    def reconcile_startup(self):
        """Pillar 1 & 2: True Reconciliation Matrix and Crash-Safe Orphan Order Inspection."""
        self.state.status = "RECONCILING"
        self.store.save(self.state)
        try:
            positions = self.broker.get_positions()
            open_orders = self.broker.get_orders()
            
            active_pos = [p for p in positions if p.get("quantity", 0) != 0]
            local_has_trade = self.state.active_trade is not None

            # 1. Check for Orphan Orders (Crash occurred after broker accepted order but before local save)
            bot_tags = {"V3.3_ENTRY", "V3.3_EXIT"}
            orphan_orders = [o for o in open_orders if o.get("tag") in bot_tags and o.get("status") in ["OPEN", "TRIGGER PENDING"]]
            
            if orphan_orders and not local_has_trade:
                # Recover context from broker order book
                o = orphan_orders[0]
                sym = o.get("tradingsymbol")
                qty = o.get("pending_quantity", o.get("quantity", 0))
                oid = o.get("order_id")
                
                self.audit.log("ORPHAN_ORDER_RECOVERED_FROM_BROKER", order_id=oid, symbol=sym, qty=qty)
                ctx = TradeContext(
                    symbol=sym,
                    entry_tag=o.get("tag", "V3.3_ENTRY"),
                    target_qty=qty * 2,
                    tranche_qty=qty,
                    entry_order_id=oid,
                    order_status="PENDING"
                )
                self.state.active_trade = ctx
                self.state.status = "ENTRY_PENDING"
                self.store.save(self.state)
                return

            # 2. Strict Position & State Matrix Reconciliation
            if active_pos and not local_has_trade:
                self.trigger_hard_halt("Reconciliation Failure: Broker holds active positions but local state is FLAT.")
                return

            if not active_pos and local_has_trade:
                # If local trade exists but position is zero, check if it was just submitted/pending or rejected
                if self.state.active_trade.order_status in ["PENDING", "PARTIAL"]:
                    self.audit.log("RECONCILIATION_NOTICE_PENDING_ORDER_NO_POSITION_YET")
                else:
                    self.trigger_hard_halt("Reconciliation Failure: Local state expects active trade but broker positions are empty.")
                    return

            if active_pos and local_has_trade:
                broker_sym = active_pos[0].get("tradingsymbol")
                broker_qty = abs(active_pos[0].get("quantity", 0))
                local_sym = self.state.active_trade.symbol
                local_filled = self.state.active_trade.filled_qty

                if broker_sym != local_sym:
                    self.trigger_hard_halt(f"Reconciliation Failure: Symbol mismatch. Broker: {broker_sym}, Local: {local_sym}")
                    return
                if local_filled > 0 and broker_qty != local_filled:
                    self.trigger_hard_halt(f"Reconciliation Failure: Quantity mismatch. Broker: {broker_qty}, Local Filled: {local_filled}")
                    return

            self.audit.log("STARTUP_RECONCILIATION_PASSED", active_positions=len(active_pos), open_orders=len(open_orders))
            self.state.status = "FLAT" if not self.state.active_trade else "MANAGING"
            self.store.save(self.state)

        except Exception as e:
            self.trigger_hard_halt(f"Reconciliation exception: {e}")

    def trigger_hard_halt(self, reason: str):
        self.state.status = "RECONCILIATION_HALT"
        self.store.save(self.state)
        self.alert.send("CRITICAL", reason)
        self.terminator.halt(reason)

    def update_and_check_mtm(self):
        """Pillar 4: Real-time Live MTM Calculation and Total Strategy Risk Gate."""
        if not self.state.active_trade or self.state.active_trade.filled_qty == 0:
            self.state.unrealised_mtm = "0"
            self.store.save(self.state)
            return

        ctx = self.state.active_trade
        quotes = self.broker.ltp([ctx.symbol])
        q = quotes.get(f"NSE:{ctx.symbol}", {})
        ltp = Decimal(str(q.get("last_price", ctx.avg_entry_price)))

        # Unrealized MTM = (Current LTP - Avg Entry Price) * Filled Quantity (for BUY side)
        unrealised = (ltp - ctx.avg_entry_price) * Decimal(str(ctx.filled_qty))
        self.state.unrealised_mtm = str(unrealised)
        self.store.save(self.state)

        # Evaluate Total Strategy Risk (Realized + Unrealized)
        total_pnl = Decimal(self.state.realised_net_pnl) + unrealised
        if total_pnl <= -self.cfg.max_daily_loss:
            self.audit.log("CIRCUIT_BREAKER_TRIPPED_TOTAL_RISK", total_pnl=str(total_pnl), realised=self.state.realised_net_pnl, unrealised=str(unrealised))
            self.state.status = "EXIT"
            self.store.save(self.state)

    def step(self) -> str:
        today = self.clock.now().date()
        if today in self.cfg.holidays or today.weekday() >= 5:
            return "CLOSED"

        if self.state.status == "RECONCILIATION_HALT":
            return "HALTED"

        try:
            # Continuously update MTM risk before evaluating states
            self.update_and_check_mtm()

            # Absolute Circuit Breaker Check
            net_pnl = Decimal(self.state.realised_net_pnl) + Decimal(self.state.unrealised_mtm)
            if net_pnl <= -self.cfg.max_daily_loss and self.state.status != "EXIT":
                self.trigger_hard_halt(f"Daily total loss limit exceeded. Strategy PnL: {net_pnl}")
                return "HALTED"

            if self.state.status == "STARTUP":
                self.reconcile_startup()
                return "STATE_CHANGED"

            elif self.state.status == "FLAT":
                self.state.status = "QUANT_SCAN"
                self.store.save(self.state)
                return "STATE_CHANGED"

            elif self.state.status == "QUANT_SCAN":
                quotes = self.broker.ltp(self.cfg.universe)
                scores = QuantitativeEngineCore.calculate_cross_sectional_scores(quotes, self.cfg.universe)
                
                if not scores:
                    self.audit.log("QUANT_SCAN_NO_VALID_CANDIDATES")
                    return "NO_ACTION"

                self.selected_symbol = max(scores, key=scores.get)
                self.selected_quote = QuantitativeEngineCore.validate_and_extract_quote(self.selected_symbol, quotes.get(f"NSE:{self.selected_symbol}", {}))
                
                self.state.status = "CANDIDATE_VALIDATION"
                self.store.save(self.state)
                return "STATE_CHANGED"

            elif self.state.status == "CANDIDATE_VALIDATION":
                if not self.selected_quote:
                    self.state.status = "FLAT"
                    self.store.save(self.state)
                    return "STATE_CHANGED"

                ltp = Decimal(str(self.selected_quote["last_price"]))
                # Mathematical Tranche Split Correction
                tranche_cap = self.cfg.trial_capital / Decimal(2)
                tranche_qty = max(int(tranche_cap / ltp), 0)
                target_qty = 2 * tranche_qty

                if tranche_qty == 0:
                    self.audit.log("CANDIDATE_REJECTED_UNAFFORDABLE", symbol=self.selected_symbol, ltp=str(ltp))
                    self.state.status = "FLAT"
                    self.store.save(self.state)
                    return "STATE_CHANGED"

                self.target_qty = target_qty
                self.tranche_qty = tranche_qty
                
                self.state.status = "OBI_VALIDATION"
                self.store.save(self.state)
                return "STATE_CHANGED"

            elif self.state.status == "OBI_VALIDATION":
                depth = self.broker.get_market_depth(self.selected_symbol)
                obi = QuantitativeEngineCore.calculate_obi(depth)
                
                self.audit.log("OBI_EVALUATED", symbol=self.selected_symbol, obi=obi)
                if obi < 0.2:
                    self.audit.log("ENTRY_BLOCKED_BY_OBI", symbol=self.selected_symbol, obi=obi)
                    self.state.status = "FLAT"
                    self.store.save(self.state)
                    return "STATE_CHANGED"

                self.state.status = "RISK_GATE"
                self.store.save(self.state)
                return "STATE_CHANGED"

            elif self.state.status == "RISK_GATE":
                ctx = TradeContext(
                    symbol=self.selected_symbol,
                    entry_tag="V3.3_ENTRY",
                    target_qty=self.target_qty,
                    tranche_qty=self.tranche_qty
                )
                self.state.active_trade = ctx
                self.state.status = "ENTRY_SUBMIT"
                self.store.save(self.state)
                return "STATE_CHANGED"

            elif self.state.status == "ENTRY_SUBMIT":
                ctx = self.state.active_trade
                oid = self.broker.place_order(
                    variety="regular",
                    exchange=EXCHANGE,
                    tradingsymbol=ctx.symbol,
                    transaction_type="BUY",
                    quantity=ctx.tranche_qty,
                    product="MIS",
                    order_type="MARKET",
                    tag=ctx.entry_tag,
                    market_protection=str(self.cfg.market_protection_pct)
                )
                ctx.entry_order_id = oid
                self.state.status = "ENTRY_PENDING"
                self.store.save(self.state)
                self.audit.log("ORDER_SUBMITTED_AND_PERSISTED", symbol=ctx.symbol, order_id=oid, qty=ctx.tranche_qty)
                return "STATE_CHANGED"

            elif self.state.status == "ENTRY_PENDING":
                ctx = self.state.active_trade
                order_info = self.broker.get_order_details(ctx.entry_order_id)
                status = order_info.get("status")

                if status == "COMPLETE":
                    ctx.order_status = "COMPLETE"
                    ctx.filled_qty = order_info.get("filled_quantity", ctx.tranche_qty)
                    ctx.pending_qty = order_info.get("pending_quantity", 0)
                    ctx.executed_tranches = 1
                    ctx.avg_entry_price = Decimal(str(order_info.get("average_price", self.selected_quote["last_price"])))
                    self.state.status = "PROTECTION"
                    self.store.save(self.state)
                    self.audit.log("ORDER_FILL_COMPLETE", order_id=ctx.entry_order_id, filled=ctx.filled_qty, price=str(ctx.avg_entry_price))
                    return "STATE_CHANGED"

                elif status == "PARTIAL":
                    ctx.order_status = "PARTIAL"
                    ctx.filled_qty = order_info.get("filled_quantity", 0)
                    ctx.pending_qty = order_info.get("pending_quantity", ctx.tranche_qty - ctx.filled_qty)
                    ctx.avg_entry_price = Decimal(str(order_info.get("average_price", self.selected_quote["last_price"])))
                    self.state.status = "PARTIAL_POSITION"
                    self.store.save(self.state)
                    self.audit.log("ORDER_PARTIAL_FILL", order_id=ctx.entry_order_id, filled=ctx.filled_qty, pending=ctx.pending_qty)
                    return "STATE_CHANGED"

                elif status in ["REJECTED", "CANCELLED"]:
                    ctx.order_status = status
                    self.audit.log(f"ORDER_{status}", order_id=ctx.entry_order_id, reason=order_info.get("status_message"))
                    # Route through reconciliation rather than blindly resetting to FLAT
                    self.state.status = "STARTUP"
                    self.store.save(self.state)
                    return "STATE_CHANGED"

                return "NO_ACTION"

            elif self.state.status == "PARTIAL_POSITION":
                ctx = self.state.active_trade
                order_info = self.broker.get_order_details(ctx.entry_order_id)
                status = order_info.get("status")

                if status == "COMPLETE":
                    ctx.order_status = "COMPLETE"
                    ctx.filled_qty = order_info.get("filled_quantity", ctx.tranche_qty)
                    ctx.pending_qty = 0
                    ctx.executed_tranches = 1
                    ctx.avg_entry_price = Decimal(str(order_info.get("average_price", ctx.avg_entry_price)))
                    self.state.status = "PROTECTION"
                    self.store.save(self.state)
                    return "STATE_CHANGED"
                elif status in ["REJECTED", "CANCELLED"]:
                    # Handle terminal partial state -> initiate recovery / reconciliation
                    self.state.status = "STARTUP"
                    self.store.save(self.state)
                    return "STATE_CHANGED"
                return "NO_ACTION"

            elif self.state.status == "PROTECTION":
                # Protective stop-loss placement goes here in subsequent release
                self.state.status = "MANAGING"
                self.store.save(self.state)
                return "STATE_CHANGED"

            elif self.state.status == "MANAGING":
                # Active risk monitoring happens continuously via update_and_check_mtm()
                return "NO_ACTION"

            elif self.state.status == "EXIT":
                self.state.status = "EXIT_PENDING"
                self.store.save(self.state)
                # Square off open positions logic here
                return "STATE_CHANGED"

            elif self.state.status == "EXIT_PENDING":
                self.state.status = "STARTUP" # Reconcile back to flat
                self.store.save(self.state)
                return "STATE_CHANGED"

            return "FLAT"

        except Exception as exc:
            self.trigger_hard_halt(f"Engine V3.3 Hardened exception: {exc}")
            return "HALTED"