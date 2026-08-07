"""Pure wallet identity resolution: derived, never trusted.

`KeyManager.add_wallet` routes through this module, so a defect here mislabels
every wallet the caller records. The signer is always derived from the private
key; a configured address is only ever a claim to check against it.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from polymarket.exceptions import ValidationError
from polymarket.models import SignatureType, WalletConfig
from polymarket.wallet_identity import (
    abbreviate_address,
    resolve_wallet_config,
    resolve_wallet_identity_from_env,
    resolve_wallet_routing_from_env,
)

PRIVATE_KEY = "0x" + "2" * 64
SIGNER = "0x1563915e194D8CfBA1943570603F7606A3115508"
FUNDER = "0x" + "3" * 40
UNRELATED = "0x" + "9" * 40


# ---------------------------------------------------------------------------
# abbreviate_address
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "<none>"),
        ("", "<none>"),
        ("primary", "primary"),
        (SIGNER, "0x1563915e…5508"),
    ],
)
def test_abbreviate_address(value, expected):
    assert abbreviate_address(value) == expected


def test_abbreviation_is_short_enough_to_be_useless_alone():
    abbreviated = abbreviate_address(SIGNER)
    assert len(abbreviated) < len(SIGNER)
    assert SIGNER not in abbreviated


# ---------------------------------------------------------------------------
# resolve_wallet_config
# ---------------------------------------------------------------------------


def test_eoa_signer_is_derived_not_taken_from_the_config():
    identity = resolve_wallet_config(WalletConfig(private_key=PRIVATE_KEY))

    assert identity.signer_address == SIGNER
    assert identity.funder_address is None
    assert identity.signature_type is SignatureType.EOA
    assert identity.wallet_type == "eoa"
    assert identity.funds_address == SIGNER


def test_eoa_config_whose_address_is_not_the_signer_is_refused():
    with pytest.raises(ValidationError, match="does not match the signer"):
        resolve_wallet_config(
            WalletConfig(private_key=PRIVATE_KEY, address=UNRELATED)
        )


def test_proxy_wallet_keeps_the_derived_signer_and_the_configured_funder():
    identity = resolve_wallet_config(
        WalletConfig(
            private_key=PRIVATE_KEY,
            address=FUNDER,
            funder=FUNDER,
            signature_type=SignatureType.GNOSIS_SAFE,
        )
    )

    assert identity.signer_address == SIGNER
    assert identity.funder_address == FUNDER
    assert identity.wallet_type == "smart_contract"
    assert identity.funds_address == FUNDER
    # The normalized config is what actually reaches the signer.
    assert identity.wallet_config.funder == FUNDER
    assert identity.wallet_config.signature_type is SignatureType.GNOSIS_SAFE


def test_proxy_wallet_without_a_funder_is_refused():
    with pytest.raises(ValidationError, match="Funder address is required"):
        resolve_wallet_config(
            WalletConfig(
                private_key=PRIVATE_KEY,
                signature_type=SignatureType.POLY_1271,
            )
        )


def test_malformed_private_key_is_refused_without_echoing_it():
    with pytest.raises(ValidationError) as exc:
        resolve_wallet_config(WalletConfig(private_key="0x" + "z" * 64))
    assert "z" * 64 not in str(exc.value)


def test_resolved_identity_does_not_repr_the_secret():
    identity = resolve_wallet_config(WalletConfig(private_key=PRIVATE_KEY))
    assert PRIVATE_KEY not in repr(identity)


# ---------------------------------------------------------------------------
# Environment convention
# ---------------------------------------------------------------------------


def test_env_routing_defaults_to_eoa():
    routing = resolve_wallet_routing_from_env("WALLET_X", {})
    assert routing.signature_type is SignatureType.EOA
    assert routing.funder_address is None
    assert routing.wallet_type == "eoa"


def test_env_routing_infers_gnosis_safe_from_a_legacy_proxy_address():
    routing = resolve_wallet_routing_from_env(
        "WALLET_X", {"WALLET_X_PROXY_ADDRESS": FUNDER}
    )
    assert routing.signature_type is SignatureType.GNOSIS_SAFE
    assert routing.funder_address == FUNDER


def test_env_routing_prefers_the_explicit_signature_type():
    routing = resolve_wallet_routing_from_env(
        "WALLET_X",
        {"WALLET_X_PROXY_ADDRESS": FUNDER, "WALLET_X_SIGNATURE_TYPE": "3"},
    )
    assert routing.signature_type is SignatureType.POLY_1271
    assert routing.funder_address == FUNDER


def test_env_routing_ignores_a_funder_when_the_wallet_is_explicitly_eoa():
    """Collateral for an EOA lives at the signer; a stray funder is not routing."""
    routing = resolve_wallet_routing_from_env(
        "WALLET_X",
        {"WALLET_X_FUNDER_ADDRESS": FUNDER, "WALLET_X_SIGNATURE_TYPE": "0"},
    )
    assert routing.signature_type is SignatureType.EOA
    assert routing.funder_address is None


def test_env_routing_rejects_a_malformed_funder():
    with pytest.raises(ValidationError, match="WALLET_X_FUNDER_ADDRESS"):
        resolve_wallet_routing_from_env(
            "WALLET_X",
            {"WALLET_X_FUNDER_ADDRESS": "not-an-address",
             "WALLET_X_SIGNATURE_TYPE": "2"},
        )


def test_env_identity_checks_the_configured_address_against_the_key():
    with pytest.raises(ValidationError, match="WALLET_X_ADDRESS"):
        resolve_wallet_identity_from_env(
            "WALLET_X",
            SecretStr(PRIVATE_KEY),
            {"WALLET_X_ADDRESS": UNRELATED, "WALLET_X_SIGNATURE_TYPE": "0"},
        )


def test_env_identity_resolves_the_full_signing_triple():
    identity = resolve_wallet_identity_from_env(
        "WALLET_X",
        SecretStr(PRIVATE_KEY),
        {
            "WALLET_X_ADDRESS": SIGNER,
            "WALLET_X_FUNDER_ADDRESS": FUNDER,
            "WALLET_X_SIGNATURE_TYPE": "2",
        },
    )

    assert identity.signer_address == SIGNER
    assert identity.funder_address == FUNDER
    assert int(identity.signature_type) == 2
