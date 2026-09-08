import sys
import os
from pathlib import Path
import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from upstox_sandbox_adapter import UpstoxSandboxClient
from revision4_engine import ExitEvent, CompletedTrade
from revision2.dataset_manifest import DatasetManifest
from market_data_loader import MarketDataLoader

def load_certified_bars(symbols, tail_bars=1500):
    manifest_path = PROJECT_ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"
    manifest = DatasetManifest.load(str(manifest_path))
    data_dir = PROJECT_ROOT / manifest.data_dir if not os.path.isabs(manifest.data_dir) else Path(manifest.data_dir)
    loader = MarketDataLoader(str(data_dir), synthetic_if_missing=False)
    
    bars = {}
    for s in symbols:
        df = loader._load_symbol_csv(s).tail(tail_bars).reset_index(drop=True)
        bars[s] = df
        print(f"[DATA] Ingested {len(df)} certified bars for {s}")
    return bars

def calculate_indicators(df):
    """Generates 10-box input features: EMA spread, volatility, and momentum."""
    df = df.copy()
    df['ema_fast'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=21, adjust=False).mean()
    df['atr'] = (df['high'] - df['low']).rolling(window=14).mean()
    df['vol_ma'] = df['volume'].rolling(window=20).mean()
    return df

def run_calibrated_sandbox():
    symbols = ["INFY", "MARUTI", "TCS"]
    bars_dict = {s: calculate_indicators(df) for s, df in load_certified_bars(symbols).items()}

    client = UpstoxSandboxClient(sandbox=True)
    capital = 100000.0
    daily_target = 400.0
    completed_trades = []

    class EngineConfig:
        def require(self, name):
            config_map = {
                "entry_confidence_threshold": 0.20,
                "profit_target_pct": 0.006,  # 0.6% target (~₹150 on ₹25k allocation)
                "stop_loss_pct": 0.003,      # 0.3% stop-loss (2:1 R:R)
                "kill_switch_active": True
            }
            return config_map.get(name, 0.1)

    config = EngineConfig()
    target_pct = config.require("profit_target_pct")
    sl_pct = config.require("stop_loss_pct")

    primary_df = bars_dict["INFY"]
    primary_df["date"] = pd.to_datetime(primary_df["timestamp"]).dt.date
    trading_days = primary_df["date"].unique()

    print("\n=== EXECUTING CALIBRATED 10-BOX ENGINE (UPSTOX SANDBOX) ===")
    print(f"Capital: Rs {capital:,.2f} | Daily Target: Rs {daily_target:,.2f} | Target R:R: 2:1\n")

    for day_idx, day_date in enumerate(trading_days, start=1):
        day_pnl = 0.0
        day_locked = False
        trades_today = 0

        for s in symbols:
            if day_locked:
                break

            df = bars_dict[s]
            day_mask = pd.to_datetime(df["timestamp"]).dt.date == day_date
            day_bars = df[day_mask]

            if len(day_bars) < 30:
                continue

            i = 25
            while i < len(day_bars) - 5:
                if day_locked:
                    break

                curr = day_bars.iloc[i]
                
                # 10-box Filter: Trend alignment + volume expansion + ATR clearance
                ema_bull = curr['ema_fast'] > curr['ema_slow']
                vol_conf = curr['volume'] > (curr['vol_ma'] * 1.1)
                atr_val = curr['atr'] if not np.isnan(curr['atr']) else curr['close'] * 0.002
                
                direction = 0
                if ema_bull and vol_conf and (atr_val / curr['close'] > 0.001):
                    direction = 1
                elif not ema_bull and vol_conf and (atr_val / curr['close'] > 0.001):
                    direction = -1

                if direction != 0:
                    entry_price = float(curr["close"])
                    quantity = max(1, int(25000 / entry_price))
                    entry_costs = 15.5
                    trade_id = f"T_{s}_{day_date}_{i}"

                    client.place_order(
                        symbol=s,
                        quantity=quantity,
                        transaction_type="BUY" if direction == 1 else "SELL",
                        price=entry_price,
                        config=config
                    )

                    # Dynamic Exit Logic: Profit Target, Stop Loss, or EOD Exit
                    target_price = entry_price * (1 + target_pct) if direction == 1 else entry_price * (1 - target_pct)
                    stop_price = entry_price * (1 - sl_pct) if direction == 1 else entry_price * (1 + sl_pct)

                    exit_idx = len(day_bars) - 1
                    exit_price = float(day_bars.iloc[exit_idx]["close"])
                    exit_bar = day_bars.iloc[exit_idx]

                    for j in range(i + 1, len(day_bars)):
                        bar_j = day_bars.iloc[j]
                        high_j, low_j = bar_j['high'], bar_j['low']

                        if direction == 1:
                            if high_j >= target_price:
                                exit_price = target_price
                                exit_idx = j
                                exit_bar = bar_j
                                break
                            elif low_j <= stop_price:
                                exit_price = stop_price
                                exit_idx = j
                                exit_bar = bar_j
                                break
                        else:
                            if low_j <= target_price:
                                exit_price = target_price
                                exit_idx = j
                                exit_bar = bar_j
                                break
                            elif high_j >= stop_price:
                                exit_price = stop_price
                                exit_idx = j
                                exit_bar = bar_j
                                break

                    exit_event = ExitEvent(
                        trade_id=trade_id,
                        symbol=s,
                        exit_price=exit_price,
                        exit_timestamp=str(exit_bar["timestamp"])
                    )

                    gross = (exit_price - entry_price) * quantity if direction == 1 else (entry_price - exit_price) * quantity
                    net = gross - (entry_costs + exit_event.total_exit_costs)

                    trade = CompletedTrade(
                        trade_id=trade_id,
                        symbol=s,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        quantity=quantity,
                        entry_costs=entry_costs,
                        exit_costs=exit_event.total_exit_costs,
                        gross_pnl=gross,
                        net_pnl=net
                    )
                    completed_trades.append(trade)
                    capital += net
                    day_pnl += net
                    trades_today += 1

                    if day_pnl >= daily_target:
                        day_locked = True
                        break

                    # Advance index past the exit to prevent overlapping fills on the same symbol
                    i = exit_idx + 1
                else:
                    i += 1

        status = "Target Hit -> Locked" if day_locked else "Session Finished"
        print(f"Day {day_idx:02d} ({day_date}) | Net PnL: Rs {day_pnl:8.2f} | Trades: {trades_today} | {status} | Capital: Rs {capital:10.2f}")

    net_return = capital - 100000.0
    print("\n=== CALIBRATED RUN COMPLETE ===")
    print(f"Total Trades:           {len(completed_trades)}")
    print(f"Initial Capital:        Rs 100,000.00")
    print(f"Final Capital:          Rs {capital:,.2f}")
    print(f"Net Realized Return:    Rs {net_return:,.2f} ({(net_return / 100000.0) * 100:.2f}%)")

if __name__ == "__main__":
    run_calibrated_sandbox()
