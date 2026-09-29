#!/usr/bin/env python3
"""Offline, process-local extension of scripts/r5_governor_trace.py.
Usage: python r5_audit_diagnostics.py --repo /path/to/repo [trace CLI options]
Adds pid_gate.jsonl and fsrn_components.jsonl; never patches engine files.
"""
import argparse
import importlib.util
import inspect
import json
import sys
from collections import Counter
from pathlib import Path


def gate_flags(row, inputs):
    a = row['error'] >= row['exit_envelope']
    b = row['control_u'] >= row['exit_control']
    # These are precisely the branches preceding the path-error branch.
    eligible = (inputs['elapsed_bars'] >= inputs['min_hold_bars']
                and row['reason'] in ('GOVERNOR_TRACKING', 'GOVERNOR_PATH_ERROR'))
    return dict(A=a, B=b, eligible=eligible,
                pid_veto=eligible and a and not b,
                envelope_only_exit=eligible and a,
                pid_path_exit=eligible and a and b)


def install(trace):
    Base = trace.Tracer

    class AuditTracer(Base):
        def _wrap_governor(self, bay_id, gov):
            signature = inspect.signature(gov.evaluate_position_control)
            super()._wrap_governor(bay_id, gov)
            original = gov.evaluate_position_control

            def evaluate(**kwargs):
                bound = signature.bind(**kwargs)
                bound.apply_defaults()
                out = original(**kwargs)
                row = dict(self.rows['inner'][-1])
                row.update(gate_flags(row, bound.arguments))
                row['inputs'] = dict(bound.arguments)
                row['gains'] = dict(kp=gov.runtime_kp, ki=gov.runtime_ki,
                                    kd=gov.runtime_kd, clamp=gov.runtime_integral_clamp, target_r=gov.runtime_target_r)
                # Assert agreement only where earlier branches cannot preempt the test.
                if row['eligible']:
                    assert (out['reason'] == 'GOVERNOR_PATH_ERROR') == row['pid_path_exit']
                self.rows['pid_gate'].append(row)
                return out
            gov.evaluate_position_control = evaluate

        def _wrap_orchestrator(self):
            super()._wrap_orchestrator()
            orch = self.orch
            observe = orch._observe_conviction
            conviction = orch._governor_conviction
            step = orch._governor_position_step
            observations, context = {}, {}
            step_signature = inspect.signature(step)

            def observe_inputs(symbol, signal, composite_result):
                window = orch.governor_config.z_window_bars
                prior = {k: list(v)[-window:] for k, v in
                         orch._conviction_history.get(symbol, {}).items()}
                result = observe(symbol, signal, composite_result)
                observations[symbol] = dict(prior=prior, window=window)
                return result

            def position_step(*args, **kwargs):
                bound = step_signature.bind(*args, **kwargs)
                trade = bound.arguments['trade']
                context.update(trade_id=trade.get('trade_id'),
                               timestamp=str(bound.arguments['timestamp']))
                try:
                    return step(*args, **kwargs)
                finally:
                    context.clear()

            def conviction_inputs(symbol, side, signal, composite_result, pa_key):
                ranks = dict(orch._conviction_rank.get(symbol) or {})
                pa_dir = int(signal.direction)
                studies_dir = (composite_result or {}).get('direction')
                expected = 1 if side == 'BUY' else -1
                row = dict(timestamp=context.get('timestamp', self.clock['ts']),
                           trade_id=context.get('trade_id'), symbol=symbol, side=side,
                           phase='position' if pa_key == 'pa_exit' else 'entry', pa_key=pa_key,
                           pa_confidence=float(signal.confidence),
                           pa_exit_confidence=float(signal.exit_confidence),
                           pa_direction=pa_dir, studies_direction=studies_dir,
                           studies_confidence=float(composite_result['confidence']),
                           ranks=ranks, pa_opposed=pa_dir == -expected,
                           studies_opposed=studies_dir is not None and int(studies_dir) == -expected,
                           **observations.get(symbol, {}))
                try:
                    result = conviction(symbol, side, signal, composite_result, pa_key)
                except Exception as exc:
                    row['error'] = str(exc)
                    self.rows['fsrn_components'].append(row)
                    raise
                row['fsrn'] = result
                row['unmasked_fsrn'] = min(ranks[pa_key], ranks['studies'])
                row['direction_mask_changed_value'] = result != row['unmasked_fsrn']
                self.rows['fsrn_components'].append(row)
                return result

            orch._observe_conviction = observe_inputs
            orch._governor_position_step = position_step
            orch._governor_conviction = conviction_inputs

    trace.Tracer = AuditTracer
    original_summary = trace._summarize

    def summarize(tracer, *args):
        result = original_summary(tracer, *args)
        rows = tracer.rows['pid_gate']
        eligible = [r for r in rows if r['eligible']]
        counts = lambda rs: dict(Counter(f"A={int(r['A'])},B={int(r['B'])}" for r in rs))
        # A veto matters to the final governor only if Mark V also returns HOLD.
        events = {(str(e.get('trade_id')), e['timestamp']): e
                  for e in tracer.rows['events'] if e['event_type'] == 'GOVERNOR_POSITION_DECISION'}
        vetoes = [r for r in rows if r['pid_veto']]
        matches = [events.get((r['position_id'], r['ts'])) for r in vetoes]
        result['pid_gate_audit'] = dict(all_bars=len(rows), eligible_bars=len(eligible),
            all_quadrants=counts(rows), eligible_quadrants=counts(eligible),
            pid_vetoes=len(vetoes), veto_fraction_eligible=len(vetoes)/len(eligible) if eligible else None,
            final_hold_vetoes=sum(e is not None and e['action'] == 'HOLD' for e in matches),
            unmatched_veto_events=sum(e is None for e in matches))
        return result
    trace._summarize = summarize
    return AuditTracer


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--repo', type=Path, required=True)
    args, rest = parser.parse_known_args()
    repo = args.repo.resolve()
    sys.path.insert(0, str(repo))
    spec = importlib.util.spec_from_file_location('r5_trace', repo/'scripts/r5_governor_trace.py')
    trace = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trace)
    install(trace)
    return trace.main(rest)

if __name__ == '__main__':
    raise SystemExit(main())
