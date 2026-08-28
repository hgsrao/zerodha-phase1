# Brain Research Lab — Version 8 Patient Entry

Version 8 retains the complete Version 7 ranking and changes only entry execution.

## Frozen change

- After the 13:15–14:15 decision candle, set a hypothetical buy limit 0.25%
  below the signal close.
- Keep the limit eligible for the next six hourly candles.
- A candle fills the limit only if its low reaches it. If it opens below the
  limit, use that opening price; otherwise use no price worse than the limit.
- Apply adverse entry slippage where possible without violating the buy limit.
- If the limit is never touched, record `UNFILLED` and skip the trade.
- Do not replace an unfilled top-two selection with a lower-ranked equity.
- Exit 18 hourly bars after the actual fill; retain Version 7 exit slippage and
  all per-side costs.

Everything else, including universe, rankings, eligibility, time windows and
maximum two daily selections, remains unchanged. This is exploratory and cannot
authorize live trading.
