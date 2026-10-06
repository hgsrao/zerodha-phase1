"""
REVISION 04 (Proper): Indexed Replay Engine

Orchestrates the full pipeline:
1. Load dataset (48 symbols, 1-month period + 60-bar warmup)
2. Calibrate all Revision 2 boxes
3. Replay bar-by-bar
4. Execute decisions through order broker and ledger
5. Collect events into sealed RunResult
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from revision4.contracts import (
    Bar, ForecastSignal, IDDecision, TradePlan, SizedProposal,
    OrderIntent, FillEvent, ExitEvent, EffectiveConfig, RunResult,
    DatasetSeal, OrderState, ExitReason
)
from revision4.dataset_seal import DatasetValidator, WarmupLoader
from revision4.portfolio import PortfolioLedger
from revision4.paper_broker import PaperBroker
from revision4.pipeline import PipelineAdapter


class ReplayEngine:
    """
    Deterministic, causal replay of trading strategy on sealed dataset.
    """

    def __init__(
        self,
        config: EffectiveConfig,
        manifest_path: str,
        data_dir: str,
    ):
        self.config = config
        self.manifest_path = manifest_path
        self.data_dir = data_dir

        # Load and seal dataset
        validator = DatasetValidator(manifest_path)
        self.seal = validator.load_manifest(data_dir)

        # Initialize ledger and broker
        self.ledger = PortfolioLedger(starting_cash=100_000.0)
        self.broker = PaperBroker()

        # Initialize pipeline adapter with Revision 2 boxes
        self.adapter = PipelineAdapter(config)

        # Event collections
        self.signals: List[ForecastSignal] = []
        self.decisions: List[IDDecision] = []
        self.orders: List[OrderIntent] = []
        self.fills: List[FillEvent] = []
        self.exits: List[ExitEvent] = []

        # Tracking
        self.bar_data: Dict[str, List[pd.DataFrame]] = {}  # {symbol: list of bars}
        self.current_bar_index = 0
        self.start_timestamp = None
        self.end_timestamp = None

    def load_data(self) -> bool:
        """Load all bar data for 48 symbols."""
        print(f"\n[LOAD] Loading {len(self.seal.symbols)} symbols...")

        # Load warmup bars
        warmup_loader = WarmupLoader(self, self.seal)
        warmup_data = warmup_loader.load_warmup_bars()

        # Load live month data
        data_path = Path(self.data_dir)

        for symbol in self.seal.symbols:
            # Try to load CSV for symbol
            csv_path = data_path / f"{symbol}.csv"
            if not csv_path.exists():
                print(f"  Warning: {symbol} CSV not found")
                continue

            try:
                df = pd.read_csv(csv_path)
                df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)

                # Filter to month period
                df = df[
                    (df['timestamp'] >= self.seal.month_start) &
                    (df['timestamp'] <= self.seal.month_end)
                ]

                self.bar_data[symbol] = df.sort_values('timestamp').reset_index(drop=True)

                # Calibrate PA box with warmup data
                if symbol in warmup_data:
                    try:
                        self.adapter.calibrate_pa_box(symbol, warmup_data[symbol])
                    except Exception as e:
                        print(f"  Warning: PA calibration failed for {symbol}: {e}")

            except Exception as e:
                print(f"  Error loading {symbol}: {e}")

        print(f"✓ Loaded {len(self.bar_data)} symbols")
        return len(self.bar_data) >= 2  # At least some data

    def _load_symbol_csv(self, symbol: str) -> pd.DataFrame:
        """Helper for WarmupLoader."""
        csv_path = Path(self.data_dir) / f"{symbol}.csv"
        df = pd.read_csv(csv_path)
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        return df

    def replay_one_symbol(self, symbol: str) -> Dict:
        """
        Replay strategy on one symbol for the month.
        Returns stats for this symbol.
        """
        if symbol not in self.bar_data:
            return {'symbol': symbol, 'status': 'NO_DATA'}

        df = self.bar_data[symbol]
        if len(df) == 0:
            return {'symbol': symbol, 'status': 'EMPTY'}

        print(f"\n[REPLAY] {symbol}: {len(df)} bars, {df['timestamp'].min()} to {df['timestamp'].max()}")

        trades = 0
        winning = 0
        losing = 0
        total_pnl = 0.0

        # Process each bar
        for idx, row in df.iterrows():
            bar_index = self.current_bar_index + idx

            # Create Bar contract
            bar = Bar(
                timestamp=row['timestamp'].isoformat(),
                symbol=symbol,
                open=float(row['open']),
                high=float(row['high']),
                low=float(row['low']),
                close=float(row['close']),
                volume=int(row['volume']),
            )

            # Add to adapter history
            self.adapter.add_bar_to_history(symbol, row.to_dict())

            # PA Box: generate forecast
            try:
                forecast = self.adapter.generate_forecast(
                    bar.timestamp,
                    bar_index,
                    symbol,
                )
                self.signals.append(forecast)
            except Exception as e:
                print(f"  PA error at {bar.timestamp}: {e}")
                continue

            if not forecast.is_valid():
                continue

            # ID Box: entry decision
            hour = pd.Timestamp(bar.timestamp).hour
            minute = pd.Timestamp(bar.timestamp).minute
            try:
                decision = self.adapter.make_id_decision(
                    forecast=forecast,
                    hour=hour,
                    minute=minute,
                    grid_sync=True,
                )
                self.decisions.append(decision)
            except Exception as e:
                print(f"  ID error: {e}")
                continue

            if not decision.entry_valid:
                continue

            # Risk Manager: trade plan
            try:
                plan = self.adapter.make_trade_plan(
                    decision=decision,
                    entry_price=bar.close,
                    atr=0.01 * bar.close,  # Simplified ATR
                )
            except Exception as e:
                print(f"  Plan error: {e}")
                continue

            # MPC Box: size proposal
            try:
                proposal = self.adapter.make_sized_proposal(
                    plan=plan,
                    daily_pnl=self.ledger.daily_pnl,
                )
            except Exception as e:
                print(f"  MPC error: {e}")
                continue

            if proposal.final_quantity <= 0:
                continue

            # Create order intent
            order_id = f"order_{symbol}_{bar_index}"
            order = OrderIntent(
                order_id=order_id,
                timestamp_created=bar.timestamp,
                bar_index_created=bar_index,
                symbol=symbol,
                direction=proposal.plan.direction,
                quantity=proposal.final_quantity,
                stop_price=proposal.plan.stop_price,
                target_price=proposal.plan.target_price,
                proposal=proposal,
            )
            self.orders.append(order)

            # Try to create order in ledger
            ok, msg = self.ledger.create_order(order_id, order)
            if not ok:
                print(f"  Order rejected: {msg}")
                continue

            # Submit to broker for next-bar fill
            self.broker.submit_order(order)

        self.current_bar_index += len(df)
        return {
            'symbol': symbol,
            'status': 'OK',
            'bars': len(df),
            'orders': sum(1 for o in self.orders if o.symbol == symbol),
        }

    def replay(self) -> RunResult:
        """
        Orchestrate full replay: warmup → calibrate → month replay → close positions.
        """
        if not self.load_data():
            raise RuntimeError("Failed to load sufficient data")

        print("\n[REPLAY] Starting month replay...")

        # Replay each symbol's bars
        for symbol in self.seal.symbols:
            result = self.replay_one_symbol(symbol)
            print(f"  {result}")

        # EOD: flatten all positions
        print("\n[EOD] Flattening all positions...")
        current_price = {}  # Last close price for each symbol
        for symbol in self.seal.symbols:
            if symbol in self.bar_data:
                df = self.bar_data[symbol]
                if len(df) > 0:
                    current_price[symbol] = float(df.iloc[-1]['close'])

        for symbol, position in list(self.ledger.positions.items()):
            exit_price = current_price.get(symbol, position.entry_price)
            exit_event = ExitEvent(
                exit_id=f"eod_{symbol}",
                symbol=symbol,
                timestamp_exit=self.end_timestamp or "2024-08-31T15:29:59Z",
                bar_index_exit=self.current_bar_index,
                entry_price=position.entry_price,
                exit_price=exit_price,
                quantity=position.quantity,
                direction=position.direction,
                bars_held=0,
                exit_reason=ExitReason.EOD_FLATTENING,
                pnl_realized=(exit_price - position.entry_price) * position.quantity - position.cost_paid,
                pnl_pct=((exit_price - position.entry_price) / position.entry_price) if position.entry_price else 0,
            )
            ok, msg = self.ledger.close_position(exit_event)
            if ok:
                self.exits.append(exit_event)

        # Build result
        result = RunResult(
            seal=self.seal,
            config=self.config,
            start_timestamp=self.start_timestamp or self.seal.month_start,
            end_timestamp=self.end_timestamp or self.seal.month_end,
            bars_processed=self.current_bar_index,
            signals=self.signals,
            decisions=self.decisions,
            orders=self.orders,
            fills=self.fills,
            exits=self.exits,
            starting_equity=100_000.0,
            ending_equity=self.ledger.cash + sum(
                p.marked_value(current_price.get(p.symbol, p.entry_price))
                for p in self.ledger.positions.values()
            ),
            final_snapshot=self.ledger.snapshot(),
            total_trades=len(self.fills),
            winning_trades=sum(1 for e in self.exits if e.pnl_realized > 0),
            losing_trades=sum(1 for e in self.exits if e.pnl_realized < 0),
            win_rate=len([e for e in self.exits if e.pnl_realized > 0]) / max(len(self.exits), 1),
            total_pnl=self.ledger.realized_pnl,
            realized_pnl=self.ledger.realized_pnl,
            total_costs=self.ledger.total_costs,
            max_drawdown=0.0,  # TODO: compute from equity curve
            sharpe_ratio=0.0,  # TODO: compute from returns
            profit_factor=1.0,  # TODO: compute from win/loss magnitudes
        )

        return result
