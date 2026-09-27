#!/usr/bin/env python3
"""Generate docs/R5_PARAMETER_INVENTORY.md: every registry parameter, classified, and every
numeric literal still embedded in the R5 engine code.

Parameter classes (agreed design):

  STRUCTURAL            windows / counts that define the measurement itself (fixed, integer)
  FIXED_SAFETY          safety contract values; never calibrated, never scheduled online
  CALIBRATABLE_OFFLINE  moved only by the sealed offline calibration protocol
  DYNAMIC_SCHEDULED     re-scheduled online from measured market state (gain scheduling)
  FIXED_ENGINEERING     registry-owned but deliberately not calibrated (disclosed)

The literal scan is the honest remainder: numbers in engine code that are NOT registry-owned.
Each is labelled NUMERICAL_GUARD (epsilons, 0/1 identities), NAMED_CONSTANT, DEFAULT_ARGUMENT
or INLINE_LITERAL so the remaining work to reach "no unowned operating values" is explicit.

Usage:  python scripts/r5_parameter_inventory.py [--output docs/R5_PARAMETER_INVENTORY.md]
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402

sys.path.insert(0, str(ROOT))
from canonical_parameter_registry import CanonicalParameterRegistry  # noqa: E402

DYNAMIC_SOURCES = ("revision2_external/dynamic_parameter_controller.py", "revision5/dynamic_parameters.py")
STRUCTURAL_HINTS = ("window", "_bars", "period", "lookback", "horizon")
IDENTITIES = {0, 1, -1, 2, 100}


def engine_files() -> List[Path]:
    """revision5 plus every revision2_external module the replay orchestrator imports."""
    files = sorted((ROOT / "revision5").glob("*.py"))
    orchestrator = ROOT / "revision2_external" / "orchestrator.py"
    files.append(orchestrator)
    for node in ast.walk(ast.parse(orchestrator.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("revision2_external."):
            path = ROOT / (node.module.replace(".", "/") + ".py")
            if path.exists() and path not in files:
                files.append(path)
    return files


def classify_parameters(registry: CanonicalParameterRegistry) -> List[Tuple[str, str, object]]:
    dynamic_text = "\n".join((ROOT / p).read_text() for p in DYNAMIC_SOURCES if (ROOT / p).exists())
    rows = []
    for name, spec in sorted(registry.safety_params.items()):
        rows.append(("FIXED_SAFETY", name, spec))
    calibratable = set(registry.calibratable_names())
    for name, spec in sorted(registry.params.items()):
        if f'"{name}"' in dynamic_text or f"'{name}'" in dynamic_text:
            cls = "DYNAMIC_SCHEDULED"
        elif name in calibratable:
            cls = "CALIBRATABLE_OFFLINE"
        elif spec.param_type == "int" and any(h in name for h in STRUCTURAL_HINTS):
            cls = "STRUCTURAL"
        else:
            cls = "FIXED_ENGINEERING"
        rows.append((cls, name, spec))
    return rows


class _LiteralScan(ast.NodeVisitor):
    def __init__(self, path: Path):
        self.path = path
        self.rows: List[Dict[str, object]] = []
        self._scope: List[str] = []
        self._defaults: set = set()
        self._named: set = set()

    def _enter(self, node, name):
        self._scope.append(name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node):
        for default in list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]:
            for sub in ast.walk(default):
                self._defaults.add(id(sub))
        self._enter(node, node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        for stmt in node.body:                       # dataclass field defaults
            if isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                for sub in ast.walk(stmt.value):
                    self._defaults.add(id(sub))
        self._enter(node, node.name)

    def visit_Assign(self, node):
        if all(isinstance(t, ast.Name) and t.id.isupper() for t in node.targets):
            for sub in ast.walk(node.value):
                self._named.add(id(sub))
        self.generic_visit(node)

    def visit_Constant(self, node):
        value = node.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        if value in IDENTITIES or (isinstance(value, float) and 0 < abs(value) <= 1e-6):
            kind = "NUMERICAL_GUARD"
        elif id(node) in self._named:
            kind = "NAMED_CONSTANT"
        elif id(node) in self._defaults:
            kind = "DEFAULT_ARGUMENT"
        else:
            kind = "INLINE_LITERAL"
        self.rows.append({"file": str(self.path.relative_to(ROOT)), "line": node.lineno, "value": value,
                          "scope": ".".join(self._scope) or "<module>", "kind": kind})


def scan_literals(files: List[Path]) -> List[Dict[str, object]]:
    rows = []
    for path in files:
        tree = ast.parse(path.read_text())
        scanner = _LiteralScan(path)
        scanner.visit(tree)
        rows.extend(scanner.rows)
    return rows


def render(registry: CanonicalParameterRegistry, params, literals) -> str:
    counts = Counter(cls for cls, _, _ in params)
    kinds = Counter(row["kind"] for row in literals)
    lines = [
        "# R5 parameter inventory",
        "",
        "Generated by `scripts/r5_parameter_inventory.py`; do not edit by hand.",
        "",
        f"Registry identity: `{registry.FROZEN_IDENTITY_SHA256}`",
        "",
        "## Registry parameters by class",
        "",
        "| class | count |", "|---|---:|",
        *[f"| {cls} | {n} |" for cls, n in sorted(counts.items())],
        "",
        "| class | name | box | type | default | range | engines | notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cls, name, spec in params:
        rng = f"[{spec.minimum}, {spec.maximum}]" if spec.param_type in ("int", "float") else ""
        notes = str(spec.notes).replace("|", "/")
        lines.append(f"| {cls} | `{name}` | {spec.black_box} | {spec.param_type} | {spec.default} | {rng} | "
                     f"{getattr(spec, 'applicable_engines', '')} | {notes} |")
    lines += [
        "", "## Numeric literals in engine code (not registry-owned)", "",
        "NUMERICAL_GUARD values (0, 1, -1, 2, 100 and epsilons) are identities, not operating values.",
        "Every other row is an operating value that is not yet registry-owned.", "",
        "| kind | count |", "|---|---:|",
        *[f"| {kind} | {n} |" for kind, n in sorted(kinds.items())], "",
        "| file | count (non-guard) |", "|---|---:|",
    ]
    per_file = Counter(row["file"] for row in literals if row["kind"] != "NUMERICAL_GUARD")
    lines += [f"| `{f}` | {n} |" for f, n in sorted(per_file.items(), key=lambda kv: -kv[1])]
    lines += ["", "| file:line | scope | value | kind |", "|---|---|---:|---|"]
    for row in literals:
        if row["kind"] != "NUMERICAL_GUARD":
            lines.append(f"| `{row['file']}:{row['line']}` | {row['scope']} | {row['value']} | {row['kind']} |")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "R5_PARAMETER_INVENTORY.md")
    args = parser.parse_args(argv)
    registry = CanonicalParameterRegistry()
    params = classify_parameters(registry)
    literals = scan_literals(engine_files())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(registry, params, literals))
    non_guard = sum(row["kind"] != "NUMERICAL_GUARD" for row in literals)
    print(f"{len(params)} registry parameters, {non_guard} non-guard literals -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
