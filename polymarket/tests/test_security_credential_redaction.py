"""
Test credential redaction in logs and error messages.

This test suite ensures that private keys, API secrets, and other credentials
are never exposed in logs, exceptions, or debug output.

Security Issue: SEC-001 (Critical)
- Private keys can leak through exception stack traces
- Credentials can appear in log messages
- Debug output can expose sensitive data
"""

import logging
import pytest
import re
from dataclasses import dataclass, field
from typing import Optional
from io import StringIO

from shared.polymarket.utils.structured_logging import CredentialRedactionFilter


class TestCredentialRedactionFilter:
    """Test credential redaction in logging."""

    def test_filter_redacts_private_keys_in_logs(self):
        """
        RED TEST: Private keys should be redacted from log messages.

        This test will FAIL until we implement CredentialRedactionFilter.
        """
        # Setup logger with our filter
        logger = logging.getLogger("test_redaction")
        logger.setLevel(logging.DEBUG)

        # Capture log output
        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(logging.Formatter('%(message)s'))

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
        """
        RED TEST: API secrets should be redacted from logs.
        """
        logger = logging.getLogger("test_api_secret")
        logger.setLevel(logging.DEBUG)

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(logging.Formatter('%(message)s'))
        handler.addFilter(CredentialRedactionFilter())
        logger.addHandler(handler)

        # Test with base64 API secret
        api_secret = "dGhpc2lzYXNlY3JldGtleXRoYXRpc3Zlcnlsb25nYW5kc2hvdWxkYmVyZWRhY3RlZA=="
        logger.info(f"API credentials: secret={api_secret}")

        log_output = log_stream.getvalue()
        assert api_secret not in log_output, "API secret should be redacted!"
        assert "[REDACTED_SECRET]" in log_output, "Should show redacted placeholder"

        logger.removeHandler(handler)

    def test_filter_handles_multiple_credentials_in_one_message(self):
        """
        RED TEST: Multiple credentials in one message should all be redacted.
        """
        logger = logging.getLogger("test_multiple")
        logger.setLevel(logging.DEBUG)

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(logging.Formatter('%(message)s'))
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
        """
        GREEN TEST: Normal log messages without credentials should pass through.
        """
        logger = logging.getLogger("test_normal")
        logger.setLevel(logging.DEBUG)

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(logging.Formatter('%(message)s'))
        handler.addFilter(CredentialRedactionFilter())
        logger.addHandler(handler)

        normal_message = "Processing 10 orders for market 0x123"
        logger.info(normal_message)

        log_output = log_stream.getvalue()
        assert normal_message in log_output, "Normal messages should pass through"

        logger.removeHandler(handler)

    def test_filter_redacts_in_exception_messages(self):
        """
        RED TEST: Credentials in exception messages should be redacted.

        This is the critical security issue - when exceptions are logged,
        local variables containing credentials can leak.
        """
        logger = logging.getLogger("test_exception")
        logger.setLevel(logging.DEBUG)

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(logging.Formatter('%(message)s'))
        handler.addFilter(CredentialRedactionFilter())
        logger.addHandler(handler)

        private_key = "0x" + "c" * 64

        try:
            raise ValueError(f"Authentication failed with key: {private_key}")
        except ValueError as e:
            logger.error(str(e))

        log_output = log_stream.getvalue()
        assert private_key not in log_output, "Private key in exception should be redacted"

        logger.removeHandler(handler)

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
        assert duration < 1.0, f"Redaction filter too slow: {duration}s for 100 messages"

        logger.removeHandler(handler)


class TestWalletCredentialsRepr:
    """Test that WalletCredentials doesn't leak in __repr__."""

    def test_repr_hides_private_key(self):
        """
        RED TEST: WalletCredentials.__repr__ should hide private_key.

        This requires adding field(repr=False) to the dataclass.
        """
        from shared.polymarket.auth.key_manager import WalletCredentials
        from shared.polymarket.models import SignatureType

        creds = WalletCredentials(
            address="0x1234567890123456789012345678901234567890",
            private_key="0x" + "a" * 64,
            signature_type=SignatureType.EOA
        )

        repr_str = repr(creds)
        assert "0x" + "a" * 64 not in repr_str, "Private key should not appear in repr"
        assert "address=" in repr_str, "Address should appear (it's public)"

    def test_repr_hides_api_secret(self):
        """
        RED TEST: API secret should be hidden from __repr__.
        """
        from shared.polymarket.auth.key_manager import WalletCredentials
        from shared.polymarket.models import SignatureType

        creds = WalletCredentials(
            address="0x1234567890123456789012345678901234567890",
            private_key="0xprivatekey123",
            signature_type=SignatureType.EOA,
            api_secret="supersecretkey12345"
        )

        repr_str = repr(creds)
        assert "supersecretkey12345" not in repr_str, "API secret should not appear in repr"

    def test_str_also_hides_credentials(self):
        """
        RED TEST: __str__ should also hide credentials.
        """
        from shared.polymarket.auth.key_manager import WalletCredentials
        from shared.polymarket.models import SignatureType

        creds = WalletCredentials(
            address="0x1234567890123456789012345678901234567890",
            private_key="0x" + "b" * 64,
            signature_type=SignatureType.EOA,
            api_passphrase="mypassphrase"
        )

        str_repr = str(creds)
        assert "0x" + "b" * 64 not in str_repr, "Private key should not appear in str"
        assert "mypassphrase" not in str_repr, "Passphrase should not appear in str"


class TestExceptionSanitization:
    """Test that exceptions don't leak credentials."""

    def test_authentication_error_doesnt_leak_key(self):
        """
        RED TEST: Authentication errors shouldn't include the actual key.
        """
        from shared.polymarket.exceptions import AuthenticationError

        private_key = "0x" + "d" * 64

        # This pattern is WRONG and should be fixed
        try:
            # Simulating what the code might do
            raise AuthenticationError(f"Failed to sign with key: {private_key}")
        except AuthenticationError as e:
            error_msg = str(e)
            assert private_key not in error_msg, "Private key leaked in exception message!"


# Run these tests - they should FAIL initially (RED phase)
# Then we'll implement the fixes (GREEN phase)
