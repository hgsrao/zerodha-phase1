import json
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from decimal import Decimal

from nautilus_trader.core.message import Event
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from nautilus_trader.indicators import BollingerBands, AverageTrueRange

class SectorBayClosedLoopGovernor:
    """Local Closed-Loop Mark V PID Governor for GTG/STG Turbine Units."""
    def __init__(self, bay_name, kp=0.25, ki=0.02, kd=0.15, droop=0.04, integral_clamp=1.5, trail_activation_u=0.15):
        self.bay_name = bay_name
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.droop = droop
        self.integral_clamp = integral_clamp
        self.trail_activation_u = trail_activation_u
        self.integral = 0.0
        self.prev_error = None

    def reset(self):
        self.integral = 0.0
        self.prev_error = None

    def update(self, current_z, target_z=0.50, grid_freq_dev=0.0):
        # Tracking displacement towards target
        error = target_z - current_z
        if self.prev_error is None:
            self.prev_error = error

        self.integral += error
        self.integral = max(-self.integral_clamp, min(self.integral_clamp, self.integral))

        deriv = error - self.prev_error
        self.prev_error = error

        # Control effort
        u = (self.kp * error) + (self.ki * self.integral) + (self.kd * deriv) - (self.droop * grid_freq_dev)
        return u

    def get_dynamic_stop(self, entry_price, current_stop, risk_ticks, current_z, target_z, u_effort):
        # Progressive ratchet as price closes the gap towards target
        # When Z crosses 0.0 (halfway back to target), pull stop to -0.35R
        if current_z >= 0.20:
            dyn_stop = entry_price + (0.10 * risk_ticks)  # Lock small profit
            return max(current_stop, dyn_stop)
        elif current_z >= 0.0:
            dyn_stop = entry_price - (0.35 * risk_ticks)  # Cut 65% of loss risk
            return max(current_stop, dyn_stop)
        elif current_z >= -0.75:
            dyn_stop = entry_price - (0.70 * risk_ticks)  # Cut 30% of loss risk
            return max(current_stop, dyn_stop)
        return current_stop
class Revision2StrategyConfig(StrategyConfig, frozen=True):
    fleet_config_path: str = "results/fleet_config.json"
    base_capital: float = 100_000.0

class NautilusRevision2PlantStrategy(Strategy):
    def __init__(self, config: Revision2StrategyConfig) -> None:
        super().__init__(config)
        try:
            with open(config.fleet_config_path, "r") as f:
                self.fleet_cfg = json.load(f)
        except Exception:
            self.fleet_cfg = {}

        self.master_grid = self.fleet_cfg.get("master_grid_dcs_pid", {})
        self.bay_regs = self.fleet_cfg.get("bay_mark_v_registers", {})
        self.hrp_weights = self.fleet_cfg.get("hrp_fleet_allocation", {})
        
        self.mapping = {
            "TATASTEEL": "GTG1_METALS_ENERGY", "RELIANCE": "GTG1_METALS_ENERGY",
            "TCS": "GTG2_TECH_AGILE", "INFY": "GTG2_TECH_AGILE",
            "HDFCBANK": "CSTG_CORE_BANKING", "ICICIBANK": "CSTG_CORE_BANKING",
            "SBIN": "CSTG_CORE_BANKING", "BAJFINANCE": "CSTG_CORE_BANKING",
            "LT": "BPSTG_INFRA", "MARUTI": "BPSTG_INFRA",
        }
        self.indicators = {}
        self.grid_error_history = []
        self.bay_error_history = {}
        self.entry_tracker = {}
        self.bar_counts = {}


        # Turbine Bay mapping and calibrated governor profiles
        self.symbol_to_bay = {
            'TATASTEEL': 'GTG1_METALS_MINING',
            'RELIANCE': 'GTG2_POWER_ENERGY_INFRA',
            'LT': 'GTG2_POWER_ENERGY_INFRA',
            'HDFCBANK': 'CSTG1_BANKING_FINANCE',
            'ICICIBANK': 'CSTG1_BANKING_FINANCE',
            'SBIN': 'CSTG1_BANKING_FINANCE',
            'BAJFINANCE': 'CSTG1_BANKING_FINANCE',
            'TCS': 'CSTG2_TECH_CONSUMER_AUTO',
            'INFY': 'CSTG2_TECH_CONSUMER_AUTO',
            'MARUTI': 'CSTG2_TECH_CONSUMER_AUTO',
        }
        
        # Load calibrated parameters
        with open('results/fleet_config.json') as f:
            cfg = json.load(f)
        self.bay_profiles = cfg.get('sector_governor_profiles', {})
        
        # Instantiate local closed-loop governors per instrument
        self.bay_governors = {}
        for sym, bay in self.symbol_to_bay.items():
            b_data = self.bay_profiles.get(bay, {})
            pid_cfg = b_data.get('pid', {})
            self.bay_governors[sym] = SectorBayClosedLoopGovernor(
                bay_name=bay,
                kp=pid_cfg.get('kp', 0.25),
                ki=pid_cfg.get('ki', 0.02),
                kd=pid_cfg.get('kd', 0.15),
                droop=pid_cfg.get('droop', 0.04),
                integral_clamp=pid_cfg.get('integral_clamp', 1.5),
                trail_activation_u=pid_cfg.get('trail_activation_u', 0.15)
            )

    def on_start(self) -> None:
        for instrument in self.cache.instruments():
            sym = instrument.id.symbol.value
            self.indicators[sym] = {
                "bb": BollingerBands(15, 2.0),
                "atr": AverageTrueRange(14),
            }
            self.bar_counts[sym] = 0
            bar_type = BarType.from_str(f"{instrument.id.value}-15-MINUTE-LAST-EXTERNAL")
            self.register_indicator_for_bars(bar_type, self.indicators[sym]["bb"])
            self.register_indicator_for_bars(bar_type, self.indicators[sym]["atr"])
            self.subscribe_bars(bar_type)

    def _compute_pid(self, Kp, Ki, Kd, err, hist, windup=2.0):
        hist.append(err)
        if len(hist) > 12:
            hist.pop(0)
        e_prev = hist[-2] if len(hist) >= 2 else err
        e_int = float(np.clip(sum(hist), -windup, windup))
        u = (Kp * err) + (Ki * e_int) + (Kd * (err - e_prev))
        return float(np.clip(u, 0.05, 1.0))

    def on_bar(self, bar: Bar) -> None:
        inst_id = bar.bar_type.instrument_id
        sym = inst_id.symbol.value
        inds = self.indicators.get(sym)
        if not inds or not inds["bb"].initialized or not inds["atr"].initialized:
            return

        self.bar_counts[sym] += 1
        curr_bar_idx = self.bar_counts[sym]

        p_close = float(bar.close)
        mean = float(inds["bb"].middle)
        std = (float(inds["bb"].upper) - mean) / 2.0 if inds["bb"].upper > mean else 1.0
        z_score = (p_close - mean) / std
        atr = float(inds["atr"].value)

        # Convert bar epoch nanoseconds directly to IST hour & minute
        ts_ns = int(getattr(bar, 'ts_init', getattr(bar, 'ts_event', 0)))
        dt_ist = pd.to_datetime(ts_ns, unit='ns', utc=True).tz_convert('Asia/Kolkata')
        hour, minute = dt_ist.hour, dt_ist.minute

        # -------------------------------------------------------------
        # 1. POSITION MANAGEMENT & EXIT RULES
        # -------------------------------------------------------------
        if not self.portfolio.is_flat(inst_id):
            pos_info = self.entry_tracker.get(inst_id, {})
            entry_px = pos_info.get("entry_px", p_close)
            stop_px = pos_info.get("stop_px", entry_px - (2.0 * atr))
            entry_bar = pos_info.get("entry_bar", curr_bar_idx)

            # Exit A: Force square-off at or past 15:15 IST
            hit_eod = (hour == 15 and minute >= 15) or (hour > 15)

            # Exit B: Mean-Reversion Target (Z-score reverted to >= 0.0)
            
            # Dynamic Stop Ratchet: Only pull to breakeven AFTER crossing mean (Z >= 0.0)
            if z_score >= 0.0:
                stop_px = max(stop_px, entry_px)
                self.entry_tracker[inst_id]['stop_px'] = stop_px

            target_px = entry_px + (1.8 * atr)
            hit_target = (z_score >= 0.50) or (p_close >= target_px)

            # Closed-Loop Turbine Governor Evaluation BEFORE Exit Checks


            sym_raw = inst_id.value.split('.')[0] if hasattr(inst_id, 'value') else str(inst_id).split('.')[0]


            gov = self.bay_governors.get(sym_raw)


            bay_name = self.symbol_to_bay.get(sym_raw, 'CSTG1_BANKING_FINANCE')


            target_z = self.bay_profiles.get(bay_name, {}).get('target_zscore', 0.50)


            risk_ticks = bay_reg.get('stop_atr_mult', 1.80) * atr if 'bay_reg' in locals() else 1.80 * atr


            gov_trip = False


            if gov:


                u_effort = gov.update(current_z=z_score, target_z=target_z)


                stop_px = gov.get_dynamic_stop(entry_price=entry_px, current_stop=stop_px, risk_ticks=risk_ticks, current_z=z_score, target_z=target_z, u_effort=u_effort)


                self.entry_tracker[inst_id]['stop_px'] = stop_px


                # Emergency Governor Trip: If momentum continues plummeting with deep negative acceleration, trip early


                if z_score < -2.85:


                    gov_trip = True



            # Exit C: Dynamic ATR Stop Loss & Emergency Governor Trip


            hit_stop = (p_close <= stop_px) or gov_trip

            # Exit D: Time Horizon Stop (30 bars held)
            hit_time = (curr_bar_idx - entry_bar) >= 30

            if hit_eod or hit_target or hit_stop or hit_time:
                self.close_all_positions(inst_id)
                if inst_id in self.entry_tracker:
                    sym_raw = inst_id.value.split('.')[0] if hasattr(inst_id, 'value') else str(inst_id).split('.')[0]
                    if sym_raw in self.bay_governors:
                        self.bay_governors[sym_raw].reset()
                    del self.entry_tracker[inst_id]
                return


        # -------------------------------------------------------------
        # 2. ENTRY CONDITIONS (Strictly before 15:00 IST)
        # -------------------------------------------------------------
        if (hour == 15 and minute >= 0) or (hour > 15):
            return

        bay_name = self.mapping.get(sym, "CSTG_CORE_BANKING")
        bay_reg = self.bay_regs.get(bay_name, {})
        z_threshold = bay_reg.get("z_entry_threshold", -1.80)

        # Require price to reject the low / close green or above open to confirm reversal
        is_reversal_bar = float(bar.close) >= float(bar.open)
        if z_score <= z_threshold and is_reversal_bar and self.portfolio.is_flat(inst_id):
            grid_valve = self._compute_pid(
                self.master_grid.get("Kp_grid", 0.05),
                self.master_grid.get("Ki_grid", 0.30),
                self.master_grid.get("Kd_grid", 0.30),
                -z_score,
                self.grid_error_history,
                windup=self.master_grid.get("windup_limit", 2.0),
            )

            if sym not in self.bay_error_history:
                self.bay_error_history[sym] = []
            bay_valve = self._compute_pid(
                bay_reg.get("Kp", 0.05),
                bay_reg.get("Ki", 0.30),
                bay_reg.get("Kd", 0.30),
                -z_score,
                self.bay_error_history[sym],
            )

            net_valve = np.clip(grid_valve * bay_valve * 3.0, 0.10, 1.0)
            
            hrp_wt = self.hrp_weights.get(sym, 0.08)
            capital_alloc = self.config.base_capital * (hrp_wt / 0.08)
            order_qty = max(1, int((capital_alloc / p_close) * net_valve))

            order = self.order_factory.market(
                instrument_id=inst_id,
                order_side=OrderSide.BUY,
                quantity=self.cache.instrument(inst_id).make_qty(order_qty),
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(order)
            self.entry_tracker[inst_id] = {
                "entry_px": p_close,
                "entry_bar": curr_bar_idx,
                "stop_px": p_close - (2.0 * atr),
            }

    def on_stop(self) -> None:
        for instrument in self.cache.instruments():
            inst_id = instrument.id
            if not self.portfolio.is_flat(inst_id):
                self.close_all_positions(inst_id)
