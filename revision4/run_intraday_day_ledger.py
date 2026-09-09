"""Run one sealed market session and retain every completed-trade record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from revision4.validate_48symbol_sealed import run_48symbol_validation


def run_intraday_day(trading_date: str) -> dict:
    """Execute one calendar session and fail its certification on an overnight trade."""
    report = run_48symbol_validation(month_start=trading_date, month_end=trading_date)
    audit = report["intraday_audit"]
    if report["status"] == "PASSED" and not audit["all_trades_same_session"]:
        report["status"] = "INTRADAY_VIOLATION"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run one sealed intraday session with complete trade ledger")
    parser.add_argument("--date", default="2023-09-01", help="Trading date in YYYY-MM-DD format")
    args = parser.parse_args()
    report = run_intraday_day(args.date)
    output = Path(f"diagnostic_output/intraday_{args.date.replace('-', '')}_trade_ledger.json")
    output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({
        "status": report["status"],
        "trades": report["intraday_audit"]["completed_trade_count"],
        "all_trades_same_session": report["intraday_audit"]["all_trades_same_session"],
        "report": str(output),
    }, indent=2))
