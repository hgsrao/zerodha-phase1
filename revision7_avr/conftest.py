from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pytest
from .r7_state import Bar, ControlConfig, Context


@pytest.fixture
def config():
    return ControlConfig('INFY', 'GTG2_TECH_TELECOM', 0.02)


def make_bar(i, volume=100.0, close=None):
    price = close if close is not None else 100 + i * 0.05
    ts = datetime(2026, 8, 13, 9, 15, tzinfo=timezone(timedelta(hours=5, minutes=30))) + timedelta(minutes=i)
    return Bar(i, ts.isoformat(), price - 0.03, price + 0.02, price - 0.05, price, volume)


def context(actual=0.0, study=0.8, dispatch=0.6):
    return Context(actual, study, dispatch)
