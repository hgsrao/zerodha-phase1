#!/usr/bin/env python3
"""
================================================================================
ECS CALIBRATION ORCHESTRATOR - INTEGRATED INTO R1
================================================================================

Black Box 1: Meta-Learning Loop Orchestrator
Black Box 2: Stage 2 Calibration Engine
Black Box 3: Phase Manager

Orchestrates entire calibration workflow as background process alongside live trading.

Key Features:
- 3-phase calibration (Random → Bayesian → Fine-tune)
- Exclusive mode lock (LIVE ⊕ CALIBRATION)
- Parameter snapshots for safe reads during trading
- Crash recovery from checkpoints
- Real-time progress tracking

================================================================================
"""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from enum import Enum
import sqlite3
from dataclasses import dataclass, asdict
import hashlib

# ============================================================================
# ENUMS & DATA CLASSES
# ============================================================================

class CalibrationPhase(Enum):
    """3-phase calibration workflow"""
    PHASE_1_EXPLORATION = "phase_1_exploration"
    PHASE_2_BAYESIAN = "phase_2_bayesian"
    PHASE_3_FINE_TUNING = "phase_3_fine_tuning"
    COMPLETE = "complete"
    PAUSED = "paused"
    FAILED = "failed"

class OperatingMode(Enum):
    """Exclusive mode lock - cannot run both simultaneously"""
    LIVE_TRADING = "live_trading"
    CALIBRATION = "calibration"
    IDLE = "idle"

@dataclass
class CalibrationCheckpoint:
    """Crash recovery checkpoint"""
    phase: str
    iteration: int
    timestamp: str
    best_win_rate: float
    best_params: Dict
    elapsed_hours: float

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(data: str) -> 'CalibrationCheckpoint':
        d = json.loads(data)
        return CalibrationCheckpoint(**d)

@dataclass
class IterationResult:
    """One iteration's metrics"""
    iteration: int
    phase: str
    parameters: Dict
    win_rate: float
    total_trades: int
    sharpe_ratio: float
    max_drawdown: float
    elapsed_hours: float
    timestamp: str

# ============================================================================
# ORCHESTRATION LAYER
# ============================================================================

class ECSCalibratorOrchestrator:
    """
    Meta-Learning Loop Orchestrator
    Controls entire calibration workflow with 3 phases
    """

    def __init__(self,
                 ecs_system,
                 db_path: str = "calibration_state.db",
                 checkpoint_dir: str = "calibration_checkpoints"):

        self.ecs_system = ecs_system
        self.db_path = db_path
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(exist_ok=True)

        # Logging
        self.logger = logging.getLogger("ECSCalibratorOrchestrator")
        handler = logging.FileHandler("ecs_calibrator.log")
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - [ORCHESTRATOR] - %(message)s'
        ))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

        # State management
        self._lock = threading.RLock()
        self._current_phase = CalibrationPhase.PHASE_1_EXPLORATION
        self._current_mode = OperatingMode.IDLE
        self._iteration_count = 0
        self._start_time = None
        self._best_win_rate = 0.5175  # Stage 1 baseline
        self._best_params = None
        self._is_running = False

        # Database
        self._init_database()

        self.logger.info("[OK] ECSCalibratorOrchestrator initialized")

    def _init_database(self):
        """Initialize SQLite for durable state"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration_iterations (
                iteration_id INTEGER PRIMARY KEY,
                phase TEXT,
                iteration INTEGER,
                parameters TEXT,
                win_rate REAL,
                total_trades INTEGER,
                sharpe_ratio REAL,
                max_drawdown REAL,
                elapsed_hours REAL,
                timestamp TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration_state (
                state_id INTEGER PRIMARY KEY,
                phase TEXT,
                iteration INTEGER,
                best_win_rate REAL,
                best_params TEXT,
                elapsed_hours REAL,
                updated_at TEXT
            )
        """)
        conn.commit()
        conn.close()
        self.logger.info("[OK] Database initialized")

    # ========================================================================
    # EXCLUSIVE MODE LOCK - SAFE CONCURRENT ACCESS
    # ========================================================================

    def acquire_mode(self, mode: OperatingMode, timeout_seconds: int = 30) -> bool:
        """
        Acquire exclusive mode lock.
        Only ONE of LIVE_TRADING or CALIBRATION can run at a time.

        Returns: True if acquired, False if timeout
        """
        deadline = time.time() + timeout_seconds

        while time.time() < deadline:
            with self._lock:
                if self._current_mode == OperatingMode.IDLE:
                    self._current_mode = mode
                    self.logger.info(f"[MODE] Acquired {mode.value}")
                    return True

            time.sleep(0.1)

        self.logger.error(f"[FAIL] Could not acquire {mode.value} (timeout)")
        return False

    def release_mode(self):
        """Release exclusive mode lock"""
        with self._lock:
            old_mode = self._current_mode
            self._current_mode = OperatingMode.IDLE
            self.logger.info(f"[MODE] Released {old_mode.value}")

    def can_trade_live(self) -> bool:
        """Check if live trading is safe (not in CALIBRATION mode)"""
        with self._lock:
            return self._current_mode != OperatingMode.CALIBRATION

    def can_calibrate(self) -> bool:
        """Check if calibration is safe (not in LIVE_TRADING mode)"""
        with self._lock:
            return self._current_mode != OperatingMode.LIVE_TRADING

    # ========================================================================
    # PHASE MANAGEMENT
    # ========================================================================

    def get_current_phase(self) -> CalibrationPhase:
        """Get current calibration phase"""
        with self._lock:
            return self._current_phase

    def transition_phase(self, new_phase: CalibrationPhase):
        """Transition to next calibration phase"""
        with self._lock:
            old_phase = self._current_phase
            self._current_phase = new_phase
            self.logger.info(f"[PHASE] {old_phase.value} → {new_phase.value}")

            # Save checkpoint
            self._save_checkpoint()

    def _save_checkpoint(self):
        """Save crash recovery checkpoint"""
        checkpoint = CalibrationCheckpoint(
            phase=self._current_phase.value,
            iteration=self._iteration_count,
            timestamp=datetime.now().isoformat(),
            best_win_rate=self._best_win_rate,
            best_params=self._best_params,
            elapsed_hours=self._get_elapsed_hours()
        )

        checkpoint_file = self.checkpoint_dir / f"checkpoint_{self._iteration_count}.json"
        with open(checkpoint_file, 'w') as f:
            f.write(checkpoint.to_json())

        self.logger.info(f"[CHECKPOINT] Saved at iteration {self._iteration_count}")

    def load_checkpoint(self) -> Optional[CalibrationCheckpoint]:
        """Load latest checkpoint for crash recovery"""
        checkpoints = sorted(self.checkpoint_dir.glob("checkpoint_*.json"))
        if not checkpoints:
            return None

        latest = checkpoints[-1]
        with open(latest, 'r') as f:
            checkpoint = CalibrationCheckpoint.from_json(f.read())

        self.logger.info(f"[RECOVERY] Loaded checkpoint from iteration {checkpoint.iteration}")
        return checkpoint

    # ========================================================================
    # ITERATION TRACKING
    # ========================================================================

    def record_iteration(self, result: IterationResult):
        """Record iteration metrics to database"""
        with self._lock:
            self._iteration_count = result.iteration

            # Update best if improvement
            if result.win_rate > self._best_win_rate:
                self._best_win_rate = result.win_rate
                self._best_params = result.parameters
                self.logger.info(f"[BEST] Iteration {result.iteration}: {result.win_rate:.2%}")

            # Store to database
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO calibration_iterations
                (phase, iteration, parameters, win_rate, total_trades, sharpe_ratio,
                 max_drawdown, elapsed_hours, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result.phase,
                result.iteration,
                json.dumps(result.parameters),
                result.win_rate,
                result.total_trades,
                result.sharpe_ratio,
                result.max_drawdown,
                result.elapsed_hours,
                result.timestamp
            ))
            conn.commit()
            conn.close()

    def get_iteration_count(self) -> int:
        """Get total iterations run so far"""
        with self._lock:
            return self._iteration_count

    def get_best_params(self) -> Optional[Dict]:
        """Get best parameters found so far"""
        with self._lock:
            return self._best_params.copy() if self._best_params else None

    def get_best_win_rate(self) -> float:
        """Get best win rate found so far"""
        with self._lock:
            return self._best_win_rate

    # ========================================================================
    # TIMING & MONITORING
    # ========================================================================

    def start(self):
        """Mark calibration start"""
        with self._lock:
            self._start_time = datetime.now()
            self._is_running = True
            self.logger.info("[START] Calibration started")

    def stop(self):
        """Mark calibration stop"""
        with self._lock:
            self._is_running = False
            elapsed = self._get_elapsed_hours()
            self.logger.info(f"[STOP] Calibration stopped after {elapsed:.2f} hours")

    def _get_elapsed_hours(self) -> float:
        """Calculate elapsed time since start"""
        if not self._start_time:
            return 0.0
        return (datetime.now() - self._start_time).total_seconds() / 3600

    def get_elapsed_hours(self) -> float:
        """Public accessor for elapsed time"""
        with self._lock:
            return self._get_elapsed_hours()

    def is_running(self) -> bool:
        """Check if calibration is running"""
        with self._lock:
            return self._is_running

    # ========================================================================
    # PARAMETER SNAPSHOTS - SAFE READS DURING TRADING
    # ========================================================================

    def get_parameter_snapshot(self) -> Dict:
        """
        Get consistent snapshot of current best parameters.
        Safe to use during live trading (thread-safe read).
        """
        with self._lock:
            if self._best_params is None:
                # Return defaults if no calibration run yet
                return self._get_default_params()
            return self._best_params.copy()

    def _get_default_params(self) -> Dict:
        """Return system default parameters"""
        return {
            'base_dp_dt_multiplier': 1.0,
            'base_dv_dt_multiplier': 1.0,
            'entry_confidence_threshold': 0.5,
            'exit_confidence_threshold': 0.6,
            # ... all 33 parameters with defaults
        }

    # ========================================================================
    # MONITORING & DIAGNOSTICS
    # ========================================================================

    def get_status(self) -> Dict:
        """Get comprehensive status"""
        with self._lock:
            return {
                'phase': self._current_phase.value,
                'mode': self._current_mode.value,
                'iterations': self._iteration_count,
                'best_win_rate': f"{self._best_win_rate:.2%}",
                'elapsed_hours': f"{self._get_elapsed_hours():.2f}",
                'is_running': self._is_running,
                'timestamp': datetime.now().isoformat()
            }

    def log_status(self):
        """Log current status"""
        status = self.get_status()
        self.logger.info(f"[STATUS] {json.dumps(status)}")


# ============================================================================
# PHASE MANAGER - Handles Phase Transitions
# ============================================================================

class CalibrationPhaseManager:
    """
    Phase Manager
    Controls transition: Random → Bayesian → Fine-tuning
    """

    def __init__(self, orchestrator: ECSCalibratorOrchestrator):
        self.orchestrator = orchestrator
        self.logger = logging.getLogger("PhaseManager")

        # Phase configuration
        self.phase_config = {
            CalibrationPhase.PHASE_1_EXPLORATION: {
                'duration_hours': 8,
                'iterations': 100,
                'description': 'Random parameter exploration'
            },
            CalibrationPhase.PHASE_2_BAYESIAN: {
                'duration_hours': 10,
                'iterations': 200,
                'description': 'Bayesian optimization (scipy differential_evolution)'
            },
            CalibrationPhase.PHASE_3_FINE_TUNING: {
                'duration_hours': 6,
                'iterations': 100,
                'description': 'Fine-tuning around best parameters'
            }
        }

    def should_transition(self, current_phase: CalibrationPhase,
                         elapsed_hours: float) -> Tuple[bool, Optional[CalibrationPhase]]:
        """
        Determine if phase transition needed
        Returns: (should_transition, next_phase)
        """
        if current_phase not in self.phase_config:
            return False, None

        config = self.phase_config[current_phase]

        if elapsed_hours >= config['duration_hours']:
            # Move to next phase
            if current_phase == CalibrationPhase.PHASE_1_EXPLORATION:
                return True, CalibrationPhase.PHASE_2_BAYESIAN
            elif current_phase == CalibrationPhase.PHASE_2_BAYESIAN:
                return True, CalibrationPhase.PHASE_3_FINE_TUNING
            elif current_phase == CalibrationPhase.PHASE_3_FINE_TUNING:
                return True, CalibrationPhase.COMPLETE

        return False, None

    def get_phase_description(self, phase: CalibrationPhase) -> str:
        """Get human-readable phase description"""
        if phase in self.phase_config:
            return self.phase_config[phase]['description']
        return "Unknown phase"


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Example usage
    orchestrator = ECSCalibratorOrchestrator(ecs_system=None)

    # Test exclusive mode lock
    print("Acquiring CALIBRATION mode...")
    acquired = orchestrator.acquire_mode(OperatingMode.CALIBRATION, timeout_seconds=5)
    print(f"Acquired: {acquired}")

    # Simulate some iterations
    orchestrator.start()
    for i in range(5):
        result = IterationResult(
            iteration=i+1,
            phase="phase_1",
            parameters={'param1': 0.5, 'param2': 1.0},
            win_rate=0.52 + (i * 0.01),
            total_trades=100 + (i * 10),
            sharpe_ratio=1.5 + (i * 0.1),
            max_drawdown=0.15 - (i * 0.01),
            elapsed_hours=i * 1.0,
            timestamp=datetime.now().isoformat()
        )
        orchestrator.record_iteration(result)
        time.sleep(0.5)

    orchestrator.stop()
    orchestrator.log_status()

    # Release mode
    orchestrator.release_mode()
    print("Released CALIBRATION mode")

