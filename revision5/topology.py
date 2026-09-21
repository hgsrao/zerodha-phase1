"""
Revision 5 Canonical Five-Bay CCPP Topology
============================================

Authoritative mapping of the certified 48-symbol universe.

Bay allocation:
    GTG1 : Heavy Industry / Metals / Mining / Energy / Infra / Defence
    GTG2 : IT Services / Telecom
    CSTG1: Banking / Financial Services / Insurance
    CSTG2: Auto / Consumer / FMCG / Retail / Aviation
    BPSTG: Healthcare / Pharmaceuticals

Every certified symbol belongs to exactly one bay.
"""

from typing import Dict, Tuple


GTG1_HEAVY_INDUSTRY = "GTG1_HEAVY_INDUSTRY"
GTG2_TECH_TELECOM = "GTG2_TECH_TELECOM"
CSTG1_BFSI = "CSTG1_BFSI"
CSTG2_CONSUMER_AUTO = "CSTG2_CONSUMER_AUTO"
BPSTG_HEALTHCARE = "BPSTG_HEALTHCARE"


FLEET_TOPOLOGY: Dict[str, Tuple[str, ...]] = {

    GTG1_HEAVY_INDUSTRY: (
        "ADANIENT",
        "ADANIPORTS",
        "BEL",
        "COALINDIA",
        "GRASIM",
        "HINDALCO",
        "JSWSTEEL",
        "LT",
        "NTPC",
        "ONGC",
        "POWERGRID",
        "RELIANCE",
        "TATASTEEL",
        "ULTRACEMCO",
    ),

    GTG2_TECH_TELECOM: (
        "BHARTIARTL",
        "HCLTECH",
        "INFY",
        "TCS",
        "TECHM",
        "WIPRO",
    ),

    CSTG1_BFSI: (
        "AXISBANK",
        "BAJAJFINSV",
        "BAJFINANCE",
        "HDFCBANK",
        "HDFCLIFE",
        "ICICIBANK",
        "JIOFIN",
        "KOTAKBANK",
        "SBILIFE",
        "SBIN",
        "SHRIRAMFIN",
    ),

    CSTG2_CONSUMER_AUTO: (
        "ASIANPAINT",
        "BAJAJ-AUTO",
        "EICHERMOT",
        "ETERNAL",
        "HINDUNILVR",
        "INDIGO",
        "ITC",
        "M&M",
        "MARUTI",
        "TATACONSUM",
        "TITAN",
        "TRENT",
    ),

    BPSTG_HEALTHCARE: (
        "APOLLOHOSP",
        "CIPLA",
        "DRREDDY",
        "MAXHEALTH",
        "SUNPHARMA",
    ),
}


BAY_IDS = tuple(FLEET_TOPOLOGY.keys())


def build_symbol_bay_map() -> Dict[str, str]:
    mapping: Dict[str, str] = {}

    for bay_id, symbols in FLEET_TOPOLOGY.items():
        for symbol in symbols:
            if symbol in mapping:
                raise ValueError(
                    f"Duplicate symbol assignment: "
                    f"{symbol} -> {mapping[symbol]} and {bay_id}"
                )
            mapping[symbol] = bay_id

    return mapping


SYMBOL_TO_BAY = build_symbol_bay_map()


def bay_for_symbol(symbol: str) -> str:
    try:
        return SYMBOL_TO_BAY[symbol]
    except KeyError as exc:
        raise KeyError(
            f"Symbol {symbol!r} is not in the certified "
            "48-symbol Revision-5 topology"
        ) from exc


def symbols_for_bay(bay_id: str) -> Tuple[str, ...]:
    try:
        return FLEET_TOPOLOGY[bay_id]
    except KeyError as exc:
        raise KeyError(
            f"Unknown Revision-5 bay: {bay_id!r}"
        ) from exc
