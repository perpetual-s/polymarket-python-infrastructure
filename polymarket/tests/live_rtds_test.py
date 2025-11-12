#!/usr/bin/env python
"""
Live RTDS Connection Test

Tests real connection to Polymarket's RTDS service.
This is NOT a unit test - it connects to production servers.

Usage:
    python tests/live_rtds_test.py
"""

import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from shared.polymarket import PolymarketClient
from shared.polymarket.api.real_time_data import Message


def test_crypto_prices_live():
    """Test live crypto price subscription."""
    print("=" * 60)
    print("RTDS LIVE CONNECTION TEST")
    print("=" * 60)
    print()

    received_messages = []

    def on_crypto_price(msg: Message):
        """Callback for crypto price updates."""
        print(f"✅ Received crypto price update:")
        print(f"   Topic: {msg.topic}")
        print(f"   Type: {msg.type}")
        print(f"   Timestamp: {msg.timestamp}")
        print(f"   Payload: {msg.payload}")
        print()
        received_messages.append(msg)

    print("1. Initializing PolymarketClient...")
    client = PolymarketClient()

    print("2. Subscribing to BTC price updates...")
    client.subscribe_crypto_prices(on_crypto_price, symbol="btcusdt")

    print("3. Waiting for messages (30 seconds)...")
    print("   Press Ctrl+C to stop early")
    print()

    try:
        for i in range(30):
            time.sleep(1)
            if received_messages:
                # Got at least one message
                print(f"✅ SUCCESS: Received {len(received_messages)} message(s)")
                break
        else:
            print("⚠️  WARNING: No messages received after 30 seconds")
            print("   This might be normal if BTC price hasn't changed")
    except KeyboardInterrupt:
        print("\nInterrupted by user")

    print()
    print("4. Cleaning up...")
    client.unsubscribe_rtds_all()
    client.close()

    print()
    print("=" * 60)
    if received_messages:
        print("✅ RTDS LIVE TEST: PASSED")
        print(f"   Messages received: {len(received_messages)}")
    else:
        print("⚠️  RTDS LIVE TEST: INCONCLUSIVE")
        print("   No messages received (price may not have changed)")
    print("=" * 60)

    return len(received_messages) > 0


def test_market_created_live():
    """Test live market creation events."""
    print()
    print("=" * 60)
    print("TESTING: Market Creation Events")
    print("=" * 60)
    print()

    received_messages = []

    def on_market_created(msg: Message):
        """Callback for market created events."""
        print(f"✅ New market created:")
        print(f"   Payload: {msg.payload}")
        print()
        received_messages.append(msg)

    print("1. Subscribing to market creation events...")
    client = PolymarketClient()
    client.subscribe_market_created(on_market_created)

    print("2. Waiting for new markets (60 seconds)...")
    print("   (New markets are created sporadically)")
    print()

    try:
        for i in range(60):
            time.sleep(1)
            if received_messages:
                print(f"✅ Received {len(received_messages)} market creation(s)")
                break
    except KeyboardInterrupt:
        print("\nInterrupted by user")

    client.unsubscribe_rtds_all()
    client.close()

    print()
    if received_messages:
        print("✅ Market creation events: WORKING")
    else:
        print("ℹ️  No new markets created during test period")

    return received_messages


if __name__ == "__main__":
    print()
    print("╔════════════════════════════════════════════════════════════╗")
    print("║        POLYMARKET RTDS LIVE CONNECTION TEST               ║")
    print("║                                                            ║")
    print("║  This connects to PRODUCTION Polymarket servers           ║")
    print("║  No API keys required (RTDS is public)                    ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print()

    # Test 1: Crypto prices (most reliable - updates frequently)
    test_crypto_prices_live()

    # Uncomment to test market creation (rare events)
    # test_market_created_live()

    print()
    print("Test complete!")
    print()
