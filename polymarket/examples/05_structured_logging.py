"""
Example 5: Structured Logging for Production

Shows how to enable structured JSON logging for production dashboards.
Critical for log aggregation and debugging.

NEW in v3.1: Automatic credential redaction for security.
"""

import os
from shared.polymarket import PolymarketClient, WalletConfig, OrderRequest, Side
from shared.polymarket.utils.structured_logging import (
    configure_structured_logging,
    set_correlation_id,
    get_logger
)

def main():
    """Structured logging example."""

    # 1. Configure structured logging (do this at startup)
    print("Configuring structured logging...")
    configure_structured_logging(
        level="INFO",
        enable_json=True  # JSON format for production
    )
    print("✓ Structured logging enabled")
    print("✓ Credential redaction enabled (v3.1)\n")

    # ========================================
    # NEW in v3.1: CREDENTIAL REDACTION DEMO
    # ========================================
    print("--- Credential Redaction Demo (v3.1) ---\n")

    logger = get_logger("security.demo")

    # These would be DANGEROUS to log without redaction
    # The filter automatically redacts them:

    print("1. Testing private key redaction:")
    logger.info("wallet_config", "Wallet configured",
                private_key="0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef")
    print("   ↑ Private key should be [REDACTED]\n")

    print("2. Testing API secret redaction:")
    logger.info("api_config", "API configured",
                api_secret="super_secret_api_key_12345",
                api_key="public_key_ok_to_show")
    print("   ↑ api_secret should be [REDACTED], api_key shown\n")

    print("3. Testing passphrase redaction:")
    logger.info("wallet_unlock", "Unlocking wallet",
                passphrase="my_secret_passphrase_123")
    print("   ↑ passphrase should be [REDACTED]\n")

    print("4. Testing multiple credentials in one message:")
    logger.info("full_config", "Complete configuration",
                private_key="0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
                api_secret="secret123",
                passphrase="pass456",
                wallet_address="0x1234567890123456789012345678901234567890")
    print("   ↑ All secrets [REDACTED], address shown\n")

    print("✓ All credentials automatically redacted!")
    print("✓ Safe to store logs in databases or send to monitoring services\n")

    print("--- End Credential Redaction Demo ---\n")

    # 2. Get structured logger
    logger = get_logger("strategy1.trading")

    # 3. Set correlation ID (useful for tracing requests)
    correlation_id = set_correlation_id()  # Generates unique ID
    print(f"Correlation ID: {correlation_id}\n")

    # 4. Initialize client
    client = PolymarketClient()

    # 5. Add wallet
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    if not private_key:
        logger.error(
            "missing_credentials",
            "Private key not found in environment",
            required_var="POLYMARKET_PRIVATE_KEY"
        )
        return

    client.add_wallet(
        WalletConfig(private_key=private_key),
        wallet_id="strategy1"
    )

    logger.info(
        "wallet_added",
        "Wallet initialized successfully",
        wallet_id="strategy1"
    )

    # 6. Place order with structured logging
    print("\n--- Structured Log Output (JSON): ---\n")

    try:
        order = OrderRequest(
            token_id="123456",
            price=0.55,
            size=10.0,
            side=Side.BUY
        )

        # Log order attempt
        logger.info(
            "order_attempt",
            "Attempting to place order",
            order_id=correlation_id,
            token_id=order.token_id,
            side=order.side.value,
            price=order.price,
            size=order.size,
            wallet="strategy1"
        )

        response = client.place_order(order, wallet_id="strategy1")

        if response.success:
            # Log success
            logger.info(
                "order_placed",
                "Order placed successfully",
                order_id=response.order_id,
                status=response.status.value if response.status else "unknown",
                token_id=order.token_id,
                price=order.price,
                size=order.size,
                wallet="strategy1"
            )
        else:
            # Log failure
            logger.error(
                "order_rejected",
                "Order rejected by exchange",
                order_id=response.order_id,
                error_msg=response.error_msg,
                token_id=order.token_id,
                wallet="strategy1"
            )

    except Exception as e:
        # Log exception with traceback
        logger.exception(
            "order_exception",
            "Unexpected error during order placement",
            wallet="strategy1",
            token_id="123456"
        )

    print("\n--- End of Logs ---\n")

    print("""
These JSON logs can be:
- Stored in PostgreSQL for querying
- Sent to Elasticsearch for aggregation
- Parsed by log aggregators (Datadog, Splunk)
- Filtered by correlation_id for request tracing
- Queried by field: event="order_rejected" AND wallet="strategy1"
""")

    # 7. Example PostgreSQL integration
    print("\nExample PostgreSQL Integration:")
    print("""
import psycopg2
import json

# Parse JSON log
log_entry = json.loads(log_line)

# Store in database
conn = psycopg2.connect(...)
cursor.execute(\"\"\"
    INSERT INTO strategy1_logs
    (timestamp, level, event, correlation_id, data)
    VALUES (%s, %s, %s, %s, %s)
\"\"\", (
    log_entry['timestamp'],
    log_entry['level'],
    log_entry['event'],
    log_entry.get('correlation_id'),
    json.dumps(log_entry)
))

# Later: Query logs
cursor.execute(\"\"\"
    SELECT * FROM strategy1_logs
    WHERE correlation_id = %s
    ORDER BY timestamp
\"\"\", (correlation_id,))

# Get full request trace
for row in cursor:
    print(row)
""")

if __name__ == "__main__":
    main()
