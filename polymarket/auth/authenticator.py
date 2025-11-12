"""
Authentication handler for Polymarket CLOB.

Handles L1 (private key) and L2 (API key) authentication.
Adapted from Polymarket's py-clob-client (MIT License).
"""

import time
import hmac
import hashlib
import base64
from typing import Optional
import logging

from poly_eip712_structs import make_domain
from eth_utils import keccak
from eth_account import Account

from .eip712_models import ClobAuth
from ..exceptions import AuthenticationError

logger = logging.getLogger(__name__)


class Authenticator:
    """
    Handles L1 and L2 authentication for Polymarket CLOB.

    L1: Private key signature for wallet operations
    L2: API key HMAC signature for API requests
    """

    def __init__(self, chain_id: int = 137):
        """
        Initialize authenticator.

        Args:
            chain_id: Polygon chain ID (default: 137)
        """
        self.chain_id = chain_id

    def create_l1_headers(
        self,
        address: str,
        private_key: str,
        timestamp: Optional[int] = None,
        nonce: int = 0
    ) -> dict[str, str]:
        """
        Create L1 authentication headers.

        Uses EIP-712 signature for wallet authentication.
        Implementation matches official py-clob-client.

        Args:
            address: Wallet address
            private_key: Private key for signing
            timestamp: Unix timestamp (uses current time if None)
            nonce: Nonce value (default: 0)

        Returns:
            L1 headers dict

        Raises:
            AuthenticationError: If signing fails
        """
        try:
            if timestamp is None:
                timestamp = int(time.time())

            # Create domain (official implementation)
            domain = make_domain(
                name="ClobAuthDomain",
                version="1",
                chainId=self.chain_id
            )

            # Create ClobAuth message struct (official implementation)
            clob_auth_msg = ClobAuth(
                address=address,
                timestamp=str(timestamp),
                nonce=nonce,
                message="This message attests that I control the given wallet"
            )

            # Hash the struct with domain (official implementation)
            signable_bytes = clob_auth_msg.signable_bytes(domain)
            auth_struct_hash = keccak(signable_bytes)

            # Prepend 0x to hash
            hash_with_prefix = "0x" + auth_struct_hash.hex()

            # Sign the hash (official implementation)
            signature = Account._sign_hash(hash_with_prefix, private_key)

            # Create headers with signature (WITH 0x prefix - matches official py-clob-client)
            headers = {
                "POLY_ADDRESS": address,
                "POLY_SIGNATURE": "0x" + signature.signature.hex(),
                "POLY_TIMESTAMP": str(timestamp),
                "POLY_NONCE": str(nonce),
            }

            logger.debug(f"Created L1 headers for {address}")
            return headers

        except Exception as e:
            # SECURITY: Sanitize error message to prevent credential leakage
            error_type = type(e).__name__
            logger.error(f"Failed to create L1 headers: {error_type}")
            raise AuthenticationError(f"L1 signature failed: {error_type}. Check private key format.")

    def create_l2_headers(
        self,
        address: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        method: str,
        path: str,
        body: str = "",
        timestamp: Optional[int] = None
    ) -> dict[str, str]:
        """
        Create L2 authentication headers.

        Uses HMAC signature for API requests.
        Implementation matches official py-clob-client.

        Args:
            address: Wallet address
            api_key: API key UUID
            api_secret: API secret (base64 encoded)
            api_passphrase: API passphrase
            method: HTTP method (GET, POST, DELETE, etc.)
            path: Request path
            body: Request body (JSON string)
            timestamp: Unix timestamp (uses current time if None)

        Returns:
            L2 headers dict
        """
        try:
            if timestamp is None:
                timestamp = int(time.time())

            # Decode base64 secret (official implementation)
            base64_secret = base64.urlsafe_b64decode(api_secret)

            # Create signature message (official implementation)
            message = str(timestamp) + str(method).upper() + str(path)
            if body:
                # Replace single quotes with double quotes (official implementation)
                # This ensures compatibility with Go and TypeScript implementations
                message += str(body).replace("'", '"')

            # HMAC signature with base64 encoding (official implementation)
            h = hmac.new(
                base64_secret,
                message.encode("utf-8"),
                hashlib.sha256
            )
            signature = base64.urlsafe_b64encode(h.digest()).decode("utf-8")

            headers = {
                "POLY_ADDRESS": address,
                "POLY_SIGNATURE": signature,
                "POLY_TIMESTAMP": str(timestamp),
                "POLY_API_KEY": api_key,
                "POLY_PASSPHRASE": api_passphrase,
            }

            logger.debug(f"Created L2 headers for {method} {path}")
            return headers

        except Exception as e:
            # SECURITY: Sanitize error message to prevent credential leakage
            error_type = type(e).__name__
            logger.error(f"Failed to create L2 headers: {error_type}")
            raise AuthenticationError(f"L2 signature failed: {error_type}. Check API credentials format.")

    def verify_l2_signature(
        self,
        api_secret: str,
        signature: str,
        timestamp: int,
        method: str,
        path: str,
        body: str = ""
    ) -> bool:
        """
        Verify L2 HMAC signature.

        Implementation matches official py-clob-client.

        Args:
            api_secret: API secret (base64 encoded)
            signature: Signature to verify
            timestamp: Request timestamp
            method: HTTP method
            path: Request path
            body: Request body

        Returns:
            True if signature is valid
        """
        # Decode base64 secret (official implementation)
        base64_secret = base64.urlsafe_b64decode(api_secret)

        # Create message (official implementation)
        message = str(timestamp) + method.upper() + path
        if body:
            message += str(body).replace("'", '"')

        # HMAC signature with base64 encoding (official implementation)
        h = hmac.new(
            base64_secret,
            message.encode("utf-8"),
            hashlib.sha256
        )
        expected_signature = base64.urlsafe_b64encode(h.digest()).decode("utf-8")

        return hmac.compare_digest(signature, expected_signature)
