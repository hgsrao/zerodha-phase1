#!/bin/bash
# Simple restoration script for kite_dual_engine_ccpp_plant.py
# Run this on your Windows PC in ~/institutional_quant_workspace/engine_v03/

cd ~/institutional_quant_workspace/engine_v03

echo "=========================================="
echo "  RESTORING KITE DUAL ENGINE CCPP PLANT"
echo "=========================================="
echo ""

# Step 1: Check if backup exists
if [ -f "kite_dual_engine_ccpp_plant.py.bak" ]; then
    echo "[✓] Backup found: kite_dual_engine_ccpp_plant.py.bak"
else
    echo "[✗] Backup not found. Cannot restore."
    echo "    File may have been deleted or moved."
    exit 1
fi

# Step 2: Restore from backup
echo "[→] Restoring from backup..."
cp kite_dual_engine_ccpp_plant.py.bak kite_dual_engine_ccpp_plant.py
echo "[✓] File restored"

# Step 3: Verify syntax
echo "[→] Checking Python syntax..."
python3 -c "
import ast
with open('kite_dual_engine_ccpp_plant.py', 'r') as f:
    try:
        ast.parse(f.read())
        print('[✓] Syntax is valid')
    except SyntaxError as e:
        print(f'[✗] Syntax error: {e}')
        exit(1)
"

# Step 4: Check importable classes
echo "[→] Checking available classes..."
python3 << 'PYTHON_END'
import ast
with open('kite_dual_engine_ccpp_plant.py', 'r') as f:
    tree = ast.parse(f.read())
    classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    if classes:
        print(f"[✓] Found {len(classes)} classes:")
        for cls in classes[:5]:
            print(f"    • {cls}")
    else:
        print("[✗] No classes found")
PYTHON_END

echo ""
echo "=========================================="
echo "  RESTORATION COMPLETE"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. The file has been restored to working state"
echo "2. Run the gate diagnostic tool:"
echo "   python3 diagnostic_48symbol_gate_analysis.py --duration 1week"
echo "3. DO NOT attempt to manually patch - wait for clean instructions"
echo ""
