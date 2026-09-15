from __future__ import annotations

import pathlib
import shutil
import zipfile

ZIP = pathlib.Path("adversarial_tests_v34_b14_STAGING_16of16.zip")
OUT = pathlib.Path("adversarial_tests_17")
SUITE = OUT / "adversarial_tests" / "run_adversarial_suite.py"


def fail(msg: str) -> None:
    raise SystemExit(f"[ERROR] {msg}")


if not ZIP.exists():
    fail(f"Archive not found: {ZIP.resolve()}")

if OUT.exists():
    fail(f"Output directory already exists: {OUT.resolve()} -- refusing to overwrite it.")

with zipfile.ZipFile(ZIP) as z:
    names = [
        n for n in z.namelist()
        if n.startswith("adversarial_tests/")
        and not n.startswith("adversarial_tests/adversarial_tests/")
        and "__pycache__/" not in n
    ]
    if not any(n.endswith("run_adversarial_suite.py") for n in names):
        fail("Canonical top-level run_adversarial_suite.py was not found.")

    for name in names:
        target = OUT / name
        if name.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)

if not SUITE.exists():
    fail(f"Extracted suite not found: {SUITE}")

s = SUITE.read_text(encoding="utf-8")

required = [
    '("Real order-placement barrier", self.test_real_order_barrier),',
    "    def test_real_order_barrier(self, td):",
    "import institutional_engine_v34_staging as eng",
]
for needle in required:
    if needle not in s:
        fail(f"Expected 16-test harness anchor not found: {needle!r}")

if "def test_startup_sl_quantity_mismatch" in s:
    fail("Test #17 already exists; refusing to duplicate it.")

registry_anchor = '            ("Real order-placement barrier", self.test_real_order_barrier),'
if s.count(registry_anchor) != 1:
    fail("Test registry anchor is not unique.")

replacement = (
    '            ("Startup SL quantity mismatch -> HALT", '
    'self.test_startup_sl_quantity_mismatch),\n'
    + registry_anchor
)
s = s.replace(registry_anchor, replacement, 1)

method_anchor = "    def test_real_order_barrier(self, td):"
if s.count(method_anchor) != 1:
    fail("test_real_order_barrier anchor is not unique.")

method = """    def test_startup_sl_quantity_mismatch(self, td):
        # Startup-specific invariant:
        # local target = 10, broker position = 10, protective SL = 5.
        # The staging engine must hard-halt during reconcile_startup()
        # before accepting the SL or successfully recovering into MANAGING.
        broker = FakeBrokerV34()

        stop = broker.simulated_place_order(
            "regular",
            "NSE",
            SYMBOL,
            "SELL",
            5,
            "MIS",
            "SL",
            trigger_price=98,
            tag="V3.4_SL",
        )
        broker.set_position(SYMBOL, TARGET, AVG)

        ctx = trade(
            "MANAGING",
            entry_id="SIM_ENTRY_STARTUP_SL_MISMATCH",
            stop_id=stop,
            filled=TARGET,
            pending=0,
        )
        ctx.avg_entry_price = Decimal(str(AVG))

        engine, *_ = make_engine(td, "MANAGING", ctx, broker)

        reason = expect_halt(
            engine.reconcile_startup,
            "Protective stop quantity does not match broker position quantity.",
        )

        assert "protective stop quantity" in reason.lower()
        assert "broker position quantity" in reason.lower()
        assert engine.terminator.halted is True
        assert engine.state.status != "MANAGING"

"""

s = s.replace(method_anchor, method + method_anchor, 1)
SUITE.write_text(s, encoding="utf-8")

print("[OK] Created isolated 17-test harness.")
print(f"[INFO] Harness: {SUITE.resolve()}")
print("[INFO] Original 16of16 ZIP was not modified.")
print("[INFO] Production engine/checkpoint were not modified.")
print("[INFO] Added Test #17: Startup SL quantity mismatch -> HALT")
print("[INFO] Test calls reconcile_startup() directly.")
