#!/usr/bin/env python3
"""
Diagnostic: Check what can be imported from kite_dual_engine_ccpp_plant.py
Run this on your local machine in ~/institutional_quant_workspace/engine_v03/
"""

import sys
from pathlib import Path
import ast

def check_file_syntax(filepath):
    """Check if Python file has valid syntax."""
    print(f"\n[1] Checking syntax of {filepath}...")
    try:
        with open(filepath, 'r') as f:
            code = f.read()
        ast.parse(code)
        print("    ✓ Syntax is valid")
        return True
    except SyntaxError as e:
        print(f"    ✗ Syntax Error at line {e.lineno}: {e.msg}")
        print(f"      {e.text}")
        return False

def extract_classes(filepath):
    """Extract all class names from Python file."""
    print(f"\n[2] Extracting class definitions...")
    try:
        with open(filepath, 'r') as f:
            tree = ast.parse(f.read())

        classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]

        if classes:
            print(f"    Found {len(classes)} classes:")
            for cls in classes:
                print(f"      • {cls}")
            return classes
        else:
            print("    ✗ No classes found")
            return []
    except Exception as e:
        print(f"    ✗ Error: {e}")
        return []

def extract_functions(filepath):
    """Extract all top-level function names from Python file."""
    print(f"\n[3] Extracting top-level functions...")
    try:
        with open(filepath, 'r') as f:
            tree = ast.parse(f.read())

        functions = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]

        if functions:
            print(f"    Found {len(functions)} functions:")
            for func in functions[:10]:  # Show first 10
                print(f"      • {func}()")
            if len(functions) > 10:
                print(f"      ... and {len(functions) - 10} more")
            return functions
        else:
            print("    No top-level functions found")
            return []
    except Exception as e:
        print(f"    ✗ Error: {e}")
        return []

def test_import(module_path, class_name):
    """Try to import specific class."""
    print(f"\n[4] Testing import: from {module_path.stem} import {class_name}")
    sys.path.insert(0, str(module_path.parent))
    try:
        module = __import__(module_path.stem)
        if hasattr(module, class_name):
            print(f"    ✓ Successfully imported {class_name}")
            cls = getattr(module, class_name)
            print(f"      Type: {type(cls)}")
            return True
        else:
            print(f"    ✗ Class '{class_name}' not found in module")
            print(f"      Available attributes: {[a for a in dir(module) if not a.startswith('_')][:10]}")
            return False
    except Exception as e:
        print(f"    ✗ Import failed: {e}")
        return False

def main():
    filepath = Path.home() / 'institutional_quant_workspace' / 'engine_v03' / 'kite_dual_engine_ccpp_plant.py'

    if not filepath.exists():
        print(f"✗ File not found: {filepath}")
        sys.exit(1)

    print("=" * 70)
    print("  KITE DUAL ENGINE CCPP PLANT - IMPORT DIAGNOSTICS")
    print("=" * 70)

    # 1. Check syntax
    if not check_file_syntax(filepath):
        print("\n✗ File has syntax errors. Cannot proceed.")
        sys.exit(1)

    # 2. Extract classes
    classes = extract_classes(filepath)

    # 3. Extract functions
    functions = extract_functions(filepath)

    # 4. Try to import the most common class name
    if classes:
        print(f"\n[4] Testing imports...")
        for cls in classes[:3]:  # Try first 3 classes
            test_import(filepath, cls)

    print("\n" + "=" * 70)
    print("  RECOMMENDATIONS")
    print("=" * 70)

    if 'CCPP_PIDGovernorActuator' in classes:
        print("✓ CCPP_PIDGovernorActuator class found - import should work")
    else:
        print("✗ CCPP_PIDGovernorActuator not found")
        if classes:
            print(f"  Try importing one of these instead: {classes[0]}")

    print("\nIf import is still failing:")
    print("1. Check that backup file exists: kite_dual_engine_ccpp_plant.py.bak")
    print("2. Try restoring again: cp kite_dual_engine_ccpp_plant.py.bak kite_dual_engine_ccpp_plant.py")
    print("3. Run this script again to verify")

if __name__ == '__main__':
    main()
