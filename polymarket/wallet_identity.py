"""Pure wallet identity resolution for Polymarket trading.

The signer is always derived from the private key. Proxy/deposit-wallet
addresses and the CLOB signature type come from the documented environment
convention, but this module performs no network or database I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr

from .exceptions import ValidationError
from .models import SignatureType, WalletConfig
from .utils.validators import validate_address, validate_private_key


@dataclass(frozen=True, slots=True)
class ResolvedWalletIdentity:
    """Canonical public identity plus a normalized secret-bearing config."""

    signer_address: str
    funder_address: str | None
    signature_type: SignatureType
    wallet_config: WalletConfig = field(repr=False)

    @property
    def wallet_type(self) -> str:
        """Coarse wallet type corresponding to the CLOB signature type."""
        return (
            "eoa"
            if self.signature_type is SignatureType.EOA
            else "smart_contract"
        )

    @property
    def funds_address(self) -> str:
        """Address that holds collateral and conditional tokens."""
        return self.funder_address or self.signer_address


@dataclass(frozen=True, slots=True)
class ResolvedWalletRouting:
    """Non-secret CLOB signature and collateral routing."""

    signature_type: SignatureType
    funder_address: str | None

    @property
    def wallet_type(self) -> str:
        return (
            "eoa"
            if self.signature_type is SignatureType.EOA
            else "smart_contract"
        )


def abbreviate_address(address: str | None) -> str:
    """Return a log-safe abbreviated public address."""
    if not address:
        return "<none>"
    value = str(address)
    if len(value) <= 14:
        return value
    return f"{value[:10]}…{value[-4:]}"


def _nonempty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _validated_address(value: str, variable_name: str) -> str:
    try:
        return validate_address(value)
    except Exception:
        raise ValidationError(f"{variable_name} is not a valid Ethereum address") from None


def _derive_signer(private_key: SecretStr | str, variable_name: str) -> tuple[str, str]:
    raw_key = (
        private_key.get_secret_value()
        if isinstance(private_key, SecretStr)
        else str(private_key)
    )
    try:
        normalized_key = validate_private_key(raw_key)
        from eth_account import Account

        signer_address = Account.from_key(normalized_key).address
    except Exception:
        raise ValidationError(f"{variable_name} is not a valid private key") from None
    return normalized_key, signer_address


def _parse_signature_type(
    value: SignatureType | int | str | None,
    variable_name: str,
) -> SignatureType:
    if value is None:
        return SignatureType.EOA
    try:
        raw_value = value.value if isinstance(value, SignatureType) else value
        return SignatureType(int(str(raw_value).strip()))
    except (TypeError, ValueError):
        raise ValidationError(
            f"{variable_name} must be one of 0, 1, 2, or 3"
        ) from None


def resolve_wallet_config(
    wallet_config: WalletConfig,
    *,
    expected_signer_address: str | None = None,
    private_key_name: str = "private_key",
    address_name: str = "address",
) -> ResolvedWalletIdentity:
    """Resolve a ``WalletConfig`` without registering a client or making I/O."""
    normalized_key, signer_address = _derive_signer(
        wallet_config.private_key,
        private_key_name,
    )
    signature_type = _parse_signature_type(
        wallet_config.signature_type,
        "signature_type",
    )

    expected_signer = _nonempty(expected_signer_address)
    if signature_type is SignatureType.EOA and expected_signer is None:
        expected_signer = _nonempty(wallet_config.address)
    if expected_signer is not None:
        validated_signer = _validated_address(expected_signer, address_name)
        if validated_signer.lower() != signer_address.lower():
            raise ValidationError(
                f"{address_name} does not match the signer derived from {private_key_name}"
            )

    funder_address: str | None = None
    if signature_type is not SignatureType.EOA:
        raw_funder = _nonempty(wallet_config.funder) or _nonempty(
            wallet_config.address
        )
        if raw_funder is None:
            raise ValidationError(
                f"Funder address is required for signature type {int(signature_type)}"
            )
        funder_address = _validated_address(raw_funder, "funder")

    normalized_config = WalletConfig(
        private_key=SecretStr(normalized_key),
        address=funder_address if funder_address is not None else signer_address,
        funder=funder_address,
        signature_type=signature_type,
    )
    return ResolvedWalletIdentity(
        signer_address=signer_address,
        funder_address=funder_address,
        signature_type=signature_type,
        wallet_config=normalized_config,
    )


def resolve_wallet_identity_from_env(
    wallet_id: str,
    private_key: SecretStr | str,
    environ: Mapping[str, str],
) -> ResolvedWalletIdentity:
    """Resolve one logical wallet from the canonical env convention."""
    key_name = f"{wallet_id}_PRIVATE_KEY"
    address_name = f"{wallet_id}_ADDRESS"
    routing = resolve_wallet_routing_from_env(wallet_id, environ)

    return resolve_wallet_config(
        WalletConfig(
            private_key=private_key,
            address=routing.funder_address,
            funder=routing.funder_address,
            signature_type=routing.signature_type,
        ),
        expected_signer_address=_nonempty(environ.get(address_name)),
        private_key_name=key_name,
        address_name=address_name,
    )


def resolve_wallet_routing_from_env(
    wallet_id: str,
    environ: Mapping[str, str],
) -> ResolvedWalletRouting:
    """Resolve the non-secret signature/funder portion of a wallet env."""
    funder_name = f"{wallet_id}_FUNDER_ADDRESS"
    proxy_name = f"{wallet_id}_PROXY_ADDRESS"
    signature_name = f"{wallet_id}_SIGNATURE_TYPE"

    explicit_signature = _nonempty(environ.get(signature_name))

    legacy_proxy = _nonempty(environ.get(proxy_name))
    configured_funder = _nonempty(environ.get(funder_name)) or legacy_proxy
    if explicit_signature is not None:
        signature_type = _parse_signature_type(explicit_signature, signature_name)
    elif legacy_proxy is not None:
        signature_type = SignatureType.GNOSIS_SAFE
    else:
        signature_type = SignatureType.EOA

    # EOA collateral lives at the signer. A stray funder-only value without an
    # explicit signature type is intentionally ignored, matching the documented
    # compatibility contract. Types 1-3 require the resolved funds holder.
    effective_funder = (
        configured_funder if signature_type is not SignatureType.EOA else None
    )
    if signature_type is not SignatureType.EOA and effective_funder is None:
        raise ValidationError(
            f"{funder_name} or {proxy_name} is required for signature type "
            f"{int(signature_type)}"
        )

    validated_funder = (
        _validated_address(effective_funder, funder_name)
        if effective_funder is not None
        else None
    )
    return ResolvedWalletRouting(
        signature_type=signature_type,
        funder_address=validated_funder,
    )
