#!/usr/bin/env python3
"""
kite_dual_engine_ccpp_plant.py
==============================
ECS Combined Cycle Power Plant (CCPP) Architecture:
- Supervisory Layer: ECSTradingSupervisor (Phase Angle Lockout, Speed Droop, Bus Voltage Sizing)
- Dual Engine Controller:
    * Engine A (Fast Intraday MIS, 40% MW Load): GTG1 (AUTO), GTG2 (TECH)
    * Engine B (Baseload Swing CNC, 60% MW Load): CSTG1 (FIN), CSTG2 (METALS/HEAVY)
- Isolated State DB: paper_trading_dual_engine.db
"""

import os
import sys
import json
import time
import sqlite3
import logging
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Dict, Any

import numpy as np
import pandas as pd
from kiteconnect import KiteConnect

from run_stage1_full_ecs_dcs import ECSTradingSupervisor

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
    handlers=[
        logging.FileHandler("dual_engine_plant.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("CCPP_DualPlant")

DB_PATH = Path("paper_trading_dual_engine.db")

# Turbine / Sector Topology
TURBINE_TOPOLOGY = {
    "GTG1_AUTO": {"engine": "ENGINE_A", "symbols": ["TATAMOTORS"]},
    "GTG2_TECH": {"engine": "ENGINE_A", "symbols": ["TCS", "INFY"]},
    "CSTG1_FIN": {"engine": "ENGINE_B", "symbols": ["HDFCBANK", "BAJFINANCE"]},
    "CSTG2_METALS": {"engine": "ENGINE_B", "symbols": ["TATASTEEL", "HINDALCO", "BEL"]},
    # --------------------------------------------------------------------------
    # BPSTG: Back-Pressure Steam Turbine (Deep Baseload, Stiff Droop: 8-10%)
    # Exhausts against process header. High inertia, wide stops, noise-filtered.
    # --------------------------------------------------------------------------
    "BPSTG_DEFENSIVE": {
        "engine": "ENGINE_B",
        "droop": 0.09,          # 9% Nominal Droop
        "z_entry": -2.35,       # Deep Z-Score barrier to reject tick chop
        "max_slots": 2,
        "holding_time_min": 180,
        "symbols": [
            "SUNPHARMA", "CIPLA", "DRREDDY", "DIVISLAB", "APOLLOHOSP",
            "ITC", "HINDUNILVR", "NESTLEIND", "BRITANNIA"
        ]
    },

}

# Auto-build watchlist across all configured turbines + NIFTY benchmark
_all_syms = set()
for t_data in TURBINE_TOPOLOGY.values():
    for s in t_data.get('symbols', []):
        _all_syms.add(f'NSE:{s}')
# Auto-build watchlist across all configured turbines + NIFTY benchmark
_all_syms = set()
for t_data in TURBINE_TOPOLOGY.values():
    for s in t_data.get('symbols', []):
        _all_syms.add(f'NSE:{s}')
ALL_WATCHLIST = ['NSE:NIFTY 50'] + sorted(list(_all_syms)) + sorted(list(_all_syms))

def load_credentials():
    ak = os.getenv("KITE_API_KEY")
    at = os.getenv("KITE_ACCESS_TOKEN")
    if ak and at and "YOUR_" not in ak:
        return ak, at

    for env_file in [".env", ".env_kite", "kite_session.env"]:
        p = Path(env_file)
        if p.exists():
            txt = p.read_text()
            import re
            ak = re.search(r"(?:KITE_API_KEY|API_KEY)\s*=\s*[\"']?([^\"'\s\r\n]+)", txt)
            at = re.search(r"(?:KITE_ACCESS_TOKEN|ACCESS_TOKEN)\s*=\s*[\"']?([^\"'\s\r\n]+)", txt)
            if ak and at and "YOUR_" not in ak.group(1):
                return ak.group(1), at.group(1)
    
    p = Path("credentials.json")
    if p.exists():
        with open(p) as f:
            d = json.load(f)
            ak = d.get("api_key")
            at = d.get("access_token")
            if ak and at and "YOUR_" not in ak:
                return ak, at
    raise FileNotFoundError("Could not find valid credentials in .env or credentials.json")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS open_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            engine TEXT,
            turbine TEXT,
            symbol TEXT,
            entry_price REAL,
            qty INTEGER,
            stop_price REAL,
            target_price REAL,
            entry_time TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS closed_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            engine TEXT,
            turbine TEXT,
            symbol TEXT,
            entry_price REAL,
            exit_price REAL,
            qty INTEGER,
            pnl REAL,
            reason TEXT,
            exit_time TEXT
        )
    """)
    conn.commit()
    conn.close()

class CCPPDualEnginePlant:
    def __init__(self):
        self.api_key, self.access_token = load_credentials()
        self.kite = KiteConnect(api_key=self.api_key)
        self.kite.set_access_token(self.access_token)
        self.supervisor = ECSTradingSupervisor()
        self.history: Dict[str, list] = {s: [] for s in ALL_WATCHLIST}
        init_db()
        logger.info("CCPP Dual-Engine Plant Initialized (5 Sectors: GTG1, GTG2, CSTG1, CSTG2, BPSTG)")

    def run_cycle(self):
        quotes = self.kite.quote(ALL_WATCHLIST)
        now_dt = datetime.now()
        cur_t = now_dt.time()

        # Update historical cache
        for s in ALL_WATCHLIST:
            if s in quotes:
                self.history[s].append({
                    "price": quotes[s]["last_price"],
                    "volume": quotes[s].get("volume", 1.0)
                })
                if len(self.history[s]) > 60:
                    self.history[s].pop(0)

        # 1. Evaluate Grid Conditions (NIFTY 50 Velocity)
        nifty_vel = 0.0
        nifty_hist = self.history["NSE:NIFTY 50"]
        if len(nifty_hist) >= 2:
            p_curr = nifty_hist[-1]["price"]
            p_prev = nifty_hist[-2]["price"]
            nifty_vel = (p_curr - p_prev) / p_prev

        # 2. Check and Manage Open Positions
        self.manage_open_positions(quotes, cur_t, now_dt)

        # Mandatory 15:15 Squareoff guard
        if cur_t >= dtime(15, 15):
            logger.info("Past 15:15 IST. Intraday dispatch barred.")
            return

        # 3. Check Turbine Slot Availability
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT engine, turbine, symbol FROM open_positions")
        active = c.fetchall()
        active_eng_a = len([x for x in active if x[0] == 'ENGINE_A'])
        active_eng_b = len([x for x in active if x[0] == 'ENGINE_B'])
        active_syms = set([x[2] for x in active])
        conn.close()

        # 4. Sector Scanning & Dispatch
        for turbine, conf in TURBINE_TOPOLOGY.items():
            engine = conf["engine"]
            if engine == "ENGINE_A" and active_eng_a >= 2:
                continue
            # Check if this specific turbine sector already has an open position
            active_turbines = [p.get("turbine") for p in open_positions.values()]
            if turbine in active_turbines:
                continue

            if engine == "ENGINE_B" and active_eng_b >= 4:
                continue

            for sym in conf["symbols"]:
                if sym in active_syms:
                    continue

                full_key = f"NSE:{sym}"
                if full_key not in quotes or len(self.history[full_key]) < 5:
                    continue

                hist = self.history[full_key]
                p_now = hist[-1]["price"]
                p_old = hist[-2]["price"]
                v_now = hist[-1]["volume"]
                v_old = hist[-2]["volume"]

                dp_dt = p_now - p_old
                dv_dt = v_now - v_old
                vol_ratio = v_now / max(v_old, 1.0)

                # Supervisor Authority Check (Phase Angle + Grid Speed + Bus Voltage)
                authorized, size_mul, reason = self.supervisor.evaluate_signals(
                    dp_dt=dp_dt, dv_dt=dv_dt, vol_ratio=vol_ratio, nifty_vel=nifty_vel
                )

                if not authorized:
                    logger.info(f"[{turbine}] {sym} Dispatch Blocked by DCS Supervisor: {reason}")
                    continue

                # Baseline Generator Dispatch Logic
                prices = [x["price"] for x in hist]
                ma = np.mean(prices)
                std = np.std(prices) if np.std(prices) > 0 else 1.0
                z_score = (p_now - ma) / std

                # Dip-reversion dispatch threshold
                if z_score <= -1.8:
                    qty = int(max(1, (100000.0 / p_now) * size_mul))
                    stop_p = round(p_now * 0.985, 2)
                    target_p = round(p_now * 1.025, 2)

                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("""
                        INSERT INTO open_positions (engine, turbine, symbol, entry_price, qty, stop_price, target_price, entry_time)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (engine, turbine, sym, p_now, qty, stop_p, target_p, now_dt.strftime("%Y-%m-%d %H:%M:%S")))
                    conn.commit()
                    conn.close()

                    logger.info(f"⚡ DISPATCH: [{engine}|{turbine}] BUY {qty} {sym} @ ₹{p_now:.2f} (Sizing: {size_mul:.2f}x, Z: {z_score:.2f})")
                    active_syms.add(sym)
                    if engine == "ENGINE_A":
                        active_eng_a += 1
                    else:
                        active_eng_b += 1

    def manage_open_positions(self, quotes, cur_t, now_dt):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id, engine, turbine, symbol, entry_price, qty, stop_price, target_price FROM open_positions")
        rows = c.fetchall()

        for pos in rows:
            p_id, eng, turb, sym, entry_p, qty, stop_p, tgt_p = pos
            full_key = f"NSE:{sym}"
            if full_key not in quotes:
                continue

            ltp = quotes[full_key]["last_price"]
            closed = False
            reason = ""
            exit_p = ltp

            if cur_t >= dtime(15, 15) and eng == "ENGINE_A":
                closed = True
                reason = "MANDATORY_1515_SQUAREOFF"
            elif ltp >= tgt_p:
                closed = True
                reason = "TARGET_HIT"
            elif ltp <= stop_p:
                closed = True
                reason = "STOP_LOSS"

            if closed:
                pnl = round((exit_p - entry_p) * qty, 2)
                c.execute("""
                    INSERT INTO closed_trades (engine, turbine, symbol, entry_price, exit_price, qty, pnl, reason, exit_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (eng, turb, sym, entry_p, exit_p, qty, pnl, reason, now_dt.strftime("%Y-%m-%d %H:%M:%S")))
                c.execute("DELETE FROM open_positions WHERE id = ?", (p_id,))
                conn.commit()
                logger.info(f"🔒 CLOSED: [{eng}|{turb}] {sym} | P&L: ₹{pnl:+,.2f} | Reason: {reason}")

        conn.close()

if __name__ == "__main__":
    plant = CCPPDualEnginePlant()
    logger.info("Entering DCS Cycle loop (60s tick interval)...")
    while True:
        try:
            plant.run_cycle()
        except Exception as e:
            logger.error(f"Cycle Exception: {e}", exc_info=True)
        time.sleep(60)
