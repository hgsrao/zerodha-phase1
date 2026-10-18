"""Engine B: the shared R5 CCPP decision engine executed natively by NautilusTrader (offline only).

Nautilus completed bars -> R5 input adapter -> shared R5 decision/control code -> approved
order intents -> Nautilus native matching -> Nautilus fill events -> R5 fill/close feedback.
No live venue, no Kite routing; the only execution client is Nautilus's SimulatedExchange.
"""
