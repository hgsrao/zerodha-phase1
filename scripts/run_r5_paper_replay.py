"""Run the verified frozen data through R5 paper admission; no broker network access."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_verified_grid_feeds(manifest_path):
    """Treat source stamps conservatively as bar starts; never consume an unfinished candle."""
    import pandas as pd
    feeds = {}
    for record in json.loads(Path(manifest_path).read_text())['files']:
        path = Path(record['path'])
        with path.open('rb') as f:
            checksum = hashlib.file_digest(f, 'sha256').hexdigest()
        if checksum != record['sha256']:
            raise ValueError(f'Grid checksum mismatch: {path}')
        frame = pd.read_csv(path).rename(columns={record['timestamp_column']: 'timestamp'})
        frame['timestamp'] = pd.to_datetime(frame['timestamp'], utc=True) + pd.Timedelta(minutes=15)
        feeds[record['name']] = frame
    return feeds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbols', default='INFY,TCS')
    parser.add_argument('--bars', type=int, default=800)
    parser.add_argument('--mode', choices=['PAPER_APPLY','SHADOW'], default='PAPER_APPLY')
    parser.add_argument('--compare-shadow', action='store_true')
    parser.add_argument('--journal', type=Path, help='Durable paper SQLite journal; rerun identical command to recover')
    parser.add_argument('--plant-commands', type=Path, help='Sealed JSON list of tick-indexed trip/reset commands')
    parser.add_argument('--crash-after-open-checkpoint', action='store_true', help='Harness: terminate worker after committing an open position')
    parser.add_argument('--grid-manifest', type=Path, default=ROOT/'local_workspace/records/grid-manifest-local.json')
    parser.add_argument('--report-dir', type=Path, default=ROOT/'local_workspace/validation/paper_replay')
    args = parser.parse_args()
    if args.journal and (args.compare_shadow or args.mode != 'PAPER_APPLY'):
        parser.error('--journal requires PAPER_APPLY without --compare-shadow')
    if (args.plant_commands or args.crash_after_open_checkpoint) and not args.journal:
        parser.error('Plant commands/crash injection require --journal')
    if args.bars <= 60:parser.error('--bars must exceed the 60-bar warmup')
    import pandas as pd
    from market_data_loader import MarketDataLoader
    from revision2.dataset_manifest import DatasetManifest, verify_manifest
    from revision2_external.grid_context import SealedGridContextProvider
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    from revision5.ccpp_unified_plant import CentralPlantMasterDCS

    manifest = DatasetManifest.load(str(ROOT/'revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json'))
    verification = verify_manifest(manifest)
    if not verification.valid:raise ValueError(verification.message)
    feeds = load_verified_grid_feeds(args.grid_manifest)
    provider = SealedGridContextProvider(feeds['NIFTY_50_15MIN'], feeds['INDIA_VIX_15MIN'])
    # Select the latest common real date; never extend a feed or synthesize missing bars.
    end = min(frame['timestamp'].max() for frame in feeds.values()).tz_convert('Asia/Kolkata').date()
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    known = {f.symbol for f in manifest.files}
    if not symbols or len(set(symbols)) != len(symbols) or not set(symbols) <= known:
        raise ValueError('Symbols must be distinct members of the verified frozen universe')
    data = {}
    for symbol in symbols:
        frame = loader._load_symbol_csv(symbol)
        times = pd.to_datetime(frame['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
        frame = frame[times.dt.date <= end].tail(args.bars).reset_index(drop=True)
        if len(frame) <= 60:raise ValueError(f'Insufficient real data for {symbol}')
        data[symbol] = frame
    args.report_dir.mkdir(parents=True,exist_ok=True)
    modes = ['SHADOW','PAPER_APPLY'] if args.compare_shadow else [args.mode]
    summary = {}
    for mode in modes:
        plant = CentralPlantMasterDCS(total_capital=1_000_000,db_path=':memory:')
        journal = None
        if args.journal:
            from revision5.paper_state_journal import PaperStateJournal
            def after_commit(journal, engine):
                if args.crash_after_open_checkpoint and engine.open_trades:
                    import os
                    os._exit(73)
            journal = PaperStateJournal(args.journal,
                commands=json.loads(args.plant_commands.read_text()) if args.plant_commands else (),
                after_commit=after_commit)
        orch = Revision2ExternalEngineOrchestrator(symbols,grid_context_provider=provider,
            real_plant_dcs=plant,plant_control_mode=mode,paper_journal=journal,closed_loop_mode='active_paper',telemetry_mode='compact')
        report = orch.run(data,warmup=60)
        report['replay_inputs'] = {'stock_manifest_hash':manifest.manifest_hash,
            'grid_manifest':str(args.grid_manifest.resolve()),'grid_availability_delay_minutes':15,
            'latest_common_date':str(end),
            'bars':{s:len(f) for s,f in data.items()},'restart_semantics':'verified prefix reconstruction' if journal else 'fresh in-memory replay only',
            'reconciled_checkpoints':journal.reconciled_checkpoints if journal else 0,
            'durable_checkpoints':journal.cursor if journal else 0}
        if journal:
            journal.close()
        (args.report_dir/f'{mode.lower()}.json').write_text(json.dumps(report,indent=2,default=str))
        summary[mode] = {k:report[k] for k in ['fills','completed_trades','net_pnl','safety_violations','plant_control']}
    (args.report_dir/'summary.json').write_text(json.dumps(summary,indent=2,default=str))
    print(json.dumps(summary,indent=2,default=str))

if __name__ == '__main__':main()
