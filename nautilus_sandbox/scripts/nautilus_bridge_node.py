import os
from pathlib import Path
import pandas as pd
from nautilus_trader.model import (
    Bar, 
    BarType, 
    BarSpecification, 
    Price, 
    Quantity, 
    InstrumentId, 
    BarAggregation, 
    PriceType
)

def build_sandbox_nautilus_stream(symbol: str = "RELIANCE"):
    """
    Ingests sandboxed parquet files and simulates an event-sourced stream
    compatible with NautilusTrader v2.0 event bus architecture using BarType.from_str().
    """
    parquet_file = Path(f"/home/shrinivas/ECS_Project_external_engine/mean_reversion/features_2026/train/{symbol}_mr_2026.parquet")
    if not parquet_file.exists():
        print(f"⚠️ Parquet source not found for {symbol}. Using fallback path.")
        return []

    df = pd.read_parquet(parquet_file).head(1000)
    instrument_id = InstrumentId.from_str(f"{symbol}.NSE")
    
    # Use standard v2.0 string representation for bar type creation
    bar_type_str = f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"
    bar_type = BarType.from_str(bar_type_str)
    
    event_stream = []
    for timestamp, row in df.iterrows():
        ts_ns = pd.Timestamp(timestamp).value
        bar = Bar(
            bar_type=bar_type,
            open=Price.from_str(str(row['open'])),
            high=Price.from_str(str(row['high'])),
            low=Price.from_str(str(row['low'])),
            close=Price.from_str(str(row['close'])),
            volume=Quantity.from_int(int(row['volume'])),
            ts_event=ts_ns,
            ts_init=ts_ns
        )
        event_stream.append(bar)
        
    print(f"✅ Generated {len(event_stream)} event-sourced Bar events for {symbol} inside the sandbox.")
    return event_stream

if __name__ == "__main__":
    build_sandbox_nautilus_stream("RELIANCE")
