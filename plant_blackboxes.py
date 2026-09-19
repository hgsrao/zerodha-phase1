import numpy as np

class ECSTradingSupervisorCalibrated:
    """Supervisory DCS coordinating grid sync, frequency droop, and voltage-stress sizing."""
    def __init__(self, cfg):
        s_cfg = cfg.get("ecs_supervisor", {})
        self.anti_phase_limit = s_cfg.get("ansi_25_synchrocheck", {}).get("anti_phase_vol_ratio_limit", 2.20)
        self.speed_deadband = s_cfg.get("speed_frequency_governor", {}).get("speed_deadband_pts", 0.00035)
        self.speed_gate = s_cfg.get("speed_frequency_governor", {}).get("speed_gate_threshold", -0.0008)
        self.min_risk = s_cfg.get("voltage_var_supervisor", {}).get("voltage_risk_min_mult", 0.40)
        self.max_risk = s_cfg.get("voltage_var_supervisor", {}).get("voltage_risk_max_mult", 1.25)
        self.ref_vol = s_cfg.get("voltage_var_supervisor", {}).get("nominal_volatility_ref", 0.0085)

    def synchrocheck(self, dp_dt, dv_dt, vol_ratio):
        # ANSI 25 Out-of-Phase cascade trip
        if dp_dt < 0 and dv_dt > 0 and vol_ratio > self.anti_phase_limit:
            return False, "ANSI_25_TRIP_OUT_OF_PHASE_CASCADE"
        return True, "SYNCHRONIZED"

    def check_grid_speed_gate(self, nifty_vel):
        if nifty_vel < self.speed_gate:
            return False, "GRID_FREQ_SHEDDING"
        return True, "GRID_FREQ_NOMINAL"

    def compute_voltage_stress_sizing(self, current_volatility):
        ratio = self.ref_vol / max(1e-6, current_volatility)
        mult = max(self.min_risk, min(self.max_risk, ratio))
        return mult


class PlantBlackBoxesCalibrated:
    """The 10 Calibrated Operational Black Boxes."""
    def __init__(self, cfg):
        bb = cfg.get("black_boxes", {})
        self.bb01 = bb.get("BB01_REGIME_CLASSIFIER", {})
        self.bb02 = bb.get("BB02_KINETIC_FLUX", {})
        self.bb03 = bb.get("BB03_THERMAL_BOUNDARY", {})
        self.bb04 = bb.get("BB04_MAIN_FUEL_VALVE", {})
        self.bb05 = bb.get("BB05_FLAME_STABILITY", {})
        self.bb06 = bb.get("BB06_STEAM_BYPASS_TARGET", {})
        self.bb07 = bb.get("BB07_PROTECTION_INTERLOCKS", {})
        self.bb08 = bb.get("BB08_CONDENSER_HOTWELL", {})
        self.bb09 = bb.get("BB09_EXCITER_AVR_RATCHET", {})
        self.bb10 = bb.get("BB10_LFC_AREA_REGULATOR", {})

    def evaluate_entry_gate(self, sym, z_score, bay_name, sector_prof):
        # BB03 Thermal Boundary check
        z_thresh = sector_prof.get("z_entry_overrides", {}).get(sym, sector_prof.get("z_entry_threshold", -1.85))
        return z_score <= z_thresh, z_thresh

    def evaluate_flame_stability(self, holding_bars, current_z, entry_z):
        # BB05 Flameout check
        if holding_bars == self.bb05.get("flameout_window_bars", 6):
            rebound = current_z - entry_z
            if rebound < self.bb05.get("flameout_min_z_rebound", 0.25):
                return False, "BB05_FLAMEOUT_BAILOUT"
        return True, "FLAME_STABLE"

    def evaluate_protection_trips(self, current_z):
        # BB07 ANSI 27 Emergency Low-Voltage Trip
        if current_z <= self.bb07.get("ansi_27_under_voltage_trip_z", -2.85):
            return True, "BB07_ANSI_27_UNDERVOLTAGE_TRIP"
        return False, "NORMAL"

    def get_ratchet_stop(self, entry_px, current_stop, risk_ticks, current_z):
        # BB09 Exciter AVR dynamic ratchet
        best_stop = current_stop
        for r in self.bb09.get("ratchets", []):
            if current_z >= r["trigger_z"]:
                dyn = entry_px + (r["stop_r"] * risk_ticks)
                best_stop = max(best_stop, dyn)
        return best_stop
