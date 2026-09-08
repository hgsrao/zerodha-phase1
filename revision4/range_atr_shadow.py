"""Non-authoritative Range/ATR monitoring for candidate decisions."""

from collections import defaultdict, deque


class RangeATRShadowMonitor:
    """Records what fixed range/ATR filters would do; never rejects orders."""

    def __init__(self, thresholds=(3.0, 4.0, 5.0, 6.0), period=20):
        self.thresholds, self.period = thresholds, period
        self.previous_close = {}
        self.tr = defaultdict(lambda: deque(maxlen=period))
        self.rows = []

    def observe_bars(self, bars):
        ratios = {}
        for symbol, bar in bars.items():
            previous = self.previous_close.get(symbol, bar.close)
            value = max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous))
            self.previous_close[symbol] = bar.close
            self.tr[symbol].append(value)
            ratios[symbol] = None if len(self.tr[symbol]) < self.period else (bar.high - bar.low) / (sum(self.tr[symbol]) / self.period)
        return ratios

    def observe_candidates(self, candidates, ratios):
        for candidate in candidates:
            ratio = ratios.get(candidate.order.symbol)
            self.rows.append({"order_id": candidate.order.order_id, "symbol": candidate.order.symbol, "range_atr": ratio})

    def summary(self):
        usable = [row for row in self.rows if row["range_atr"] is not None]
        return {
            "candidate_count": len(self.rows),
            "usable_count": len(usable),
            "would_reject": {
                str(threshold): sum(row["range_atr"] > threshold for row in usable)
                for threshold in self.thresholds
            },
        }
