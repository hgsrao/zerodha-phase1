# V3.4 Observatory

A **read-only local dashboard** for the V3.4 staging bot.

## Safety boundary

This first release deliberately:
- does not import `institutional_engine_v34`
- does not import `kiteconnect`
- does not read API credentials
- does not call Zerodha
- does not place, modify, or cancel orders
- does not write bot state
- only reads:
  - `bot_state_v34_staging.json`
  - `bot_production.log`

This keeps the observatory outside the trading execution path.

## Start

From the bot directory:

```powershell
python .\v34_observatory.py --base (Get-Location)
```

Then open:

http://127.0.0.1:8765/

Or run:

```powershell
.\START_OBSERVATORY.ps1
```

## What it shows

- V3.4 operating mode
- Kite connection evidence from the log
- four-pillar cards
- current state
- state pipeline
- P&L / MTM
- active trade context
- recent audit/log events
- raw observation payload

## Important limitation

The current bot does not expose every internal decision variable as telemetry. Therefore the dashboard labels unsupported details as **NOT INSTRUMENTED** rather than inventing values.

The next version can add a dedicated, read-only telemetry file/socket emitted by the engine. That would let the dashboard show the exact per-symbol alpha scores, candidate validation gates, order/HWM lifecycle, MTM calculations, and the precise reason each gate passed or failed.
