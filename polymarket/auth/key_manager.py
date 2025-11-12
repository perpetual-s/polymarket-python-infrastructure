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
from ..exceptions import AuthenticationError, ValidationError
from ..utils.validators import validate_private_key, validate_address

logger = logging.getLogger(__name__)


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
            # Validate private key
            private_key = validate_private_key(wallet_config.private_key)

            # ALWAYS derive signer address from private key
            # This is the address that signs transactions (EOA address)
            try:
                from web3 import Web3
                account = Web3().eth.account.from_key(private_key)
                signer_address = account.address
            except ImportError:
                raise AuthenticationError(
                    "web3 not installed, cannot derive address from private key"
                )

            # For proxy wallets: address field = proxy address, funder = not used
            # For EOA wallets: address field = EOA address, funder = None
            # Official py-clob-client convention:
            #   - Signer always uses EOA's private key and address
            #   - For proxy wallets, the "funder" is the proxy address (confusing name!)
            funder = None
            if wallet_config.signature_type in (SignatureType.MAGIC, SignatureType.PROXY):
                # For proxy wallets, the provided address IS the proxy address (funder)
                if not wallet_config.address:
                    raise ValidationError(
                        f"Proxy address required in 'address' field for signature type "
                        f"{wallet_config.signature_type}"
                    )
                funder = validate_address(wallet_config.address)  # Store proxy as funder
                # Use signer address for authentication
                address = signer_address
            else:
                # For EOA wallets, use signer address
                address = signer_address

            # Use address as wallet_id if not provided
            if not wallet_id:
                wallet_id = address

            with self._lock:
                # Check if wallet already exists
                if wallet_id in self._wallets:
                    raise AuthenticationError(f"Wallet {wallet_id} already exists")

                # Create credentials
                credentials = WalletCredentials(
                    address=address,
                    private_key=private_key,
                    signature_type=wallet_config.signature_type,
                    funder=funder
                )

                self._wallets[wallet_id] = credentials

                # Set as default if requested or first wallet
                if set_default or self._default_wallet is None:
                    self._default_wallet = wallet_id

                logger.info(
                    f"Added wallet {wallet_id} ({address}) "
                    f"with signature type {wallet_config.signature_type}"
                )

            return wallet_id

        except Exception as e:
            logger.error(f"Failed to add wallet: {e}")
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
                raise AuthenticationError(f"Wallet {wallet_id} not found")

            del self._wallets[wallet_id]

            # Update default if removed
            if self._default_wallet == wallet_id:
                self._default_wallet = next(iter(self._wallets), None)

            logger.info(f"Removed wallet {wallet_id}")

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
                raise AuthenticationError(f"Wallet {wallet_id} not found")

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

            logger.info(f"Set API credentials for wallet {wallet_id}")

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
                raise AuthenticationError(f"Wallet {wallet_id} not found")

            self._default_wallet = wallet_id
            logger.info(f"Set default wallet to {wallet_id}")

    def clear(self) -> None:
        """Clear all wallets."""
        with self._lock:
            self._wallets.clear()
            self._default_wallet = None
            logger.info("Cleared all wallets")
