"""Pytest configuration for polymarket tests."""

import os
import sys
from pathlib import Path

import pytest

# Add repository root to PYTHONPATH so local `polymarket` imports resolve.
repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(repo_root))


def pytest_collection_modifyitems(items):
    """Keep live, funded, and testnet checks opt-in even when secrets exist."""
    run_live = os.environ.get("POLYMARKET_RUN_LIVE_TESTS") == "1"
    run_testnet = os.environ.get("POLYMARKET_RUN_TESTNET_TESTS") == "1"
    skip_live = pytest.mark.skip(
        reason="set POLYMARKET_RUN_LIVE_TESTS=1 to run live/manual tests"
    )
    skip_testnet = pytest.mark.skip(
        reason="set POLYMARKET_RUN_TESTNET_TESTS=1 to run testnet tests"
    )

    for item in items:
        if (
            item.get_closest_marker("live_network") is not None
            or item.get_closest_marker("manual_operator") is not None
        ) and not run_live:
            item.add_marker(skip_live)
        if item.get_closest_marker("testnet") is not None and not run_testnet:
            item.add_marker(skip_testnet)
