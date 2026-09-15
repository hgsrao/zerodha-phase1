import pandas as pd
import openpyxl
from openpyxl.chart import LineChart, BarChart
from openpyxl.styles import Font, PatternFill, Alignment
import io

# Read calibration data
df = pd.read_csv(r'C:\Users\Dishan\Documents\Codex\Zerodha_live_bot_3.4_ENTRY_UNKNOWN\calibration_progress.csv')
df.columns = ['Iteration', 'Win Rate %', 'Trades']

# Add moving averages
df['Win Rate MA (50)'] = df['Win Rate %'].rolling(window=50, center=True).mean()
df['Win Rate MA (100)'] = df['Win Rate %'].rolling(window=100, center=True).mean()

# Create workbook
wb = openpyxl.Workbook()
wb.remove(wb.active)

# ============================================================================
# SHEET 1: DCS ARCHITECTURE OVERVIEW
# ============================================================================

ws_arch = wb.create_sheet("1. DCS Architecture", 0)

arch_content = [
    ["DISTRIBUTED CONTROL SYSTEM (DCS) - 6-STAGE TRADING PIPELINE"],
    [],
    ["STAGE", "NAME", "FUNCTION", "INPUT", "OUTPUT"],
    ["1", "Data Validation", "Load and validate historical data", "Raw market data (15-min bars)", "Cleaned OHLCV data"],
    ["2", "PA Engine + FB Loop 1", "Predictive Analytics signal quality", "Market data, Momentum indicators", "Signal strength (0-1)"],
    ["3", "ID Threshold", "Intelligent Discriminator entry filter", "PA signal, Technical conditions", "Pass/Fail entry permission"],
    ["4", "Bridge Economic Viability", "Risk/Reward validation", "Entry signal, profit_target, stop_loss", "Trade viable (profit > costs)"],
    ["5", "MPC + FB Loop 2", "Model Predictive Control exit logic", "Position state, Market data", "Exit signal (profit or stop)"],
    ["6", "P01D Governor", "Position control execution", "All signals, Parameters", "BUY/HOLD/SELL decisions"],
    [],
    ["DESIGN PRINCIPLE", "EXPLANATION"],
    ["Governor Theory", "Based on power plant governor control maintains optimal trading balance"],
    ["Multi-Stage Gating", "Each stage filters validates before next stage reduces false signals"],
    ["Real-time Feedback", "PA learns price/volume patterns; MPC learns exit timing"],
    ["Economic Viability", "Bridge ensures profit_target > transaction costs (10 bps NSE)"],
    ["ATR-Scaled Parameters", "Profit targets and stop losses scale with volatility (ATR)"],
]

for row_idx, row_data in enumerate(arch_content, 1):
    for col_idx, value in enumerate(row_data, 1):
        cell = ws_arch.cell(row=row_idx, column=col_idx, value=value)
        if row_idx == 1:
            cell.font = Font(bold=True, size=12, color="FFFFFF")
            cell.fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        elif row_idx == 3:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        elif row_idx == 11:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")

ws_arch.column_dimensions['A'].width = 15
ws_arch.column_dimensions['B'].width = 25
ws_arch.column_dimensions['C'].width = 35
ws_arch.column_dimensions['D'].width = 30
ws_arch.column_dimensions['E'].width = 30

# ============================================================================
# SHEET 2: PARAMETERS & OPTIMIZATION
# ============================================================================

ws_params = wb.create_sheet("2. Parameters & Ranges", 1)

params_content = [
    ["LEARNABLE PARAMETERS - META-LEARNING OPTIMIZATION"],
    [],
    ["PARAMETER", "MIN", "MAX", "CURRENT (BEST)", "WHAT IT DOES", "UPDATE MECHANISM"],
    ["profit_target_atr_mult", 1.50, 2.00, "1.60 (approx)", "Scales profit target with volatility (ATR)", "Bayesian + Phase 3 tuning"],
    ["stop_loss_atr_mult", 0.50, 1.00, "0.75 (approx)", "Scales stop loss with volatility", "Bayesian + Phase 3 tuning"],
    ["entry_pid_kp", 0.05, 0.25, "0.15 (approx)", "Entry signal confidence (PID gain)", "Bayesian + Phase 3 tuning"],
    ["exit_pid_kp", 0.05, 0.25, "0.08 (approx)", "Exit signal timing (PID gain)", "Bayesian + Phase 3 tuning"],
    ["min_hold_bars", 1, 5, "2.5 (approx)", "Minimum bars to hold position", "Bayesian + Phase 3 tuning"],
    ["max_hold_bars", 10, 120, "80 (approx)", "Maximum bars to hold position", "Bayesian + Phase 3 tuning"],
    [],
    ["FIXED PARAMETERS (Not Optimized)"],
    [],
    ["PARAMETER", "VALUE", "PURPOSE"],
    ["Entry Confidence Target", 0.50, "Minimum signal strength to enter (0-1 scale)"],
    ["Risk/Reward Minimum", 1.5, "profit_target must be >= 1.5x stop_loss"],
    ["Sync Threshold (Price)", "ATR x 0.05", "Entry gate: price must move >= this amount"],
    ["Sync Threshold (Volume)", "Vol_mean x 0.02", "Entry gate: volume must change >= this amount"],
    ["NSE Entry Cost", "3.5 bps", "Transaction cost for entry (Bridge check)"],
    ["NSE Exit Cost", "6.5 bps", "Transaction cost for exit (Bridge check)"],
]

for row_idx, row_data in enumerate(params_content, 1):
    for col_idx, value in enumerate(row_data, 1):
        cell = ws_params.cell(row=row_idx, column=col_idx, value=value)
        if row_idx == 1:
            cell.font = Font(bold=True, size=12, color="FFFFFF")
            cell.fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        elif row_idx == 3 or row_idx == 11 or row_idx == 13:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")

for col in ['A', 'B', 'C', 'D', 'E', 'F']:
    ws_params.column_dimensions[col].width = 25

# ============================================================================
# SHEET 3: CALIBRATION PHASES
# ============================================================================

ws_phases = wb.create_sheet("3. Calibration Phases", 2)

phases_content = [
    ["CALIBRATION PHASES - 500 ITERATION META-LEARNING"],
    [],
    ["PHASE", "ITERATIONS", "STRATEGY", "PURPOSE", "EXPECTED OUTCOME"],
    ["Phase 1", "1-50", "Random Exploration", "Test diverse parameter combinations", "Find initial promising regions"],
    ["Phase 2", "51-250", "Bayesian Optimization", "Concentrate search in high-performing regions", "Converge to local optimum"],
    ["Phase 3", "251-500", "Fine-tuning", "Small perturbations around best found", "Validate and stabilize optimal parameters"],
    [],
    ["CALIBRATION STATISTICS"],
    [],
    ["METRIC", "VALUE"],
    ["Total Iterations (Logged)", 1028],
    ["Average Win Rate", "48.76%"],
    ["Win Rate Std Dev", "10.61%"],
    ["Min Win Rate", "0% (early exploration)"],
    ["Max Win Rate", "71.43% (best iteration!)"],
    ["Total Trades Generated", "528,906"],
    ["Avg Trades/Iteration", "514.5"],
    ["Status", "PHASE 3 RUNNING"],
]

for row_idx, row_data in enumerate(phases_content, 1):
    for col_idx, value in enumerate(row_data, 1):
        cell = ws_phases.cell(row=row_idx, column=col_idx, value=value)
        if row_idx == 1:
            cell.font = Font(bold=True, size=12, color="FFFFFF")
            cell.fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        elif row_idx in [3, 9]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")

ws_phases.column_dimensions['A'].width = 25
ws_phases.column_dimensions['B'].width = 30
ws_phases.column_dimensions['C'].width = 25
ws_phases.column_dimensions['D'].width = 35
ws_phases.column_dimensions['E'].width = 30

# ============================================================================
# SHEET 4: CALIBRATION DATA & CHARTS
# ============================================================================

ws_data = wb.create_sheet("4. Calibration Data", 3)

# Write headers
headers = ["Iteration", "Win Rate %", "Trades", "Win Rate MA (50)", "Win Rate MA (100)"]
for col_idx, header in enumerate(headers, 1):
    cell = ws_data.cell(row=1, column=col_idx, value=header)
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")

# Write data rows
for row_idx, (_, row_data) in enumerate(df.iterrows(), 2):
    ws_data.cell(row=row_idx, column=1, value=int(row_data['Iteration']))
    ws_data.cell(row=row_idx, column=2, value=round(row_data['Win Rate %'], 2))
    ws_data.cell(row=row_idx, column=3, value=int(row_data['Trades']))
    ws_data.cell(row=row_idx, column=4, value=round(row_data['Win Rate MA (50)'], 2) if pd.notna(row_data['Win Rate MA (50)']) else None)
    ws_data.cell(row=row_idx, column=5, value=round(row_data['Win Rate MA (100)'], 2) if pd.notna(row_data['Win Rate MA (100)']) else None)

# Add chart 1: Win Rate Progression
chart1 = LineChart()
chart1.title = "Win Rate Progression & Trend"
chart1.style = 12
chart1.y_axis.title = 'Win Rate %'
chart1.x_axis.title = 'Iteration'
chart1.height = 15
chart1.width = 25

data1 = openpyxl.chart.Reference(ws_data, min_col=2, min_row=1, max_row=len(df)+1)
data_ma50 = openpyxl.chart.Reference(ws_data, min_col=4, min_row=1, max_row=len(df)+1)
cats = openpyxl.chart.Reference(ws_data, min_col=1, min_row=2, max_row=len(df)+1)

chart1.add_data(data1, titles_from_data=True)
chart1.add_data(data_ma50, titles_from_data=True)
chart1.set_categories(cats)
ws_data.add_chart(chart1, "G2")

# Add chart 2: Trades count
chart2 = BarChart()
chart2.title = "Trades per Iteration"
chart2.style = 11
chart2.y_axis.title = 'Trades'
chart2.x_axis.title = 'Iteration'
chart2.height = 12
chart2.width = 25

data2 = openpyxl.chart.Reference(ws_data, min_col=3, min_row=1, max_row=len(df)+1)
chart2.add_data(data2, titles_from_data=True)
chart2.set_categories(cats)
ws_data.add_chart(chart2, "G22")

ws_data.column_dimensions['A'].width = 12
ws_data.column_dimensions['B'].width = 15
ws_data.column_dimensions['C'].width = 15
ws_data.column_dimensions['D'].width = 18
ws_data.column_dimensions['E'].width = 18

# ============================================================================
# SAVE
# ============================================================================

wb.save(r'C:\Users\Dishan\Documents\Codex\Zerodha_live_bot_3.4_ENTRY_UNKNOWN\DCS_System_Complete_Analysis.xlsx')

print("=" * 80)
print("SUCCESS: DCS_System_Complete_Analysis.xlsx")
print("=" * 80)
print("\nSheets included:")
print("  1. DCS Architecture    - 6-stage pipeline with design principles")
print("  2. Parameters & Ranges - All learnable + fixed parameters")
print("  3. Calibration Phases  - 500-iteration meta-learning phases")
print("  4. Calibration Data    - Raw iteration data + win rate charts")
print("\nStatistics Summary:")
print(f"  Total Iterations: {len(df)}")
print(f"  Avg Win Rate: {df['Win Rate %'].mean():.2f}%")
print(f"  Max Win Rate: {df['Win Rate %'].max():.2f}%")
print(f"  Total Trades: {df['Trades'].sum():.0f}")
