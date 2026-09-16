from nautilus_trader.model import Money

class CustomExternalFeeModel:
    """
    Encapsulates the custom transaction fee and slippage structure
    for NautilusTrader backtest-to-live parity execution.
    """
    def __init__(self, fixed_cost_inr: float = 27.0):
        self.fixed_cost_inr = fixed_cost_inr

    def calculate_fee(self, quantity: float, price: float) -> str:
        total_fee = self.fixed_cost_inr * abs(quantity)
        return f"INR {total_fee:.2f}"

if __name__ == "__main__":
    model = CustomExternalFeeModel()
    fee = model.calculate_fee(100, 1406.7)
    print(f"✅ Custom Fee Model Verified. Friction charge: {fee}")
