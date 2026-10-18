"""Frozen replay friction model. No rate changes in audit remediation."""
def leg_cost(price: float, quantity: int, side: str) -> float:
    turnover = price * quantity
    cost = min(20.0, 0.0003 * turnover) + 0.0000345 * turnover
    if side == "SELL":
        cost += 0.00025 * turnover
    return cost

def paper_fill_price(market_price: float, side: str, slippage_fraction: float) -> float:
    slip = float(market_price) * float(slippage_fraction)
    return round(float(market_price) + slip if side == "BUY" else float(market_price) - slip, 4)
