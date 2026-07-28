"""
Multi-wallet key management.

Thread-safe manager for multiple wallet credentials.
Adapted from Polymarket's py-clob-client (MIT License).
"""

import threading
from typing import Optional
from dataclasses import dataclass, field
import logging

from ..models import WalletConfig, SignatureType
from ..exceptions import AuthenticationError
from ..wallet_identity import abbreviate_address, resolve_wallet_config

logger = logging.getLogger(__name__)


def _display_wallet_id(wallet_id: str) -> str:
    """Keep logical IDs readable and abbreviate address-shaped IDs."""
    return (
        abbreviate_address(wallet_id)
        if str(wallet_id).lower().startswith("0x")
        else str(wallet_id)
    )


@dataclass
class WalletCredentials:
    """
    Wallet credentials and API keys.

    SECURITY: Sensitive fields are hidden from repr to prevent credential leakage in logs.
    """
    address: str
    private_key: str = field(repr=False)  # SECURITY: Hide from logs
    signature_type: SignatureType
    funder: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = field(default=None, repr=False)  # SECURITY: Hide from logs
    api_passphrase: Optional[str] = field(default=None, repr=False)  # SECURITY: Hide from logs


class KeyManager:
    """
    Thread-safe multi-wallet key manager.

    Manages credentials for multiple wallets used across strategies.
    Each wallet has its own API credentials.
    """

    def __init__(self):
        """Initialize key manager."""
        self._wallets: dict[str, WalletCredentials] = {}
        self._lock = threading.RLock()  # Reentrant lock
        self._default_wallet: Optional[str] = None

    def add_wallet(
        self,
        wallet_config: WalletConfig,
        wallet_id: Optional[str] = None,
        set_default: bool = False
    ) -> str:
        """
        Add wallet credentials.

        Args:
            wallet_config: Wallet configuration
            wallet_id: Unique wallet identifier (uses address if None)
            set_default: Set as default wallet

        Returns:
            Wallet ID

        Raises:
            ValidationError: If wallet config is invalid
            AuthenticationError: If wallet already exists
        """
        try:
            identity = resolve_wallet_config(wallet_config)
            normalized_config = identity.wallet_config
            private_key = normalized_config.private_key.get_secret_value()
            address = identity.signer_address
            funder = identity.funder_address
            signature_type = identity.signature_type

            # Use address as wallet_id if not provided
            if not wallet_id:
                wallet_id = address

            with self._lock:
                # Check if wallet already exists
                if wallet_id in self._wallets:
                    raise AuthenticationError(
                        f"Wallet {_display_wallet_id(wallet_id)} already exists"
                    )

                # Create credentials
                credentials = WalletCredentials(
                    address=address,
                    private_key=private_key,
                    signature_type=signature_type,
                    funder=funder
                )

                self._wallets[wallet_id] = credentials

                # Set as default if requested or first wallet
                if set_default or self._default_wallet is None:
                    self._default_wallet = wallet_id

                logger.info(
                    "Added wallet %s (signer=%s, funder=%s, signature_type=%s)",
                    _display_wallet_id(wallet_id),
                    abbreviate_address(address),
                    abbreviate_address(funder),
                    int(signature_type),
                )

            return wallet_id

        except Exception as e:
            logger.error("Failed to add wallet: %s", e)
            raise

    def remove_wallet(self, wallet_id: str) -> None:
        """
        Remove wallet credentials.

        Args:
            wallet_id: Wallet identifier

        Raises:
            AuthenticationError: If wallet not found
        """
        with self._lock:
            if wallet_id not in self._wallets:
                raise AuthenticationError(
                    f"Wallet {_display_wallet_id(wallet_id)} not found"
                )

            del self._wallets[wallet_id]

            # Update default if removed
            if self._default_wallet == wallet_id:
                self._default_wallet = next(iter(self._wallets), None)

            logger.info("Removed wallet %s", _display_wallet_id(wallet_id))

    def get_wallet(self, wallet_id: Optional[str] = None) -> WalletCredentials:
        """
        Get wallet credentials.

        Args:
            wallet_id: Wallet identifier (uses default if None)

        Returns:
            Wallet credentials

        Raises:
            AuthenticationError: If wallet not found
        """
        with self._lock:
            # Use default if not specified
            if wallet_id is None:
                wallet_id = self._default_wallet
                if wallet_id is None:
                    raise AuthenticationError("No wallets configured")

            if wallet_id not in self._wallets:
                raise AuthenticationError(
                    f"Wallet {_display_wallet_id(wallet_id)} not found"
                )

            return self._wallets[wallet_id]

    def set_api_credentials(
        self,
        wallet_id: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str
    ) -> None:
        """
        Set API credentials for wallet.

        Args:
            wallet_id: Wallet identifier
            api_key: API key UUID
            api_secret: API secret
            api_passphrase: API passphrase

        Raises:
            AuthenticationError: If wallet not found
        """
        with self._lock:
            credentials = self.get_wallet(wallet_id)
            credentials.api_key = api_key
            credentials.api_secret = api_secret
            credentials.api_passphrase = api_passphrase

            logger.info(
                "Set API credentials for wallet %s",
                _display_wallet_id(wallet_id),
            )

    def has_api_credentials(self, wallet_id: Optional[str] = None) -> bool:
        """
        Check if wallet has API credentials.

        Args:
            wallet_id: Wallet identifier (uses default if None)

        Returns:
            True if credentials exist
        """
        try:
            credentials = self.get_wallet(wallet_id)
            return all([
                credentials.api_key,
                credentials.api_secret,
                credentials.api_passphrase
            ])
        except AuthenticationError:
            return False

    def list_wallets(self) -> list[str]:
        """
        List all wallet IDs.

        Returns:
            List of wallet IDs
        """
        with self._lock:
            return list(self._wallets.keys())

    def get_default_wallet(self) -> Optional[str]:
        """
        Get default wallet ID.

        Returns:
            Default wallet ID or None
        """
        return self._default_wallet

    def set_default_wallet(self, wallet_id: str) -> None:
        """
        Set default wallet.

        Args:
            wallet_id: Wallet identifier

        Raises:
            AuthenticationError: If wallet not found
        """
        with self._lock:
            if wallet_id not in self._wallets:
                raise AuthenticationError(
                    f"Wallet {_display_wallet_id(wallet_id)} not found"
                )

            self._default_wallet = wallet_id
            logger.info(
                "Set default wallet to %s",
                _display_wallet_id(wallet_id),
            )

    def clear(self) -> None:
        """Clear all wallets."""
        with self._lock:
            self._wallets.clear()
            self._default_wallet = None
            logger.info("Cleared all wallets")
