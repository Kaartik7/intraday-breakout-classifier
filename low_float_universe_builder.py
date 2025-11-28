"""
low_float_universe_builder.py

Builds a tradeable universe of low-float, micro-cap stocks using
Interactive Brokers fundamentals and pricing data.

Steps:
    1. Load candidate tickers from a JSON file
    2. For each symbol:
        - Fetch price, market cap, 30-day average volume from IB
    3. Filter to:
        - Cheap stocks (0.1 < price < 7 USD)
        - Micro-cap market caps (100k < MC < 50M)
        - Sufficient liquidity (avg volume >= 5,000)
    4. For the filtered list, fetch float (shares outstanding in float)
    5. Write 'stocks_with_float.csv' for use by intraday_momentum_executor.py
"""

import json
import time
import xml.etree.ElementTree as ET
from typing import List, Tuple, Optional

import yfinance as yf
import pandas as pd
from ib_insync import *


# ---------------------------------------------------------------------------
# XML parsing helpers
# ---------------------------------------------------------------------------

def extract_market_cap_from_xml(xml_data: str) -> Optional[float]:
    """
    Parse market capitalization from IB fundamental XML snapshot.
    """
    try:
        root = ET.fromstring(xml_data)
        mkcap_element = root.find('.//Ratio[@FieldName="MKTCAP"]')
        if mkcap_element is not None and mkcap_element.text:
            return float(mkcap_element.text)
    except Exception as exc:
        print(f"Error parsing market cap from XML: {exc}")
    return None


def extract_float_shares_from_xml(xml_data: str) -> Optional[float]:
    """
    Parse float shares from IB fundamental XML snapshot.
    """
    try:
        root = ET.fromstring(xml_data)
        shares_out_element = root.find(".//SharesOut")
        if shares_out_element is not None:
            float_shares_str = shares_out_element.get("TotalFloat")
            if float_shares_str is not None:
                return float(float_shares_str)
    except ET.ParseError:
        print("Error parsing XML data")
    except ValueError:
        print("Error converting float value")
    return None


# ---------------------------------------------------------------------------
# Data providers
# ---------------------------------------------------------------------------

def fetch_price_mc_volume_yahoo(symbol: str) -> Tuple[
    Optional[float], Optional[float], Optional[float]]:
    """
    Optional Yahoo Finance provider.
    Not used in the main pipeline, but kept for extensibility.
    """
    try:
        ticker = yf.Ticker(symbol)
        data_1d = ticker.history(period="1d")
        if data_1d.empty:
            print(f"[{symbol}] No daily data found on Yahoo. Possibly invalid or delisted.")
            return None, None, None

        price = float(data_1d["Close"].iloc[-1])
        market_cap = ticker.info.get("marketCap", None)

        data_1mo = ticker.history(period="1mo")
        if data_1mo.empty or "Volume" not in data_1mo.columns:
            avg_volume = None
            print(f"[{symbol}] No 1-month volume data found.")
        else:
            avg_volume = float(data_1mo["Volume"].mean())

        return price, market_cap, avg_volume
    except Exception as exc:
        print(f"[{symbol}] Error fetching data from Yahoo Finance: {exc}")
        return None, None, None


def fetch_price_mc_volume_ib(
        ib: IB, symbol: str
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Fetch closing price, micro-cap market cap, and 30-day average volume from IB.
    """
    try:
        contract = Stock(symbol, "SMART", "USD")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            print(f"[{symbol}] Contract not found.")
            return None, None, None

        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr="30 D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=False,
            formatDate=1,
        )
        if not bars:
            print(f"[{symbol}] No historical bars returned.")
            return None, None, None

        latest_bar = bars[-1]
        price = latest_bar.close

        volumes = [b.volume for b in bars[:-1] if b.volume is not None]
        avg_volume = sum(volumes) / len(volumes) if volumes else 0

        funda_xml = ib.reqFundamentalData(contract, "ReportSnapshot")
        market_cap = extract_market_cap_from_xml(funda_xml) if funda_xml else None
        if market_cap is not None:
            market_cap *= 1_000_000  # IB returns in millions

        return price, market_cap, avg_volume
    except Exception as exc:
        print(f"[{symbol}] Error fetching data from IB: {exc}")
        return None, None, None


def fetch_float_shares_ib(ib: IB, symbol: str) -> Optional[float]:
    """
    Fetch float shares (TotalFloat) via IB fundamental XML.
    """
    try:
        contract = Stock(symbol, "SMART", "USD")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            print(f"[{symbol}] Contract not found.")
            return None

        funda_xml = ib.reqFundamentalData(contract, "ReportSnapshot")
        if funda_xml:
            return extract_float_shares_from_xml(funda_xml)
        else:
            print(f"[{symbol}] No fundamental data.")
            return None
    except Exception as exc:
        print(f"[{symbol}] Error fetching float: {exc}")
        return None


# ---------------------------------------------------------------------------
# Universe selection
# ---------------------------------------------------------------------------

def load_tickers(json_path: str) -> list[str]:
    """
    Load candidate tickers from a JSON mapping.
    """
    with open(json_path, "r") as f:
        data = json.load(f)
    return [entry["ticker"] for entry in data.values()]


def select_universe_candidates(
        results: list[tuple[str, float, float, float]],
        price_thresh: float = 7.0,
        mc_thresh: float = 50_000_000.0,
        vol_thresh: int = 5_000,
) -> list[dict]:
    """
    Filter raw (symbol, price, market_cap, avg_volume) tuples down to a
    low-float, micro-cap, reasonable-liquidity universe.
    """
    filtered = []
    for symbol, price, mc, vol in results:
        if price is None or mc is None or vol is None:
            continue
        if 0.1 < price < price_thresh and 100_000 < mc < mc_thresh and vol >= vol_thresh:
            filtered.append(
                {
                    "symbol": symbol,
                    "price": price,
                    "market_cap": mc,
                    "volume": vol,
                }
            )
    return filtered


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

JSON_TICKERS_PATH = "tickers.json"
UNIVERSE_OUTPUT_PATH = "stocks_with_float.csv"


def build_low_float_universe():
    """
    End-to-end pipeline:

        1. Load tickers from JSON
        2. Pull price/MC/volume for each ticker from IB
        3. Filter by micro-cap / liquidity criteria
        4. Enrich with float data
        5. Write `stocks_with_float.csv` for downstream trading
    """
    tickers = load_tickers(JSON_TICKERS_PATH)

    ib = IB()
    ib.connect("127.0.0.1", 7497, clientId=123)

    raw_results: list[tuple[str, float, float, float]] = []

    for symbol in tickers:
        if len(symbol) > 4:
            continue

        print(f"[SYMBOL] {symbol}")
        price, market_cap, volume = fetch_price_mc_volume_ib(ib, symbol)
        print("  ->", price, market_cap, volume)
        raw_results.append((symbol, price, market_cap, volume))
        time.sleep(0.2)

    filtered = select_universe_candidates(raw_results)

    if not filtered:
        print("No stocks meet the criteria.")
        ib.disconnect()
        return

    base_df = pd.DataFrame(filtered)
    print("\nFiltered stocks:")
    print(base_df)

    # Fetch float for each candidate
    float_rows = []
    for _, row in base_df.iterrows():
        symbol = row["symbol"]
        print(f"Fetching float for {symbol}...")
        float_val = fetch_float_shares_ib(ib, symbol)
        float_rows.append({"symbol": symbol, "float": float_val})
        time.sleep(0.2)

    ib.disconnect()
    print("\nDisconnected from IB.")

    float_df = pd.DataFrame(float_rows)
    universe_df = pd.merge(base_df, float_df, on="symbol", how="left")

    # Sort by market cap (ascending) and write to disk
    universe_df = universe_df.sort_values(by=["market_cap"])
    universe_df.to_csv(UNIVERSE_OUTPUT_PATH, index=False)
    print(f"\nUniverse saved to {UNIVERSE_OUTPUT_PATH}.")


if __name__ == "__main__":
    build_low_float_universe()
