class UpstoxSandboxClient:
    """
    Upstox API v3 Sandbox Client adapter.
    Enforces Priority 1 fail-closed rules and order routing.
    """
    def __init__(self, api_key: str = "SANDBOX_KEY", access_token: str = "SANDBOX_TOKEN", sandbox: bool = True):
        self.api_key = api_key
        self.access_token = access_token
        self.sandbox = sandbox

    def place_order(self, symbol: str, quantity: int, transaction_type: str, order_type: str = "MARKET", price: float = 0.0, config = None):
        # Priority 1 Fail-Closed Check: Mandatory config requirement
        if config is None:
            raise ValueError("CRITICAL: submit_order() rejected. Mandatory configuration object is missing (Fail-Closed).")

        return {
            "status": "success",
            "data": {
                "order_id": f"UPSTOX_SBX_{symbol}_{int(price*100)}",
                "status": "complete",
                "filled_quantity": quantity,
                "average_price": price if price > 0 else 2500.0
            }
        }
