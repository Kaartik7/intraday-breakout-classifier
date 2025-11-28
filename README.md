# Intraday Low-Float Momentum Engine

This repository implements an end-to-end intraday trading pipeline for low-float equities using Interactive Brokers (IB) as the execution venue. The system consists of:

1. **Low-float universe construction** (`low_float_universe_builder.py`)  
2. **Asynchronous intraday execution engine** (`intraday_momentum_executor.py`)

The design is intentionally modular so that the selection and execution components can be backtested or replaced independently.

---

## 1. Universe Construction

The universe builder ingests a broad list of symbols from `company_tickers.json` and then applies a sequence of microstructure-aware filters using IB fundamental data:

- **Price filter:**  
  \( 0.1 < P_{\text{close}} < 7 \) USD  
- **Market-cap filter:**  
  \( 10^5 < \text{MC} < 5 \times 10^7 \)  
- **Liquidity filter:**  
  30-day average daily volume \( \geq 5{,}000 \) shares  

Symbols that satisfy these constraints are then enriched with **float shares** (TotalFloat) parsed from IB fundamental XML. The resulting dataset, sorted by market cap, is exported as:

```text
stocks_with_float.csv
