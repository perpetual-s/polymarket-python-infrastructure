"""Regression tests for dashboard helper calculations."""

from decimal import Decimal

from ..models import Activity
from ..utils.dashboard_helpers import calculate_time_weighted_pnl


def test_time_weighted_pnl_uses_current_activity_payload_shape():
    """Current Activity payloads expose type as a value and not usd_value."""
    activity = Activity(
        timestamp=4_102_444_800,
        type="TRADE",
        transactionHash="0xabc",
        size="40",
        usdcSize="18",
        side="SELL",
    )

    result = calculate_time_weighted_pnl([], [activity], days=30)

    assert result == {
        "pnl_30d": Decimal("18"),
        "roi_30d": Decimal("0.0"),
        "trades_30d": 1,
    }
