# R5 three-engine real-data validation

SCOPE: as PHASE10_THREE_ENGINE_REPORT.md (48 symbols, last 1,200 bars, real data, full authority, full telemetry, final_exit_authority, authority_telemetry) WITH EVERY OPT-IN BEHAVIOUR CHANGE ON: governor_position_mode=v3, avr_voltage_mode=measured, dispatcher_allocation_mode=bounded, hrsg_correlation_mode=causal_bay_returns.  Evidence for the behaviour-change approval; not sealed.

| Engine | Entries | Exits | Gross P&L | Costs | Net P&L | Closed-trade DD | Mean hold (bars) | Plant trips | Reconciliation breaks |
|---|---|---|---|---|---|---|---|---|---|
| A | 11 | 11 | -380.70 | 226.02 | -606.72 | 606.72 | 4.27 | 1 | 0 |
| B | 11 | 11 | -108.15 | 227.02 | -335.17 | 335.17 | 4.45 | 1 | 0 |
| C | 11 | 11 | -236.16 | 222.71 | -458.87 | 458.87 | 3.45 | 2 | 0 |

## Exit authority

| Engine | Group | Exits | Gross | Costs | Net |
|---|---|---|---|---|---|
| A | FINAL_EXECUTION_CONTROLLER | 3 | -41.05 | 49.68 | -90.73 |
| A | GOVERNOR | 6 | -267.42 | 136.57 | -403.99 |
| A | PLANT_PROTECTION | 1 | -62.71 | 24.71 | -87.42 |
| A | PROTECTIVE_STOP | 1 | -9.52 | 15.06 | -24.58 |
| B | FINAL_EXECUTION_CONTROLLER | 2 | -3.50 | 41.79 | -45.29 |
| B | GOVERNOR | 5 | -68.15 | 114.00 | -182.15 |
| B | PLANT_PROTECTION | 1 | -47.60 | 24.72 | -72.32 |
| B | PROTECTIVE_STOP | 3 | 11.10 | 46.51 | -35.41 |
| C | FINAL_EXECUTION_CONTROLLER | 3 | -15.90 | 49.69 | -65.59 |
| C | GOVERNOR | 5 | -124.94 | 114.00 | -238.95 |
| C | PLANT_PROTECTION | 2 | -73.11 | 43.95 | -117.05 |
| C | PROTECTIVE_STOP | 1 | -22.20 | 15.07 | -37.28 |

## Bay

| Engine | Group | Exits | Gross | Costs | Net |
|---|---|---|---|---|---|
| A | BPSTG_HEALTHCARE | 2 | -3.32 | 15.99 | -19.31 |
| A | CSTG1_BFSI | 2 | -111.58 | 46.34 | -157.92 |
| A | CSTG2_CONSUMER_AUTO | 1 | -37.74 | 33.69 | -71.43 |
| A | GTG1_HEAVY_INDUSTRY | 3 | -114.20 | 67.73 | -181.93 |
| A | GTG2_TECH_TELECOM | 3 | -113.87 | 62.27 | -176.14 |
| B | BPSTG_HEALTHCARE | 2 | 25.00 | 15.99 | 9.01 |
| B | CSTG1_BFSI | 2 | -19.00 | 47.32 | -66.32 |
| B | CSTG2_CONSUMER_AUTO | 1 | -10.50 | 33.70 | -44.20 |
| B | GTG1_HEAVY_INDUSTRY | 3 | -12.65 | 67.72 | -80.37 |
| B | GTG2_TECH_TELECOM | 3 | -91.00 | 62.30 | -153.30 |
| C | BPSTG_HEALTHCARE | 2 | 15.60 | 15.99 | -0.39 |
| C | CSTG1_BFSI | 1 | -28.80 | 23.78 | -52.58 |
| C | CSTG2_CONSUMER_AUTO | 1 | -31.50 | 33.70 | -65.20 |
| C | GTG1_HEAVY_INDUSTRY | 3 | -56.60 | 67.72 | -124.32 |
| C | GTG2_TECH_TELECOM | 4 | -134.85 | 81.52 | -216.38 |

## Sector

| Engine | Group | Exits | Gross | Costs | Net |
|---|---|---|---|---|---|
| A | Aviation | 1 | -37.74 | 33.69 | -71.43 |
| A | CapitalGoods&Defense | 1 | -65.80 | 25.48 | -91.28 |
| A | Financials | 2 | -111.58 | 46.34 | -157.92 |
| A | Healthcare | 2 | -3.32 | 15.99 | -19.31 |
| A | IT | 2 | -51.16 | 37.56 | -88.72 |
| A | Infra&Cement | 1 | -52.24 | 25.90 | -78.14 |
| A | Metals&Mining | 1 | 3.84 | 16.34 | -12.50 |
| A | Telecom | 1 | -62.71 | 24.71 | -87.42 |
| B | Aviation | 1 | -10.50 | 33.70 | -44.20 |
| B | CapitalGoods&Defense | 1 | -17.25 | 25.47 | -42.72 |
| B | Financials | 2 | -19.00 | 47.32 | -66.32 |
| B | Healthcare | 2 | 25.00 | 15.99 | 9.01 |
| B | IT | 2 | -43.40 | 37.58 | -80.98 |
| B | Infra&Cement | 1 | -27.20 | 25.91 | -53.11 |
| B | Metals&Mining | 1 | 31.80 | 16.34 | 15.46 |
| B | Telecom | 1 | -47.60 | 24.72 | -72.32 |
| C | Aviation | 1 | -31.50 | 33.70 | -65.20 |
| C | CapitalGoods&Defense | 1 | -41.40 | 25.48 | -66.88 |
| C | Financials | 1 | -28.80 | 23.78 | -52.58 |
| C | Healthcare | 2 | 15.60 | 15.99 | -0.39 |
| C | IT | 3 | -66.82 | 56.81 | -123.63 |
| C | Infra&Cement | 1 | -37.40 | 25.91 | -63.31 |
| C | Metals&Mining | 1 | 22.20 | 16.34 | 5.86 |
| C | Telecom | 1 | -68.04 | 24.71 | -92.75 |

## Holding duration (bars)

| Engine | Group | Exits | Gross | Costs | Net |
|---|---|---|---|---|---|
| A | 0-2 | 1 | -62.71 | 24.71 | -87.42 |
| A | 3-5 | 8 | -242.35 | 162.70 | -405.04 |
| A | 6-10 | 2 | -75.64 | 38.62 | -114.26 |
| B | 0-2 | 1 | -47.60 | 24.72 | -72.32 |
| B | 3-5 | 7 | -71.65 | 155.79 | -227.44 |
| B | 6-10 | 3 | 11.10 | 46.51 | -35.41 |
| C | 0-2 | 2 | -73.11 | 43.95 | -117.05 |
| C | 3-5 | 8 | -140.85 | 163.69 | -304.54 |
| C | 6-10 | 1 | -22.20 | 15.07 | -37.28 |

## Controller events

- **A**: governor {'GOVERNOR_ENTRY_DECISION': 4224, 'GOVERNOR_POSITION_DECISION': 46, 'GOVERNOR_STOP_UPDATE': 40, 'GOVERNOR_EXIT_ARMED': 6, 'GOVERNOR_EXIT': 6}; AVR {'AVR_VOLTAGE_REGULATION': 1139}; dispatcher {'PLANT_CONTROL_PAPER_ADMISSION': 888, 'DISPATCHER_UPDATE': 11}; HRSG {'HRSG_RETURN_SNAPSHOT': 1139, 'PLANT_BAR_STEP': 1139}; final execution {'FINAL_EXECUTION_ENTRY_DECISION': 877, 'FINAL_EXECUTION_EXIT_DECISION': 48, 'FINAL_EXECUTION_EXIT_ARMED': 10}; plant trips {'SEL300G_TRIP': 1}
- **B**: governor {'GOVERNOR_ENTRY_DECISION': 4249, 'GOVERNOR_POSITION_DECISION': 48, 'GOVERNOR_STOP_UPDATE': 43, 'GOVERNOR_EXIT_ARMED': 5, 'GOVERNOR_EXIT': 5}; AVR {'AVR_VOLTAGE_REGULATION': 1140}; dispatcher {'PLANT_CONTROL_PAPER_ADMISSION': 959, 'DISPATCHER_UPDATE': 11}; HRSG {'HRSG_RETURN_SNAPSHOT': 1140, 'PLANT_BAR_STEP': 1140}; final execution {'FINAL_EXECUTION_ENTRY_DECISION': 948, 'FINAL_EXECUTION_EXIT_DECISION': 52, 'FINAL_EXECUTION_EXIT_ARMED': 6}; plant trips {'SEL300G_TRIP': 1}
- **C**: governor {'GOVERNOR_ENTRY_DECISION': 4253, 'GOVERNOR_POSITION_DECISION': 37, 'GOVERNOR_STOP_UPDATE': 32, 'GOVERNOR_EXIT_ARMED': 5, 'GOVERNOR_EXIT': 5}; AVR {'AVR_VOLTAGE_REGULATION': 1140}; dispatcher {'PLANT_CONTROL_PAPER_ADMISSION': 919, 'DISPATCHER_UPDATE': 11}; HRSG {'HRSG_RETURN_SNAPSHOT': 1140, 'PLANT_BAR_STEP': 1140}; final execution {'FINAL_EXECUTION_ENTRY_DECISION': 908, 'FINAL_EXECUTION_EXIT_DECISION': 39, 'FINAL_EXECUTION_EXIT_ARMED': 9}; plant trips {'SEL300G_TRIP': 1, 'ENGINE_A_FILL_LATCH_REJECTED': 1}

## Entry and hold authority

- **A**: entry decisions {'NO_ACTION:FSR_BELOW_ENTRY_HURDLE:FSRN': 2225, 'NO_ACTION:GOVERNOR_OVERSPEED_LIMIT': 957, 'ENTRY:GOVERNOR_ENTRY': 877, 'NO_ACTION:GRID_REFERENCE_UNAVAILABLE': 160, 'NO_ACTION:EXHAUST_SPREAD_HOLD': 5}; governor HOLD bar-decisions 40
- **B**: entry decisions {'NO_ACTION:FSR_BELOW_ENTRY_HURDLE:FSRN': 2343, 'NO_ACTION:GOVERNOR_OVERSPEED_LIMIT': 793, 'ENTRY:GOVERNOR_ENTRY': 948, 'NO_ACTION:GRID_REFERENCE_UNAVAILABLE': 160, 'NO_ACTION:EXHAUST_SPREAD_HOLD': 5}; governor HOLD bar-decisions 43
- **C**: entry decisions {'NO_ACTION:FSR_BELOW_ENTRY_HURDLE:FSRN': 2301, 'NO_ACTION:GOVERNOR_OVERSPEED_LIMIT': 879, 'ENTRY:GOVERNOR_ENTRY': 908, 'NO_ACTION:GRID_REFERENCE_UNAVAILABLE': 160, 'NO_ACTION:EXHAUST_SPREAD_HOLD': 5}; governor HOLD bar-decisions 32

Per-symbol tables, every trade (entry/exit timestamps and prices) and the controller parameter values (startup report + applied-value min/max/last) are in the JSON report.
