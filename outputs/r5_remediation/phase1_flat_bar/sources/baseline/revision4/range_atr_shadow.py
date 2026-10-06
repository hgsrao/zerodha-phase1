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
            atr = sum(self.tr[symbol]) / self.period if len(self.tr[symbol]) >= self.period else None
            # A flat/zero-priced synthetic or malformed bar stream has no
            # meaningful Range/ATR value.  Shadow instrumentation must never
            # become a new failure mode for the execution replay.
            ratios[symbol] = None if atr is None or atr <= 0 else (bar.high - bar.low) / atr
        return ratios

    def observe_candidates(self, candidates, ratios):
        for candidate in candidates:
            ratio = ratios.get(candidate.order.symbol)
            self.rows.append({
                "order_id": candidate.order.order_id,
                "symbol": candidate.order.symbol,
                "range_atr": ratio,
                "pa_confidence": candidate.pa_confidence,
                "id_risk_reward": candidate.id_risk_reward,
            })

    def summary(self):
        usable = [row for row in self.rows if row["range_atr"] is not None]
        confidences = [row["pa_confidence"] for row in self.rows if row["pa_confidence"] is not None]
        return {
            "candidate_count": len(self.rows),
            "usable_count": len(usable),
            "pa_confidence": {
                "count": len(confidences),
                "minimum": min(confidences) if confidences else None,
                "maximum": max(confidences) if confidences else None,
            },
            "would_reject": {
                str(threshold): sum(row["range_atr"] > threshold for row in usable)
                for threshold in self.thresholds
            },
        }
