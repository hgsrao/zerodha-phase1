# ============================================================================
# STREAMLIT DASHBOARD - ENHANCED WITH ORDER IMBALANCE [RESEARCH ONLY]
# Simulation monitoring of ECS + order imbalance heatmaps
# RESEARCH/EXPLORATORY - NOT production-grade
# Date: August 30, 2026
#
# ⚠️ WARNING: This dashboard displays SIMULATED data only.
# No real orders are executed. Redis connection is for testing purposes.
# See CRITICAL_AUDIT_RESPONSE_20260830.md for full details.
# ============================================================================

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import redis
import json
from datetime import datetime, timedelta
from typing import Dict, List

# ============================================================================
# PAGE CONFIGURATION
# ============================================================================

st.set_page_config(
    page_title="ECS + Order Imbalance",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================================================
# CUSTOM STYLING
# ============================================================================

st.markdown("""
<style>
    .metric-box { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                  padding: 20px; border-radius: 10px; color: white; text-align: center; }
    .metric-value { font-size: 36px; font-weight: bold; }
    .metric-label { font-size: 12px; opacity: 0.8; }
    .status-ok { color: #00FF41; font-weight: bold; }
    .status-warning { color: #FFD700; font-weight: bold; }
    .status-alert { color: #FF0000; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# ============================================================================
# REDIS CONNECTION
# ============================================================================

@st.cache_resource
def get_redis_client():
    """Get Redis client (cached)"""
    try:
        r = redis.Redis(host='localhost', port=6379, decode_responses=True)
        r.ping()
        return r
    except Exception as e:
        st.error(f"Redis connection failed: {e}")
        return None

redis_client = get_redis_client()

# ============================================================================
# DATA RETRIEVAL FUNCTIONS
# ============================================================================

def get_imbalance_data(symbols: List[str]) -> Dict:
    """Fetch all imbalance data from Redis"""
    data = {}
    for symbol in symbols:
        try:
            imb_data = redis_client.get(f'imbalance:{symbol}')
            if imb_data:
                data[symbol] = json.loads(imb_data)
            else:
                data[symbol] = {'value_pct': 0, 'heat': 'NO_DATA', 'confidence': 0}
        except:
            data[symbol] = {'value_pct': 0, 'heat': 'ERROR', 'confidence': 0}
    return data

def get_ecs_state() -> Dict:
    """Fetch ECS state from Redis"""
    try:
        ecs_data = redis_client.get('ecs:state')
        if ecs_data:
            return json.loads(ecs_data)
    except:
        pass
    return {
        'mode': 'UNKNOWN',
        'stress_factor': 0.0,
        'symbols_managed': 48
    }

def get_panic_scores(symbols: List[str]) -> Dict:
    """Fetch panic scores from Redis"""
    data = {}
    for symbol in symbols:
        try:
            panic_data = redis_client.get(f'panic_score:{symbol}')
            if panic_data:
                data[symbol] = json.loads(panic_data)
            else:
                data[symbol] = {'score': 0.0, 'severity': 'LOW', 'should_halt': False}
        except:
            data[symbol] = {'score': 0.0, 'severity': 'ERROR', 'should_halt': False}
    return data

# ============================================================================
# MAIN DASHBOARD
# ============================================================================

def main():
    st.title("⚡ ECS Trading Supervisor + Order Imbalance Monitor [RESEARCH ONLY]")
    st.markdown("**RESEARCH/SIMULATION ONLY** — Real-time hierarchical control system with microstructure analysis")
    st.warning("⚠️ This dashboard displays SIMULATED data. No real orders are executed. See CRITICAL_AUDIT_RESPONSE_20260830.md")

    # Sidebar configuration
    with st.sidebar:
        st.header("⚙️ Configuration")

        # Real NSE NIFTY 50 symbols
        all_symbols = [
            'RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK',
            'HDFC', 'SBIN', 'BAJAJFINSV', 'AXISBANK', 'LT',
            'MARUTI', 'WIPRO', 'SUNPHARMA', 'ASIANPAINT', 'HCLTECH',
            'TECHM', 'DRREDDY', 'INDIGO', 'JSWSTEEL', 'POWERGRID',
            'APOLLOHOSP', 'HINDUNILVR', 'SBILIFE', 'HINDPETRO', 'BHARTIARTL',
            'COALINDIA', 'NTPC', 'ADANIGREEN', 'ADANIPORTS', 'ONGC',
            'BAJAJ-AUTO', 'CIPLA', 'DIVISLAB', 'EICHERMOT', 'GRASIM',
            'ITC', 'KOTAKBANK', 'LT', 'MARICO', 'NTPC',
            'SHREECEM', 'TATAMOTORS', 'TATACONSUM', 'TATASTEEL', 'TITAN',
            'ULTRACEMCO', 'HEROMOTOCO', 'HINDALCO', 'M&M'
        ]
        # Ensure we have 48 symbols (pad with additional ones if needed)
        if len(all_symbols) < 48:
            additional = ['BOSCHIND', 'BPCL', 'SIEMENS']
            all_symbols.extend(additional[:48-len(all_symbols)])

        display_count = st.slider("Symbols to display", 5, len(all_symbols), 15)
        sample_symbols = all_symbols[:display_count]

        # Refresh rate
        refresh_rate = st.select_slider("Refresh interval (seconds)",
                                       options=[1, 2, 5, 10, 30],
                                       value=5)

        st.divider()

        # System health
        st.subheader("🏥 System Health")
        if redis_client:
            try:
                redis_client.ping()
                st.success("✅ Redis: CONNECTED")
            except:
                st.error("❌ Redis: DISCONNECTED")
        else:
            st.error("❌ Redis: NOT INITIALIZED")

    # ========================================================================
    # SECTION 1: KEY METRICS (Top)
    # ========================================================================

    st.subheader("📊 Key Metrics - Real-Time Snapshot")

    col1, col2, col3, col4, col5 = st.columns(5)

    ecs_state = get_ecs_state()
    imbalance_data = get_imbalance_data(sample_symbols)

    with col1:
        st.metric("Current Mode", ecs_state.get('mode', 'UNKNOWN'),
                 help="ECS operating mode (7 modes)")

    with col2:
        stress = ecs_state.get('stress_factor', 0.0)
        st.metric("Stress Factor", f"{stress:.2f}",
                 help="-1=Euphoria, 0=Normal, +1=Crisis")

    with col3:
        avg_imbalance = np.mean([d.get('value_pct', 0) for d in imbalance_data.values()])
        st.metric("Avg Imbalance", f"{avg_imbalance:+.1f}%",
                 help="Portfolio average buy/sell bias")

    with col4:
        active_symbols = sum(1 for d in imbalance_data.values()
                           if d.get('confidence', 0) > 0.5)
        st.metric("Active Symbols", f"{active_symbols}/{len(sample_symbols)}",
                 help="Symbols with high confidence data")

    with col5:
        st.metric("Symbols Tracked", ecs_state.get('symbols_managed', 48),
                 help="Total 48-symbol portfolio")

    st.divider()

    # ========================================================================
    # SECTION 2: IMBALANCE HEATMAP (48 Symbols)
    # ========================================================================

    st.subheader("🔥 Order Imbalance Heatmap")

    # Create heatmap data
    imbalance_values = [imbalance_data[sym].get('value_pct', 0) for sym in sample_symbols]
    heat_levels = [imbalance_data[sym].get('heat', 'NO_DATA') for sym in sample_symbols]
    confidences = [imbalance_data[sym].get('confidence', 0) for sym in sample_symbols]

    # Create plotly heatmap
    fig_heatmap = go.Figure(data=go.Heatmap(
        z=[imbalance_values],
        x=sample_symbols,
        colorscale='RdYlGn_r',
        zmid=0,
        zmin=-100,
        zmax=100,
        colorbar=dict(title="Imbalance %", thickness=20),
        hovertemplate='<b>%{x}</b><br>Imbalance: %{z:.1f}%<extra></extra>'
    ))

    fig_heatmap.update_layout(
        title="Order Imbalance Distribution (-100% SELL to +100% BUY)",
        xaxis_title="Symbols",
        yaxis_title="",
        height=200,
        margin=dict(l=50, r=50, t=80, b=50)
    )

    st.plotly_chart(fig_heatmap, use_container_width=True)

    st.divider()

    # ========================================================================
    # SECTION 3: DETAILED METRICS (Grid)
    # ========================================================================

    st.subheader("📈 Detailed Imbalance Metrics")

    # Create multi-column layout for detailed view
    cols = st.columns(3)

    for idx, symbol in enumerate(sample_symbols[:15]):  # Show first 15
        col = cols[idx % 3]

        with col:
            data = imbalance_data[symbol]
            imb_pct = data.get('value_pct', 0)
            heat = data.get('heat', 'NO_DATA')
            conf = data.get('confidence', 0)

            # Color indicator
            if 'EXTREME' in heat:
                indicator = '🔴🔴🔴'
            elif 'STRONG' in heat:
                indicator = '🔴🟠'
            elif 'MODERATE' in heat:
                indicator = '🟠'
            elif 'NEUTRAL' in heat:
                indicator = '⚪'
            else:
                indicator = '❓'

            st.metric(
                f"{symbol}",
                f"{imb_pct:+.1f}%",
                f"{indicator} {heat} (conf: {conf:.1f})"
            )

    st.divider()

    # ========================================================================
    # SECTION 4: PANIC DETECTION (Circuit Breaker)
    # ========================================================================

    st.subheader("🚨 Panic Detection & Circuit Breaker Status")

    panic_scores = get_panic_scores(sample_symbols)

    col1, col2, col3 = st.columns(3)

    with col1:
        critical_count = sum(1 for p in panic_scores.values()
                           if p.get('should_halt', False))

        if critical_count > 0:
            st.error(f"⚠️ ALERTS: {critical_count} symbols in panic")
        else:
            st.success("✅ All symbols clear")

    with col2:
        avg_panic = np.mean([p.get('score', 0) for p in panic_scores.values()])

        if avg_panic > 0.7:
            color = "error"
        elif avg_panic > 0.5:
            color = "warning"
        else:
            color = "info"

        st.metric("Avg Panic Score", f"{avg_panic:.3f}",
                 help="Portfolio average panic (0=calm, 1=crisis)")

    with col3:
        high_severity = sum(1 for p in panic_scores.values()
                          if p.get('severity', '') in ['HIGH', 'EXTREME'])

        if high_severity > 0:
            st.warning(f"⚠️ {high_severity} high-severity symbols")
        else:
            st.info("No high-severity alerts")

    # Panic score details
    st.write("**Panic Scores by Symbol:**")

    panic_df = pd.DataFrame([
        {
            'Symbol': sym,
            'Panic Score': panic_scores[sym].get('score', 0),
            'Severity': panic_scores[sym].get('severity', '?'),
            'Should Halt': '🛑' if panic_scores[sym].get('should_halt', False) else '✅'
        }
        for sym in sample_symbols[:10]
    ])

    st.dataframe(panic_df, use_container_width=True)

    st.divider()

    # ========================================================================
    # SECTION 5: SIGNAL DISTRIBUTION
    # ========================================================================

    st.subheader("📊 Signal Distribution Analysis")

    col1, col2 = st.columns(2)

    with col1:
        # Imbalance distribution
        fig_dist = go.Figure(data=[
            go.Histogram(
                x=imbalance_values,
                nbinsx=20,
                marker_color='rgba(100, 150, 200, 0.7)',
                name='Imbalance Distribution'
            )
        ])

        fig_dist.update_layout(
            title="Imbalance Distribution (All Symbols)",
            xaxis_title="Imbalance %",
            yaxis_title="Count",
            height=350
        )

        st.plotly_chart(fig_dist, use_container_width=True)

    with col2:
        # Heat level breakdown
        heat_counts = pd.Series(heat_levels).value_counts()

        fig_heat = go.Figure(data=[
            go.Bar(
                x=heat_counts.index,
                y=heat_counts.values,
                marker_color=['red', 'orange', 'yellow', 'green', 'gray'],
                text=heat_counts.values,
                textposition='auto'
            )
        ])

        fig_heat.update_layout(
            title="Heat Level Breakdown",
            xaxis_title="Heat Level",
            yaxis_title="Number of Symbols",
            height=350,
            showlegend=False
        )

        st.plotly_chart(fig_heat, use_container_width=True)

    st.divider()

    # ========================================================================
    # SECTION 6: LIVE LOG (Auto-update)
    # ========================================================================

    st.subheader("📋 Live Activity Log")

    log_placeholder = st.empty()

    # Simulate log entries
    log_entries = [
        {
            'Time': (datetime.now() - timedelta(seconds=i)).strftime('%H:%M:%S'),
            'Symbol': sample_symbols[i % len(sample_symbols)],
            'Event': f"Imbalance={imbalance_values[i % len(sample_symbols)]:+.1f}%",
            'Status': heat_levels[i % len(heat_levels)]
        }
        for i in range(10)
    ]

    log_df = pd.DataFrame(log_entries)
    log_placeholder.dataframe(log_df, use_container_width=True, height=200)

    st.divider()

    # ========================================================================
    # FOOTER & AUTO-REFRESH
    # ========================================================================

    col1, col2 = st.columns(2)

    with col1:
        st.info(f"🔄 Auto-refreshing every {refresh_rate} seconds")

    with col2:
        st.info(f"Last update: {datetime.now().strftime('%H:%M:%S')}")

    # Auto-refresh using streamlit's rerun
    import time
    time.sleep(refresh_rate)
    st.rerun()


# ============================================================================
# RUN DASHBOARD
# ============================================================================

if __name__ == '__main__':
    main()
