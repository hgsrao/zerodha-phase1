# R5 three-engine real-data validation

SCOPE: all 48 frozen symbols, the most recent 1,200 one-minute bars per symbol (last decision 2026-08-13), real NSE data (manifest f724074ca45e) and the real NIFTY/VIX grid; governor_authority=full, telemetry_mode=full, final_exit_authority=True, authority_telemetry=True, all other controller modes at their defaults (legacy).  NOT the full July 2023 - August 2026 history the programme requires: see Incomplete items.

| Engine | Entries | Exits | Gross P&L | Costs | Net P&L | Closed-trade DD | Mean hold (bars) | Plant trips | Reconciliation breaks |
|---|---|---|---|---|---|---|---|---|---|
| A | 11 | 11 | -386.08 | 216.52 | -602.60 | 602.60 | 4.36 | 1 | 0 |
| B | 11 | 11 | -140.90 | 216.54 | -357.44 | 357.44 | 5.0 | 1 | 0 |
| C | 11 | 11 | -216.99 | 211.14 | -428.13 | 428.13 | 3.55 | 2 | 0 |

## Exit authority

| Engine | Group | Exits | Gross | Costs | Net |
|---|---|---|---|---|---|
| A | FINAL_EXECUTION_CONTROLLER | 3 | -35.66 | 44.87 | -80.53 |
| A | GOVERNOR | 7 | -287.71 | 146.94 | -434.65 |
| A | PLANT_PROTECTION | 1 | -62.71 | 24.71 | -87.42 |
| B | FINAL_EXECUTION_CONTROLLER | 2 | -2.00 | 36.98 | -38.98 |
| B | GOVERNOR | 8 | -91.30 | 154.84 | -246.14 |
| B | PLANT_PROTECTION | 1 | -47.60 | 24.72 | -72.32 |
| C | FINAL_EXECUTION_CONTROLLER | 3 | -11.40 | 44.88 | -56.28 |
| C | GOVERNOR | 6 | -132.76 | 123.39 | -256.15 |
| C | PLANT_PROTECTION | 2 | -72.83 | 42.88 | -115.70 |

## Bay

| Engine | Group | Exits | Gross | Costs | Net |
|---|---|---|---|---|---|
| A | BPSTG_HEALTHCARE | 2 | -3.32 | 15.99 | -19.31 |
| A | CSTG1_BFSI | 2 | -109.60 | 45.35 | -154.95 |
| A | CSTG2_CONSUMER_AUTO | 1 | -32.35 | 28.88 | -61.22 |
| A | GTG1_HEAVY_INDUSTRY | 3 | -108.27 | 65.09 | -173.36 |
| A | GTG2_TECH_TELECOM | 3 | -132.55 | 61.21 | -193.76 |
| B | BPSTG_HEALTHCARE | 2 | 23.50 | 15.99 | 7.51 |
| B | CSTG1_BFSI | 2 | -57.00 | 45.35 | -102.35 |
| B | CSTG2_CONSUMER_AUTO | 1 | -9.00 | 28.89 | -37.89 |
| B | GTG1_HEAVY_INDUSTRY | 3 | -10.30 | 65.09 | -75.39 |
| B | GTG2_TECH_TELECOM | 3 | -88.10 | 61.22 | -149.32 |
| C | BPSTG_HEALTHCARE | 2 | 15.60 | 15.99 | -0.39 |
| C | CSTG1_BFSI | 1 | -26.40 | 21.80 | -48.20 |
| C | CSTG2_CONSUMER_AUTO | 1 | -27.00 | 28.89 | -55.89 |
| C | GTG1_HEAVY_INDUSTRY | 3 | -52.60 | 65.09 | -117.69 |
| C | GTG2_TECH_TELECOM | 4 | -126.59 | 79.38 | -205.97 |

## Sector

| Engine | Group | Exits | Gross | Costs | Net |
|---|---|---|---|---|---|
| A | Aviation | 1 | -32.35 | 28.88 | -61.22 |
| A | CapitalGoods&Defense | 1 | -62.94 | 24.38 | -87.31 |
| A | Financials | 2 | -109.60 | 45.35 | -154.95 |
| A | Healthcare | 2 | -3.32 | 15.99 | -19.31 |
| A | IT | 2 | -69.84 | 36.50 | -106.34 |
| A | Infra&Cement | 1 | -49.17 | 24.38 | -73.55 |
| A | Metals&Mining | 1 | 3.84 | 16.34 | -12.50 |
| A | Telecom | 1 | -62.71 | 24.71 | -87.42 |
| B | Aviation | 1 | -9.00 | 28.89 | -37.89 |
| B | CapitalGoods&Defense | 1 | -16.50 | 24.37 | -40.87 |
| B | Financials | 2 | -57.00 | 45.35 | -102.35 |
| B | Healthcare | 2 | 23.50 | 15.99 | 7.51 |
| B | IT | 2 | -40.50 | 36.51 | -77.01 |
| B | Infra&Cement | 1 | -25.60 | 24.38 | -49.98 |
| B | Metals&Mining | 1 | 31.80 | 16.34 | 15.46 |
| B | Telecom | 1 | -47.60 | 24.72 | -72.32 |
| C | Aviation | 1 | -27.00 | 28.89 | -55.89 |
| C | CapitalGoods&Defense | 1 | -39.60 | 24.37 | -63.97 |
| C | Financials | 1 | -26.40 | 21.80 | -48.20 |
| C | Healthcare | 2 | 15.60 | 15.99 | -0.39 |
| C | IT | 3 | -58.55 | 54.67 | -113.22 |
| C | Infra&Cement | 1 | -35.20 | 24.39 | -59.59 |
| C | Metals&Mining | 1 | 22.20 | 16.34 | 5.86 |
| C | Telecom | 1 | -68.04 | 24.71 | -92.75 |

## Holding duration (bars)

| Engine | Group | Exits | Gross | Costs | Net |
|---|---|---|---|---|---|
| A | 0-2 | 1 | -62.71 | 24.71 | -87.42 |
| A | 3-5 | 8 | -227.07 | 153.19 | -380.25 |
| A | 6-10 | 2 | -96.30 | 38.62 | -134.93 |
| B | 0-2 | 1 | -47.60 | 24.72 | -72.32 |
| B | 11-20 | 1 | 16.50 | 7.90 | 8.60 |
| B | 3-5 | 7 | -64.30 | 145.29 | -209.59 |
| B | 6-10 | 2 | -45.50 | 38.63 | -84.13 |
| C | 0-2 | 2 | -72.83 | 42.88 | -115.70 |
| C | 3-5 | 8 | -128.06 | 153.19 | -281.26 |
| C | 6-10 | 1 | -16.10 | 15.07 | -31.17 |

## Controller events

- **A**: governor {'GOVERNOR_ENTRY_DECISION': 4224, 'GOVERNOR_POSITION_DECISION': 47, 'GOVERNOR_STOP_UPDATE': 40, 'GOVERNOR_EXIT_ARMED': 7, 'GOVERNOR_EXIT': 7}; AVR {}; dispatcher {'PLANT_CONTROL_PAPER_ADMISSION': 880, 'DISPATCHER_UPDATE': 11}; HRSG {'PLANT_BAR_STEP': 1139}; final execution {'FINAL_EXECUTION_ENTRY_DECISION': 869, 'FINAL_EXECUTION_EXIT_DECISION': 48, 'FINAL_EXECUTION_EXIT_ARMED': 10}; plant trips {'SEL300G_TRIP': 1}
- **B**: governor {'GOVERNOR_ENTRY_DECISION': 4247, 'GOVERNOR_POSITION_DECISION': 54, 'GOVERNOR_STOP_UPDATE': 46, 'GOVERNOR_EXIT_ARMED': 8, 'GOVERNOR_EXIT': 8}; AVR {}; dispatcher {'PLANT_CONTROL_PAPER_ADMISSION': 955, 'DISPATCHER_UPDATE': 11}; HRSG {'PLANT_BAR_STEP': 1140}; final execution {'FINAL_EXECUTION_ENTRY_DECISION': 944, 'FINAL_EXECUTION_EXIT_DECISION': 55, 'FINAL_EXECUTION_EXIT_ARMED': 7}; plant trips {'SEL300G_TRIP': 1}
- **C**: governor {'GOVERNOR_ENTRY_DECISION': 4253, 'GOVERNOR_POSITION_DECISION': 38, 'GOVERNOR_STOP_UPDATE': 32, 'GOVERNOR_EXIT_ARMED': 6, 'GOVERNOR_EXIT': 6}; AVR {}; dispatcher {'PLANT_CONTROL_PAPER_ADMISSION': 924, 'DISPATCHER_UPDATE': 11}; HRSG {'PLANT_BAR_STEP': 1140}; final execution {'FINAL_EXECUTION_ENTRY_DECISION': 913, 'FINAL_EXECUTION_EXIT_DECISION': 39, 'FINAL_EXECUTION_EXIT_ARMED': 9}; plant trips {'SEL300G_TRIP': 1, 'ENGINE_A_FILL_LATCH_REJECTED': 1}

## Entry and hold authority

- **A**: entry decisions {'NO_ACTION:FSR_BELOW_ENTRY_HURDLE:FSRN': 2220, 'NO_ACTION:GOVERNOR_OVERSPEED_LIMIT': 970, 'ENTRY:GOVERNOR_ENTRY': 869, 'NO_ACTION:GRID_REFERENCE_UNAVAILABLE': 160, 'NO_ACTION:EXHAUST_SPREAD_HOLD': 5}; governor HOLD bar-decisions 40
- **B**: entry decisions {'NO_ACTION:FSR_BELOW_ENTRY_HURDLE:FSRN': 2337, 'NO_ACTION:GOVERNOR_OVERSPEED_LIMIT': 801, 'ENTRY:GOVERNOR_ENTRY': 944, 'NO_ACTION:GRID_REFERENCE_UNAVAILABLE': 160, 'NO_ACTION:EXHAUST_SPREAD_HOLD': 5}; governor HOLD bar-decisions 46
- **C**: entry decisions {'NO_ACTION:FSR_BELOW_ENTRY_HURDLE:FSRN': 2304, 'NO_ACTION:GOVERNOR_OVERSPEED_LIMIT': 871, 'ENTRY:GOVERNOR_ENTRY': 913, 'NO_ACTION:GRID_REFERENCE_UNAVAILABLE': 160, 'NO_ACTION:EXHAUST_SPREAD_HOLD': 5}; governor HOLD bar-decisions 32

Per-symbol tables, every trade (entry/exit timestamps and prices) and the controller parameter values (startup report + applied-value min/max/last) are in the JSON report.
