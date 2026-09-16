"""
Alpha Universe Trading Engine (Production Replay Core)
-----------------------------------------------------
Architecture:
  - Feature Engine: Numerically stable Welford rolling variance & VWAP
  - Entry Filter: ANSI 25 Synchrocheck (zero-crossing confirmation candle)
  - Exit Engine: Dynamic Volatility Governor with Time-Decay Schedule
  - Risk Sizing: Sector-decoupled Price-Proportional Slot Allocation
  - Cost Model: Zerodha Equity Intraday Statutory Fee Schedule
"""

import sys
import argparse
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)


# =====================================================================
# 1. DOMAIN MODELS & DATA STRUCTURES
# =====================================================================

@dataclass(slots=True)
class EngineConfig:
    portfolio_capital: float = 1_000_000.0
    max_concurrent_positions: int = 3
    symbols: List[str] = field(default_factory=lambda: [
        "TCS", "HDFCBANK", "BAJFINANCE", "LT", "TATAMOTORS", "INFY"
    ])
    sector_map: Dict[str, str] = field(default_factory=lambda: {
        "HDFCBANK": "BANK",
        "INFY": "IT",
        "TCS": "IT",
        "LT": "INFRA",
        "BAJFINANCE": "FIN",
        "TATAMOTORS": "AUTO"
    })
    # Entry criteria
    z_arm_threshold: float = -2.20
    rsi_arm_threshold: float = 32.0
    entry_cutoff_time: str = "13:30:00"
    session_close_time: str = "15:15:00"
    
    # Target / Governor parameters
    base_r_target: float = 2.25
    base_z_target: float = 0.60
    min_harvest_r: float = 0.35
    decay_start_time: str = "12:30:00"
    min_decay_floor: float = 0.30
    stop_atr_multiplier: float = 1.00
    trailing_profit_lock_r: float = 1.00
    trailing_profit_lock_pct: float = 0.50

    @property
    def slot_capital(self) -> float:
        return self.portfolio_capital / self.max_concurrent_positions


@dataclass(slots=True)
class Position:
    symbol: str
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: float
    risk_ticks: float
    shares: int
    peak_r: float = 0.0


@dataclass(slots=True)
class ArmedState:
    armed_time: pd.Timestamp
    swing_low: float


# =====================================================================
# 2. STATUTORY COST ENGINE
# =====================================================================

class ZerodhaFeeCalculator:
    """Calculates all round-trip statutory costs for NSE intraday equity."""
    
    @staticmethod
    def calculate_round_trip(entry_val: float, exit_val: float) -> float:
        turnover = entry_val + exit_val
        brokerage = min(40.0, 0.0003 * turnover)    # ₹20 flat per order leg cap
        stt = 0.00025 * exit_val                     # 0.025% on sell side
        exchange_txn = 0.0000325 * turnover          # NSE turnover charge
        sebi = 0.000001 * turnover                   # SEBI turnover charge
        stamp_duty = 0.00003 * entry_val             # 0.003% on buy side
        gst = 0.18 * (brokerage + exchange_txn + sebi)
        return brokerage + stt + exchange_txn + sebi + stamp_duty + gst


# =====================================================================
# 3. FEATURE PIPELINE (WELFORD RUNNING STATS)
# =====================================================================

class WelfordFeaturePipeline:
    """Computes technical indicators and streaming Welford intraday VWAP."""
    
    @staticmethod
    def resample_to_15m(raw_df: pd.DataFrame) -> pd.DataFrame:
        raw_df.columns = [c.lower() for c in raw_df.columns]
        time_col = [c for c in raw_df.columns if c in ['date', 'datetime', 'time', 'timestamp']][0]
        raw_df['dt'] = pd.to_datetime(raw_df[time_col]).dt.tz_localize(None)
        raw_df = raw_df.sort_values('dt').set_index('dt')
        
        ohlc = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
        df_15 = raw_df.resample('15min').agg(ohlc).dropna().reset_index()
        df_15['date_only'] = df_15['dt'].dt.date
        df_15['time_only'] = df_15['dt'].dt.time
        return df_15

    @classmethod
    def compute_features(cls, df: pd.DataFrame) -> pd.DataFrame:
        # RSI 14
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0.0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-5)
        df['rsi_14'] = 100 - (100 / (1 + rs))
        
        # ATR & Volatility Expansion Ratio
        tr = np.maximum(
            df['high'] - df['low'],
            np.maximum(
                abs(df['high'] - df['close'].shift(1)),
                abs(df['low'] - df['close'].shift(1))
            )
        )
        df['atr'] = tr.rolling(14).mean()
        df['atr_baseline'] = df['atr'].rolling(40).mean().replace(0, 1e-5)
        df['vol_ratio'] = (df['atr'] / df['atr_baseline']).replace(0, 1.0)
        
        # Weighted Welford VWAP
        df['typ_price'] = (df['high'] + df['low'] + df['close']) / 3.0
        vwap_arr = np.zeros(len(df))
        std_arr = np.zeros(len(df))
        
        for _, idxs in df.groupby('date_only').groups.items():
            w_sum = 0.0
            mean = 0.0
            m2 = 0.0
            for i in idxs:
                p = df.at[i, 'typ_price']
                w = max(1.0, df.at[i, 'volume'])
                w_sum_old = w_sum
                w_sum += w
                delta_p = p - mean
                r = delta_p * w / w_sum
                mean += r
                m2 += w_sum_old * delta_p * r
                vwap_arr[i] = mean
                std_arr[i] = np.sqrt(m2 / w_sum) if w_sum > 0 and m2 > 0 else 1.0
                
        df['vwap'] = vwap_arr
        df['vwap_std'] = np.where(std_arr < 1e-4, 1.0, std_arr)
        df['vwap_zscore'] = (df['close'] - df['vwap']) / df['vwap_std']
        return df.dropna().reset_index(drop=True)


# =====================================================================
# 4. TIME-DECAY GOVERNOR MODULE
# =====================================================================

class TimeDecayGovernor:
    """Manages adaptive targets and linearly decays thresholds into the close."""
    
    def __init__(self, config: EngineConfig):
        self.cfg = config
        self.cutoff_dt = pd.to_datetime(config.session_close_time).time()
        self.decay_dt = pd.to_datetime(config.decay_start_time).time()

    def get_decay_factor(self, current_time) -> float:
        curr_mins = current_time.hour * 60 + current_time.minute
        cutoff_mins = self.cutoff_dt.hour * 60 + self.cutoff_dt.minute
        start_decay = self.decay_dt.hour * 60 + self.decay_dt.minute
        
        if curr_mins < start_decay:
            return 1.0
        remaining = max(0, cutoff_mins - curr_mins)
        total_window = cutoff_mins - start_decay
        return float(np.clip(remaining / total_window, self.cfg.min_decay_floor, 1.0))

    def compute_targets(self, vol_ratio: float, current_time) -> Tuple[float, float]:
        decay = self.get_decay_factor(current_time)
        base_r = self.cfg.base_r_target * np.clip(vol_ratio, 0.90, 1.40)
        base_z = self.cfg.base_z_target
        
        decayed_r = max(0.50, base_r * decay)
        decayed_z = -0.05 if decay < 0.50 else base_z * decay
        return float(decayed_r), float(decayed_z)


# =====================================================================
# 5. CORE EXECUTION ENGINE
# =====================================================================

class ExecutionEngine:
    def compute_daily_gaps(self) -> set:
        gapped_days = set()
        for sym, df in self.symbol_frames.items():
            daily = df.groupby('date_only').agg(
                first_open=('open', 'first'),
                last_close=('close', 'last')
            ).sort_index()
            daily['prev_close'] = daily['last_close'].shift(1)
            daily['gap_pct'] = (daily['first_open'] - daily['prev_close']) / daily['prev_close'] * 100.0
            for d in daily[daily['gap_pct'] <= -1.75].index:
                gapped_days.add((sym, d))
        return gapped_days

    def __init__(self, config: EngineConfig, data_dir: Path):
        self.cfg = config
        self.data_dir = data_dir
        self.governor = TimeDecayGovernor(config)
        self.active_positions: Dict[str, Position] = {}
        self.armed_symbols: Dict[str, ArmedState] = {}
        self.closed_trades: List[Dict] = []
        self.symbol_frames: Dict[str, pd.DataFrame] = {}

    def load_dataset(self, start_date: str, end_date: str):
        print(f"Loading data for universe {self.cfg.symbols} from {start_date} to {end_date}...")
        for sym in self.cfg.symbols:
            matches = list(self.data_dir.glob(f"*_{sym}_*.csv"))
            if not matches:
                continue
            fpath = matches[0]
            try:
                raw = pd.read_csv(fpath)
                t_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
                raw['dt'] = pd.to_datetime(raw[t_col]).dt.tz_localize(None)
                m = raw[(raw['dt'] >= start_date) & (raw['dt'] <= end_date)].copy()
                if len(m) > 200:
                    df_15 = WelfordFeaturePipeline.resample_to_15m(m)
                    if len(df_15) > 30:
                        self.symbol_frames[sym] = WelfordFeaturePipeline.compute_features(df_15).set_index('dt')
            except Exception as e:
                print(f"  [WARN] Failed to process {sym}: {e}", file=sys.stderr)
                continue

    def run_replay(self) -> pd.DataFrame:
        if not self.symbol_frames:
            raise RuntimeError("No precomputed symbol frames found. Check data directory and date bounds.")
            
        gapped_days = self.compute_daily_gaps()
        all_timestamps = sorted(list(set.union(*[set(df.index) for df in self.symbol_frames.values()])))
        print(f"Executing replay over {len(self.symbol_frames)} symbols across {len(all_timestamps):,} bars...")
        
        session_cutoff = pd.to_datetime(self.cfg.session_close_time).time()
        entry_cutoff = pd.to_datetime(self.cfg.entry_cutoff_time).time()

        for t in all_timestamps:
            symbols_to_close = []

            # -------------------------------------------------------------
            # PHASE 1: EVALUATE ACTIVE POSITIONS
            # -------------------------------------------------------------
            for sym, pos in self.active_positions.items():
                if t not in self.symbol_frames[sym].index:
                    continue
                bar = self.symbol_frames[sym].loc[t]
                bar_open, bar_high, bar_low, bar_close = bar['open'], bar['high'], bar['low'], bar['close']
                bar_time = bar['time_only']
                risk_ticks = pos.risk_ticks

                # A. 15:15 IST Mandatory Square-off
                if bar_time >= session_cutoff:
                    symbols_to_close.append((sym, bar_close, "SESSION_1515_SQUAREOFF"))
                    continue

                # B. Worst-Case Stop-Loss Breach (Checked at bar Low)
                if bar_low <= pos.stop_price:
                    fill = min(bar_open, pos.stop_price) - (0.05 * risk_ticks)
                    symbols_to_close.append((sym, fill, "STOP_TRIGGERED"))
                    continue

                curr_peak_r = (bar_high - pos.entry_price) / risk_ticks
                curr_r = (bar_close - pos.entry_price) / risk_ticks
                pos.peak_r = max(pos.peak_r, curr_peak_r)

                # C. Dynamic Target Scheduling
                r_target, z_target = self.governor.compute_targets(bar['vol_ratio'], bar_time)

                if curr_peak_r >= r_target:
                    fill = pos.entry_price + (r_target * risk_ticks)
                    symbols_to_close.append((sym, fill, "DYNAMIC_R_TARGET"))
                    continue

                # Enforce minimal viable harvest to clear round-trip friction
                if bar['vwap_zscore'] >= z_target and curr_r >= self.cfg.min_harvest_r:
                    symbols_to_close.append((sym, bar_close, "DYNAMIC_Z_TARGET"))
                    continue

                # D. Trailing Profit Lock
                if pos.peak_r >= self.cfg.trailing_profit_lock_r:
                    new_stop = pos.entry_price + (pos.peak_r * self.cfg.trailing_profit_lock_pct * risk_ticks)
                    pos.stop_price = max(pos.stop_price, new_stop)

            # Settle Closed Positions
            for sym, fill_p, reason in symbols_to_close:
                p = self.active_positions.pop(sym)
                gross_pnl = (fill_p - p.entry_price) * p.shares
                friction = ZerodhaFeeCalculator.calculate_round_trip(
                    p.entry_price * p.shares, fill_p * p.shares
                )
                net_pnl = gross_pnl - friction
                risk_inr = p.risk_ticks * p.shares

                self.closed_trades.append({
                    'symbol': sym,
                    'entry_time': p.entry_time,
                    'exit_time': t,
                    'entry_price': p.entry_price,
                    'exit_price': fill_p,
                    'shares': p.shares,
                    'risk_inr': risk_inr,
                    'gross_pnl': gross_pnl,
                    'friction': friction,
                    'net_pnl': net_pnl,
                    'net_r': net_pnl / max(1.0, risk_inr),
                    'exit_reason': reason
                })

            # -------------------------------------------------------------
            # PHASE 2: EVALUATE ENTRIES (ANSI 25 SYNCHROCHECK)
            # -------------------------------------------------------------
            available_slots = self.cfg.max_concurrent_positions - len(self.active_positions)
            if available_slots <= 0:
                continue

            active_sectors = [self.cfg.sector_map.get(s, "OTHER") for s in self.active_positions.keys()]

            for sym, df in self.symbol_frames.items():
                if sym in self.active_positions or t not in df.index:
                    continue
                current_date = df.loc[t, 'date_only']
                if (sym, current_date) in gapped_days:
                    continue

                # Sector De-clustering Interlock: Max 1 position per industrial sector
                sym_sector = self.cfg.sector_map.get(sym, "OTHER")
                if active_sectors.count(sym_sector) >= 1:
                    continue

                loc_idx = df.index.get_loc(t)
                if loc_idx < 2 or loc_idx + 1 >= len(df):
                    continue

                curr_bar = df.iloc[loc_idx]
                prev_bar = df.iloc[loc_idx - 1]

                # Hard Time Permissive: Inhibit late entries
                if curr_bar['time_only'] >= entry_cutoff:
                    if sym in self.armed_symbols:
                        del self.armed_symbols[sym]
                    continue

                # Condition 1: Arming state on extreme divergence
                if curr_bar['vwap_zscore'] < self.cfg.z_arm_threshold and curr_bar['rsi_14'] < self.cfg.rsi_arm_threshold:
                    self.armed_symbols[sym] = ArmedState(
                        armed_time=t,
                        swing_low=min(curr_bar['low'], prev_bar['low'])
                    )

                # Condition 2: ANSI 25 Synchrocheck (Reversal confirmation candle)
                if sym in self.armed_symbols:
                    # Update trailing lowest support
                    self.armed_symbols[sym].swing_low = min(self.armed_symbols[sym].swing_low, curr_bar['low'])

                    # Synchrocheck confirmation: Higher Close than previous High & Bullish Body
                    if curr_bar['close'] > prev_bar['high'] and curr_bar['close'] > curr_bar['open']:
                        next_bar = df.iloc[loc_idx + 1]
                        entry_p = next_bar['open']
                        swing_low = self.armed_symbols[sym].swing_low
                        risk_t = max(entry_p - swing_low, self.cfg.stop_atr_multiplier * curr_bar['atr'])

                        # Price-Proportional Share Sizing
                        shares = int(self.cfg.slot_capital / entry_p)

                        if shares >= 1:
                            self.active_positions[sym] = Position(
                                symbol=sym,
                                entry_time=next_bar.name,
                                entry_price=entry_p,
                                stop_price=entry_p - risk_t,
                                risk_ticks=risk_t,
                                shares=shares,
                                peak_r=0.0
                            )
                            del self.armed_symbols[sym]
                            active_sectors.append(sym_sector)
                            available_slots -= 1
                            if available_slots <= 0:
                                break

        return pd.DataFrame(self.closed_trades)


# =====================================================================
# 6. REPORT GENERATOR & CLI
# =====================================================================

def print_performance_audit(tdf: pd.DataFrame):
    print("\n" + "=" * 75)
    print("ALPHA UNIVERSE ENGINE: AUDIT REPORT")
    print("=" * 75)
    total = len(tdf)
    print(f"Total Completed Trades        : {total}")
    if total == 0:
        print("No completed trades recorded.")
        print("=" * 75)
        return

    wr = (tdf['net_pnl'] > 0).mean() * 100
    gross_pnl = tdf['gross_pnl'].sum()
    friction = tdf['friction'].sum()
    net_pnl = tdf['net_pnl'].sum()
    net_exp = tdf['net_r'].mean()
    avg_fee = tdf['friction'].mean()

    print(f"Win Rate                      : {wr:.2f}%")
    print(f"Total Gross P&L               : ₹{gross_pnl:+,.2f}")
    print(f"Total Statutory Fees          : ₹{friction:,.2f}")
    print(f"Total Net Portfolio P&L       : ₹{net_pnl:+,.2f}")
    print(f"Average Net Expectancy        : {net_exp:+.3f}R / trade")
    print(f"Average Fee per Trade         : ₹{avg_fee:.2f}")
    print("-" * 75)
    print("Exit Distribution:")
    for r, cnt in tdf['exit_reason'].value_counts().items():
        print(f"  • {r:<30}: {cnt:<3} trades ({cnt/total*100:.1f}%)")
    print("-" * 75)
    print("Performance by Asset:")
    for sym, g in tdf.groupby('symbol'):
        sym_wr = (g['net_pnl'] > 0).mean() * 100
        print(f"  • {sym:<10}: {len(g):<2} trades | WR: {sym_wr:5.1f}% | Avg Risk: ₹{g['risk_inr'].mean():6.1f} | Net: ₹{g['net_pnl'].sum():+10,.2f}")
    print("=" * 75)


def main():
    parser = argparse.ArgumentParser(description="Run the Alpha Universe Intraday Engine")
    parser.add_argument("--start", default="2023-08-01", help="Replay start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2023-08-31", help="Replay end date (YYYY-MM-DD)")
    parser.add_argument("--output", default="alpha_production_ledger.parquet", help="Output ledger path")
    args = parser.parse_args()

    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    if not data_dir.exists():
        print(f"[ERROR] Data directory does not exist: {data_dir}", file=sys.stderr)
        sys.exit(1)

    config = EngineConfig()
    engine = ExecutionEngine(config, data_dir)
    engine.load_dataset(args.start, args.end)
    ledger_df = engine.run_replay()

    print_performance_audit(ledger_df)
    if not ledger_df.empty:
        ledger_df.to_parquet(args.output)
        print(f"Ledger saved to {args.output}")

if __name__ == "__main__":
    main()
