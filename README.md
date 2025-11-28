# Intraday Low-Float Momentum Engine

**A machine-learning–oriented intraday trading pipeline for low-float microcap equities using Interactive Brokers (IB).
The system is structured into two modules:

low_float_universe_builder.py — universe construction and feature sourcing

intraday_momentum_executor.py — real-time feature extraction and ML-style breakout decision engine

This repository frames a momentum-driven microcap strategy as a lightweight online classifier operating over intraday microstructure features.**

1. Universe Construction

The universe builder loads symbols from company_tickers.json and applies microstructure-aware filters using IB fundamentals.

Filtering Criteria (GitHub-safe, no LaTeX)

Price filter

0.1 < closing_price < 7.0


Market-cap filter

100,000 < market_cap < 50,000,000


Liquidity filter

avg_30day_volume >= 5,000


These constraints isolate liquid, tradeable microcaps with sufficient volatility.

Float Extraction

For each valid ticker, the script extracts float shares:

float_shares = TotalFloat (IB fundamental XML)


The enriched universe is exported as:

stocks_with_float.csv


This serves as input to the intraday engine.

2. Intraday Momentum Execution Algorithm

The execution engine streams 1-minute bars for each symbol and computes a set of engineered microstructure features. Each minute, the engine evaluates a rule-based ML-style classifier to determine whether a symbol is entering a short-horizon breakout regime.

2.1 Intraday Feature Vector

For each symbol and minute t, the system constructs a feature vector:

x_t = {
  O_t, H_t, L_t, C_t, V_t,
  r_bar, r_prev, r_5
}


where:

O_t, H_t, L_t, C_t — open, high, low, close of the current bar

V_t — volume

r_bar = H_t / L_t — current-bar expansion ratio

r_prev = H_(t-1) / L_(t-1) — previous-bar expansion

r_5 = max(H) / min(L) over the last five completed minutes

This forms the intraday state representation for each symbol.

2.2 ML-Framed Breakout Classifier

The strategy uses a deterministic decision boundary acting as a binary classifier:

f(x_t) -> {enter_long, ignore}


The classification rules:

Directional bias

C_t > O_t


Price constraint

C_t <= 5.00


Momentum (intrabar expansion)

1.05 < r_bar < 1.30


Noise suppression (previous-bar stability)

r_prev <= 1.05


Avoid extended moves (5-minute window)

r_5 < 1.03


If the feature vector satisfies these constraints, the model issues a breakout entry signal.

This rule set can be viewed as a hand-crafted decision boundary approximating a supervised classifier trained on intraday bar dynamics.

2.3 Order Generation Logic

When the classifier outputs enter_long, the engine constructs a stop-limit entry:

Stop price

stop_price = 1.02 * H_t


Limit price

limit_price = 1.08 * H_t


Position sizing

qty = floor(dollar_risk / stop_price)


Orders are submitted as GTD stop-limit orders expiring a few minutes after the signal.

The trader avoids symbols already traded earlier in the day and symbols with wide spreads:

ask/bid > 1.05  -> skipped

3. Machine Learning Perspective

While the engine uses a deterministic rule set, the system maps directly onto a machine-learning framework:

Feature Engineering

The bar-level microstructure features (r_bar, r_prev, r_5, etc.) correspond to a structured intraday feature space.

Labels

A supervised model could assign labels such as:

y_t = 1   if breakout continuation leads to profitable return
y_t = 0   otherwise

Model Classes

Possible ML replacements for the rule-based classifier:

Logistic Regression (interpretable boundaries)

Gradient Boosted Trees (nonlinear patterns)

Random Forests (noise-robust decisions)

LSTMs or Transformers (sequential modeling of bar data)

Policy Formulation

The decision rule can be reframed as:

enter_long  if  P(y_t = 1 | x_t) > threshold

Reinforcement Learning View

The streaming engine naturally fits a contextual bandit formulation:

context = feature vector

action = enter vs. skip

reward = realized intraday PnL

This repository provides the entire feature-generation and decision-execution stack required for training ML-based trading agents.

4. Usage
Build the Universe
python low_float_universe_builder.py


This generates:

stocks_with_float.csv

Run the Intraday Engine

Ensure IB Gateway or TWS is running:

python intraday_momentum_executor.py


The engine will:

connect to IB,

stream 1-minute bars,

compute microstructure features,

run the ML-style decision classifier,

place stop-limit entries.

5. Disclaimer

This code is for research and educational purposes only.
Trading in microcaps involves significant risk.
No profitability is implied.
