"""
Example: RTDS Live Market Monitoring

Demonstrates using RTDS (Real-Time Data Service) to monitor:
- Live trade activity
- Market creation/resolution events
- Real-time crypto prices (BTC/ETH/SOL/XRP)
- Market price changes

ZERO ASSUMPTIONS:
- Handles connection failures gracefully
- Logs all events
- Clean shutdown on Ctrl+C
"""

import time
import logging
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.polymarket import PolymarketClient
from shared.polymarket.api.real_time_data import Message

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def on_trade(msg: Message):
    """Handle trade events."""
    logger.info(f"TRADE: {msg.payload}")


def on_market_created(msg: Message):
    """Handle new market creation."""
    logger.info(f"NEW MARKET: {msg.payload.get('title', 'Unknown')}")


def on_market_resolved(msg: Message):
    """Handle market resolution."""
    logger.info(f"MARKET RESOLVED: {msg.payload}")


def on_price_change(msg: Message):
    """Handle price changes."""
    logger.info(f"PRICE CHANGE: {msg.payload}")


def on_crypto_price(msg: Message):
    """Handle crypto price updates."""
    payload = msg.payload
    symbol = payload.get('symbol', 'unknown')
    price = payload.get('price', 0)
    logger.info(f"CRYPTO: {symbol.upper()} = ${price:,.2f}")


def on_rfq_request(msg: Message):
    """Handle RFQ (Request for Quote) requests."""
    logger.info(f"RFQ REQUEST: {msg.payload}")


def main():
    """
    Main execution.

    Subscribes to multiple RTDS streams and monitors events.
    """
    logger.info("Starting RTDS Live Monitoring Example...")

    # Initialize client
    client = PolymarketClient()

    try:
        logger.info("Subscribing to RTDS streams...")

        # Subscribe to trade activity
        client.subscribe_activity_trades(on_trade)
        logger.info("✓ Subscribed to trade activity")

        # Subscribe to market lifecycle events
        client.subscribe_market_created(on_market_created)
        logger.info("✓ Subscribed to market creation events")

        client.subscribe_market_resolved(on_market_resolved)
        logger.info("✓ Subscribed to market resolution events")

        # Subscribe to crypto prices
        for symbol in ["btcusdt", "ethusdt", "solusdt"]:
            client.subscribe_crypto_prices(on_crypto_price, symbol=symbol)
            logger.info(f"✓ Subscribed to {symbol.upper()} prices")

        # Subscribe to RFQ (OTC trading) events
        client.subscribe_rfq_requests(on_rfq_request)
        logger.info("✓ Subscribed to RFQ requests")

        logger.info("\nMonitoring live events (press Ctrl+C to stop)...\n")

        # Keep running
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("\nShutting down gracefully...")

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)

    finally:
        # Cleanup
        try:
            client.unsubscribe_rtds_all()
            client.close()
            logger.info("Cleanup complete")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")


if __name__ == "__main__":
    main()
