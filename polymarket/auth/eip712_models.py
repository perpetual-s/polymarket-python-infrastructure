"""
EIP-712 struct models for Polymarket CLOB authentication.

Uses poly_eip712_structs library (Polymarket's fork).
Adapted from Polymarket's py-clob-client (MIT License).
"""

from poly_eip712_structs import EIP712Struct, Address, String, Uint


class ClobAuth(EIP712Struct):
    """
    CLOB authentication message structure.

    Used for Level 1 (private key) authentication with Polymarket CLOB.
    """
    address = Address()
    timestamp = String()
    nonce = Uint()
    message = String()
