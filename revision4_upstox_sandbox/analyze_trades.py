import pandas as pd
import os

def run_analysis():
    csv_file = "sandbox_48_symbol_trades.csv"
    
    if not os.path.exists(csv_file):
        print(f"[!] Error: '{csv_file}' not found.")
        print("Please run the 'run_48_symbol_engine.py' script first to generate the data.")
        return

    # Read the data
    df = pd.read_csv(csv_file)
    
    if df.empty:
        print("[!] The CSV is empty. No trades were recorded.")
        return

    # 1. Calculate Overall Win Rate
    total_trades = len(df)
    winning_trades = len(df[df['pnl_realized'] > 0])
    losing_trades = len(df[df['pnl_realized'] < 0])
    breakeven_trades = total_trades - winning_trades - losing_trades
    
    win_rate = (winning_trades / total_trades) * 100

    # 2. Group PnL by Symbol
    symbol_pnl = df.groupby('symbol')['pnl_realized'].sum().reset_index()
    
    # Sort for top and bottom
    symbol_pnl_sorted = symbol_pnl.sort_values(by='pnl_realized', ascending=False)
    top_5 = symbol_pnl_sorted.head(5)
    bottom_5 = symbol_pnl_sorted.tail(5).sort_values(by='pnl_realized', ascending=True)

    # 3. Print the Report
    print("\n" + "="*45)
    print(" 📊 48-SYMBOL SANDBOX ANALYSIS REPORT")
    print("="*45)
    
    print(f"\n--- OVERALL METRICS ---")
    print(f"  Total Trades : {total_trades}")
    print(f"  Win Rate     : {win_rate:.2f}% ({winning_trades}W / {losing_trades}L / {breakeven_trades}BE)")
    
    gross_pnl = df['pnl_realized'].sum()
    print(f"  Total Net PnL: Rs {gross_pnl:,.2f}")

    print(f"\n--- TOP 5 MOST PROFITABLE SYMBOLS ---")
    for _, row in top_5.iterrows():
        print(f"  {row['symbol']:<15} : Rs {row['pnl_realized']:>10,.2f}")

    print(f"\n--- TOP 5 BIGGEST LOSERS ---")
    for _, row in bottom_5.iterrows():
        print(f"  {row['symbol']:<15} : Rs {row['pnl_realized']:>10,.2f}")
        
    print("\n" + "="*45 + "\n")

if __name__ == "__main__":
    run_analysis()
