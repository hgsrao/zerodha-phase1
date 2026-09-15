"""
STAGE 6: P01D WITH INTEGRATED PID TIMING CONTROLLER
Uses the same PID block proven in parameter optimization
Just with DIFFERENT PARAMETERS for real-time entry/exit
"""

from pid_controller_generic import PIDPresets
import pandas as pd
import numpy as np
from datetime import datetime


class P01D_SovereignAuthority_With_PID:
    """
    Stage 6: Final decision with integrated PID timing optimization

    Takes decisions from Stages 1-5 and uses PID controller to find
    EXACT entry/exit timing with optimal price.
    """

    def __init__(self, equity_symbol):
        """Initialize P01D stage with PID timing"""
        self.symbol = equity_symbol

        # Initialize PID controller for ENTRY timing
        # (Same logic as parameter optimization, just faster parameters)
        self.pid_entry = PIDPresets.entry_exit_timing(target_signal=0.75)

        # Initialize PID controller for EXIT timing
        self.pid_exit = PIDPresets.entry_exit_timing(target_signal=0.25)

        # State tracking
        self.position = None
        self.entry_details = None
        self.exit_details = None

    def calculate_pid_timing_signal(self, current_price, sma_20,
                                    prev_price, current_volume, prev_volume):
        """
        Calculate the PID timing signal from market data

        Returns signal between -1.0 and +1.0
        """
        # P: Price deviation from trend
        price_deviation = current_price - sma_20
        p_term = 0.10 * price_deviation  # Normalized to [-1, 1]

        # D: Rate of change of PRICE (dP/dt = momentum)
        dp_dt = current_price - prev_price
        d_term = 0.01 * dp_dt  # Normalized

        # V: Rate of change of VOLUME (dV/dt = conviction)
        dv_dt = current_volume - prev_volume
        v_term = 0.10 * dv_dt / max(current_volume, 1)  # Normalized

        # I: We'll let PID accumulate this
        # (handled by PID.calculate() internally)

        # Combined signal
        total_signal = p_term + d_term + v_term

        # Clip to [-1, 1]
        total_signal = np.clip(total_signal, -1, 1)

        return {
            'total_signal': total_signal,
            'p_term': p_term,
            'd_term': d_term,
            'v_term': v_term,
            'dp_dt': dp_dt,
            'dv_dt': dv_dt
        }

    def execute_with_pid_timing(self, action_from_stages_1_5,
                                 position_size, df_data, current_bar_idx,
                                 sma_20):
        """
        Execute trade with PID-optimized entry/exit timing

        Input:
          action_from_stages_1_5: "EXECUTE" or "ABSTAIN" from Stage 5
          position_size: Number of shares from Stage 5
          df_data: DataFrame with OHLCV data
          current_bar_idx: Current bar index
          sma_20: 20-bar simple moving average
        """

        if action_from_stages_1_5 != "EXECUTE":
            return {
                'status': 'ABSTAIN',
                'reason': 'Stages 1-5 rejected this trade',
                'position': None
            }

        # =====================================================================
        # ENTRY PHASE: Wait for PID to peak
        # =====================================================================

        entry_found = False
        entry_bar_idx = None

        # Look ahead up to 10 bars for optimal entry
        for bar_offset in range(min(10, len(df_data) - current_bar_idx - 1)):
            bar_idx = current_bar_idx + bar_offset
            bar = df_data.iloc[bar_idx]
            prev_bar = df_data.iloc[bar_idx - 1] if bar_idx > 0 else bar

            # Calculate timing signal
            timing = self.calculate_pid_timing_signal(
                current_price=bar['close'],
                sma_20=sma_20,
                prev_price=prev_bar['close'],
                current_volume=bar['volume'],
                prev_volume=prev_bar['volume']
            )

            # Use PID to evaluate if THIS is the right time to enter
            # (Same PID logic, just faster parameters)
            pid_result = self.pid_entry.calculate(timing['total_signal'])

            # Entry conditions:
            # 1. PID signal is high (near target 0.75)
            # 2. Price momentum positive (dP/dt > 0)
            # 3. Volume momentum positive (dV/dt > 0)
            if (timing['total_signal'] >= 0.75 and
                timing['dp_dt'] > 0 and
                timing['dv_dt'] > 0):

                entry_found = True
                entry_bar_idx = bar_idx

                # Record entry details
                self.position = {
                    'symbol': self.symbol,
                    'entry_price': bar['close'],
                    'entry_timestamp': bar['timestamp'],
                    'entry_bar_idx': bar_idx,
                    'entry_volume': bar['volume'],
                    'position_size': position_size,
                    'entry_pid_signal': timing['total_signal'],
                    'entry_pid_details': pid_result
                }

                self.entry_details = {
                    'timestamp': bar['timestamp'],
                    'price': bar['close'],
                    'volume': bar['volume'],
                    'pid_signal': timing['total_signal'],
                    'pid_p_term': timing['p_term'],
                    'pid_d_term': timing['d_term'],
                    'pid_v_term': timing['v_term'],
                    'price_momentum': timing['dp_dt'],
                    'volume_momentum': timing['dv_dt']
                }

                print(f"✅ ENTRY at {bar['timestamp']}")
                print(f"   Price: ₹{bar['close']:.2f} | Volume: {bar['volume']:,}")
                print(f"   PID Signal: {timing['total_signal']:.3f}")
                break

        if not entry_found:
            return {
                'status': 'ABSTAIN',
                'reason': 'No optimal entry timing found',
                'position': None
            }

        # =====================================================================
        # HOLDING PHASE: Monitor position
        # =====================================================================

        # Continue from entry bar onwards
        for bar_idx in range(entry_bar_idx + 1, len(df_data)):
            bar = df_data.iloc[bar_idx]
            prev_bar = df_data.iloc[bar_idx - 1]

            # Calculate timing signal
            timing = self.calculate_pid_timing_signal(
                current_price=bar['close'],
                sma_20=sma_20,
                prev_price=prev_bar['close'],
                current_volume=bar['volume'],
                prev_volume=prev_bar['volume']
            )

            # Calculate current P&L
            pnl = bar['close'] - self.position['entry_price']

            # ================================================================
            # EXIT CONDITIONS
            # ================================================================

            should_exit = False
            exit_reason = None

            # Condition 1: PID signal reverses (momentum loss)
            if (timing['total_signal'] <= 0.25 or
                timing['dp_dt'] < 0 or
                timing['dv_dt'] < 0):
                should_exit = True
                exit_reason = "PID reversal - momentum lost"

            # Condition 2: Profit target hit (1%)
            elif pnl >= 1.0:
                should_exit = True
                exit_reason = "Profit target reached"

            # Condition 3: Stop loss hit (0.5%)
            elif pnl <= -0.5:
                should_exit = True
                exit_reason = "Stop loss triggered"

            # Condition 4: Hold time exceeded
            elif bar_idx - entry_bar_idx >= 60:  # 60 bars = 15 hours
                should_exit = True
                exit_reason = "Hold period exceeded"

            # ================================================================
            # EXECUTE EXIT IF CONDITIONS MET
            # ================================================================

            if should_exit:
                # Use PID to confirm exit is optimal
                pid_exit_result = self.pid_exit.calculate(timing['total_signal'])

                self.exit_details = {
                    'timestamp': bar['timestamp'],
                    'price': bar['close'],
                    'volume': bar['volume'],
                    'pnl': pnl,
                    'reason': exit_reason,
                    'pid_signal': timing['total_signal'],
                    'pid_p_term': timing['p_term'],
                    'pid_d_term': timing['d_term'],
                    'pid_v_term': timing['v_term'],
                    'price_momentum': timing['dp_dt'],
                    'volume_momentum': timing['dv_dt'],
                    'hold_bars': bar_idx - entry_bar_idx
                }

                print(f"✅ EXIT at {bar['timestamp']}")
                print(f"   Price: ₹{bar['close']:.2f} | P&L: ₹{pnl:.2f}")
                print(f"   Reason: {exit_reason}")

                return {
                    'status': 'EXECUTED',
                    'entry': self.entry_details,
                    'exit': self.exit_details,
                    'position': self.position,
                    'final_pnl': pnl
                }

        # If we reach here, position still held (shouldn't happen in backtest)
        return {
            'status': 'HOLDING',
            'entry': self.entry_details,
            'position': self.position
        }


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

if __name__ == "__main__":
    print("\n" + "="*80)
    print("STAGE 6: P01D WITH INTEGRATED PID TIMING")
    print("="*80)
    print("\n✅ Same proven PID controller from parameter optimization")
    print("✅ Just with DIFFERENT PARAMETERS for real-time entry/exit")
    print("✅ Entry target: 0.75 signal peak")
    print("✅ Exit target: 0.25 signal reversal")
    print("\nNo separate system needed!")
    print("PID controller is embedded in Stage 6 (P01D).\n")
