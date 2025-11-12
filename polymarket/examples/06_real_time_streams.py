"""
Real-Time Data Streams Example

Demonstrates 12+ WebSocket streams from Polymarket:
- Live trades and order matching
- Comments and sentiment
- Crypto prices for hedging
- Market lifecycle events
- User orders (authenticated)

Based on Phase 2: Real-Time Data Client
"""

import time
import json
from shared.polymarket.api.real_time_data import (
    RealTimeDataClient,
    ConnectionStatus,
    StreamHelpers,
    ClobApiKeyCreds
)


# ========== Example 1: Market Trades (Strategy-2 use case) ==========

def example_market_trades():
    """Track live trades for a specific market."""
    print("\n=== Example 1: Live Market Trades ===\n")

    def on_trade(client, message):
        """Handle incoming trade."""
        payload = message.payload
        print(f"[{message.topic}/{message.type}] Trade executed:")
        print(f"  Market: {payload.get('title')}")
        print(f"  Side: {payload.get('side')}")
        print(f"  Price: ${payload.get('price'):.2f}")
        print(f"  Size: {payload.get('size')} shares")
        print(f"  User: {payload.get('pseudonym', 'Anonymous')}")
        print()

    client = RealTimeDataClient(
        on_connect=lambda c: StreamHelpers.subscribe_to_market_trades(
            c,
            market_slug="trump-2024-election"
        ),
        on_message=on_trade
    )

    client.connect()

    # Run for 60 seconds
    try:
        time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()


# ========== Example 2: Event Comments (Sentiment Analysis) ==========

def example_event_comments():
    """Monitor comments for sentiment analysis."""
    print("\n=== Example 2: Event Comments (Sentiment) ===\n")

    def on_comment(client, message):
        """Handle comment events."""
        payload = message.payload

        if message.type == "comment_created":
            print(f"[NEW COMMENT]")
            print(f"  Body: {payload.get('body')[:100]}...")
            print(f"  User: {payload.get('userAddress')}")
            print()

        elif message.type == "reaction_created":
            print(f"[REACTION] {payload.get('reactionType')} {payload.get('icon')}")
            print()

    client = RealTimeDataClient(
        on_connect=lambda c: StreamHelpers.subscribe_to_event_comments(
            c,
            event_id=100,  # Replace with actual event ID
            parent_type="Event"
        ),
        on_message=on_comment
    )

    client.connect()

    try:
        time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()


# ========== Example 3: Crypto Prices (Strategy-1 Hedging) ==========

def example_crypto_prices():
    """Monitor BTC/ETH prices for cross-platform hedging."""
    print("\n=== Example 3: Crypto Prices (Hedging) ===\n")

    prices = {}

    def on_price_update(client, message):
        """Handle crypto price updates."""
        payload = message.payload
        symbol = payload.get('symbol')
        value = payload.get('value')

        prices[symbol] = value

        print(f"[PRICE UPDATE] {symbol.upper()}: ${value:,.2f}")
        print(f"  Current prices: {prices}")
        print()

    def on_connect(client):
        """Subscribe to multiple crypto pairs."""
        StreamHelpers.subscribe_to_crypto_price(client, "btcusdt")
        StreamHelpers.subscribe_to_crypto_price(client, "ethusdt")
        StreamHelpers.subscribe_to_crypto_price(client, "solusdt")

    client = RealTimeDataClient(
        on_connect=on_connect,
        on_message=on_price_update
    )

    client.connect()

    try:
        time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()


# ========== Example 4: Market Lifecycle Events ==========

def example_market_lifecycle():
    """Monitor new markets and resolutions."""
    print("\n=== Example 4: Market Lifecycle ===\n")

    def on_market_event(client, message):
        """Handle market lifecycle events."""
        payload = message.payload

        if message.type == "market_created":
            print(f"[NEW MARKET CREATED]")
            print(f"  Market: {payload.get('market')}")
            print(f"  Token IDs: {payload.get('asset_ids')}")
            print(f"  Min order size: {payload.get('min_order_size')}")
            print()

        elif message.type == "market_resolved":
            print(f"[MARKET RESOLVED]")
            print(f"  Market: {payload.get('market')}")
            print(f"  Asset IDs: {payload.get('asset_ids')}")
            print()

    def on_connect(client):
        """Subscribe to lifecycle events."""
        StreamHelpers.subscribe_to_new_markets(client)
        StreamHelpers.subscribe_to_market_resolutions(client)

    client = RealTimeDataClient(
        on_connect=on_connect,
        on_message=on_market_event
    )

    client.connect()

    try:
        time.sleep(300)  # Run for 5 minutes
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()


# ========== Example 5: Price Changes (Strategy-1) ==========

def example_price_changes():
    """Monitor price changes for arbitrage opportunities."""
    print("\n=== Example 5: Price Changes (Arbitrage) ===\n")

    def on_price_change(client, message):
        """Handle price change events."""
        payload = message.payload

        for change in payload.get('pc', []):  # pc = price changes
            print(f"[PRICE CHANGE]")
            print(f"  Token: {change.get('a')}")  # a = asset_id
            print(f"  Price: ${change.get('p')}")  # p = price
            print(f"  Side: {change.get('s')}")    # s = side
            print(f"  Best Bid: ${change.get('bb')}")  # bb = best_bid
            print(f"  Best Ask: ${change.get('ba')}")  # ba = best_ask
            print(f"  Spread: ${float(change.get('ba', 0)) - float(change.get('bb', 0)):.4f}")
            print()

    # Example token IDs (replace with actual)
    token_ids = [
        "71321045679252212594626385532706912750332728571942532289631379312455583992833",
        "48331043336612883890938759509493159234755048973500640148014422747788308965732"
    ]

    client = RealTimeDataClient(
        on_connect=lambda c: StreamHelpers.subscribe_to_price_changes(c, token_ids),
        on_message=on_price_change
    )

    client.connect()

    try:
        time.sleep(120)
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()


# ========== Example 6: Aggregated Orderbook Updates ==========

def example_orderbook_stream():
    """Stream orderbook updates for multiple markets."""
    print("\n=== Example 6: Orderbook Streams ===\n")

    def on_orderbook_update(client, message):
        """Handle orderbook updates."""
        payload = message.payload

        print(f"[ORDERBOOK UPDATE]")
        print(f"  Market: {payload.get('market')}")
        print(f"  Token: {payload.get('asset_id')}")
        print(f"  Hash: {payload.get('hash')}")

        # Best prices
        bids = payload.get('bids', [])
        asks = payload.get('asks', [])

        if bids:
            best_bid = bids[0]
            print(f"  Best Bid: ${best_bid['price']} ({best_bid['size']} shares)")

        if asks:
            best_ask = asks[0]
            print(f"  Best Ask: ${best_ask['price']} ({best_ask['size']} shares)")

        if bids and asks:
            spread = float(asks[0]['price']) - float(bids[0]['price'])
            print(f"  Spread: ${spread:.4f}")

        print()

    # Example token IDs
    token_ids = [
        "71321045679252212594626385532706912750332728571942532289631379312455583992833"
    ]

    client = RealTimeDataClient(
        on_connect=lambda c: StreamHelpers.subscribe_to_market_orderbook(c, token_ids),
        on_message=on_orderbook_update
    )

    client.connect()

    try:
        time.sleep(120)
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()


# ========== Example 7: User Orders (Authenticated) ==========

def example_user_orders():
    """Monitor your own orders (requires API credentials)."""
    print("\n=== Example 7: User Orders (Authenticated) ===\n")

    # IMPORTANT: Replace with your actual API credentials
    clob_auth = ClobApiKeyCreds(
        key="your-api-key",
        secret="your-api-secret",
        passphrase="your-passphrase"
    )

    def on_user_event(client, message):
        """Handle user order/trade events."""
        payload = message.payload

        if message.type == "order":
            print(f"[ORDER UPDATE]")
            print(f"  Order ID: {payload.get('id')}")
            print(f"  Market: {payload.get('market')}")
            print(f"  Side: {payload.get('side')}")
            print(f"  Price: ${payload.get('price')}")
            print(f"  Size: {payload.get('original_size')}")
            print(f"  Status: {payload.get('status')}")
            print(f"  Type: {payload.get('type')}")  # PLACEMENT, CANCELLATION, FILL
            print()

        elif message.type == "trade":
            print(f"[TRADE EXECUTION]")
            print(f"  Match ID: {payload.get('id')}")
            print(f"  Market: {payload.get('market')}")
            print(f"  Price: ${payload.get('price')}")
            print(f"  Size: {payload.get('size')}")
            print(f"  Status: {payload.get('status')}")  # MINED
            print(f"  TX Hash: {payload.get('transaction_hash')}")
            print()

    client = RealTimeDataClient(
        on_connect=lambda c: c.subscribe(
            topic="clob_user",
            type="*",
            clob_auth=clob_auth
        ),
        on_message=on_user_event
    )

    client.connect()

    try:
        time.sleep(300)
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()


# ========== Example 8: Connection Status Monitoring ==========

def example_connection_monitoring():
    """Monitor connection status with auto-reconnect."""
    print("\n=== Example 8: Connection Monitoring ===\n")

    def on_status_change(status):
        """Handle connection status changes."""
        print(f"[STATUS] {status.value}")

    def on_connect(client):
        """Setup subscriptions after connect."""
        print("[CONNECTED] Setting up subscriptions...")
        client.subscribe("activity", "trades")
        client.subscribe("clob_market", "market_created")

    def on_message(client, message):
        """Handle messages."""
        print(f"[{message.topic}/{message.type}] Message received")

    client = RealTimeDataClient(
        on_connect=on_connect,
        on_message=on_message,
        on_status_change=on_status_change,
        auto_reconnect=True,  # Auto-reconnect on disconnect
        ping_interval=5.0      # Ping every 5 seconds
    )

    client.connect()

    try:
        # Run indefinitely
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Disconnecting...")
        client.disconnect()


# ========== Example 9: Multi-Stream Dashboard ==========

def example_multi_stream_dashboard():
    """Subscribe to multiple streams in one client."""
    print("\n=== Example 9: Multi-Stream Dashboard ===\n")

    stats = {
        "trades": 0,
        "comments": 0,
        "price_updates": 0,
        "new_markets": 0
    }

    def on_message(client, message):
        """Handle all message types."""
        if message.topic == "activity":
            stats["trades"] += 1
        elif message.topic == "comments":
            stats["comments"] += 1
        elif message.topic == "crypto_prices":
            stats["price_updates"] += 1
        elif message.topic == "clob_market" and message.type == "market_created":
            stats["new_markets"] += 1

        # Print stats every 10 messages
        total = sum(stats.values())
        if total % 10 == 0:
            print(f"\n[STATS] Total messages: {total}")
            print(f"  Trades: {stats['trades']}")
            print(f"  Comments: {stats['comments']}")
            print(f"  Price updates: {stats['price_updates']}")
            print(f"  New markets: {stats['new_markets']}")

    def on_connect(client):
        """Subscribe to multiple streams."""
        print("[SETUP] Subscribing to multiple streams...")

        # Market data
        client.subscribe("activity", "trades")
        client.subscribe("activity", "orders_matched")

        # Sentiment
        client.subscribe("comments", "*")

        # Prices
        StreamHelpers.subscribe_to_crypto_price(client, "btcusdt")
        StreamHelpers.subscribe_to_crypto_price(client, "ethusdt")

        # Market lifecycle
        StreamHelpers.subscribe_to_new_markets(client)
        StreamHelpers.subscribe_to_market_resolutions(client)

        print("[READY] All subscriptions active")

    client = RealTimeDataClient(
        on_connect=on_connect,
        on_message=on_message
    )

    client.connect()

    try:
        time.sleep(300)  # Run for 5 minutes
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\n[FINAL STATS]")
        print(f"  Total messages: {sum(stats.values())}")
        print(f"  Breakdown: {stats}")
        client.disconnect()


# ========== Run Examples ==========

if __name__ == "__main__":
    import sys

    examples = {
        "1": ("Market Trades", example_market_trades),
        "2": ("Event Comments", example_event_comments),
        "3": ("Crypto Prices", example_crypto_prices),
        "4": ("Market Lifecycle", example_market_lifecycle),
        "5": ("Price Changes", example_price_changes),
        "6": ("Orderbook Stream", example_orderbook_stream),
        "7": ("User Orders", example_user_orders),
        "8": ("Connection Monitoring", example_connection_monitoring),
        "9": ("Multi-Stream Dashboard", example_multi_stream_dashboard),
    }

    print("\n=== Polymarket Real-Time Data Streams ===\n")
    print("Available examples:")
    for key, (name, _) in examples.items():
        print(f"  {key}. {name}")
    print()

    choice = input("Select example (1-9) or 'all': ").strip()

    if choice == "all":
        for key in sorted(examples.keys()):
            name, func = examples[key]
            print(f"\n\n{'='*60}")
            print(f"Running: {name}")
            print('='*60)
            func()
    elif choice in examples:
        _, func = examples[choice]
        func()
    else:
        print("Invalid choice")
        sys.exit(1)
