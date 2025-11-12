"""
Token allowance management for Polymarket trading.

EOA wallets must approve USDC and Conditional Token contracts before trading.
This module provides utilities to check and set required allowances.
"""

import logging
from typing import Dict, List
from web3 import Web3
from web3.contract import Contract
from eth_account import Account

from ..exceptions import InsufficientAllowanceError, ValidationError
from ..ctf.addresses import (
    USDC_ADDRESS,
    CTF_ADDRESS,
    EXCHANGE_CONTRACTS,
)

logger = logging.getLogger(__name__)

# Standard ERC20 ABI (minimal for allowance operations)
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"}
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "value", "type": "uint256"}
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    }
]

# Infinite approval amount (standard practice for trading)
MAX_UINT256 = 2**256 - 1


class AllowanceManager:
    """Manages token allowances for Polymarket trading."""

    def __init__(self, web3_provider: str = "https://polygon-rpc.com"):
        """
        Initialize allowance manager.

        Args:
            web3_provider: Web3 RPC endpoint for Polygon network
        """
        self.web3 = Web3(Web3.HTTPProvider(web3_provider))
        if not self.web3.is_connected():
            logger.warning(f"Web3 provider not connected: {web3_provider}")

        self.usdc = self.web3.eth.contract(
            address=Web3.to_checksum_address(USDC_ADDRESS),
            abi=ERC20_ABI
        )
        self.ctf = self.web3.eth.contract(
            address=Web3.to_checksum_address(CTF_ADDRESS),
            abi=ERC20_ABI
        )

    def check_allowances(
        self,
        wallet_address: str,
        token_type: str = "both"
    ) -> Dict[str, Dict[str, int]]:
        """
        Check current allowances for all exchange contracts.

        Args:
            wallet_address: Wallet address to check
            token_type: "usdc", "ctf", or "both"

        Returns:
            Dict mapping contract addresses to allowance amounts:
            {
                "USDC": {"0x4bF...": 1000000000, ...},
                "CTF": {"0x4bF...": 1000000000, ...}
            }

        Raises:
            ValidationError: If wallet address is invalid
        """
        if not Web3.is_address(wallet_address):
            raise ValidationError(f"Invalid wallet address: {wallet_address}")

        wallet = Web3.to_checksum_address(wallet_address)
        result = {}

        try:
            # Check USDC allowances
            if token_type in ("usdc", "both"):
                usdc_allowances = {}
                for exchange in EXCHANGE_CONTRACTS:
                    exchange_addr = Web3.to_checksum_address(exchange)
                    allowance = self.usdc.functions.allowance(
                        wallet,
                        exchange_addr
                    ).call()
                    usdc_allowances[exchange] = allowance

                result["USDC"] = usdc_allowances

            # Check CTF allowances
            if token_type in ("ctf", "both"):
                ctf_allowances = {}
                for exchange in EXCHANGE_CONTRACTS:
                    exchange_addr = Web3.to_checksum_address(exchange)
                    allowance = self.ctf.functions.allowance(
                        wallet,
                        exchange_addr
                    ).call()
                    ctf_allowances[exchange] = allowance

                result["CTF"] = ctf_allowances

            return result

        except Exception as e:
            logger.error(f"Failed to check allowances for {wallet_address}: {e}")
            raise

    def has_sufficient_allowances(
        self,
        wallet_address: str,
        min_amount: int = MAX_UINT256 // 2
    ) -> Dict[str, bool]:
        """
        Check if wallet has sufficient allowances for trading.

        Args:
            wallet_address: Wallet address to check
            min_amount: Minimum acceptable allowance (default: half of max uint256)

        Returns:
            Dict indicating sufficiency:
            {
                "USDC": True/False,
                "CTF": True/False,
                "ready": True/False (overall)
            }
        """
        allowances = self.check_allowances(wallet_address)

        usdc_sufficient = all(
            amount >= min_amount
            for amount in allowances.get("USDC", {}).values()
        )

        ctf_sufficient = all(
            amount >= min_amount
            for amount in allowances.get("CTF", {}).values()
        )

        return {
            "USDC": usdc_sufficient,
            "CTF": ctf_sufficient,
            "ready": usdc_sufficient and ctf_sufficient
        }

    def set_allowances(
        self,
        private_key: str,
        token_type: str = "both",
        amount: int = MAX_UINT256,
        gas_price_gwei: int = 50
    ) -> List[str]:
        """
        Set allowances for all exchange contracts.

        IMPORTANT: This sends transactions on-chain. Ensure you have MATIC
        for gas fees and understand what you're approving.

        Args:
            private_key: Private key (with 0x prefix)
            token_type: "usdc", "ctf", or "both"
            amount: Approval amount (default: max uint256 for infinite approval)
            gas_price_gwei: Gas price in gwei

        Returns:
            List of transaction hashes

        Raises:
            ValidationError: If parameters are invalid
            Exception: If transaction fails
        """
        # Derive address from private key
        account = Account.from_key(private_key)
        wallet_address = account.address

        logger.info(f"Setting allowances for {wallet_address}")

        tx_hashes = []
        gas_price = self.web3.to_wei(gas_price_gwei, 'gwei')

        try:
            # Get current nonce
            nonce = self.web3.eth.get_transaction_count(wallet_address)

            # Approve USDC
            if token_type in ("usdc", "both"):
                for exchange in EXCHANGE_CONTRACTS:
                    exchange_addr = Web3.to_checksum_address(exchange)

                    # Build transaction
                    tx = self.usdc.functions.approve(
                        exchange_addr,
                        amount
                    ).build_transaction({
                        'from': wallet_address,
                        'nonce': nonce,
                        'gas': 100000,  # Standard for ERC20 approve
                        'gasPrice': gas_price,
                    })

                    # Sign and send
                    signed_tx = self.web3.eth.account.sign_transaction(
                        tx,
                        private_key=private_key
                    )
                    tx_hash = self.web3.eth.send_raw_transaction(
                        signed_tx.rawTransaction
                    )

                    tx_hashes.append(tx_hash.hex())
                    logger.info(f"USDC approval tx for {exchange}: {tx_hash.hex()}")

                    nonce += 1

            # Approve CTF
            if token_type in ("ctf", "both"):
                for exchange in EXCHANGE_CONTRACTS:
                    exchange_addr = Web3.to_checksum_address(exchange)

                    # Build transaction
                    tx = self.ctf.functions.approve(
                        exchange_addr,
                        amount
                    ).build_transaction({
                        'from': wallet_address,
                        'nonce': nonce,
                        'gas': 100000,
                        'gasPrice': gas_price,
                    })

                    # Sign and send
                    signed_tx = self.web3.eth.account.sign_transaction(
                        tx,
                        private_key=private_key
                    )
                    tx_hash = self.web3.eth.send_raw_transaction(
                        signed_tx.rawTransaction
                    )

                    tx_hashes.append(tx_hash.hex())
                    logger.info(f"CTF approval tx for {exchange}: {tx_hash.hex()}")

                    nonce += 1

            logger.info(
                f"Submitted {len(tx_hashes)} approval transactions. "
                f"Wait for confirmations before trading."
            )

            return tx_hashes

        except Exception as e:
            logger.error(f"Failed to set allowances: {e}")
            raise

    def wait_for_approvals(
        self,
        tx_hashes: List[str],
        timeout: int = 300,
        poll_interval: int = 5
    ) -> bool:
        """
        Wait for approval transactions to be mined.

        Args:
            tx_hashes: List of transaction hashes to wait for
            timeout: Maximum wait time in seconds
            poll_interval: How often to check (seconds)

        Returns:
            True if all transactions confirmed

        Raises:
            TimeoutError: If timeout exceeded
        """
        import time

        start = time.time()
        pending = set(tx_hashes)

        while pending and (time.time() - start) < timeout:
            for tx_hash in list(pending):
                try:
                    receipt = self.web3.eth.get_transaction_receipt(tx_hash)
                    if receipt and receipt['status'] == 1:
                        logger.info(f"Transaction {tx_hash} confirmed")
                        pending.remove(tx_hash)
                    elif receipt and receipt['status'] == 0:
                        logger.error(f"Transaction {tx_hash} failed")
                        pending.remove(tx_hash)
                except Exception:
                    # Transaction not yet mined
                    pass

            if pending:
                time.sleep(poll_interval)

        if pending:
            raise TimeoutError(
                f"Timeout waiting for transactions: {list(pending)}"
            )

        logger.info("All approval transactions confirmed")
        return True


def check_wallet_ready(
    wallet_address: str,
    web3_provider: str = "https://polygon-rpc.com"
) -> bool:
    """
    Quick check if wallet is ready for trading.

    Args:
        wallet_address: Wallet address
        web3_provider: Web3 RPC endpoint

    Returns:
        True if wallet has all required allowances
    """
    manager = AllowanceManager(web3_provider)
    status = manager.has_sufficient_allowances(wallet_address)
    return status["ready"]
