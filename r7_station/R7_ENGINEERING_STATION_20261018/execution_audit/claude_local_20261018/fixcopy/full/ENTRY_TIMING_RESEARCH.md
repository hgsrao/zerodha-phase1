# Intraday Entry Timing: What Can Be Estimated Causally

## Verdict

No indicator, PID, or model can identify the exact intraday low before it occurs. With one-minute OHLCV, even the order of a bar's high and low is unknown. The correct engineering goal is not “buy the exact low,” but “enter only when the estimated probability of target-before-stop, net of costs, is high enough.”

The current engine is a momentum-confirmation engine. It observes a move, then may enter on the next eligible bar. It has no dedicated reversal or pullback alpha. The new study-entry shadow ledger is the safe place to test such an alpha without changing orders.

## SUNPHARMA Example

On 2023-09-01, SUNPHARMA made its day low at ₹1,105.75 at 12:38 and later reached ₹1,111.00. The 12:38 bar closed near its own high with elevated volume. Stochastic had turned bullish, but Ichimoku, the Bollinger middle-band read, and session VWAP remained bearish. A causal system could flag a possible reversal and wait for confirmation; it could not know that ₹1,105.75 was the final low.

The shadow setup entered at the following bar's adverse paper price of ₹1,108.80, used a frozen stop of ₹1,105.44 and 1.5R target of ₹1,113.85, and expired before target. This is useful evidence: the candle was a possible reversal setup, not proof that the chosen target geometry had positive net expectancy.

## What External Evidence Supports

At sub-minute horizons, the strongest documented predictors generally use market-microstructure inputs rather than OHLC indicators alone. Cont, Kukanov, and Stoikov found short-horizon price changes related more robustly to order-flow imbalance than trade volume in their NYSE study. [1] Gould and Bonart found bid/ask queue imbalance had statistically significant out-of-sample predictive content for the next mid-price movement in their Nasdaq sample; its strength depended on tick-size characteristics. [2] Stoikov's micro-price framework combines spread and book imbalance to estimate a conditional short-horizon fair price. [3]

These findings do not automatically transfer to NSE cash symbols or prove profitability after costs. They do explain why exact-entry timing cannot be solved from one-minute OHLCV: OHLCV omits bid/ask queues, cancellations, and trade direction.

NSE states that Level 1 supplies best bid/ask, Level 2 up to five depth levels, Level 3 up to twenty, and tick-by-tick offers the full order book. [4] Its historical order/trade specification documents order entries, cancellations, modifications, buy/sell indicators, and high-resolution transaction-time fields. [5]

## Separate Hypotheses

| Model | Causal question | Current one-minute OHLCV can test it? | Better data |
|---|---|---|---|
| Continuation | Is a confirmed move likely to continue? | Yes | Best bid/ask and trade direction |
| Pullback | Has price retraced in an established trend and reclaimed support? | Yes | Best bid/ask and queue quality |
| Reversal | Has exhaustion been followed by a confirmed turn? | Partly | L2 depth, order-flow imbalance, cancellations |

These must remain independent candidates. Averaging incompatible hypotheses into one confidence score hides their performance and creates false confidence.

Ichimoku and session VWAP are trend/context inputs. The current Bollinger vote is only above/below the middle band, not upper/lower-band exhaustion. Stochastic is the fastest turn-sensitive input, but is noisy alone. A reversal candidate is a divergence—fast rejection evidence turns while slower context is still bearish/bullish—followed by confirmation, not an immediate order.

## Correct PID Loop

The PID cannot manufacture an entry point; it regulates an observed process. For each model, symbol, and regime bucket:

```text
process variable p:  causal estimate P(target before stop | features)
setpoint p*:         (L + C) / (W + L)
                      L = planned loss at stop
                      W = planned gross target gain
                      C = round-trip estimated cost
error:               p* - p
```

The shadow ledger supplies target-before-stop, MAE/MFE, next-bar adverse excursion, slippage, costs, and net outcomes. A bounded PID output may eventually lower a study weight, require stronger confirmation, or derate size. It must never force an entry, enlarge risk, override a safety gate, or retune gains after a handful of outcomes.

Avoid forty-eight manual configurations through hierarchical estimation:

```text
global history → sector/regime history → symbol-pattern history
```

Sparse symbols borrow the broader estimate. Only sufficient verified symbol history earns material influence.

## Implementation Sequence

1. Keep execution unchanged; study-entry feedback remains observer-only.
2. Record causal features: lower/upper-band position, close-in-range, range/ATR, volume ratio, VWAP distance, stochastic level/cross, trend context, votes, and weights.
3. Resolve fixed virtual trades: next-bar fill only; exclude fill-bar resolution; pessimistically handle later bars containing both target and stop.
4. Evaluate economics, not direction: target-before-stop, costs, slippage, net P&L, probability calibration, and adverse next-bar movement.
5. Use train/validation/test time splits. Freeze any selected model before untouched test reporting.
6. Only then consider bounded paper activation: initial allowed action is a derate or added confirmation, not automatic threshold relaxation, gain scheduling, or live deployment.
7. Acquire bid/ask or order-book data only if OHLCV shadow evidence justifies the data and engineering cost. Start with spread and best bid/ask, then test order-flow/queue imbalance independently.

## Immediate Decision

Do not add a live reversal gate now. The one-day SUNPHARMA shadow result was negative. Run the shadow ledger across separated months, compare continuation/pullback/reversal models net of costs, and stop adding indicators if none has stable out-of-sample utility.

## Sources

1. Rama Cont, Arseniy Kukanov, and Sasha Stoikov, “[The Price Impact of Order Book Events](https://arxiv.org/abs/1011.6402),” 2014.
2. Martin D. Gould and Justin Bonart, “[Queue Imbalance as a One-Tick-Ahead Price Predictor in a Limit Order Book](https://arxiv.org/abs/1512.03492),” 2015.
3. Sasha Stoikov, “[The Micro-Price: A High Frequency Estimator of Future Prices](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694),” 2017, revised 2020.
4. National Stock Exchange of India, “[Paid Real Time Data](https://www.nseindia.com/static/market-data/real-time-data-subscription),” accessed September 2026.
5. National Stock Exchange of India, “[Historical Data: Order and Trade Specification](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/NSE_Hist_Order_Trade_Data_1.15.pdf),” accessed September 2026.
