"""
Order builder with EIP-712 signing — CLOB V2.

Implements V2 order construction and signing in-repo (py_order_utils is
V1-only). Struct, domain, and wire shape mirror the official
py-clob-client-v2 `ExchangeOrderBuilderV2`; known-answer vectors pin the
byte-level behavior in tests/test_clob_v2_signing.py.

V2 (live since 2026-04-28): the signed struct drops `taker`, `expiration`,
`nonce`, and `feeRateBps` and adds `timestamp` (creation ms; per-address
uniqueness), `metadata`, `builder`. `expiration` still rides the wire body
for GTD but is NOT signed. Domain version is "2"; collateral is pUSD.
"""

import hashlib
import time
from typing import Optional, Dict, Any
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_HALF_UP,
)
import logging

from ..models import OrderRequest, Side, OrderType
from ..exceptions import ValidationError, TradingError
from ..utils.validators import validate_order
from ..utils.cache import MarketMetadataCache

logger = logging.getLogger(__name__)


# Order constants
BUY = 0
SELL = 1

# V2 exchange addresses (Polygon mainnet; docs.polymarket.com/resources/contracts,
# cross-checked against py-clob-client-v2 config.py, 2026-07-18)
EXCHANGE_ADDRESS = "0xE111180000d2663C0091e4f400237545B87B996B"  # CTF Exchange V2
NEG_RISK_EXCHANGE_ADDRESS = "0xe2222d279d744050d28e00520010520000310F59"  # Neg Risk CTF Exchange V2
COLLATERAL_TOKEN = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # pUSD

# V2 EIP-712 typed-data constants (reference: py-clob-client-v2
# order_utils/model/ctf_exchange_v2_typed_data.py — this field ORDER is what
# the exchange verifies; the docs prose misplaces signatureType)
V2_DOMAIN_NAME = "Polymarket CTF Exchange"
V2_DOMAIN_VERSION = "2"
BYTES32_ZERO = "0x" + "00" * 32

ORDER_STRUCT = [
    {"name": "salt", "type": "uint256"},
    {"name": "maker", "type": "address"},
    {"name": "signer", "type": "address"},
    {"name": "tokenId", "type": "uint256"},
    {"name": "makerAmount", "type": "uint256"},
    {"name": "takerAmount", "type": "uint256"},
    {"name": "side", "type": "uint8"},
    {"name": "signatureType", "type": "uint8"},
    {"name": "timestamp", "type": "uint256"},
    {"name": "metadata", "type": "bytes32"},
    {"name": "builder", "type": "bytes32"},
]
ORDER_TYPE_STRING = (
    "Order(" + ",".join(f"{f['type']} {f['name']}" for f in ORDER_STRUCT) + ")"
)
EIP712_DOMAIN = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]

TYPED_DATA_SIGN_STRUCT = [
    {"name": "contents", "type": "Order"},
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
    {"name": "salt", "type": "bytes32"},
]

SIGNATURE_TYPE_POLY_1271 = 3

# Per-tick precision (reference: py-clob-client-v2 order_builder/builder.py
# ROUNDING_CONFIG). size is always 2 decimals (token side); amount is the
# USD-side decimal count.
ROUNDING_CONFIG = {
    "0.1": {"price": 1, "size": 2, "amount": 3},
    "0.01": {"price": 2, "size": 2, "amount": 4},
    "0.005": {"price": 3, "size": 2, "amount": 5},
    "0.0025": {"price": 4, "size": 2, "amount": 6},
    "0.001": {"price": 3, "size": 2, "amount": 5},
    "0.0001": {"price": 4, "size": 2, "amount": 6},
}


def _rounding_config_for_tick(tick_size: Decimal) -> Dict[str, int]:
    """Return exact official rounding metadata or reject unknown market data."""
    config = ROUNDING_CONFIG.get(str(tick_size.normalize()))
    if config is None:
        raise ValidationError(f"Unsupported CLOB tick size: {tick_size}")
    return config


def _divisors_for_tick(tick_size: Decimal) -> tuple:
    """Return (token_divisor, usd_divisor) for 6-decimal atomic amounts."""
    config = _rounding_config_for_tick(tick_size)
    return 10 ** (6 - config["size"]), 10 ** (6 - config["amount"])


class OrderBuilder:
    """
    Builds and signs orders for Polymarket CLOB.

    Handles:
    - Order construction
    - Tick size validation
    - Fee rate resolution
    - Nonce management
    - EIP-712 signing (when web3 available)
    """

    def __init__(
        self,
        chain_id: int = 137,
        exchange: str = EXCHANGE_ADDRESS,
        metadata_cache: Optional[MarketMetadataCache] = None
    ):
        """
        Initialize order builder.

        Args:
            chain_id: Polygon chain ID (137 for mainnet)
            exchange: Exchange contract address
            metadata_cache: Optional metadata cache
        """
        self.chain_id = chain_id
        self.exchange = exchange
        self.metadata_cache = metadata_cache or MarketMetadataCache()

    def build_order(
        self,
        order: OrderRequest,
        private_key: str,
        address: str,
        nonce: int = 0,
        tick_size: Optional[Decimal] = None,
        fee_rate_bps: Optional[int] = None,
        neg_risk: bool = False,
        idempotency_key: Optional[str] = None,
        signature_type: int = 0,
        funder: Optional[str] = None,
        timestamp_ms: Optional[int] = None,
        salt_override: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Build and sign a CLOB V2 order.

        Args:
            order: Order request
            private_key: Private key for signing
            address: Signer (EOA) address
            nonce: Ignored — V2 removed nonce from the signed struct.
                   Accepted for call compatibility.
            tick_size: Market tick size (fetched if not provided)
            fee_rate_bps: Ignored in signing — V2 removed feeRateBps from the
                          struct. Fee cost math lives in utils/fees.py.
            neg_risk: Negative risk flag (selects the V2 neg-risk exchange)
            idempotency_key: Optional key for deterministic salt generation.
                           Full retry identity also requires the caller to reuse
                           ``timestamp_ms`` and the same order payload.
            signature_type: 0=EOA, 1=Polymarket proxy, 2=Gnosis Safe,
                3=POLY_1271 deposit wallet
            funder: Funds-holding address (proxy); maker defaults to `address`
            timestamp_ms: Exact order-creation timestamp override; defaults to
                          the current time in milliseconds.
            salt_override: Exact salt override (tests); defaults to
                          idempotency-key-derived or random salt

        Returns:
            Wire-ready signed order dict (V2 shape) plus a private
            ``_orderHash`` key — the EIP-712 order digest, which IS the
            exchange ``orderID``, computable before submission. Transport
            strips underscore keys.

        Raises:
            ValidationError: If order parameters invalid
            TradingError: If signing fails
        """
        try:
            # Validate order
            validate_order(
                order.token_id,
                order.price,
                order.size,
                order.side.value
            )

            # Resolve tick size
            if tick_size is None:
                tick_size = self._resolve_tick_size(order.token_id)

            # Active facade callers normalize computed prices before balance
            # reservation. The signer remains strict so direct callers cannot
            # hide an off-grid limit behind implicit rounding.
            _rounding_config_for_tick(tick_size)
            price = order.price

            # Validate price against tick size
            if not self._price_valid(price, tick_size):
                raise ValidationError(
                    f"Price {price} invalid for tick size {tick_size}. "
                    f"Must be between {tick_size} and {1 - tick_size}"
                )

            # CRITICAL: Explicit zero-price validation before division
            if price <= 0:
                raise ValidationError(
                    f"Price must be positive, got {price}. "
                    f"Valid range: [{tick_size}, {1 - tick_size}]"
                )

            # Calculate amounts (using Decimal for precision)
            # CRITICAL: Per official py-clob-client, size = number of tokens (NOT USD)
            side_int = BUY if order.side == Side.BUY else SELL

            # Precision is tick-size dependent (C2 carry): fine-tick markets
            # need finer USD-side rounding or the exchange rejects amounts.
            token_divisor, usd_divisor = _divisors_for_tick(tick_size)

            if side_int == BUY:
                # BUY: size = tokens to buy
                # taker_amount = tokens to receive (what we get)
                # maker_amount = collateral to pay (what we give)
                taker_amount = self._to_amount(order.size, neg_risk)
                taker_amount = self._round_to_precision(taker_amount, token_divisor)

                # Recalculate maker amount from rounded taker amount for consistency
                taker_amount_decimal = Decimal(str(taker_amount)) / Decimal("1e6")
                maker_amount_decimal = taker_amount_decimal * price
                maker_amount = self._to_wei(maker_amount_decimal)
                maker_amount = self._round_to_precision(maker_amount, usd_divisor)
            else:
                # SELL: size = tokens to sell
                # maker_amount = tokens to give (what we sell)
                # taker_amount = collateral to receive (what we get)
                maker_amount = self._to_amount(order.size, neg_risk)
                maker_amount = self._round_to_precision(maker_amount, token_divisor)

                # Recalculate taker amount from rounded maker amount for consistency
                maker_amount_decimal = Decimal(str(maker_amount)) / Decimal("1e6")
                taker_amount_decimal = maker_amount_decimal * price
                taker_amount = self._to_wei(taker_amount_decimal)
                taker_amount = self._round_to_precision(taker_amount, usd_divisor)

            # Calculate expiration based on order type
            # GTC (Good Till Canceled) → expiration = 0
            # GTD (Good Till Date) → expiration = Unix timestamp
            if order.order_type == OrderType.GTD:
                # GTD orders use provided expiration or default to 30 days
                expiration = order.expiration if order.expiration else int(time.time()) + (30 * 24 * 60 * 60)
            else:
                # GTC, FOK, FAK orders must have expiration = 0
                expiration = 0

            # Existing proxy/safe wallets keep the EOA in the signer field.
            # POLY_1271 is different: both maker and signer are the deposit
            # wallet, while its owner/session EOA signs an ERC-7739-wrapped
            # TypedDataSign payload that the wallet validates via ERC-1271.
            maker_address = funder if funder else address
            if signature_type == SIGNATURE_TYPE_POLY_1271 and not funder:
                raise ValidationError("POLY_1271 orders require a deposit-wallet funder")
            signer_address = (
                maker_address
                if signature_type == SIGNATURE_TYPE_POLY_1271
                else address
            )

            # Salt: explicit override (tests) > deterministic from idempotency
            # key (retry safety) > random. Always 32-bit (JS-safe).
            if salt_override is not None:
                salt = int(salt_override)
            else:
                salt = self.generate_salt_from_key(idempotency_key)

            # V2: creation timestamp in MILLISECONDS — per-address uniqueness
            # (replaces nonce; not an expiration). Callers that rebuild an
            # order on retry must reuse an explicit timestamp_ms because the
            # timestamp contributes to the signed order hash.
            timestamp = (
                int(timestamp_ms)
                if timestamp_ms is not None
                else time.time_ns() // 1_000_000
            )

            # Select exchange based on neg_risk flag
            # CRITICAL: neg_risk markets use a different exchange contract
            exchange = NEG_RISK_EXCHANGE_ADDRESS if neg_risk else self.exchange

            typed_data = self._build_typed_data(
                exchange=exchange,
                salt=salt,
                maker=maker_address,
                signer=signer_address,
                token_id=int(order.token_id),
                maker_amount=int(maker_amount),
                taker_amount=int(taker_amount),
                side=side_int,
                signature_type=int(signature_type),
                timestamp=timestamp,
            )
            signature, order_hash = self._sign_typed_data(typed_data, private_key)

            order_dict = {
                "salt": salt,
                "maker": maker_address,
                "signer": signer_address,
                "tokenId": str(order.token_id),
                "makerAmount": str(int(maker_amount)),
                "takerAmount": str(int(taker_amount)),
                "side": "BUY" if side_int == BUY else "SELL",
                "expiration": str(int(expiration)),
                "signatureType": int(signature_type),
                "timestamp": str(timestamp),
                "metadata": BYTES32_ZERO,
                "builder": BYTES32_ZERO,
                "signature": signature,
                # Private: the EIP-712 digest == exchange orderID, known
                # BEFORE submit (persist-before-submit identity). Stripped
                # by transport.
                "_orderHash": order_hash,
            }

            logger.info(
                f"Built V2 order: {order.side.value} {order.size} @ {price} "
                f"(token={order.token_id}, hash={order_hash[:10]}…)"
            )

            return order_dict

        except ValidationError:
            raise
        except TradingError:
            raise
        except Exception as e:
            # Private-key material crossed the signing boundary above. A
            # signer/library exception may echo raw input, so expose only the
            # exception type and sever the original exception chain.
            error_type = type(e).__name__
            logger.error(f"Failed to build order: {error_type}")
            raise TradingError(f"Failed to build order: {error_type}") from None

    def _build_typed_data(
        self,
        *,
        exchange: str,
        salt: int,
        maker: str,
        signer: str,
        token_id: int,
        maker_amount: int,
        taker_amount: int,
        side: int,
        signature_type: int,
        timestamp: int,
    ) -> Dict[str, Any]:
        """EIP-712 typed data exactly as py-clob-client-v2 builds it."""
        return {
            "primaryType": "Order",
            "types": {
                "EIP712Domain": EIP712_DOMAIN,
                "Order": ORDER_STRUCT,
            },
            "domain": {
                "name": V2_DOMAIN_NAME,
                "version": V2_DOMAIN_VERSION,
                "chainId": self.chain_id,
                "verifyingContract": exchange,
            },
            "message": {
                "salt": salt,
                "maker": maker,
                "signer": signer,
                "tokenId": token_id,
                "makerAmount": maker_amount,
                "takerAmount": taker_amount,
                "side": side,
                "signatureType": signature_type,
                "timestamp": timestamp,
                "metadata": bytes(32),
                "builder": bytes(32),
            },
        }

    @staticmethod
    def _sign_typed_data(typed_data: Dict[str, Any], private_key: str) -> tuple:
        """Sign a V2 order and return ``(wire_signature, order_hash)``.

        Types 0-2 use the normal 65-byte EIP-712 signature. Type 3 mirrors
        Polymarket's official ``py-clob-client-v2`` 1.1 implementation: sign
        the nested ``TypedDataSign`` payload and append the ERC-7739 trailer
        consumed by the deposit wallet's ERC-1271 validator.
        """
        from eth_account import Account
        from eth_account.messages import encode_typed_data
        from eth_utils import keccak

        encoded = encode_typed_data(full_message=typed_data)
        order_hash = keccak(
            primitive=b"\x19" + encoded.version + encoded.header + encoded.body
        )
        if int(typed_data["message"]["signatureType"]) != SIGNATURE_TYPE_POLY_1271:
            signed = Account.sign_message(encoded, private_key=private_key)
            return "0x" + signed.signature.hex(), "0x" + order_hash.hex()

        wrapped_typed_data = {
            "primaryType": "TypedDataSign",
            "types": {
                "EIP712Domain": EIP712_DOMAIN,
                "Order": ORDER_STRUCT,
                "TypedDataSign": TYPED_DATA_SIGN_STRUCT,
            },
            "domain": typed_data["domain"],
            "message": {
                "contents": typed_data["message"],
                "name": "DepositWallet",
                "version": "1",
                "chainId": typed_data["domain"]["chainId"],
                "verifyingContract": typed_data["message"]["signer"],
                "salt": bytes(32),
            },
        }
        wrapped = encode_typed_data(full_message=wrapped_typed_data)
        signed = Account.sign_message(wrapped, private_key=private_key)
        contents_type = ORDER_TYPE_STRING.encode("utf-8")
        trailer = (
            encoded.header
            + encoded.body
            + contents_type
            + len(contents_type).to_bytes(2, "big")
        )
        return (
            "0x" + signed.signature.hex() + trailer.hex(),
            "0x" + order_hash.hex(),
        )

    def _resolve_tick_size(self, token_id: str) -> Decimal:
        """
        Resolve an already-fetched tick size for a direct builder caller.

        Args:
            token_id: Token ID

        Returns:
            Tick size (Decimal)
        """
        cached = self.metadata_cache.get_tick_size(token_id)
        if cached is None:
            raise ValidationError(
                f"Tick size metadata is required for token {token_id}"
            )
        return cached if isinstance(cached, Decimal) else Decimal(str(cached))

    def _price_valid(self, price: Decimal, tick_size: Decimal) -> bool:
        """
        Validate price against tick size.

        Args:
            price: Order price (Decimal)
            tick_size: Market tick size (Decimal)

        Returns:
            True if valid
        """
        # Bounds check
        if price < tick_size or price > (Decimal("1") - tick_size):
            return False

        try:
            # Price should be divisible by tick size
            remainder = price % tick_size
            return remainder == 0
        except (ValueError, InvalidOperation) as e:
            # BUG FIX (P1-2): Raise error instead of returning True
            # Invalid prices should be rejected, not allowed
            logger.error(f"Decimal validation failed for price {price}, tick {tick_size}: {e}")
            from ..exceptions import ValidationError
            raise ValidationError(f"Invalid price or tick size: price={price}, tick_size={tick_size}, error={e}")

    def generate_salt_from_key(self, idempotency_key: Optional[str]) -> int:
        """
        Generate deterministic salt from idempotency key.

        If idempotency_key is None, generates random salt (backward compatible).
        If provided, uses SHA-256 hash of key to generate deterministic 32-bit salt.
        This stabilizes only the salt; timestamp and normalized payload are
        separate signed fields and must also match to reproduce an order hash.

        CRITICAL: Polymarket API (TypeScript/JavaScript-based) cannot handle integers larger
        than 2^53 - 1 (Number.MAX_SAFE_INTEGER). We use 32-bit salts to ensure compatibility.

        Args:
            idempotency_key: Unique identifier (e.g., database UUID)
                           None for random salt

        Returns:
            32-bit integer salt (0 to 4,294,967,295)

        Example:
            >>> builder = OrderBuilder()
            >>> # Deterministic salt input
            >>> salt1 = builder.generate_salt_from_key("550e8400-e29b-41d4-a716-446655440000")
            >>> salt2 = builder.generate_salt_from_key("550e8400-e29b-41d4-a716-446655440000")
            >>> assert salt1 == salt2  # Same key → same salt
            >>>
            >>> # Random salt (backward compatible)
            >>> salt3 = builder.generate_salt_from_key(None)
            >>> salt4 = builder.generate_salt_from_key(None)
            >>> assert salt3 != salt4  # Random
        """
        if idempotency_key is None:
            # Random 32-bit salt (JavaScript-safe)
            import secrets
            return secrets.randbits(32)

        # Deterministic: hash the key to get 32-bit salt
        # SHA-256 produces 256 bits, we take first 4 bytes = 32 bits
        hash_bytes = hashlib.sha256(idempotency_key.encode("utf-8")).digest()

        # Convert first 4 bytes to 32-bit integer
        salt = int.from_bytes(hash_bytes[:4], byteorder="big")

        return salt

    def _round_to_precision(self, amount: int, divisor: int) -> int:
        """
        Round amount to nearest multiple of divisor.

        Polymarket requires specific decimal precision for order amounts:
        - BUY: maker (USDC) divisible by 100, taker (tokens) divisible by 10,000
        - SELL: maker (tokens) divisible by 10,000, taker (USDC) divisible by 100

        Args:
            amount: Amount in smallest unit (int)
            divisor: Rounding precision (e.g., 100 for 4 decimals, 10000 for 2 decimals)

        Returns:
            Rounded amount (int)

        Example:
            >>> self._round_to_precision(1020202, 10000)  # Round to nearest 10,000
            1020000  # 1.02 tokens → 1.02 tokens (rounded down)
        """
        # Round to nearest multiple of divisor
        return (amount // divisor) * divisor

    def _to_wei(self, amount: Decimal) -> int:
        """
        Convert USDC amount to wei (6 decimals) with Decimal precision.

        Args:
            amount: Amount in USDC (Decimal)

        Returns:
            Amount in wei (int)
        """
        # Use Decimal arithmetic to avoid float precision loss
        wei = amount * Decimal("1e6")
        # Round to nearest integer (banker's rounding)
        return int(wei.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def _to_amount(self, size: Decimal, neg_risk: bool) -> int:
        """
        Convert size to token amount with Decimal precision.

        Args:
            size: Size in tokens (Decimal)
            neg_risk: Negative risk flag

        Returns:
            Amount in smallest unit (int)
        """
        # Conditional tokens use 6 decimals
        amount = size * Decimal("1e6")
        # Round to nearest integer
        return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def set_tick_size(self, token_id: str, tick_size: Decimal) -> None:
        """
        Manually set tick size for token.

        Args:
            token_id: Token ID
            tick_size: Tick size (Decimal)
        """
        self.metadata_cache.set_tick_size(token_id, tick_size)

    def set_fee_rate(self, token_id: str, fee_rate_bps: int) -> None:
        """
        Manually set fee rate for token.

        Args:
            token_id: Token ID
            fee_rate_bps: Fee rate in basis points
        """
        self.metadata_cache.set_fee_rate(token_id, fee_rate_bps)

    def set_neg_risk(self, token_id: str, neg_risk: bool) -> None:
        """
        Manually set negative risk flag for token.

        Args:
            token_id: Token ID
            neg_risk: Negative risk flag
        """
        self.metadata_cache.set_neg_risk(token_id, neg_risk)
