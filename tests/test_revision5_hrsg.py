import pytest
from revision5.topology import (
    BAY_IDS,
    GTG1_HEAVY_INDUSTRY,
    GTG2_TECH_TELECOM,
    CSTG1_BFSI,
    CSTG2_CONSUMER_AUTO,
    BPSTG_HEALTHCARE,
)
from revision5.hrsg import HeatRecoverySteamGenerator


@pytest.fixture
def hrsg():
    return HeatRecoverySteamGenerator(base_plant_capital=1_000_000.0, rho_crit=0.65)


def test_hrsg_neutral_allocation_under_healthy_conditions(hrsg):
    base_allocs = {
        GTG1_HEAVY_INDUSTRY: 250_000.0,
        GTG2_TECH_TELECOM: 200_000.0,
        CSTG1_BFSI: 250_000.0,
        CSTG2_CONSUMER_AUTO: 180_000.0,
        BPSTG_HEALTHCARE: 120_000.0,
    }
    states = {b: {"tripped_offline": False, "cooldown_cycles": 0} for b in BAY_IDS}

    adjusted = hrsg.harvest_exhaust_capital(states, base_allocs)
    assert sum(adjusted.values()) == pytest.approx(1_000_000.0, abs=1.0)
    assert adjusted[GTG1_HEAVY_INDUSTRY] == pytest.approx(250_000.0, abs=10.0)


def test_hrsg_harvests_and_recirculates_gtg_exhaust(hrsg):
    base_allocs = {
        GTG1_HEAVY_INDUSTRY: 250_000.0,
        GTG2_TECH_TELECOM: 200_000.0,
        CSTG1_BFSI: 250_000.0,
        CSTG2_CONSUMER_AUTO: 180_000.0,
        BPSTG_HEALTHCARE: 120_000.0,
    }
    # GTG1 trips offline
    states = {
        GTG1_HEAVY_INDUSTRY: {"tripped_offline": True, "cooldown_cycles": 0},
        GTG2_TECH_TELECOM: {"tripped_offline": False, "cooldown_cycles": 0},
        CSTG1_BFSI: {"tripped_offline": False, "cooldown_cycles": 0},
        CSTG2_CONSUMER_AUTO: {"tripped_offline": False, "cooldown_cycles": 0},
        BPSTG_HEALTHCARE: {"tripped_offline": False, "cooldown_cycles": 0},
    }

    adjusted = hrsg.harvest_exhaust_capital(states, base_allocs)
    assert sum(adjusted.values()) == pytest.approx(1_000_000.0, abs=1.0)
    # GTG1 allocation curtailed, STGs receive steam boost
    assert adjusted[GTG1_HEAVY_INDUSTRY] < 200_000.0
    assert adjusted[CSTG1_BFSI] > 250_000.0
    assert adjusted[BPSTG_HEALTHCARE] > 120_000.0


def test_hrsg_economizer_correlation_damping(hrsg):
    # Simulate high pairwise correlation between GTG1 and CSTG1
    for r in [0.02, -0.01, 0.03, -0.02, 0.04, -0.01, 0.02, 0.03]:
        hrsg.record_bay_return(GTG1_HEAVY_INDUSTRY, r)
        hrsg.record_bay_return(CSTG1_BFSI, r)  # Perfect correlation (1.0 > 0.65)

    penalties = hrsg.compute_economizer_penalties()
    assert penalties[GTG1_HEAVY_INDUSTRY] < 1.0
    assert penalties[CSTG1_BFSI] < 1.0
    assert penalties[BPSTG_HEALTHCARE] == 1.0


def test_superheat_boost_only_applies_to_steam_turbines(hrsg):
    assert hrsg.get_superheat_headroom(GTG1_HEAVY_INDUSTRY, win_rate_r=0.80) == 1.0
    assert hrsg.get_superheat_headroom(BPSTG_HEALTHCARE, win_rate_r=0.65) == 1.15
    assert hrsg.get_superheat_headroom(CSTG1_BFSI, win_rate_r=0.50) == 1.075
    assert hrsg.get_superheat_headroom(CSTG2_CONSUMER_AUTO, win_rate_r=0.30) == 1.0
