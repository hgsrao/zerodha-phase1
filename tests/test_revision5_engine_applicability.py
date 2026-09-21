"""Engine applicability of registry parameters (three-controller work).

Runtime configurability, optimizer eligibility (``calibratable``) and engine
applicability (``applicable_engines``) are three separate properties.  An
optimizer surface is calibratable AND applicable to the optimizer's engine.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.calibration_supervisor import trading_search_space
from revision2.contracts import EffectiveConfig, MarketSnapshot
from revision2.optimizer import SearchSpace

REG = CanonicalParameterRegistry()
ROOT = Path(__file__).resolve().parent.parent
FIXED_EXTERNAL = {"mpc_shadow_r_gamma", "mpc_slippage_vol_gain", "mpc_base_slippage_fraction",
                  "id_variance_floor", "id_initial_variance_regularizer"}
BB04_PA = sorted(n for n, s in REG.params.items() if "BB04" in s.notes and "ENGINEERING_INITIAL" in s.notes)


def _names(engine):
    return set(trading_search_space(REG, engine=engine).names)


# ------------------------------------------------------- search-space scoping

def test_shared_parameter_is_in_both_search_spaces():
    assert REG.params["entry_confidence_threshold"].applicable_engines == "BOTH"
    assert "entry_confidence_threshold" in _names("IN_HOUSE") and "entry_confidence_threshold" in _names("EXTERNAL")


def test_external_only_parameter_is_only_in_the_external_space():
    for name in ("studies_pid_kp", "cl_hmm_stress_enter", "id_feature_window", "momentum_normalization_divisor"):
        assert REG.params[name].applicable_engines == "EXTERNAL" and REG.params[name].calibratable
        assert name in _names("EXTERNAL") and name not in _names("IN_HOUSE")


def test_in_house_only_parameter_is_only_in_the_in_house_space():
    """No shipped parameter is currently IN_HOUSE-only *and* eligible, so exercise the
    mechanism on a registry instance where one shared parameter is re-scoped."""
    registry = CanonicalParameterRegistry()
    name = "max_hold_bars"
    registry.params[name] = replace(registry.params[name], applicable_engines="IN_HOUSE")
    assert name in registry.calibratable_names("IN_HOUSE") and name not in registry.calibratable_names("EXTERNAL")
    assert name in SearchSpace.from_registry(registry, engine="IN_HOUSE").names
    assert name not in SearchSpace.from_registry(registry, engine="EXTERNAL").names


def test_learning_rate_exploration_factor_is_in_house_fixed_diagnostic():
    """Its only consumer, UnifiedExecutionBox.check_window, multiplies it into a
    diagnostic exploration_bias that every production caller discards."""
    name = "learning_rate_exploration_factor"
    spec = REG.params[name]
    assert spec.applicable_engines == "IN_HOUSE" and not spec.calibratable
    assert name in REG.fixed_target_names()
    for engine in (None, "IN_HOUSE", "EXTERNAL"):
        assert name not in REG.calibratable_names(engine)
    for path in ("revision2/orchestrator.py", "revision2/portfolio_orchestrator.py", "revision4/ten_box_integration.py"):
        source = (ROOT / path).read_text()
        match = re.search(r"in_window,\s*(\w+),\s*\w+\s*=.*check_window", source)
        assert match and match.group(1).startswith("_"), path   # exploration_bias is discarded


def test_fixed_external_parameters_are_in_no_optimizer_surface():
    for name in FIXED_EXTERNAL:
        spec = REG.params[name]
        assert spec.applicable_engines == "EXTERNAL" and not spec.calibratable
        assert name in REG.fixed_target_names()
        for engine in (None, "IN_HOUSE", "EXTERNAL"):
            assert name not in REG.calibratable_names(engine)
        assert not REG.is_calibratable(name, "EXTERNAL")


def test_applicability_never_makes_a_fixed_parameter_eligible():
    fixed = set(REG.fixed_target_names())
    for engine in (None, "IN_HOUSE", "EXTERNAL"):
        assert not fixed & set(REG.calibratable_names(engine))
    for name in fixed:
        assert REG.params[name].calibratable is False


def test_safety_parameters_are_in_no_optimizer_surface():
    safety = set(REG.safety_params)
    for engine in (None, "IN_HOUSE", "EXTERNAL"):
        assert not safety & set(REG.calibratable_names(engine))
    for engine in ("IN_HOUSE", "EXTERNAL"):
        assert not safety & _names(engine)


def test_invalid_engine_fails_explicitly():
    for bad in ("BOTH", "external", "", "OTHER"):
        with pytest.raises(ValueError):
            REG.calibratable_names(bad)
        with pytest.raises(ValueError):
            REG.is_calibratable("entry_confidence_threshold", bad)
        with pytest.raises(ValueError):
            trading_search_space(REG, engine=bad)
        with pytest.raises(ValueError):
            REG.validate_calibration_payload({}, engine=bad)


def test_optimizer_apis_require_an_explicit_engine():
    with pytest.raises(TypeError):
        trading_search_space(REG)
    with pytest.raises(TypeError):
        SearchSpace.from_registry(REG)


def test_no_engine_default_is_the_union_of_engine_surfaces():
    union = set(REG.calibratable_names())
    assert union == set(REG.calibratable_names("IN_HOUSE")) | set(REG.calibratable_names("EXTERNAL"))


def test_calibration_payload_is_engine_scoped():
    assert REG.validate_calibration_payload({"studies_pid_kp": 0.2}, engine="EXTERNAL") == []
    assert REG.validate_calibration_payload({"studies_pid_kp": 0.2}, engine="IN_HOUSE")
    assert REG.validate_calibration_payload({"mpc_shadow_r_gamma": 0.1}, engine="EXTERNAL")
    assert REG.validate_calibration_payload({"entry_confidence_threshold": 0.2}, engine="IN_HOUSE") == []


def test_registry_accounting_is_reported_per_surface():
    assert REG.surface_counts() == {
        "total_targets": 136, "fixed_targets": 28, "safety_params": 20,
        "in_house_eligible": 46, "external_eligible": 108, "shared_eligible": 46,
        "external_only_eligible": 62, "in_house_only_eligible": 0,
    }
    REG.verify_frozen_identity()


def test_identity_covers_applicability():
    changed = CanonicalParameterRegistry()
    changed.params["studies_pid_kp"] = replace(changed.params["studies_pid_kp"], applicable_engines="BOTH")
    assert changed.identity_sha256() != REG.FROZEN_IDENTITY_SHA256


# ------------------------------------------------ source-proved consumer scope

def _sources(*packages):
    return {p: p.read_text(errors="ignore") for pkg in packages for p in (ROOT / pkg).rglob("*.py")}


IN_HOUSE_SRC = _sources("revision2")
EXTERNAL_SRC = _sources("revision2_external", "revision5")
_SKIP = ("canonical_parameter_registry", "parameters.py")


def _consumers(name, sources):
    pattern = re.compile(rf"\b{name}\b")
    return [p.name for p, text in sources.items() if pattern.search(text) and not p.name.startswith(_SKIP[0])
            and not p.name.endswith(_SKIP[1])]


def test_every_external_only_parameter_has_an_external_consumer_and_no_in_house_consumer():
    for name in sorted(REG.EXTERNAL_ONLY_NAMES):
        assert _consumers(name, EXTERNAL_SRC), f"{name}: no external consumer"
        assert not _consumers(name, IN_HOUSE_SRC), f"{name}: consumed in-house, cannot be EXTERNAL-only"
    assert len(REG.EXTERNAL_ONLY_NAMES) == 67


def test_every_shared_optimizer_eligible_parameter_is_mentioned_by_both_engines():
    shared = [n for n, s in REG.params.items() if s.applicable_engines == "BOTH" and s.calibratable]
    for name in shared:
        assert _consumers(name, EXTERNAL_SRC), f"{name}: BOTH but no external consumer"
        assert _consumers(name, IN_HOUSE_SRC), f"{name}: BOTH but no in-house consumer"


def test_in_house_only_parameters_have_no_external_consumer():
    for name in REG.IN_HOUSE_ONLY_NAMES:
        assert _consumers(name, IN_HOUSE_SRC) and not _consumers(name, EXTERNAL_SRC)
        assert REG.params[name].applicable_engines == "IN_HOUSE"


# ---------------- momentum_normalization_divisor / BB04 PA: verdict is scoping

def _bars(n=120, seed=3):
    rng = np.random.default_rng(seed)
    prices = 1000.0 * np.cumprod(1 + rng.normal(0.0004, 0.004, n))
    idx = pd.date_range("2024-01-02 09:15", periods=n, freq="min", tz="Asia/Kolkata")
    return pd.DataFrame({"timestamp": idx, "open": prices, "high": prices * 1.002, "low": prices * 0.998,
                         "close": prices, "volume": rng.integers(1000, 5000, n)})


def _cfg(**overrides):
    values = {n: s.default for n, s in REG.params.items()}
    values.update(overrides)
    return EffectiveConfig.build(values, registry_hash=REG.FROZEN_IDENTITY_SHA256)


def test_bb04_pa_parameters_do_not_reach_the_in_house_pa_box_but_do_reach_the_external_one():
    """Case A: the in-house PredictiveAnalyticsBox never reads these controls, so a
    sensitivity sweep of the in-house box is wrongly scoped for them; the external PA does."""
    from revision2.boxes import PredictiveAnalyticsBox
    from revision2_external.indicators_talib import TALibPredictiveAnalyticsBox
    assert len(BB04_PA) == 16 and set(BB04_PA) <= REG.EXTERNAL_ONLY_NAMES
    bars = _bars()

    def sweep(box, config):
        rows = []
        for i in range(70, len(bars), 5):
            signal, _ = box.evaluate(MarketSnapshot("T", str(bars.iloc[i]["timestamp"]), bars.iloc[:i + 1]), config)
            rows.append((round(signal.confidence, 6), signal.direction, round(signal.momentum, 6)))
        return rows

    base_in = sweep(PredictiveAnalyticsBox(), _cfg())
    base_ex = sweep(TALibPredictiveAnalyticsBox(), _cfg())
    external_reacts = 0
    for name in BB04_PA:
        spec = REG.params[name]
        for value in (spec.minimum, spec.maximum):
            assert sweep(PredictiveAnalyticsBox(), _cfg(**{name: value})) == base_in, name
        if any(sweep(TALibPredictiveAnalyticsBox(), _cfg(**{name: v})) != base_ex
               for v in (spec.minimum, spec.maximum)):
            external_reacts += 1
    assert external_reacts >= 1
    assert sweep(TALibPredictiveAnalyticsBox(), _cfg(momentum_normalization_divisor=1.5)) != \
        sweep(TALibPredictiveAnalyticsBox(), _cfg(momentum_normalization_divisor=6.0))


# ------------------------- orchestrator-level Studies PID consumer sensitivity

def _orchestrator(**defaults):
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    registry = CanonicalParameterRegistry()
    for name, value in defaults.items():
        registry.params[name] = replace(registry.params[name], default=value)
    return Revision2ExternalEngineOrchestrator(["S"], registry)


def _drive(orch, bars, start=30):
    result = None
    for i in range(start, len(bars)):
        result = orch.chart_studies.evaluate("S", bars.iloc[:i + 1])
    return result


def test_orchestrator_registry_values_reach_the_real_per_study_pid_objects():
    bars = _bars(160, seed=11)
    orch = _orchestrator(studies_pid_kp=0.25, studies_pid_ki=0.1, studies_pid_kd=0.08,
                         studies_pid_output_clamp=0.2, studies_grading_horizon_bars=7,
                         studies_hit_rate_window_bars=13)
    _drive(orch, bars)
    states = orch.chart_studies._symbols["S"]
    assert states
    for state in states.values():
        assert state.pid.tunings == (0.25, 0.1, 0.08)
        assert state.pid.output_limits == (-0.2, 0.2)
        assert state.vote_history.maxlen == 8 and state.close_history.maxlen == 8
        assert state.hit_history.maxlen == 13


def test_orchestrator_studies_controls_change_the_real_composite_output():
    bars = _bars(160, seed=11)
    base = _drive(_orchestrator(), bars)
    weights = lambda r: tuple(round(v, 9) for v in r["weights"].values())
    assert weights(_drive(_orchestrator(studies_pid_kp=0.3), bars)) != weights(base)
    assert weights(_drive(_orchestrator(studies_pid_ki=0.15), bars)) != weights(base)
    assert weights(_drive(_orchestrator(studies_pid_output_clamp=0.05), bars)) != weights(base)
    # grading horizon / hit-rate window alter which votes are graded and averaged
    horizon = _drive(_orchestrator(studies_grading_horizon_bars=10), bars)
    window = _drive(_orchestrator(studies_hit_rate_window_bars=10), bars)
    assert weights(horizon) != weights(base) or weights(window) != weights(base)


def test_orchestrator_refresh_keeps_pid_state_while_changing_tunings():
    bars = _bars(160, seed=11)
    orch = _orchestrator()
    _drive(orch, bars, start=30)
    before = {k: (id(s.pid), s.pid._integral, list(s.hit_history)) for k, s in orch.chart_studies._symbols["S"].items()}
    new = _orchestrator(studies_pid_kp=0.3, studies_hit_rate_window_bars=30).config
    orch.chart_studies.configure(new)
    for k, s in orch.chart_studies._symbols["S"].items():
        assert id(s.pid) == before[k][0] and s.pid._integral == before[k][1]
        assert s.pid.tunings[0] == 0.3 and s.hit_history.maxlen == 30
        assert list(s.hit_history) == before[k][2]


# ------------------------------------- structural: no unscoped optimizer path

import ast
import subprocess

_SKIP_DIRS = ("work/", "tests/", "tests_external/", "revision2_backup/", ".venv/")
# Engine-agnostic REPORTING sites (never build an optimizer surface).
_UNION_REPORTING = {"canonical_parameter_registry.py", "parameter_blackbox_bindings.py"}
_ENGINE_ARG_POSITION = {  # name -> number of positional args before ``engine``
    "calibratable_names": 0, "trading_search_space": 1, "validate_calibration_payload": 1, "is_calibratable": 1,
    "applicable_names": 0,
}


def _tracked_source():
    listing = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / p for p in listing.split() if not p.startswith(_SKIP_DIRS)]


def _calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            receiver = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else None
            yield node, name, receiver


def _selects_engine(node, name, receiver):
    if name == "from_registry" and receiver == "SearchSpace":
        return any(k.arg == "engine" for k in node.keywords)
    if name == "ContractValidator":
        return any(k.arg == "engine" for k in node.keywords)
    base = _ENGINE_ARG_POSITION[name]
    return any(k.arg == "engine" for k in node.keywords) or len(node.args) > base


def test_every_optimizer_and_search_space_caller_selects_an_explicit_engine():
    checked, unscoped = 0, []
    for path in _tracked_source():
        if path.name in _UNION_REPORTING:
            continue
        tree = ast.parse(path.read_text(errors="ignore"))
        for node, name, receiver in _calls(tree):
            relevant = (name in _ENGINE_ARG_POSITION or name == "ContractValidator"
                        or (name == "from_registry" and receiver == "SearchSpace"))
            if not relevant:
                continue
            if path.name == "contract_validator.py" and receiver == "self":
                continue     # ContractValidator's own methods already apply self.engine
            checked += 1
            if not _selects_engine(node, name, receiver):
                unscoped.append(f"{path.relative_to(ROOT)}:{node.lineno} {name}")
    assert checked >= 12, "scan found suspiciously few call sites"
    assert not unscoped, unscoped


def test_no_tracked_source_reads_spec_calibratable_outside_the_registry_and_reports():
    allowed = _UNION_REPORTING | {"canonical_config.py"}   # canonical_config: by-box reporting table only
    offenders = []
    for path in _tracked_source():
        if path.name in allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text(errors="ignore"))):
            if isinstance(node, ast.Attribute) and node.attr == "calibratable" and isinstance(node.ctx, ast.Load):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, offenders


def test_contract_validator_requires_and_applies_an_engine():
    from runtime.contract_validator import ContractValidator
    with pytest.raises(TypeError):
        ContractValidator()
    with pytest.raises(ValueError):
        ContractValidator(engine="BOTH")
    payload = {"studies_pid_kp": 0.2}
    assert ContractValidator(engine="EXTERNAL").validate_calibration_payload(payload) == []
    assert ContractValidator(engine="IN_HOUSE").validate_calibration_payload(payload)   # engine-specific name refused


def test_supervisory_bridge_payload_is_genuinely_shared():
    assert REG.params["entry_confidence_threshold"].applicable_engines == "BOTH"
    assert REG.params["entry_confidence_threshold"].calibratable
    source = (ROOT / "revision5/supervisory_bridge.py").read_text()
    assert 'engine="EXTERNAL"' in source


# ------------------------------------------- HMM hysteresis envelope

def test_every_corner_of_the_hysteresis_search_envelope_satisfies_exit_below_enter():
    from revision2_external.closed_loop_control import HMMRiskHysteresis
    enter, exit_ = REG.params["cl_hmm_stress_enter"], REG.params["cl_hmm_stress_exit"]
    assert (enter.minimum, enter.maximum, enter.default) == (0.7, 0.9, 0.75)
    assert (exit_.minimum, exit_.maximum, exit_.default) == (0.3, 0.65, 0.55)
    for e in (enter.minimum, enter.default, enter.maximum):
        for x in (exit_.minimum, exit_.default, exit_.maximum):
            HMMRiskHysteresis(enter_stress_probability=e, exit_stress_probability=x)   # must not raise
    # the previous, overlapping ranges could violate the invariant
    with pytest.raises(ValueError):
        HMMRiskHysteresis(enter_stress_probability=0.6, exit_stress_probability=0.7)
    assert "NOT calibrated" in enter.notes and "NOT calibrated" in exit_.notes


# ------------------------------------- Studies PID canonical ownership

def test_production_code_never_passes_literal_studies_gains_to_composite_study_signal():
    seen = []
    for path in _tracked_source():
        rel = str(path.relative_to(ROOT))
        if rel.startswith("scripts/"):
            continue     # research scripts use registry defaults via default_config()
        for node, name, _ in _calls(ast.parse(path.read_text(errors="ignore"))):
            if name == "CompositeStudySignal":
                seen.append(rel)
                assert not {k.arg for k in node.keywords} & {"kp", "ki", "kd", "clamp"}, rel
                assert not node.args, rel
                if rel == "revision2_external/orchestrator.py":
                    assert [k.arg for k in node.keywords] == ["config"]
    assert "revision2_external/orchestrator.py" in seen
