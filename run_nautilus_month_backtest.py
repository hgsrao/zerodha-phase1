import numpy as np
import pandas as pd
from pathlib import Path
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model.currencies import INR
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue, InstrumentId, Symbol
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_revision2_strategy import NautilusRevision2PlantStrategy, Revision2StrategyConfig

def make_inr_equity(sym: str, venue: Venue) -> Equity:
    inst_id = InstrumentId(Symbol(sym), venue)
    return Equity(
        instrument_id=inst_id,
        raw_symbol=Symbol(sym),
        currency=INR,
        price_precision=2,
        price_increment=Price(0.05, 2),
        lot_size=Quantity(1, 0),
        ts_event=0,
        ts_init=0,
    )

def main():
    print("=" * 70)
    print("NAUTILUS TRADER: REVISION 2 COORDINATED PLANT (1-MONTH BACKTEST - INR NATIVE)")
    print("=" * 70)

    engine_config = BacktestEngineConfig(trader_id="DCS-PLANT-01")
    engine = BacktestEngine(config=engine_config)
    venue = Venue("NSE")

    engine.add_venue(
        venue=venue,
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        base_currency=INR,
        starting_balances=[Money(2_500_000, INR)],
    )

    data_path = Path("/home/shrinivas/ECS_Complete/MARKET_DATA_LIBRARY/DATA/15MIN/ZERODHA_BASELINE20")
    symbols = ["TATASTEEL", "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BAJFINANCE", "LT", "MARUTI"]

    loaded_count = 0
    total_bars_loaded = 0

    for sym in symbols:
        matching = list(data_path.glob(f"*{sym}*.csv"))
        if not matching:
            continue

        raw_df = pd.read_csv(matching[0])
        raw_df.columns = [c.lower() for c in raw_df.columns]

        t_col = next((c for c in ['date', 'datetime', 'timestamp', 'time'] if c in raw_df.columns), None)
        if not t_col:
            continue

        df = raw_df.dropna(subset=[t_col, 'open', 'high', 'low', 'close']).copy()
        df['dt'] = pd.to_datetime(df[t_col], utc=True)
        df = df.drop_duplicates(subset=['dt']).sort_values('dt')
        
        df = df.tail(2500).reset_index(drop=True)
        if len(df) < 50:
            continue

        for col in ['open', 'high', 'low', 'close']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['volume'] = pd.to_numeric(df.get('volume', 1000.0), errors='coerce').fillna(1000.0)
        df = df.dropna(subset=['open', 'high', 'low', 'close', 'volume'])

        instrument = make_inr_equity(sym, venue)
        engine.add_instrument(instrument)

        bar_type = BarType.from_str(f"{instrument.id.value}-15-MINUTE-LAST-EXTERNAL")
        
        bar_list = []
        for row in df.itertuples():
            ts_ns = int(row.dt.value)
            o = Price(float(row.open), instrument.price_precision)
            h = Price(float(row.high), instrument.price_precision)
            l = Price(float(row.low), instrument.price_precision)
            c = Price(float(row.close), instrument.price_precision)
            v = Quantity(max(1.0, float(row.volume)), 0)
            
            b = Bar(
                bar_type=bar_type,
                open=o,
                high=h,
                low=l,
                close=c,
                volume=v,
                ts_event=ts_ns,
                ts_init=ts_ns,
            )
            bar_list.append(b)

        if bar_list:
            engine.add_data(bar_list)
            loaded_count += 1
            total_bars_loaded += len(bar_list)
            print(f"  ✓ {sym:<10} : Ingested {len(bar_list):>4} bars in INR ({str(df['dt'].iloc[0])[:10]} to {str(df['dt'].iloc[-1])[:10]})")

    print(f"\nFleet Ingestion Complete: {loaded_count} symbols, {total_bars_loaded:,} total bars.")

    strat_cfg = Revision2StrategyConfig(
        fleet_config_path="results/fleet_config.json",
        base_capital=100_000.0,
    )
    strategy = NautilusRevision2PlantStrategy(config=strat_cfg)
    engine.add_strategy(strategy)

    print("\n[*] Starting Nautilus Backtest Event Engine...")
    engine.run()

    account_report = engine.trader.generate_account_report(venue)
    order_fills = engine.trader.generate_order_fills_report()
    positions_report = engine.trader.generate_positions_report()

    print("\n" + "=" * 70)
    print("ACCOUNT PERFORMANCE REPORT (INR)")
    print("=" * 70)
    print(account_report)
    print("\n" + "=" * 70)
    print("POSITIONS REPORT")
    print("=" * 70)
    print(positions_report)
    positions_report.to_csv("results/nautilus_positions.csv")
    print("\n" + "=" * 70)
    print("ORDER FILLS SUMMARY")
    print("=" * 70)
    print(order_fills)
    engine.dispose()

if __name__ == "__main__":
    main()
