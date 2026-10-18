"""Register of the numeric literals that remain in the native R5 controller modules, with the
justification for each.  test_no_active_controller_consumes_an_unregistered_tuning_literal fails
if any new literal (or one more occurrence of a listed value) appears in these files, so a new
tuning constant cannot be added without either registering it or justifying it here.

0 and 1 are not listed (identities, unit bounds, unit counters).  Human-readable copies of this
register: docs/r5_parameter_authority/REMAINING_HARDCODED_VALUES.md.
"""

ALLOWED_LITERALS = {
    "revision5/governor.py": {
        1e-12: 1,   # capital-weight sum tolerance check on the registry default view (numerical guard)
        2.0: 1,     # trend_overspeed limit mirrors the BUY threshold: threshold - 2 * base_z (structural symmetry)
    },
    "revision5/dynamic_parameters.py": {
        # R5EnvironmentState default probabilities 1/3 each (uninformative prior; 3 = number of regimes).
        3.0: 5,     # 3 x prior 1/3, and the dead strategy profile (min_rr upper clip 3.00, max hold 3.0 * turbulent)
        # Everything below belongs to snapshot outputs with NO runtime consumer in Engines A/B/C
        # (snapshot.strategy, snapshot.execution, snapshot.safety, plant.dispatcher_min_floor /
        # dispatcher_max_ceiling).  Registering them would claim an authority they do not have.
        0.0005: 1, 0.001: 1, 0.002: 1, 0.02: 1, 0.025: 1, 0.05: 2, 0.08: 1, 0.1: 3, 0.15: 4,
        0.2: 2, 0.25: 2, 0.3: 1, 0.35: 3, 0.4: 1, 0.5: 4, 0.6: 1, 0.75: 2, 1.5: 1, 2.0: 2,
        5.0: 2, 8.0: 1, 10.0: 2, 15.0: 1, 20.0: 2, 30.0: 3, 60.0: 2, 120.0: 1,
    },
    "revision5/ccpp_protection_cubicles.py": {
        100.0: 10,  # 1 / (ATR% of price) in the AVR lot volatility scalar (unit conversion) and % formatting in trip messages
        2: 3,       # MiCOM ANSI 21: a z-score needs >= 2 returns and window + 2 closes (structural)
        1e-9: 1,    # spread division guard (numerical)
    },
    "revision5/hrsg.py": {
        1_000_000.0: 1,  # constructor default plant capital (the plant always passes its own capital)
        0.15: 1, 0.6: 1, 0.45: 1, 0.5: 1,  # superheat_headroom_factor(): no runtime caller (dead path)
        2: 2,       # a Pearson correlation needs >= 2 samples (structural)
        1e-15: 2,   # zero-variance guard (numerical)
        1e-6: 2,    # capital conservation tolerance (numerical invariant)
    },
    "revision5/ccpp_unified_plant.py": {
        1_000_000.0: 2,  # constructor default capitals (dispatcher, DCS); runners pass the run capital
        2: 2,       # plant stop/take-profit rounded to 2 decimals (rupee paise; advisory outputs)
        1e-15: 2, 1e-12: 2, 1e-9: 1, 1e-6: 1,  # projection / bounds / conservation tolerances (numerical)
        # MarkVExecutiveController and its SPEEDTRONIC_* constants: used only by tests (dead in
        # Engines A/B/C; superseded by revision5/governor_authority.py, whose values are registered).
        0.15: 1, 0.25: 1, 0.6: 3, 4: 5,
    },
}
