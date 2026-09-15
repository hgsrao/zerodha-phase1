#!/usr/bin/env python3
"""
================================================================================
ECS STATE MANAGEMENT LAYER - INTEGRATED INTO R1
================================================================================

Black Box 17: State Persistence Layer
Black Box 18: Calibration Result Storage
Black Box 19: Result Archive

Durable state management with crash recovery:
- SQLite checkpoints for resuming mid-calibration
- JSON parameter archive (all iterations)
- Version control for best parameters
- Fast recovery on crash

================================================================================
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass, asdict
import hashlib

# ============================================================================
# STATE CHECKPOINT
# ============================================================================

@dataclass
class StateCheckpoint:
    """Single checkpoint of calibration state"""
    phase: str
    iteration: int
    best_win_rate: float
    best_parameters: Dict
    elapsed_hours: float
    timestamp: str
    parameters_hash: str  # SHA256 for integrity check

    def to_json(self) -> str:
        data = asdict(self)
        return json.dumps(data)

    @staticmethod
    def from_json(data: str) -> 'StateCheckpoint':
        d = json.loads(data)
        return StateCheckpoint(**d)

    def verify_integrity(self) -> bool:
        """Verify parameters hash matches"""
        params_str = json.dumps(self.best_parameters, sort_keys=True)
        calculated_hash = hashlib.sha256(params_str.encode()).hexdigest()
        return calculated_hash == self.parameters_hash


# ============================================================================
# STATE PERSISTENCE LAYER
# ============================================================================

class StatePersistenceLayer:
    """
    State Persistence Layer

    Saves calibration state at regular intervals.
    Enables crash recovery without losing progress.

    Strategy:
    - Save checkpoint every 10 iterations (or every hour, whichever first)
    - Store in SQLite for durability + JSON for human readability
    - Include hash for data integrity verification
    - Keep last 10 checkpoints for safety
    """

    def __init__(self, db_path: str = "calibration_state.db",
                 checkpoint_dir: str = "calibration_checkpoints"):
        self.db_path = db_path
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(exist_ok=True)

        self.logger = logging.getLogger("StatePersistenceLayer")
        self._lock = threading.RLock()

        # Configuration
        self.CHECKPOINT_INTERVAL_ITERATIONS = 10
        self.MAX_CHECKPOINTS_KEEP = 10

        self._init_database()

    def _init_database(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS state_checkpoints (
                checkpoint_id INTEGER PRIMARY KEY,
                phase TEXT,
                iteration INTEGER,
                best_win_rate REAL,
                best_parameters TEXT,
                elapsed_hours REAL,
                parameters_hash TEXT,
                timestamp TEXT,
                checkpoint_file TEXT,
                UNIQUE(iteration)
            )
        """)
        conn.commit()
        conn.close()
        self.logger.info("[OK] State persistence database initialized")

    def save_checkpoint(self, phase: str, iteration: int,
                       best_win_rate: float,
                       best_parameters: Dict,
                       elapsed_hours: float) -> bool:
        """
        Save calibration state checkpoint.

        Returns:
            True if saved successfully
        """
        try:
            with self._lock:
                # Calculate hash
                params_str = json.dumps(best_parameters, sort_keys=True)
                params_hash = hashlib.sha256(params_str.encode()).hexdigest()

                # Create checkpoint object
                checkpoint = StateCheckpoint(
                    phase=phase,
                    iteration=iteration,
                    best_win_rate=best_win_rate,
                    best_parameters=best_parameters,
                    elapsed_hours=elapsed_hours,
                    timestamp=datetime.now().isoformat(),
                    parameters_hash=params_hash
                )

                # Save to SQLite
                conn = sqlite3.connect(self.db_path)
                conn.execute("""
                    INSERT OR REPLACE INTO state_checkpoints
                    (phase, iteration, best_win_rate, best_parameters,
                     elapsed_hours, parameters_hash, timestamp, checkpoint_file)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    phase, iteration, best_win_rate,
                    json.dumps(best_parameters), elapsed_hours,
                    params_hash, checkpoint.timestamp,
                    f"checkpoint_{iteration}.json"
                ))
                conn.commit()
                conn.close()

                # Save to JSON file
                checkpoint_file = self.checkpoint_dir / f"checkpoint_{iteration}.json"
                with open(checkpoint_file, 'w') as f:
                    f.write(checkpoint.to_json())

                self.logger.info(
                    f"[OK] Checkpoint saved: iteration {iteration}, "
                    f"win_rate {best_win_rate:.2%}"
                )

                # Clean old checkpoints
                self._cleanup_old_checkpoints()

                return True

        except Exception as e:
            self.logger.error(f"[FAIL] Save checkpoint error: {str(e)}")
            return False

    def load_latest_checkpoint(self) -> Optional[StateCheckpoint]:
        """
        Load latest valid checkpoint for crash recovery.

        Returns:
            StateCheckpoint if found and valid, None otherwise
        """
        try:
            with self._lock:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.execute("""
                    SELECT phase, iteration, best_win_rate, best_parameters,
                           elapsed_hours, parameters_hash, timestamp
                    FROM state_checkpoints
                    ORDER BY iteration DESC
                    LIMIT 1
                """)
                row = cursor.fetchone()
                conn.close()

                if not row:
                    self.logger.info("[OK] No previous checkpoint found")
                    return None

                checkpoint = StateCheckpoint(
                    phase=row[0],
                    iteration=row[1],
                    best_win_rate=row[2],
                    best_parameters=json.loads(row[3]),
                    elapsed_hours=row[4],
                    parameters_hash=row[5],
                    timestamp=row[6]
                )

                # Verify integrity
                if not checkpoint.verify_integrity():
                    self.logger.error("[FAIL] Checkpoint integrity check failed")
                    return None

                self.logger.info(
                    f"[OK] Loaded checkpoint from iteration {checkpoint.iteration}, "
                    f"win_rate {checkpoint.best_win_rate:.2%}"
                )

                return checkpoint

        except Exception as e:
            self.logger.error(f"[FAIL] Load checkpoint error: {str(e)}")
            return None

    def get_checkpoint_list(self) -> List[Dict]:
        """Get list of all saved checkpoints"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT iteration, best_win_rate, phase, timestamp
                FROM state_checkpoints
                ORDER BY iteration DESC
            """)
            checkpoints = [
                {
                    'iteration': row[0],
                    'win_rate': row[1],
                    'phase': row[2],
                    'timestamp': row[3]
                }
                for row in cursor.fetchall()
            ]
            conn.close()
            return checkpoints

    def _cleanup_old_checkpoints(self):
        """Keep only last N checkpoints"""
        try:
            checkpoints = self.get_checkpoint_list()

            if len(checkpoints) > self.MAX_CHECKPOINTS_KEEP:
                # Delete oldest checkpoints
                to_delete = len(checkpoints) - self.MAX_CHECKPOINTS_KEEP

                for checkpoint in checkpoints[-to_delete:]:
                    iteration = checkpoint['iteration']
                    checkpoint_file = self.checkpoint_dir / f"checkpoint_{iteration}.json"

                    if checkpoint_file.exists():
                        checkpoint_file.unlink()

                    conn = sqlite3.connect(self.db_path)
                    conn.execute(
                        "DELETE FROM state_checkpoints WHERE iteration = ?",
                        (iteration,)
                    )
                    conn.commit()
                    conn.close()

                self.logger.info(
                    f"[OK] Cleaned {to_delete} old checkpoints, kept {self.MAX_CHECKPOINTS_KEEP}"
                )

        except Exception as e:
            self.logger.warning(f"[WARN] Cleanup error: {str(e)}")


# ============================================================================
# CALIBRATION RESULT STORAGE
# ============================================================================

class CalibrationResultStorage:
    """
    Calibration Result Storage

    Stores all iterations from a calibration run.
    Enables post-analysis and parameter history.

    Structure:
    - Store each iteration with parameters and metrics
    - Group by calibration run ID
    - Support querying best N parameters
    """

    def __init__(self, db_path: str = "calibration_results.db"):
        self.db_path = db_path
        self.logger = logging.getLogger("CalibrationResultStorage")
        self._lock = threading.RLock()

        self._init_database()

    def _init_database(self):
        """Initialize results database"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration_runs (
                run_id TEXT PRIMARY KEY,
                start_timestamp TEXT,
                end_timestamp TEXT,
                total_iterations INTEGER,
                best_win_rate REAL,
                best_phase TEXT,
                duration_hours REAL,
                description TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS run_iterations (
                iteration_id INTEGER PRIMARY KEY,
                run_id TEXT,
                iteration INTEGER,
                phase TEXT,
                parameters TEXT,
                win_rate REAL,
                sharpe_ratio REAL,
                max_drawdown REAL,
                total_trades INTEGER,
                is_best INTEGER,
                timestamp TEXT,
                FOREIGN KEY(run_id) REFERENCES calibration_runs(run_id)
            )
        """)
        conn.commit()
        conn.close()
        self.logger.info("[OK] Results storage database initialized")

    def create_run(self, description: str = "") -> str:
        """
        Create new calibration run record.

        Returns:
            run_id (timestamp-based)
        """
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO calibration_runs
                (run_id, start_timestamp, description)
                VALUES (?, ?, ?)
            """, (run_id, datetime.now().isoformat(), description))
            conn.commit()
            conn.close()

        self.logger.info(f"[OK] Created run: {run_id}")
        return run_id

    def record_iteration(self, run_id: str, iteration: int,
                        phase: str, parameters: Dict,
                        win_rate: float, sharpe_ratio: float,
                        max_drawdown: float, total_trades: int,
                        is_best: bool):
        """Record iteration result"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO run_iterations
                (run_id, iteration, phase, parameters, win_rate,
                 sharpe_ratio, max_drawdown, total_trades, is_best, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id, iteration, phase, json.dumps(parameters),
                win_rate, sharpe_ratio, max_drawdown, total_trades,
                1 if is_best else 0, datetime.now().isoformat()
            ))
            conn.commit()
            conn.close()

    def finalize_run(self, run_id: str, best_win_rate: float,
                    best_phase: str, total_iterations: int,
                    duration_hours: float):
        """Mark run as complete"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                UPDATE calibration_runs
                SET end_timestamp = ?, best_win_rate = ?, best_phase = ?,
                    total_iterations = ?, duration_hours = ?
                WHERE run_id = ?
            """, (
                datetime.now().isoformat(), best_win_rate, best_phase,
                total_iterations, duration_hours, run_id
            ))
            conn.commit()
            conn.close()

        self.logger.info(
            f"[OK] Finalized run {run_id}: {best_win_rate:.2%} "
            f"in {total_iterations} iterations ({duration_hours:.2f}h)"
        )

    def get_best_parameters(self, run_id: str) -> Optional[Dict]:
        """Get best parameters from a run"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT parameters FROM run_iterations
                WHERE run_id = ? AND is_best = 1
                ORDER BY win_rate DESC
                LIMIT 1
            """, (run_id,))
            row = cursor.fetchone()
            conn.close()

            if row:
                return json.loads(row[0])
            return None

    def get_run_summary(self, run_id: str) -> Optional[Dict]:
        """Get summary of a calibration run"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT run_id, start_timestamp, end_timestamp,
                       total_iterations, best_win_rate, best_phase, duration_hours
                FROM calibration_runs
                WHERE run_id = ?
            """, (run_id,))
            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            return {
                'run_id': row[0],
                'start_timestamp': row[1],
                'end_timestamp': row[2],
                'total_iterations': row[3],
                'best_win_rate': row[4],
                'best_phase': row[5],
                'duration_hours': row[6]
            }

    def list_all_runs(self) -> List[Dict]:
        """List all calibration runs"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT run_id, start_timestamp, total_iterations,
                       best_win_rate, duration_hours
                FROM calibration_runs
                ORDER BY start_timestamp DESC
            """)
            runs = [
                {
                    'run_id': row[0],
                    'start': row[1],
                    'iterations': row[2],
                    'best_wr': row[3],
                    'duration_h': row[4]
                }
                for row in cursor.fetchall()
            ]
            conn.close()
            return runs

    def export_run_to_json(self, run_id: str, filename: str):
        """Export calibration run to JSON"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)

            # Get run summary
            cursor = conn.execute(
                "SELECT * FROM calibration_runs WHERE run_id = ?",
                (run_id,)
            )
            run_row = cursor.fetchone()

            # Get all iterations
            cursor = conn.execute("""
                SELECT iteration, phase, parameters, win_rate,
                       sharpe_ratio, max_drawdown, total_trades, is_best
                FROM run_iterations
                WHERE run_id = ?
                ORDER BY iteration
            """, (run_id,))
            iterations = cursor.fetchall()

            conn.close()

            # Format for export
            export_data = {
                'run_id': run_row[0],
                'start_timestamp': run_row[1],
                'end_timestamp': run_row[2],
                'total_iterations': run_row[3],
                'best_win_rate': run_row[4],
                'best_phase': run_row[5],
                'duration_hours': run_row[6],
                'iterations': [
                    {
                        'iteration': it[0],
                        'phase': it[1],
                        'parameters': json.loads(it[2]),
                        'win_rate': it[3],
                        'sharpe_ratio': it[4],
                        'max_drawdown': it[5],
                        'total_trades': it[6],
                        'is_best': bool(it[7])
                    }
                    for it in iterations
                ]
            }

            with open(filename, 'w') as f:
                json.dump(export_data, f, indent=2)

            self.logger.info(f"[OK] Exported run {run_id} to {filename}")


# ============================================================================
# RESULT ARCHIVE
# ============================================================================

class ResultArchive:
    """
    Result Archive

    Maintains historical archive of all calibration runs.
    Tracks parameter evolution over time.
    Supports comparison between runs.
    """

    def __init__(self, archive_dir: str = "calibration_archive"):
        self.archive_dir = Path(archive_dir)
        self.archive_dir.mkdir(exist_ok=True)

        self.logger = logging.getLogger("ResultArchive")
        self._lock = threading.RLock()

    def archive_run(self, run_id: str, run_data: Dict) -> bool:
        """
        Archive a calibration run.

        Args:
            run_id: Unique run identifier
            run_data: Complete run data (from ResultStorage export)

        Returns:
            True if archived successfully
        """
        try:
            with self._lock:
                archive_file = self.archive_dir / f"{run_id}_archive.json"

                with open(archive_file, 'w') as f:
                    json.dump(run_data, f, indent=2)

                self.logger.info(f"[OK] Archived run {run_id} to {archive_file}")
                return True

        except Exception as e:
            self.logger.error(f"[FAIL] Archive error: {str(e)}")
            return False

    def get_archived_run(self, run_id: str) -> Optional[Dict]:
        """Load archived run"""
        try:
            archive_file = self.archive_dir / f"{run_id}_archive.json"

            if not archive_file.exists():
                return None

            with open(archive_file, 'r') as f:
                data = json.load(f)

            return data

        except Exception as e:
            self.logger.error(f"[FAIL] Load archive error: {str(e)}")
            return None

    def list_archived_runs(self) -> List[str]:
        """List all archived run IDs"""
        archive_files = sorted(self.archive_dir.glob("*_archive.json"))
        run_ids = [f.stem.replace("_archive", "") for f in archive_files]
        return run_ids

    def compare_runs(self, run_id_1: str, run_id_2: str) -> Dict:
        """
        Compare two calibration runs.

        Returns metrics like parameter differences, improvement, etc.
        """
        run1 = self.get_archived_run(run_id_1)
        run2 = self.get_archived_run(run_id_2)

        if not run1 or not run2:
            return {}

        # Compare best parameters
        params1 = run1['iterations'][-1]['parameters'] if run1['iterations'] else {}
        params2 = run2['iterations'][-1]['parameters'] if run2['iterations'] else {}

        # Find differences
        differences = {}
        for param_name in params1.keys():
            val1 = params1.get(param_name, 0)
            val2 = params2.get(param_name, 0)
            if val1 != val2:
                differences[param_name] = {
                    'run1': val1,
                    'run2': val2,
                    'change_percent': ((val2 - val1) / max(abs(val1), 1)) * 100
                }

        return {
            'run1_id': run_id_1,
            'run2_id': run_id_2,
            'run1_best_wr': run1['best_win_rate'],
            'run2_best_wr': run2['best_win_rate'],
            'improvement': (run2['best_win_rate'] - run1['best_win_rate']) * 100,
            'parameter_differences': differences,
            'run1_duration_h': run1['duration_hours'],
            'run2_duration_h': run2['duration_hours']
        }


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test state persistence
    print("\n=== TESTING STATE PERSISTENCE ===")
    persistence = StatePersistenceLayer()

    checkpoint = persistence.save_checkpoint(
        phase="phase_1",
        iteration=50,
        best_win_rate=0.55,
        best_parameters={'param1': 0.5, 'param2': 1.0},
        elapsed_hours=2.5
    )
    print(f"Saved checkpoint: {checkpoint}")

    # Test result storage
    print("\n=== TESTING RESULT STORAGE ===")
    storage = CalibrationResultStorage()

    run_id = storage.create_run(description="Test calibration")
    print(f"Created run: {run_id}")

    storage.record_iteration(
        run_id=run_id,
        iteration=1,
        phase="phase_1",
        parameters={'param1': 0.5},
        win_rate=0.52,
        sharpe_ratio=1.0,
        max_drawdown=0.15,
        total_trades=100,
        is_best=True
    )
    print(f"Recorded iteration 1")

    # Test archive
    print("\n=== TESTING RESULT ARCHIVE ===")
    archive = ResultArchive()
    print(f"Archive directory: {archive.archive_dir}")

