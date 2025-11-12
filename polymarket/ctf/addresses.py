"""
Contract addresses for Polymarket Neg-Risk CTF Adapter.

Source: https://github.com/Polymarket/neg-risk-ctf-adapter/blob/main/addresses.json
Network: Polygon Mainnet (Chain ID: 137)
License: MIT
"""

from typing import Dict

# Polygon Mainnet (Chain ID: 137)
CHAIN_ID = 137

# Core CTF Contracts
CTF_ADDRESS = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"
USDC_ADDRESS = "0x2791bca1f2de4661ed88a30c99a7a9449aa84174"

# Neg-Risk CTF Adapter Contracts (Polygon Mainnet)
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEG_RISK_FEE_MODULE = "0x78769D50Be1763ed1CA0D5E878D93f05aabff29e"
NEG_RISK_OPERATOR = "0x71523d0f655B41E805Cec45b17163f528B59B820"
NEG_RISK_VAULT = "0x7f67327E88c258932D7d8f72950bE0d46975E11D"
NEG_RISK_UMA_CTF_ADAPTER = "0x2F5e3684cb1F318ec51b00Edba38d79Ac2c0aA9d"
NEG_RISK_WRAPPED_COLLATERAL = "0x3A3BD7bb9528E159577F7C2e685CC81A765002E2"

# Standard CTF Exchange (for comparison)
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

# Aliases for backward compatibility
NEG_RISK_EXCHANGE = NEG_RISK_CTF_EXCHANGE  # Common alias

# All exchange contracts that need USDC + CTF approval
EXCHANGE_CONTRACTS = [
    CTF_EXCHANGE,  # Standard CTF Exchange
    NEG_RISK_CTF_EXCHANGE,  # Neg-Risk CTF Exchange
    NEG_RISK_ADAPTER,  # Neg-Risk Adapter (for conversions)
]

# Contract groups for easy access
NEG_RISK_CONTRACTS: Dict[str, str] = {
    "adapter": NEG_RISK_ADAPTER,
    "exchange": NEG_RISK_CTF_EXCHANGE,
    "fee_module": NEG_RISK_FEE_MODULE,
    "operator": NEG_RISK_OPERATOR,
    "vault": NEG_RISK_VAULT,
    "uma_ctf_adapter": NEG_RISK_UMA_CTF_ADAPTER,
    "wrapped_collateral": NEG_RISK_WRAPPED_COLLATERAL,
}

CORE_CONTRACTS: Dict[str, str] = {
    "ctf": CTF_ADDRESS,
    "usdc": USDC_ADDRESS,
    "ctf_exchange": CTF_EXCHANGE,
}

ALL_CONTRACTS: Dict[str, str] = {
    **CORE_CONTRACTS,
    **NEG_RISK_CONTRACTS,
}


def get_contract_address(contract_name: str) -> str:
    """
    Get contract address by name.

    Args:
        contract_name: Contract name (e.g., "adapter", "exchange", "ctf", "usdc")

    Returns:
        Contract address

    Raises:
        KeyError: If contract name not found
    """
    return ALL_CONTRACTS[contract_name]


def is_neg_risk_contract(address: str) -> bool:
    """
    Check if address is a neg-risk contract.

    Args:
        address: Contract address (case-insensitive)

    Returns:
        True if address is a neg-risk contract
    """
    address_lower = address.lower()
    return address_lower in [addr.lower() for addr in NEG_RISK_CONTRACTS.values()]
