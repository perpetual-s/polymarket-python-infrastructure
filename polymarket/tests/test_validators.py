"""Tests for validators."""

import pytest
from ..utils.validators import (
    validate_price,
    validate_size,
    validate_token_id,
    validate_order,
)
from ..exceptions import ValidationError


def test_validate_price():
    """Test price validation."""
    assert validate_price(0.50) == 0.50
    assert validate_price(0.01) == 0.01
    assert validate_price(0.99) == 0.99

    with pytest.raises(ValidationError):
        validate_price(0.0)  # Too low

    with pytest.raises(ValidationError):
        validate_price(1.0)  # Too high

    with pytest.raises(ValidationError):
        validate_price("invalid")  # Not numeric


def test_validate_size():
    """Test size validation."""
    assert validate_size(10.0) == 10.0
    assert validate_size(100.5) == 100.5

    with pytest.raises(ValidationError):
        validate_size(0.5)  # Below min

    with pytest.raises(ValidationError):
        validate_size(-10)  # Negative


def test_validate_token_id():
    """Test token ID validation."""
    assert validate_token_id("123456") == "123456"

    with pytest.raises(ValidationError):
        validate_token_id("")  # Empty

    with pytest.raises(ValidationError):
        validate_token_id("abc")  # Not numeric


def test_validate_order():
    """Test complete order validation."""
    result = validate_order("123", 0.55, 10.0, "BUY")
    assert result == ("123", 0.55, 10.0, "BUY")

    with pytest.raises(ValidationError):
        validate_order("123", 1.5, 10.0, "BUY")  # Invalid price

    with pytest.raises(ValidationError):
        validate_order("123", 0.55, 10.0, "INVALID")  # Invalid side
