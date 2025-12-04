"""
intraday_momentum_executor.py

Asynchronous intraday momentum scanner for low-float stocks.

- Loads a pre-filtered universe from `stocks_with_float.csv`
- Streams 1-minute bars from Interactive Brokers (IB)
- Computes simple intrabar and short-term range features
- Places stop-limit BUY orders when breakout conditions are met
"""

import math
from datetime import datetime, timedelta
import asyncio

import pandas as pd
from ib_insync import *
import nest_asyncio

nest_asyncio.apply()

# ---------------------------------------------------------------------------
# IB connection
# ---------------------------------------------------------------------------

ib = IB()
ib.connect("", 7497, clientId=888) #redacted since I can't reveal my connections to public

# ---------------------------------------------------------------------------
# Universe construction
# ---------------------------------------------------------------------------

ONLINE_TICKERS = {}
UNIVERSE_FILE = "stocks_with_float.csv"

universe_symbols = set(pd.read_csv(UNIVERSE_FILE)["symbol"][:550])
universe_symbols = list(universe_symbols.union(ONLINE_TICKERS))

if "INTJ" in universe_symbols:
    universe_symbols.remove("INTJ")

print("Universe size:", len(universe_symbols))
UNIVERSE_CONTRACTS = [Stock(symbol, "SMART", "USD") for symbol in universe_symbols]


# ---------------------------------------------------------------------------
# Market data utilities
# ---------------------------------------------------------------------------

def get_spread_ratio(contract: Contract) -> float:
    """
    Request a single tick and return ask/bid spread ratio.

    A spread ratio >> 1 indicates illiquidity or bad quotes; such names are skipped.
    """
    ticker = ib.reqMktData(contract)
    ib.sleep(1)
    bid_price = ticker.bid
    ask_price = ticker.ask
    if not bid_price or not ask_price:
        return math.inf
    return ask_price / bid_price


def fetch_symbols_traded_today() -> set[str]:
    """
    Query today's executions from IB and return a set of symbols already traded.

    Used to avoid placing multiple entries into the same ticker on a single day.
    """
    today_str = datetime.now().strftime("%Y%m%d") + "-00:00:00"
    exec_filter = ExecutionFilter(time=today_str)
    executions = ib.reqExecutions(exec_filter)
    return {details.contract.symbol for details in executions}


def submit_entry_order(
        contract: Contract,
        reference_price: float,
        minutes_valid: int,
        dollar_risk: float,
) -> None:
    """
    Place a stop-limit BUY order around the breakout level.

    - Stop price = 1.02 * reference_price
    - Limit price = 1.08 * reference_price
    - Quantity sized from `dollar_risk`
    - Good-Till-Date order expiring a few minutes after signal
    """
    # Risk filters
    if get_spread_ratio(contract) > 1.05:
        return
    if contract.symbol in fetch_symbols_traded_today():
        return

    stop_price = round(1.02 * reference_price, 2)
    limit_price = round(1.08 * reference_price, 2)
    quantity = int(dollar_risk / max(stop_price, 0.01))
    if quantity <= 0:
        return

    order = StopLimitOrder(
        action="BUY",
        totalQuantity=quantity,
        stopPrice=stop_price,
        lmtPrice=limit_price,
        outsideRth=True,
    )

    now = datetime.now()
    start_of_minute = now.replace(second=0, microsecond=0)
    expiration = start_of_minute + timedelta(minutes=minutes_valid) - timedelta(hours=4)
    order.tif = "GTD"
    order.goodTillDate = expiration.strftime("%Y%m%d %H:%M:%S")

    ib.placeOrder(contract, order)
    print(
        f"[ENTRY] {contract.symbol}: {quantity} @ stop={stop_price}, limit={limit_price}"
    )


async def fetch_intraminute_features(
        contract: Contract,
) -> tuple[float, float, float, float, float, float, float]:
    """
    Fetch 1-minute bars for the last trading day and compute features.

    Returns:
        open_price, high_price, low_price, close_price, volume,
        prev_bar_range_ratio, last_five_range_ratio
    """
    bars = await ib.reqHistoricalDataAsync(
        contract,
        endDateTime="",
        durationStr="1 D",
        barSizeSetting="1 min",
        whatToShow="TRADES",
        useRTH=False,
    )

    if not bars:
        # No data available.
        return 0, 0, 0, 0, 0, 0, 1

    latest = bars[-1]

    # Single bar: no history.
    if len(bars) == 1:
        return (
            latest.open,
            latest.high,
            latest.low,
            latest.close,
            latest.volume,
            1.0,
            1.0,
        )

    prev = bars[-2]

    # Short history: use previous bar but not last-five range.
    if len(bars) < 6:
        prev_range_ratio = (prev.high / prev.low) if prev.low else 1.0
        return (
            latest.open,
            latest.high,
            latest.low,
            latest.close,
            latest.volume,
            prev_range_ratio,
            1.0,
        )

    # Full last-five computation
    recent_window = bars[-6:-1]
    max_price = max(bar.high for bar in recent_window)
    min_price = min(bar.low for bar in recent_window if bar.low)

    prev_range_ratio = (prev.high / prev.low) if prev.low else 1.0
    last_five_ratio = (max_price / min_price) if min_price else 1.0

    return (
        latest.open,
        latest.high,
        latest.low,
        latest.close,
        latest.volume,
        prev_range_ratio,
        last_five_ratio,
    )


# ---------------------------------------------------------------------------
# Core trading logic
# ---------------------------------------------------------------------------

async def evaluate_and_trade_symbol(contract: Contract, sem: asyncio.Semaphore) -> None:
    """
    Core decision logic for a single symbol:

    - Skip if data is missing or price > 5 USD
    - Require bullish bar (close > open)
    - Require intrabar expansion but not parabolic:
        1.05 < high/low < 1.30
    - Filter out names with very volatile previous minute or extended last 5 minutes
    - Place different-sized orders depending on the strength of the move
    """
    async with sem:
        try:
            (
                open_price,
                high_price,
                low_price,
                close_price,
                volume,
                prev_range_ratio,
                last_five_ratio,
            ) = await fetch_intraminute_features(contract)

            if not high_price or not low_price:
                return

            bar_range_ratio = high_price / low_price
            # Truncate to 2 decimals like original code
            bar_range_ratio = math.floor(bar_range_ratio * 100) / 100

            # Basic sanity and risk filters
            if close_price <= open_price:
                return
            if bar_range_ratio > 1.30:
                return
            if prev_range_ratio > 1.05:
                return
            if close_price > 5:
                return

            # Breakout logic approximating a simple rule-based classifier
            if 1.10 < bar_range_ratio < 1.30:
                # Stronger breakout, slightly larger notional
                submit_entry_order(contract, high_price, minutes_valid=3, dollar_risk=10)
            elif bar_range_ratio > 1.05 and last_five_ratio < 1.03:
                # Mild breakout following a tight consolidation
                submit_entry_order(contract, high_price, minutes_valid=3, dollar_risk=5)

        except Exception as exc:
            print(f"Error processing {contract.symbol}: {exc}")


def _on_ib_disconnected():
    print("Disconnected from IB. Attempting to reconnect...")
    ib.disconnect()
    ib.connect("127.x.x.x", xxxx, clientId=xxx) # redacted


ib.disconnectedEvent += _on_ib_disconnected


async def scan_universe_once():
    """
    Launch concurrent evaluation tasks for the entire universe.
    """
    sem = asyncio.Semaphore(50)
    tasks = [evaluate_and_trade_symbol(contract, sem) for contract in UNIVERSE_CONTRACTS]
    await asyncio.gather(*tasks)


async def run_intraday_scanner():
    """
    Main event loop.

    Every ~30 seconds (once the current second > 45), it runs a full scan of the universe.
    """
    while True:
        now = datetime.now()
        print("Heartbeat:", now)
        if now.second > 45:
            await scan_universe_once()
            await asyncio.sleep(30)
        else:
            await asyncio.sleep(1)


if __name__ == "__main__":
    ib.run(run_intraday_scanner())
