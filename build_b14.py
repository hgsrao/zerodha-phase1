"""Institutional-grade fail-closed builder for institutional_engine_v34_B14_temporal.py
Verifies B1.3 provenance, normalizes line endings, dynamically extracts indentation, and verifies anchor counts.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

EXPECTED_B13_SHA256 = "9A6DA05D3131FF6D66E727C758E14FA5453799B52618352498950DF4D06217DC"
B13_FILE = "institutional_engine_v34_B13_resilience.py"
B14_FILE = "institutional_engine_v34_B14_temporal.py"

def build_b14() -> None:
    b13_path = Path(B13_FILE)
    if not b13_path.exists():
        raise FileNotFoundError(f"Fail-Closed: Frozen B1.3 artifact '{B13_FILE}' not found.")

    b13_bytes = b13_path.read_bytes()
    actual_hash = hashlib.sha256(b13_bytes).hexdigest().upper()
    if actual_hash != EXPECTED_B13_SHA256:
        raise RuntimeError(
            f"PROVENANCE HALT: B1.3 SHA-256 mismatch!\nExpected: {EXPECTED_B13_SHA256}\nGot:      {actual_hash}"
        )
    print(f"[PROVENANCE OK] B1.3 SHA-256 verified: {actual_hash}")

    # Normalize line endings to prevent Windows CRLF vs LF mismatch
    code = b13_bytes.decode("utf-8").replace("\r\n", "\n")

    # 1. Patch TradeContext to include the six tracking fields
    old_tc = """@dataclass
class TradeContext:
    symbol: str
    entry_tag: str
    target_qty: int
    tranche_qty: int
    filled_qty: int = 0
    pending_qty: int = 0
    entry_order_id: Optional[str] = None
    stop_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    order_status: str = "PENDING"
    executed_tranches: int = 0
    booked_pnl: Decimal = Decimal("0")
    avg_entry_price: Decimal = Decimal("0")
    stop_loss_price: Decimal = Decimal("0")
    exit_reason: Optional[str] = None"""

    new_tc = old_tc + """
    entry_max_observed_fill: int = 0
    entry_hwm_order_id: Optional[str] = None
    stop_max_observed_fill: int = 0
    stop_hwm_order_id: Optional[str] = None
    exit_max_observed_fill: int = 0
    exit_hwm_order_id: Optional[str] = None"""

    if code.count(old_tc) != 1:
        raise RuntimeError(f"Fail-Closed: TradeContext anchor count is {code.count(old_tc)}, expected exactly 1.")
    code = code.replace(old_tc, new_tc, 1)

    # 2. Inject the deterministic HWM helper method into TradingEngineV34
    hwm_helper = """
    def _check_cumulative_fill_hwm(
        self,
        current_order_id: Optional[str],
        current_fill: int,
        hwm_order_id_attr: str,
        max_fill_attr: str,
    ) -> bool:
        \"\"\"Enforces monotonic non-decreasing cumulative fills strictly within
        the lifecycle of a specific order ID. Automatically resets baseline on rotation.
        \"\"\"
        ctx = self.state.active_trade
        if not ctx or not current_order_id:
            return True

        tracked_id = getattr(ctx, hwm_order_id_attr)
        current_hwm = getattr(ctx, max_fill_attr)

        if current_order_id != tracked_id:
            setattr(ctx, hwm_order_id_attr, current_order_id)
            setattr(ctx, max_fill_attr, current_fill)
            self.store.save(self.state)
            return True

        if current_fill < current_hwm:
            return False

        if current_fill > current_hwm:
            setattr(ctx, max_fill_attr, current_fill)
            self.store.save(self.state)

        return True
"""
    anchor_cls = "class TradingEngineV34:"
    if code.count(anchor_cls) != 1:
        raise RuntimeError(f"Fail-Closed: TradingEngineV34 class anchor count is {code.count(anchor_cls)}, expected 1.")
    code = code.replace(anchor_cls, anchor_cls + hwm_helper, 1)

    # 3. Dynamic Line-by-Line Indentation Extraction & Injection
    lines = code.split("\n")
    new_lines = []
    entry_matched = 0
    exit_matched = 0
    stop_matched = 0

    for line in lines:
        # Dynamically extract leading whitespace from the matched line
        indent = line[:len(line) - len(line.lstrip())]

        if "filled_qty = self._filled_qty(order_info)" in line:
            entry_matched += 1
            new_lines.append(line)
            new_lines.append(f'{indent}if not self._check_cumulative_fill_hwm(ctx.entry_order_id, filled_qty, "entry_hwm_order_id", "entry_max_observed_fill"):')
            new_lines.append(f'{indent}    self.trigger_hard_halt(f"CRITICAL P0: Entry cumulative fill regressed for order {{ctx.entry_order_id}}.")')
            new_lines.append(f'{indent}    return "HALTED"')
        elif "filled_qty = self._filled_qty(exit_info)" in line:
            exit_matched += 1
            new_lines.append(line)
            new_lines.append(f'{indent}if not self._check_cumulative_fill_hwm(ctx.exit_order_id, filled_qty, "exit_hwm_order_id", "exit_max_observed_fill"):')
            new_lines.append(f'{indent}    self.trigger_hard_halt(f"CRITICAL P0: Exit cumulative fill regressed for order {{ctx.exit_order_id}}.")')
            new_lines.append(f'{indent}    return "HALTED"')
        elif "sl_filled = self._filled_qty(sl_info)" in line:
            stop_matched += 1
            new_lines.append(line)
            new_lines.append(f'{indent}if not self._check_cumulative_fill_hwm(ctx.stop_order_id, sl_filled, "stop_hwm_order_id", "stop_max_observed_fill"):')
            new_lines.append(f'{indent}    self.trigger_hard_halt(f"CRITICAL P0: Protective stop cumulative fill regressed for order {{ctx.stop_order_id}}.")')
            new_lines.append(f'{indent}    return "HALTED"')
        else:
            new_lines.append(line)

    if entry_matched != 1:
        raise RuntimeError(f"Fail-Closed: Entry hook match count is {entry_matched}, expected 1.")
    if exit_matched != 1:
        raise RuntimeError(f"Fail-Closed: Exit hook match count is {exit_matched}, expected 1.")
    if stop_matched != 3:
        raise RuntimeError(f"Fail-Closed: Stop hook match count is {stop_matched}, expected exactly 3.")

    code = "\n".join(new_lines)

    # Write out B1.4 candidate
    b14_path = Path(B14_FILE)
    b14_path.write_text(code, encoding="utf-8")
    b14_hash = hashlib.sha256(b14_path.read_bytes()).hexdigest().upper()

    # Final verification: ensure B1.3 remains completely unmodified
    b13_post_hash = hashlib.sha256(b13_path.read_bytes()).hexdigest().upper()
    if b13_post_hash != EXPECTED_B13_SHA256:
        raise RuntimeError("FATAL: B1.3 source file was modified during build!")

    print(f"[SUCCESS] Generated {B14_FILE} with dynamic indentation (SHA-256: {b14_hash})")
    print(f"[VERIFIED] B1.3 remains pristine and untouched ({b13_post_hash}).")

if __name__ == "__main__":
    build_b14()