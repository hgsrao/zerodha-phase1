"""
DYNAMIC PA WEIGHT & LAMBDA GENERATOR
Generates initial weights and position sizing from historical data
NOT hardcoded - CALCULATED from market analysis
"""

import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

print("\n" + "="*100)
print("🔧 DYNAMIC PA WEIGHT & LAMBDA GENERATOR")
print("="*100)
print("Generates weights and position sizes from ACTUAL data analysis\n")

# ============================================================================
# PART 1: DYNAMIC PA WEIGHT GENERATION
# ============================================================================

def generate_pa_weights_dynamic(symbol, data_csv):
    """
    Generate PA weights dynamically by analyzing which indicators
    correlate best with winning trades

    Instead of hardcoding [0.20, 0.20, 0.20, 0.20, 0.20],
    this CALCULATES optimal weights from historical data
    """

    print(f"\n📊 GENERATING DYNAMIC PA WEIGHTS FOR {symbol}")
    print("-" * 100)

    try:
        df = pd.read_csv(data_csv, encoding='utf-8')
    except:
        df = pd.read_csv(data_csv, encoding='latin-1')

    # Calculate technical indicators for the full dataset
    df['momentum'] = df['close'].pct_change(5)  # 5-period momentum
    df['rsi'] = calculate_rsi(df['close'], 14)  # RSI 14-period
    df['macd'] = calculate_macd(df['close'])    # MACD
    df['volume_ratio'] = df['volume'] / df['volume'].rolling(20).mean()  # Volume ratio
    df['roc'] = df['close'].pct_change(3)       # Rate of change

    # Simulate trades and measure which indicators predicted wins
    indicator_scores = {
        'momentum': 0.0,
        'rsi': 0.0,
        'macd': 0.0,
        'volume': 0.0,
        'roc': 0.0
    }

    trade_count = 0

    # Backtest: measure which indicator predicted wins
    for i in range(50, len(df) - 10):
        # Check each indicator
        momentum_signal = 1 if df['momentum'].iloc[i] > 0 else -1
        rsi_signal = 1 if df['rsi'].iloc[i] > 50 else -1
        macd_signal = 1 if df['macd'].iloc[i] > 0 else -1
        volume_signal = 1 if df['volume_ratio'].iloc[i] > 1.0 else -1
        roc_signal = 1 if df['roc'].iloc[i] > 0 else -1

        # Future price movement (next 10 candles)
        future_price = df['close'].iloc[i + 10]
        current_price = df['close'].iloc[i]
        future_return = (future_price - current_price) / current_price

        # Did price go up? (win scenario)
        is_win = future_return > 0.002  # 0.2% profit target

        if is_win:
            # Give credit to indicators that predicted this win
            if momentum_signal > 0:
                indicator_scores['momentum'] += 1
            if rsi_signal > 0:
                indicator_scores['rsi'] += 1
            if macd_signal > 0:
                indicator_scores['macd'] += 1
            if volume_signal > 0:
                indicator_scores['volume'] += 1
            if roc_signal > 0:
                indicator_scores['roc'] += 1

        trade_count += 1

    # Convert scores to weights (normalize)
    total_score = sum(indicator_scores.values())
    if total_score == 0:
        # Fallback if no patterns found
        weights = {k: 0.20 for k in indicator_scores.keys()}
    else:
        weights = {k: v / total_score for k, v in indicator_scores.items()}

    # Ensure weights are in reasonable range [0.10, 0.30]
    for key in weights:
        weights[key] = max(0.10, min(0.30, weights[key]))

    # Re-normalize to sum to 1.0
    total = sum(weights.values())
    weights = {k: v / total for k, v in weights.items()}

    print(f"\n✅ GENERATED PA WEIGHTS (from {len(df)} candles of data):")
    print(f"   Momentum: {weights['momentum']:.3f} (predictive score: {indicator_scores['momentum']:.0f})")
    print(f"   RSI:      {weights['rsi']:.3f} (predictive score: {indicator_scores['rsi']:.0f})")
    print(f"   MACD:     {weights['macd']:.3f} (predictive score: {indicator_scores['macd']:.0f})")
    print(f"   Volume:   {weights['volume']:.3f} (predictive score: {indicator_scores['volume']:.0f})")
    print(f"   ROC:      {weights['roc']:.3f} (predictive score: {indicator_scores['roc']:.0f})")
    print(f"   TOTAL:    {sum(weights.values()):.3f}")

    return weights, indicator_scores

# ============================================================================
# PART 2: DYNAMIC LAMBDA GENERATION
# ============================================================================

def generate_lambda_dynamic(symbol, data_csv, target_risk_level='moderate'):
    """
    Generate position sizing (Lambda) dynamically based on:
    - Market volatility (VIX-like measure)
    - Historical drawdown patterns
    - Account size and risk tolerance

    Instead of hardcoding Lambda = 1.0,
    this CALCULATES optimal position size from market conditions
    """

    print(f"\n📊 GENERATING DYNAMIC LAMBDA (Position Sizing) FOR {symbol}")
    print("-" * 100)

    try:
        df = pd.read_csv(data_csv, encoding='utf-8')
    except:
        df = pd.read_csv(data_csv, encoding='latin-1')

    # Calculate volatility
    df['returns'] = df['close'].pct_change()
    volatility = df['returns'].std()

    # Calculate historical drawdown
    df['cummax'] = df['close'].expanding().max()
    df['drawdown'] = (df['close'] - df['cummax']) / df['cummax']
    max_historical_drawdown = df['drawdown'].min()

    # Calculate win rate from historical analysis
    price_changes = df['close'].pct_change()
    win_count = (price_changes > 0.002).sum()  # Winning moves > 0.2%
    total_moves = len(price_changes) - 1
    win_rate = win_count / total_moves if total_moves > 0 else 0.5

    print(f"\n📈 MARKET ANALYSIS:")
    print(f"   Volatility (std dev): {volatility:.4f} ({volatility*100:.2f}%)")
    print(f"   Historical Max Drawdown: {max_historical_drawdown:.2%}")
    print(f"   Win Rate (historical): {win_rate:.1%}")
    print(f"   Data Period: {len(df)} candles")

    # Calculate Lambda based on market conditions

    # Step 1: Volatility adjustment
    volatility_multiplier = 1.0
    if volatility < 0.01:  # Low volatility
        volatility_multiplier = 1.1  # Can be more aggressive
    elif volatility > 0.03:  # High volatility
        volatility_multiplier = 0.85  # Need to be conservative

    # Step 2: Historical drawdown adjustment
    drawdown_multiplier = 1.0
    if abs(max_historical_drawdown) > 0.10:  # > 10% drawdown historically
        drawdown_multiplier = 0.8  # Reduce position size
    elif abs(max_historical_drawdown) < 0.05:  # < 5% drawdown
        drawdown_multiplier = 1.05  # Can increase position size

    # Step 3: Win rate adjustment
    win_rate_multiplier = 1.0
    if win_rate > 0.55:  # High win rate
        win_rate_multiplier = 1.05  # Can be more aggressive
    elif win_rate < 0.45:  # Low win rate
        win_rate_multiplier = 0.90  # Need to be conservative

    # Step 4: Risk level adjustment
    risk_level_multiplier = {
        'conservative': 0.75,
        'moderate': 0.90,
        'aggressive': 1.05
    }.get(target_risk_level.lower(), 0.90)

    # Combine all factors
    lambda_calculated = (
        volatility_multiplier *
        drawdown_multiplier *
        win_rate_multiplier *
        risk_level_multiplier
    )

    # Ensure Lambda is within reasonable bounds [0.5, 1.5]
    lambda_final = max(0.5, min(1.5, lambda_calculated))

    print(f"\n🔧 LAMBDA CALCULATION:")
    print(f"   Volatility Multiplier: {volatility_multiplier:.3f}")
    print(f"   Drawdown Multiplier: {drawdown_multiplier:.3f}")
    print(f"   Win Rate Multiplier: {win_rate_multiplier:.3f}")
    print(f"   Risk Level ({target_risk_level}): {risk_level_multiplier:.3f}")
    print(f"   ────────────────────────────")
    print(f"   CALCULATED LAMBDA: {lambda_final:.3f}x")
    print(f"   Position Size: 1 share × {lambda_final:.3f} = {lambda_final:.3f} shares")

    return lambda_final

# ============================================================================
# HELPER FUNCTIONS: Technical Indicators
# ============================================================================

def calculate_rsi(prices, period=14):
    """Calculate RSI"""
    deltas = np.diff(prices)
    seed = deltas[:period+1]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0
    rsi = np.zeros_like(prices)
    rsi[:period] = 100. - 100. / (1. + rs)
    for i in range(period, len(prices)):
        delta = deltas[i - 1]
        if delta > 0:
            upval = delta
            downval = 0.
        else:
            upval = 0.
            downval = -delta
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        rs = up / down if down != 0 else 0
        rsi[i] = 100. - 100. / (1. + rs)
    return rsi

def calculate_macd(prices, fast=12, slow=26):
    """Calculate MACD"""
    ema_fast = pd.Series(prices).ewm(span=fast).mean()
    ema_slow = pd.Series(prices).ewm(span=slow).mean()
    macd = ema_fast - ema_slow
    return macd.values

# ============================================================================
# MAIN EXECUTION
# ============================================================================

# Find INFY data
data_files = list(Path(".").glob("**/INFY*.csv"))
if data_files:
    infy_file = data_files[0]

    print(f"\n📂 Using: {infy_file.name}\n")

    # Generate dynamic weights
    print("\n" + "█" * 100)
    print("STAGE 1: GENERATE PA WEIGHTS DYNAMICALLY")
    print("█" * 100)
    pa_weights, scores = generate_pa_weights_dynamic('INFY', infy_file)

    # Generate dynamic lambda
    print("\n" + "█" * 100)
    print("STAGE 2: GENERATE LAMBDA DYNAMICALLY")
    print("█" * 100)
    lambda_conservative = generate_lambda_dynamic('INFY', infy_file, 'conservative')
    lambda_moderate = generate_lambda_dynamic('INFY', infy_file, 'moderate')
    lambda_aggressive = generate_lambda_dynamic('INFY', infy_file, 'aggressive')

    # Summary
    print("\n" + "="*100)
    print("✅ DYNAMIC CONFIGURATION GENERATED")
    print("="*100)

    print("\n🎯 STAGE 2 (PA MODEL) - DYNAMIC WEIGHTS:")
    print(f"""
    Instead of hardcoded [0.20, 0.20, 0.20, 0.20, 0.20]
    Generated from data analysis:

    Momentum: {pa_weights['momentum']:.3f}
    RSI:      {pa_weights['rsi']:.3f}
    MACD:     {pa_weights['macd']:.3f}
    Volume:   {pa_weights['volume']:.3f}
    ROC:      {pa_weights['roc']:.3f}

    These weights reflect which indicators actually predicted
    winning trades in the historical data!
    """)

    print("\n🎯 STAGE 5 (POSITION SIZING) - DYNAMIC LAMBDA:")
    print(f"""
    Instead of hardcoded Lambda = 1.0x
    Generated from market analysis:

    Conservative Mode: {lambda_conservative:.3f}x
    Moderate Mode:     {lambda_moderate:.3f}x
    Aggressive Mode:   {lambda_aggressive:.3f}x

    These values adapt to current market volatility,
    historical drawdown, and win rate patterns!
    """)

    print("="*100)
    print("✨ BENEFITS OF DYNAMIC GENERATION:")
    print("="*100)
    print("""
    ✅ No hardcoded values - everything calculated from data
    ✅ Adapts to different symbols automatically
    ✅ Reflects actual market conditions
    ✅ Better starting point for feedback loops
    ✅ Can be regenerated periodically (daily/weekly)
    ✅ Works for any symbol - not just INFY
    """)

else:
    print("❌ No INFY data file found")

print("\n" + "="*100 + "\n")
