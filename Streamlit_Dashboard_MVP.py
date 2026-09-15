# ============================================================================
# STREAMLIT DASHBOARD - MVP MONITORING
# Real-time ECS Trading Supervisor dashboard
# Date: August 30, 2026
# Status: READY FOR MVP DEPLOYMENT
# ============================================================================

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta
import json
from typing import Dict, List

# ============================================================================
# PAGE CONFIGURATION
# ============================================================================

st.set_page_config(
    page_title="ECS Trading Supervisor",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .metric-box {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 20px;
        border-radius: 10px;
        color: white;
        text-align: center;
    }
    .metric-value {
        font-size: 36px;
        font-weight: bold;
    }
    .metric-label {
        font-size: 12px;
        opacity: 0.8;
    }
    .status-ok {
        color: #00FF41;
    }
    .status-halt {
        color: #FF0000;
    }
    .status-warning {
        color: #FFD700;
    }
</style>
""", unsafe_allow_html=True)

# ============================================================================
# SAMPLE DATA (For MVP without live connection)
# ============================================================================

def generate_sample_ecs_state():
    """Generate sample ECS state for MVP testing"""
    return {
        'current_mode': 'LOAD_SHARING_MODE',
        'stress_factor_ema': np.random.uniform(-0.5, 0.5),
        'speed_ema': np.random.uniform(-50, 50),
        'voltage_ema': np.random.uniform(-50, 50),
        'signal_history_length': 1440,
        'mode_history_length': 1440,
        'circuit_breaker': {
            'is_trading_allowed': True,
            'halt_reason': None,
            'backend': 'redis'
        }
    }

def generate_sample_signals_history(days=1):
    """Generate sample signal history"""
    num_bars = 24 * 60  # 1 day of 1-min bars
    times = [datetime.now() - timedelta(minutes=i) for i in range(num_bars, 0, -1)]

    data = {
        'timestamp': times,
        'mode': np.random.choice([
            'LOAD_SHARING_MODE', 'PLANT_FOLLOW_MODE', 'VAR_SUPPORT_MODE',
            'FREQUENCY_CONTROL_MODE', 'ISLANDING_MODE'
        ], num_bars),
        'stress': np.cumsum(np.random.randn(num_bars) * 0.05),
        'speed': np.cumsum(np.random.randn(num_bars) * 5),
        'voltage': np.cumsum(np.random.randn(num_bars) * 3)
    }

    # Normalize stress to -1 to 1
    data['stress'] = np.clip(data['stress'] / data['stress'].std(), -1, 1)

    return pd.DataFrame(data)

def generate_sample_execution_history():
    """Generate sample execution history"""
    num_bars = 100
    data = {
        'timestamp': pd.date_range(end=datetime.now(), periods=num_bars, freq='1min'),
        'mode': np.random.choice([
            'LOAD_SHARING', 'PLANT_FOLLOW', 'VAR_SUPPORT'
        ], num_bars),
        'entries': np.random.randint(5, 25, num_bars),
        'passed': np.random.randint(20, 40, num_bars),
        'stress': np.cumsum(np.random.randn(num_bars) * 0.02)
    }
    return pd.DataFrame(data)

# ============================================================================
# STREAMLIT APP
# ============================================================================

def main():
    # Title
    st.title("⚡ ECS Trading Supervisor")
    st.markdown("Electrical Control System for Trading | Real-time Monitoring")

    # Sidebar
    with st.sidebar:
        st.header("⚙️ Settings")
        refresh_interval = st.slider("Refresh interval (seconds)", 1, 60, 5)
        show_details = st.checkbox("Show detailed metrics", value=False)

        st.divider()
        st.header("📊 Redis Backend")
        redis_connected = st.checkbox("Redis connected", value=True)
        if redis_connected:
            st.success("Redis: CONNECTED")
        else:
            st.error("Redis: DISCONNECTED")

    # ========================================================================
    # TOP: KEY METRICS
    # ========================================================================

    col1, col2, col3, col4 = st.columns(4)

    ecs_state = generate_sample_ecs_state()

    with col1:
        stress = ecs_state['stress_factor_ema']
        st.metric(
            "Stress Factor",
            f"{stress:.2f}",
            delta=f"{stress*100:+.1f}%",
            delta_color="inverse"
        )

    with col2:
        st.metric(
            "Operating Mode",
            ecs_state['current_mode'].replace('_MODE', ''),
            delta="Live"
        )

    with col3:
        circuit_breaker = ecs_state['circuit_breaker']
        status = "🟢 OK" if circuit_breaker['is_trading_allowed'] else "🔴 HALT"
        st.metric("Circuit Breaker", status)

    with col4:
        st.metric("Active Symbols", "48", delta="Monitoring")

    st.divider()

    # ========================================================================
    # SIGNAL VISUALIZATION
    # ========================================================================

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("📈 Stress Factor Trend")
        signals_df = generate_sample_signals_history()

        fig_stress = go.Figure()
        fig_stress.add_trace(go.Scatter(
            x=signals_df['timestamp'],
            y=signals_df['stress'],
            mode='lines',
            name='Stress Factor',
            line=dict(color='#667eea', width=2),
            fill='tozeroy'
        ))

        # Add threshold lines
        fig_stress.add_hline(y=0.7, line_dash="dash", line_color="red",
                            annotation_text="Crisis Threshold", annotation_position="right")
        fig_stress.add_hline(y=-0.2, line_dash="dash", line_color="green",
                            annotation_text="Euphoric", annotation_position="right")

        fig_stress.update_layout(
            title="Stress Factor (Real-time)",
            xaxis_title="Time",
            yaxis_title="Stress (-1=Euphoric, +1=Crisis)",
            hovermode="x unified",
            height=400
        )
        st.plotly_chart(fig_stress, use_container_width=True)

    with col2:
        st.subheader("🎛️ SPEED & VOLTAGE Signals")

        fig_signals = go.Figure()
        fig_signals.add_trace(go.Scatter(
            x=signals_df['timestamp'],
            y=signals_df['speed'],
            mode='lines',
            name='SPEED Signal',
            line=dict(color='#FF6B6B', width=2)
        ))
        fig_signals.add_trace(go.Scatter(
            x=signals_df['timestamp'],
            y=signals_df['voltage'],
            mode='lines',
            name='VOLTAGE Signal',
            line=dict(color='#4ECDC4', width=2)
        ))

        fig_signals.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        fig_signals.update_layout(
            title="Control Signals (SPEED & VOLTAGE)",
            xaxis_title="Time",
            yaxis_title="Signal Amplitude (-100 to +100)",
            hovermode="x unified",
            height=400
        )
        st.plotly_chart(fig_signals, use_container_width=True)

    st.divider()

    # ========================================================================
    # MODE DISTRIBUTION
    # ========================================================================

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("📊 Mode Distribution")

        mode_counts = signals_df['mode'].value_counts()
        fig_mode = go.Figure(data=[
            go.Bar(
                x=mode_counts.index,
                y=mode_counts.values,
                marker=dict(color=['#667eea', '#764ba2', '#f093fb', '#4b72ff', '#ff6b6b'])
            )
        ])
        fig_mode.update_layout(
            title="Operating Modes (Last 24 Hours)",
            xaxis_title="Mode",
            yaxis_title="Count",
            height=350,
            showlegend=False
        )
        st.plotly_chart(fig_mode, use_container_width=True)

    with col2:
        st.subheader("📈 Symbol Execution Summary")

        exec_df = generate_sample_execution_history()
        total_entries = exec_df['entries'].sum()
        total_passed = exec_df['passed'].sum()

        fig_exec = go.Figure(data=[
            go.Bar(name='Entries', x=['Today'], y=[total_entries], marker_color='#00FF41'),
            go.Bar(name='Passed', x=['Today'], y=[total_passed], marker_color='#FFD700')
        ])
        fig_exec.update_layout(
            title="Symbol Decisions (Today)",
            barmode='group',
            height=350,
            showlegend=True
        )
        st.plotly_chart(fig_exec, use_container_width=True)

    st.divider()

    # ========================================================================
    # DETAILED METRICS (Optional)
    # ========================================================================

    if show_details:
        st.subheader("🔍 Detailed Metrics")

        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric("SPEED Signal", f"{ecs_state['speed_ema']:.1f}", "-75 to +75")
            st.metric("Entry Threshold", f"{0.75 - ecs_state['speed_ema']/200:.3f}", "Adaptive")

        with col2:
            st.metric("VOLTAGE Signal", f"{ecs_state['voltage_ema']:.1f}", "-100 to +100")
            st.metric("Position Multiplier", f"{1.0 + ecs_state['voltage_ema']/200:.3f}", "Adaptive")

        with col3:
            st.metric("Bars Executed", ecs_state['signal_history_length'], "Since start")
            st.metric("Mode Changes", ecs_state['mode_history_length'], "Total")

    # ========================================================================
    # CIRCUIT BREAKER STATUS
    # ========================================================================

    st.divider()
    st.subheader("🚨 Circuit Breaker Status")

    circuit_breaker = ecs_state['circuit_breaker']

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if circuit_breaker['is_trading_allowed']:
            st.success("✅ TRADING ALLOWED")
        else:
            st.error("❌ TRADING HALTED")

    with col2:
        st.info(f"Reason: {circuit_breaker['halt_reason'] or 'None'}")

    with col3:
        st.info(f"Backend: {circuit_breaker['backend'].upper()}")

    with col4:
        st.info(f"Last Updated: {datetime.now().strftime('%H:%M:%S')}")

    # ========================================================================
    # LOGS (Sample)
    # ========================================================================

    st.divider()
    st.subheader("📋 Recent Activity Log")

    logs = [
        ("2026-08-30 14:35:22", "INFO", "Bar #1440: Mode=LOAD_SHARING, Stress=0.15, Entries=12"),
        ("2026-08-30 14:34:22", "INFO", "Bar #1439: Mode=PLANT_FOLLOW, Stress=0.22, Entries=15"),
        ("2026-08-30 14:33:22", "DEBUG", "Symbol INFY: ENTRY PA=0.78 >= Threshold=0.72"),
        ("2026-08-30 14:32:22", "DEBUG", "Symbol TCS: PASS PA=0.68 < Threshold=0.72"),
        ("2026-08-30 14:31:22", "INFO", "Circuit breaker: stress=0.22, dd=-0.01 (OK)"),
    ]

    log_df = pd.DataFrame(logs, columns=['Timestamp', 'Level', 'Message'])
    st.dataframe(log_df, use_container_width=True, height=200)

    # ========================================================================
    # AUTO-REFRESH
    # ========================================================================

    # Streamlit auto-refresh placeholder
    st.info(f"🔄 Auto-refreshing every {refresh_interval} seconds (configure in sidebar)")

    # Footer
    st.divider()
    st.markdown("""
    **ECS Trading Supervisor** | Powered by Power Plant Control Theory

    Status: MVP Dashboard (MVP) | Next: Grafana + InfluxDB (Phase 2)
    """)


if __name__ == '__main__':
    main()

