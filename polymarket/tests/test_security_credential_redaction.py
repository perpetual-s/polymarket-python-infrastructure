"""
Test credential redaction in logs and error messages.

This test suite ensures that private keys, API secrets, and other credentials
are never exposed in logs, exceptions, or debug output.

Security Issue: SEC-001 (Critical)
- Private keys can leak through exception stack traces
- Credentials can appear in log messages
- Debug output can expose sensitive data
"""

import json
import logging
import traceback
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import pytest

from polymarket.exceptions import (
    APIError,
    AuthenticationError,
    InsufficientAllowanceError,
)
from polymarket.redaction import redact_text, redact_value
from polymarket.utils.structured_logging import (
    CredentialRedactionFilter,
    RedactingJsonFormatter,
    StructuredFormatter,
)


class TestCredentialRedactionFilter:
    """Test credential redaction in logging."""

    def test_filter_redacts_private_keys_in_logs(self):
        """Private keys should be redacted from log messages."""
        # Setup logger with our filter
        logger = logging.getLogger("test_redaction")
        logger.setLevel(logging.DEBUG)

        # Capture log output
        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(logging.Formatter("%(message)s"))

        # Add our redaction filter
        handler.addFilter(CredentialRedactionFilter())
        logger.addHandler(handler)

        # Test with fake private key (64 hex chars)
        private_key = "0x" + "a" * 64
        logger.info(f"Processing wallet with key: {private_key}")

        # Check that private key is redacted
        log_output = log_stream.getvalue()
        assert private_key not in log_output, "Private key should be redacted!"
        assert "0x[REDACTED]" in log_output, "Should show redacted placeholder"

        # Cleanup
        logger.removeHandler(handler)

    def test_filter_redacts_api_secrets(self):
        """API secrets should be redacted from logs."""
        logger = logging.getLogger("test_api_secret")
        logger.setLevel(logging.DEBUG)

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(CredentialRedactionFilter())
        logger.addHandler(handler)

        # Test with base64 API secret
        api_secret = (
            "dGhpc2lzYXNlY3JldGtleXRoYXRpc3Zlcnlsb25nYW5kc2hvdWxkYmVyZWRhY3RlZA=="
        )
        logger.info(f"API credentials: secret={api_secret}")

        log_output = log_stream.getvalue()
        assert api_secret not in log_output, "API secret should be redacted!"
        assert "[REDACTED]" in log_output, "Should show redacted placeholder"

        logger.removeHandler(handler)

    def test_filter_redacts_percent_style_arguments_without_breaking_format(self):
        """Short secrets supplied as logging arguments are redacted safely."""
        logger = logging.getLogger("test_argument_secret")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(CredentialRedactionFilter())
        logger.addHandler(handler)

        api_secret = "tiny-secret"
        try:
            logger.info("api_secret=%s attempt=%d", api_secret, 3)
        finally:
            logger.removeHandler(handler)

        log_output = log_stream.getvalue()
        assert api_secret not in log_output
        assert "api_secret=[REDACTED] attempt=3" in log_output

    def test_filter_handles_multiple_credentials_in_one_message(self):
        """Multiple credentials in one message should all be redacted."""
        logger = logging.getLogger("test_multiple")
        logger.setLevel(logging.DEBUG)

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(CredentialRedactionFilter())
        logger.addHandler(handler)

        # Multiple credentials
        pk1 = "0x" + "a" * 64
        pk2 = "0x" + "b" * 64
        secret = "somesecretapikey1234567890"

        logger.info(f"Wallets: {pk1}, {pk2}, secret: {secret}")

        log_output = log_stream.getvalue()
        assert pk1 not in log_output, "First private key should be redacted"
        assert pk2 not in log_output, "Second private key should be redacted"
        assert secret not in log_output, "API secret should be redacted"

        logger.removeHandler(handler)

    def test_filter_preserves_normal_log_messages(self):
        """Normal log messages without credentials should pass through."""
        logger = logging.getLogger("test_normal")
        logger.setLevel(logging.DEBUG)

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(CredentialRedactionFilter())
        logger.addHandler(handler)

        normal_message = "Processing 10 orders for market 0x123"
        logger.info(normal_message)

        log_output = log_stream.getvalue()
        assert normal_message in log_output, "Normal messages should pass through"

        logger.removeHandler(handler)

    def test_filter_redacts_in_exception_messages(self):
        """Credentials in formatted exception tracebacks should be redacted."""
        logger = logging.getLogger("test_exception")
        logger.setLevel(logging.DEBUG)

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(CredentialRedactionFilter())
        logger.addHandler(handler)

        private_key = "0x" + "c" * 64

        try:
            raise ValueError(f"Authentication failed with key: {private_key}")
        except ValueError:
            logger.exception("Signing failed")

        log_output = log_stream.getvalue()
        assert private_key not in log_output, (
            "Private key in exception should be redacted"
        )
        assert "ValueError" in log_output, "Exception type should remain useful"
        assert "Authentication failed with key: 0x[REDACTED]" in log_output

        logger.removeHandler(handler)

    def test_structured_fields_redact_short_secrets_and_dsn(self):
        """Structured fields are recursively redacted before serialization."""

        class CredentialCarrier:
            def __str__(self):
                return "postgresql://custom:object-password@localhost/downstream_project"

        logger = logging.getLogger("test_structured_fields")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(StructuredFormatter())
        handler.addFilter(CredentialRedactionFilter())
        logger.addHandler(handler)

        api_secret = "short-secret"
        dsn = "postgresql://operator:hunter2@localhost:5432/downstream_project"
        try:
            logger.error(
                "Database request failed",
                extra={
                    "extra_fields": {
                        "credentials": {"api_secret": api_secret},
                        "database_url": dsn,
                        "carrier": CredentialCarrier(),
                    }
                },
            )
        finally:
            logger.removeHandler(handler)

        raw_output = log_stream.getvalue()
        payload = json.loads(raw_output)
        assert api_secret not in raw_output
        assert dsn not in raw_output
        assert "hunter2" not in raw_output
        assert "object-password" not in raw_output
        assert payload["credentials"]["api_secret"] == "[REDACTED]"
        assert payload["database_url"] == "[REDACTED]"

    def test_structured_exception_output_redacts_message_and_traceback(self):
        """Structured exception fields preserve context without credentials."""
        logger = logging.getLogger("test_structured_exception")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(StructuredFormatter())
        handler.addFilter(CredentialRedactionFilter())
        logger.addHandler(handler)

        private_key = "0x" + "e" * 64
        try:
            try:
                raise RuntimeError(f"Signer rejected private_key={private_key}")
            except RuntimeError:
                logger.exception("Unable to create signature")
        finally:
            logger.removeHandler(handler)

        raw_output = log_stream.getvalue()
        payload = json.loads(raw_output)
        assert private_key not in raw_output
        assert payload["exception"]["type"] == "RuntimeError"
        assert payload["exception"]["message"] == (
            "Signer rejected private_key=[REDACTED]"
        )
        assert (
            "RuntimeError: Signer rejected private_key=[REDACTED]"
            in payload["exception"]["traceback"]
        )

    def test_filtered_handler_removes_raw_exception_for_later_handlers(self):
        """One safe handler must not leave raw exc_info for a later sink."""

        class CapturingHandler(logging.Handler):
            def __init__(self):
                super().__init__()
                self.seen_exc_info = object()
                self.rendered = ""

            def emit(self, record):
                self.seen_exc_info = record.exc_info
                self.rendered = self.format(record)

        logger = logging.getLogger("test_multi_handler_exception")
        logger.setLevel(logging.ERROR)
        logger.propagate = False
        first_stream = StringIO()
        first = logging.StreamHandler(first_stream)
        first.setFormatter(logging.Formatter("%(message)s"))
        first.addFilter(CredentialRedactionFilter())
        second = CapturingHandler()
        second.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(first)
        logger.addHandler(second)
        secret = "multi-handler-secret"
        try:
            try:
                raise RuntimeError(f"credential={secret}")
            except RuntimeError:
                logger.exception("signing failed")
        finally:
            logger.removeHandler(first)
            logger.removeHandler(second)

        assert second.seen_exc_info is None
        assert secret not in first_stream.getvalue()
        assert secret not in second.rendered
        assert "credential=[REDACTED]" in second.rendered

    def test_default_json_formatter_redacts_raw_exception_and_arbitrary_extras(self):
        """Default JSON output is safe even without the handler filter."""
        from polymarket.logging_config import DEFAULT_LOGGING_CONFIG

        class OpaqueExtra:
            __slots__ = ()

            def __str__(self):
                return "default-json-object-secret"

        private_key = "0x" + "d" * 64
        address = "0x" + "1" * 40
        condition_id = "0x" + "f" * 64
        dsn = "postgresql://operator:hunter2@localhost:5432/downstream_project"
        secrets = {
            "credentials": "tiny-secret",
            "api_credentials": "tiny-api-secret",
            "cookie": "session-secret",
            "clob_auth": {"key": "poly-key-123", "address": address},
        }

        formatter_config = DEFAULT_LOGGING_CONFIG["formatters"]["json"]
        formatter = formatter_config["()"](fmt=formatter_config["format"])
        assert isinstance(formatter, RedactingJsonFormatter)

        logger = logging.getLogger("test_default_json_formatter")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(formatter)
        # Deliberately omit CredentialRedactionFilter: the formatter itself is
        # the last output boundary and must remain safe.
        logger.addHandler(handler)
        try:
            try:
                raise RuntimeError(f"signer rejected private_key={private_key}")
            except RuntimeError:
                logger.exception(
                    "request failed",
                    extra={
                        **secrets,
                        "database_url": dsn,
                        "wallet_address": address,
                        "condition_id": condition_id,
                        "untyped_hex": condition_id,
                        "source": "clob_public",
                        "opaque": OpaqueExtra(),
                    },
                )
        finally:
            logger.removeHandler(handler)

        raw_output = stream.getvalue()
        payload = json.loads(raw_output)
        for secret in (
            private_key,
            "tiny-secret",
            "tiny-api-secret",
            "session-secret",
            "poly-key-123",
            "default-json-object-secret",
            dsn,
            "hunter2",
        ):
            assert secret not in raw_output
        assert payload["credentials"] == "[REDACTED]"
        assert payload["api_credentials"] == "[REDACTED]"
        assert payload["cookie"] == "[REDACTED]"
        assert payload["clob_auth"]["key"] == "[REDACTED]"
        assert payload["clob_auth"]["address"] == "[REDACTED]"
        assert payload["wallet_address"] == address
        assert payload["condition_id"] == condition_id
        assert payload["untyped_hex"] == "0x[REDACTED]"
        assert payload["source"] == "clob_public"
        assert payload["opaque"] == "<OpaqueExtra>"
        assert "private_key=[REDACTED]" in payload["exc_info"]

    def test_filter_performance_on_large_messages(self):
        """
        Test that redaction filter doesn't significantly slow down logging.
        """
        logger = logging.getLogger("test_performance")
        logger.setLevel(logging.DEBUG)

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.addFilter(CredentialRedactionFilter())
        logger.addHandler(handler)

        # Large message with no credentials
        large_message = "Normal log message " * 1000

        import time

        start = time.time()
        for _ in range(100):
            logger.info(large_message)
        duration = time.time() - start

        # Should process 100 large messages in less than 1 second
        assert duration < 1.0, (
            f"Redaction filter too slow: {duration}s for 100 messages"
        )

        logger.removeHandler(handler)


class TestWalletCredentialsRepr:
    """Test that WalletCredentials doesn't leak in __repr__."""

    def test_repr_hides_private_key(self):
        """WalletCredentials.__repr__ should hide private_key."""
        from polymarket.auth.key_manager import WalletCredentials
        from polymarket.models import SignatureType

        creds = WalletCredentials(
            address="0x1234567890123456789012345678901234567890",
            private_key="0x" + "a" * 64,
            signature_type=SignatureType.EOA,
        )

        repr_str = repr(creds)
        assert "0x" + "a" * 64 not in repr_str, "Private key should not appear in repr"
        assert "address=" in repr_str, "Address should appear (it's public)"

    def test_repr_hides_api_secret(self):
        """API secret should be hidden from __repr__."""
        from polymarket.auth.key_manager import WalletCredentials
        from polymarket.models import SignatureType

        creds = WalletCredentials(
            address="0x1234567890123456789012345678901234567890",
            private_key="0xprivatekey123",
            signature_type=SignatureType.EOA,
            api_secret="supersecretkey12345",
        )

        repr_str = repr(creds)
        assert "supersecretkey12345" not in repr_str, (
            "API secret should not appear in repr"
        )

    def test_str_also_hides_credentials(self):
        """__str__ should also hide credentials."""
        from polymarket.auth.key_manager import WalletCredentials
        from polymarket.models import SignatureType

        creds = WalletCredentials(
            address="0x1234567890123456789012345678901234567890",
            private_key="0x" + "b" * 64,
            signature_type=SignatureType.EOA,
            api_passphrase="mypassphrase",
        )

        str_repr = str(creds)
        assert "0x" + "b" * 64 not in str_repr, "Private key should not appear in str"
        assert "mypassphrase" not in str_repr, "Passphrase should not appear in str"


class TestExceptionSanitization:
    """Test that exceptions don't leak credentials."""

    @pytest.mark.parametrize(
        ("message", "secret"),
        [
            (
                f"Failed to sign with key: {'0x' + 'd' * 64}",
                "0x" + "d" * 64,
            ),
            ("Exchange rejected api_key=client-key-123", "client-key-123"),
            ('Exchange rejected api_passphrase="two words"', "two words"),
            (
                "Database unavailable at "
                "postgresql://operator:hunter2@localhost:5432/downstream_project",
                "hunter2",
            ),
        ],
    )
    def test_exception_message_args_and_repr_are_sanitized(self, message, secret):
        """Base exception state never retains common credential forms."""
        error = AuthenticationError(message)

        for rendered in (str(error), repr(error), error.message, repr(error.args)):
            assert secret not in rendered
            assert "[REDACTED]" in rendered

    def test_nested_api_error_details_are_sanitized(self):
        """Nested response payloads redact secrets but retain public IDs."""
        private_key = "0x" + "a" * 64
        api_secret = "short-secret"
        dsn = "postgresql://operator:hunter2@localhost:5432/downstream_project"
        condition_id = "0x" + "f" * 64
        error = APIError(
            "Upstream authentication failed",
            status_code=401,
            response={
                "api_secret": api_secret,
                "POLY_API_KEY": "poly-key-123",
                "database_url": dsn,
                "condition_id": condition_id,
                "nested": {"message": f"private_key={private_key}"},
            },
        )

        rendered = repr(error.details) + repr(error.response)
        assert private_key not in rendered
        assert api_secret not in rendered
        assert "poly-key-123" not in rendered
        assert dsn not in rendered
        assert "hunter2" not in rendered
        assert error.response["api_secret"] == "[REDACTED]"
        assert error.response["POLY_API_KEY"] == "[REDACTED]"
        assert error.response["database_url"] == "[REDACTED]"
        assert error.response["condition_id"] == condition_id
        assert error.response["nested"]["message"] == "private_key=[REDACTED]"

    def test_sensitive_containers_and_custom_objects_are_sanitized(self):
        """Container semantics and object reprs cannot bypass sanitization."""

        @dataclass
        class CredentialEnvelope:
            key: str
            database_url: str
            address: str

        class OpaqueSecret:
            __slots__ = ()

            def __str__(self):
                return "opaque-tiny-secret"

        address = "0x" + "2" * 40
        dsn = "postgresql://custom:object-password@localhost/downstream_project"
        error = APIError(
            "bad response",
            response={
                "credentials": "tiny-secret",
                "api_credentials": "tiny-api-secret",
                "cookie": "session-secret",
                "clob_auth": {"key": "poly-key-123", "address": address},
                "custom": CredentialEnvelope("object-key", dsn, address),
                "opaque": OpaqueSecret(),
            },
        )

        rendered = repr(error.response)
        for secret in (
            "tiny-secret",
            "tiny-api-secret",
            "session-secret",
            "poly-key-123",
            "object-key",
            "object-password",
            "opaque-tiny-secret",
        ):
            assert secret not in rendered
        assert error.response["credentials"] == "[REDACTED]"
        assert error.response["api_credentials"] == "[REDACTED]"
        assert error.response["cookie"] == "[REDACTED]"
        assert error.response["clob_auth"]["key"] == "[REDACTED]"
        assert error.response["clob_auth"]["address"] == "[REDACTED]"
        assert error.response["custom"]["address"] == "[REDACTED]"

    def test_recovery_tokens_and_key_material_fields_are_sensitive(self):
        """Credential-like token and recovery fields fail closed."""
        token_id = "0x" + "7" * 64
        secret_fields = {
            "token": "plain-token-secret",
            "bearer_token": "bearer-secret",
            "csrf_token": "csrf-secret",
            "id_token": "id-secret",
            "secret_key": "signing-secret",
            "credential": "credential-secret",
            "seed_phrase": "twelve words would be secret",
            "mnemonic": "recovery words are secret",
        }

        sanitized = redact_value({**secret_fields, "token_id": token_id})

        for field, secret in secret_fields.items():
            assert sanitized[field] == "[REDACTED]"
            assert secret not in redact_text(f"{field}='{secret}'")
        assert sanitized["token_id"] == token_id
        source_line = "raise RuntimeError('csrf_token=tiny-source-secret')"
        assert "tiny-source-secret" not in redact_text(source_line)

    def test_public_id_exemption_is_scalar_and_cannot_escape_secret_container(self):
        """Typed IDs do not make nested or credential subtrees public."""
        address = "0x" + "8" * 40
        token_id = "0x" + "9" * 64

        sanitized = redact_value(
            {
                "token_id": token_id,
                "condition_id": {"nested": token_id},
                "market_id": [token_id],
                "credentials": {
                    "address": address,
                    "token_id": token_id,
                },
            }
        )

        assert sanitized["token_id"] == token_id
        assert sanitized["condition_id"]["nested"] == "0x[REDACTED]"
        assert sanitized["market_id"] == ["0x[REDACTED]"]
        assert sanitized["credentials"]["address"] == "[REDACTED]"
        assert sanitized["credentials"]["token_id"] == "[REDACTED]"

    def test_exception_inside_secret_container_is_forced_secret(self):
        """A short exception message inherits sensitive-container semantics."""
        secret = "exception-carried-secret"
        sanitized = redact_value({"credentials": ValueError(secret)})

        assert secret not in repr(sanitized)
        assert sanitized["credentials"] == {
            "type": "ValueError",
            "message": "[REDACTED]",
        }

    def test_mapping_keys_and_object_state_are_bounded_without_repr(self):
        """Hostile keys and large object dictionaries cannot amplify logging."""

        class HostileKey:
            def __str__(self):
                raise AssertionError("redactor called attacker key __str__")

        class LargeObject:
            pass

        class ExplosiveObject:
            __slots__ = ()

            def __getattribute__(self, name):
                if name in {"__dataclass_fields__", "__dict__", "get_secret_value"}:
                    raise RuntimeError("attacker-controlled attribute access")
                return object.__getattribute__(self, name)

        large = LargeObject()
        for index in range(1_000):
            setattr(large, f"field_{index}", index)

        huge_key = "x" * 1_000_000
        sanitized = redact_value(
            {
                HostileKey(): "safe",
                huge_key: "safe",
                "large": large,
                "explosive": ExplosiveObject(),
            }
        )

        assert sanitized["<HostileKey>"] == "safe"
        assert any(str(key).startswith("[TRUNCATED]:") for key in sanitized)
        assert sanitized["large"]["__redaction_truncated__"] == "[TRUNCATED]"
        assert len(sanitized["large"]) <= 258
        assert sanitized["explosive"] == "<ExplosiveObject>"

    def test_recursive_redaction_is_cycle_depth_and_size_safe(self):
        """Adversarial object graphs produce bounded sentinel output."""
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        assert redact_value(cyclic)["self"] == "[CYCLE]"

        deep: dict[str, object] = {}
        cursor = deep
        for _ in range(100):
            child: dict[str, object] = {}
            cursor["next"] = child
            cursor = child
        assert "[MAX_DEPTH]" in repr(redact_value(deep))

        bounded = redact_value(list(range(1_000)))
        assert len(bounded) == 257
        assert bounded[-1] == "[TRUNCATED]"

        bounded_text = redact_text("database_url=" + "x" * 70_000)
        assert bounded_text.startswith("[TRUNCATED]:")
        assert len(bounded_text) < 100

    def test_public_identifiers_require_typed_fields(self):
        """Addresses remain public; ambiguous 32-byte hex needs field context."""
        address = "0x" + "3" * 40
        condition_id = "0x" + "4" * 64

        assert redact_text(address) == address
        assert redact_text(condition_id) == "0x[REDACTED]"
        sanitized = redact_value(
            {
                "wallet_address": address,
                "condition_id": condition_id,
                "untyped": condition_id,
                "source": "clob_public",
            }
        )
        assert sanitized["wallet_address"] == address
        assert sanitized["condition_id"] == condition_id
        assert sanitized["untyped"] == "0x[REDACTED]"

    def test_labeled_urlsafe_secret_is_redacted_but_public_slug_is_kept(self):
        """URL-safe secrets are caught by label/structure, not the base64
        backstop, so a long kebab public slug is not over-redacted."""
        # 44-char urlsafe secret containing both '-' and '_'.
        secret = "abcDEF-hijkLMN_pqrsTUV0123456789-_ABCDEFGHIJ"
        assert redact_text(f"api_secret={secret}") == "api_secret=[REDACTED]"
        assert redact_value({"api_secret": secret})["api_secret"] == "[REDACTED]"
        # A long public market slug (kebab case) must survive both free text and
        # structured rendering: it is core data, not a credential.
        slug = "presidential-election-winner-twenty-twenty-eight"
        assert redact_text(slug) == slug
        assert redact_value({"market_slug": slug})["market_slug"] == slug

    def test_empty_username_credential_uri_is_redacted(self):
        """redis://:password@host is the standard credentialed Redis form."""
        for uri in (
            "redis://:hunter2@10.0.0.5:6379/0",
            "postgres://:pw@db:5432/app",
        ):
            redacted = redact_text(f"connecting to {uri}")
            assert "hunter2" not in redacted
            assert "pw@" not in redacted
            assert "[REDACTED]@" in redacted

    def test_labeled_unquoted_value_does_not_leak_after_inner_separators(self):
        """A labeled secret containing ':' or a scheme keyword is redacted whole."""
        assert "def" not in redact_text("password=abc:def")
        assert redact_text("password=abc:def") == "password=[REDACTED]"
        basic = redact_text("authorization: Basic dXNlcjpwYXNzd29yZA==")
        assert "dXNlcjpwYXNzd29yZA" not in basic
        dsn_line = redact_text("dsn=postgres://:pw@host:5432/db")
        assert "pw@host" not in dsn_line

    def test_allowance_error_keeps_public_token_compatibility_and_typed_details(self):
        """Allowance diagnostics retain token while details use token_id typing."""
        token_id = "0x" + "a" * 64

        error = InsufficientAllowanceError(
            "allowance too low",
            token=token_id,
            required=10,
            current=2,
        )

        assert error.token == token_id
        assert error.token_id == token_id
        assert error.details == {"token_id": token_id, "required": 10, "current": 2}

    def test_authentication_boundary_doesnt_reintroduce_signer_input(
        self, monkeypatch, caplog
    ):
        """Signing failures expose only the error type, never signer input."""
        from eth_account import Account

        from polymarket.auth.authenticator import Authenticator

        private_key = "0x" + "b" * 64
        address = "0x" + "1" * 40

        def fail_signing(*args, **kwargs):
            raise ValueError(f"invalid signing key: {private_key}")

        monkeypatch.setattr(Account, "_sign_hash", fail_signing)

        with caplog.at_level(logging.ERROR, logger="polymarket.auth.authenticator"):
            with pytest.raises(AuthenticationError) as exc_info:
                Authenticator().create_l1_headers(
                    address=address,
                    private_key=private_key,
                    timestamp=1_700_000_000,
                )

        assert private_key not in str(exc_info.value)
        assert private_key not in caplog.text
        assert "ValueError" in str(exc_info.value)
        assert exc_info.value.__suppress_context__ is True
        rendered_traceback = "".join(traceback.format_exception(exc_info.value))
        assert private_key not in rendered_traceback

    def test_l2_authentication_boundary_suppresses_raw_decoder_context(
        self, monkeypatch
    ):
        """L2 decoding errors cannot re-render the supplied secret as a cause."""
        import base64

        from polymarket.auth.authenticator import Authenticator

        api_secret = "tiny-api-secret"

        def fail_decode(value):
            raise ValueError(value)

        monkeypatch.setattr(base64, "urlsafe_b64decode", fail_decode)

        with pytest.raises(AuthenticationError) as exc_info:
            Authenticator().create_l2_headers(
                address="0x" + "1" * 40,
                api_key="public-api-key-id",
                api_secret=api_secret,
                api_passphrase="tiny-passphrase",
                method="GET",
                path="/orders",
                timestamp=1_700_000_000,
            )

        assert exc_info.value.__suppress_context__ is True
        rendered_traceback = "".join(traceback.format_exception(exc_info.value))
        assert api_secret not in rendered_traceback


class TestOrderSignerBoundary:
    """Order signing failures must never re-render private-key material."""

    def test_signer_failure_suppresses_raw_exception_context(
        self, monkeypatch, caplog
    ):
        """A signer that echoes its key cannot reach logs or tracebacks."""
        from decimal import Decimal

        from polymarket.exceptions import TradingError
        from polymarket.models import OrderRequest, Side
        from polymarket.trading.order_builder import OrderBuilder

        # Deliberately unlabeled and non-hex: no redaction pattern can catch
        # it, so only the fixed-message/``from None`` boundary protects it.
        private_key = "tiny-signer-secret"

        def fail_signing(_typed_data, key: str):
            raise ValueError(f"unprocessable signing key: {key}")

        monkeypatch.setattr(
            OrderBuilder,
            "_sign_typed_data",
            staticmethod(fail_signing),
        )

        order = OrderRequest(
            token_id="1234567890",
            price=Decimal("0.50"),
            size=Decimal("100"),
            side=Side.BUY,
        )

        with caplog.at_level(logging.ERROR, logger=OrderBuilder.__module__):
            with pytest.raises(TradingError) as exc_info:
                OrderBuilder().build_order(
                    order,
                    private_key=private_key,
                    address="0x" + "1" * 40,
                    nonce=0,
                    tick_size=Decimal("0.01"),
                    fee_rate_bps=0,
                )

        assert private_key not in str(exc_info.value)
        assert private_key not in caplog.text
        assert "ValueError" in str(exc_info.value)
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__suppress_context__ is True
        rendered_traceback = "".join(traceback.format_exception(exc_info.value))
        assert private_key not in rendered_traceback


def test_default_logging_handlers_install_redaction_filter():
    """Every default output handler applies credential redaction."""
    from polymarket.logging_config import DEFAULT_LOGGING_CONFIG

    for handler in DEFAULT_LOGGING_CONFIG["handlers"].values():
        assert "credential_redaction" in handler["filters"]


def test_json_formatter_dependency_is_declared():
    """The formatter imported by production logging is on the install surface."""
    requirements = Path(__file__).resolve().parents[1] / "requirements.txt"
    assert "python-json-logger>=4.0.0" in requirements.read_text(encoding="utf-8")
