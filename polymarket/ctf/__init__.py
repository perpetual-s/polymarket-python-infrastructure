"""
Conditional Token Framework (CTF) integration.

Provides smart contract interfaces for:
- NegRiskAdapter: NO→YES token conversions for mutually exclusive markets
- NegRiskOperator: Market preparation and resolution
- WrappedCollateral: USDC wrapper for neg-risk markets

Official repository: https://github.com/Polymarket/neg-risk-ctf-adapter
License: MIT
"""

from .addresses import (
    NEG_RISK_ADAPTER,
    NEG_RISK_OPERATOR,
    NEG_RISK_EXCHANGE,
    NEG_RISK_FEE_MODULE,
    NEG_RISK_VAULT,
    NEG_RISK_UMA_CTF_ADAPTER,
    NEG_RISK_WRAPPED_COLLATERAL,
    CTF_ADDRESS,
    USDC_ADDRESS,
)

from .adapter import NegRiskAdapter
from .utils import ConversionCalculator, is_safe_to_trade

__all__ = [
    # Contract addresses
    "NEG_RISK_ADAPTER",
    "NEG_RISK_OPERATOR",
    "NEG_RISK_EXCHANGE",
    "NEG_RISK_FEE_MODULE",
    "NEG_RISK_VAULT",
    "NEG_RISK_UMA_CTF_ADAPTER",
    "NEG_RISK_WRAPPED_COLLATERAL",
    "CTF_ADDRESS",
    "USDC_ADDRESS",
    # Classes
    "NegRiskAdapter",
    "ConversionCalculator",
    # Utilities
    "is_safe_to_trade",
]
