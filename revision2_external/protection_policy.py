"""Shared contingent-protection geometry and delivery-rejection classification."""
from decimal import Decimal, ROUND_FLOOR
from math import isfinite


DELIVERY_UNAUTHORIZED = 'PLANT_TRIP_DELIVERY_UNAUTHORIZED'


class DeliveryAuthorisationTrip(RuntimeError):
    pass


def delivery_authorisation_failure(value):
    message = str(value).casefold().replace('-', '').replace('_', '')
    return (any(token in message for token in ('tpin', 'edis', 'ddpi', 'poa'))
            or ('cdsl' in message and any(token in message for token in ('authoris', 'authoriz', 'otp', 'verify')))
            or ('holding' in message and ('not authoris' in message or 'not authoriz' in message)))


def gtt_sell_limit(trigger_price, tick_size):
    """Requested 5% sell buffer, rounded down to instrument ticks; no fill guarantee."""
    if any(isinstance(v, bool) or not isfinite(float(v)) or float(v) <= 0 for v in (trigger_price,tick_size)):
        raise ValueError('Invalid GTT stop/tick')
    trigger, tick = Decimal(str(trigger_price)), Decimal(str(tick_size))
    if trigger % tick != 0:
        raise ValueError('Governor stop is not aligned to instrument tick size')
    limit = (trigger*Decimal('0.95')/tick).to_integral_value(rounding=ROUND_FLOOR)*tick
    if limit <= 0:
        raise ValueError('Invalid GTT limit price')
    return float(limit)
