import os

source_file = 'institutional_engine_v34_B12_resilience.py'
target_file = 'institutional_engine_v34_B13_resilience.py'

old_block = '''        self.state = self.store.load(self.clock.now().date())

        if self.state.clearance_required or self.state.status == "RECONCILIATION_HALT":'''

new_block = '''        self.state = self.store.load(self.clock.now().date())

        # Architectural Fix: Never trust a persisted FLAT state. Force broker reconciliation.
        if self.state.status == "FLAT":
            self.state.status = "STARTUP"

        if self.state.clearance_required or self.state.status == "RECONCILIATION_HALT":'''

with open(source_file, 'r', encoding='utf-8') as f:
    content = f.read()

if old_block in content:
    content = content.replace(old_block, new_block)
    with open(target_file, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Successfully generated {target_file}")
else:
    print("CRITICAL: Could not find exact block to replace. Aborting.")
