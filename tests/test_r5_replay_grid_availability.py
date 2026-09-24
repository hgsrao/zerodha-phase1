"""Real replay input contract: source candles cannot influence an unfinished interval."""
import hashlib
import json
import pandas as pd
import pytest
from scripts.run_r5_paper_replay import load_verified_grid_feeds
from revision2_external.grid_context import SealedGridContextProvider


def test_source_candle_waits_for_full_interval_and_checksum_is_enforced(tmp_path):
    source=tmp_path/'source.csv'
    source.write_text('date,close\n2024-01-01 09:15:00+05:30,15\n')
    record={'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'timestamp_column':'date','name':'fixture'}
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'files':[record]}))
    frame=load_verified_grid_feeds(manifest)['fixture']
    assert frame['timestamp'].iloc[0]==pd.Timestamp('2024-01-01 09:30:00+05:30')
    provider=SealedGridContextProvider(frame,frame,minimum_aligned_bars=1)
    assert not provider.causal_context('2024-01-01 09:29:00+05:30').available
    assert not provider.causal_context('2024-01-01 09:30:00+05:30').available
    assert provider.causal_context('2024-01-01 09:31:00+05:30').available
    source.write_text(source.read_text().replace(',15',',25'))
    with pytest.raises(ValueError,match='checksum mismatch'):
        load_verified_grid_feeds(manifest)
