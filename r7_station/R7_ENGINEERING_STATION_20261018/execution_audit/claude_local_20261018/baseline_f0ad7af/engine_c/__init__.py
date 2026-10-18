"""Engine C: the shared R5 CCPP decision engine executed natively by Backtrader (offline only).

Backtrader Cerebro feeds completed 1-minute bars -> R5 input adapter -> shared R5 decision/control
code -> approved order intents -> Backtrader BackBroker market orders -> Backtrader order/trade
notifications -> R5 fill/close feedback.  Backtrader's broker owns cash, positions, commissions
and realized P&L.  No live venue, no Kite routing, no network access.
"""
