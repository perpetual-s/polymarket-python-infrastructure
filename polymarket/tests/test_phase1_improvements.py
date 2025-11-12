"""
Tests for Phase 1 improvements from GitHub analysis.

Tests:
- New Market and Event fields
- Helper methods (get_all_current_markets, get_clob_tradable_markets, etc.)
- archived parameter support
- Contract address verification
"""

import pytest
from typing import List
from shared.polymarket.models import Market, Event
from shared.polymarket.utils.allowances import EXCHANGE_CONTRACTS, USDC_ADDRESS, CTF_ADDRESS


class TestContractAddresses:
    """Test that contract addresses match official Polymarket agents repo."""

    def test_exchange_contracts_count(self):
        """Verify we have all 3 exchange contracts."""
        assert len(EXCHANGE_CONTRACTS) == 3, "Should have 3 exchange contracts"

    def test_exchange_contracts_include_neg_risk_adapter(self):
        """Verify Neg Risk Adapter is included."""
        NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
        assert NEG_RISK_ADAPTER in EXCHANGE_CONTRACTS, "Missing Neg Risk Adapter"

    def test_ctf_exchange_included(self):
        """Verify CTF Exchange is included."""
        CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
        assert CTF_EXCHANGE in EXCHANGE_CONTRACTS, "Missing CTF Exchange"

    def test_neg_risk_ctf_exchange_included(self):
        """Verify Neg Risk CTF Exchange is included."""
        NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
        assert NEG_RISK_EXCHANGE in EXCHANGE_CONTRACTS, "Missing Neg Risk CTF Exchange"

    def test_usdc_address_matches_official(self):
        """Verify USDC address matches official."""
        OFFICIAL_USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        assert USDC_ADDRESS == OFFICIAL_USDC, f"USDC address mismatch: {USDC_ADDRESS}"

    def test_ctf_address_matches_official(self):
        """Verify CTF address matches official."""
        OFFICIAL_CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        assert CTF_ADDRESS == OFFICIAL_CTF, f"CTF address mismatch: {CTF_ADDRESS}"


class TestMarketFieldsAdded:
    """Test that new Market fields from official agents repo are present."""

    def test_market_has_rewards_min_size(self):
        """Test Market has rewards_min_size field."""
        market = Market(
            id="1",
            question="Test",
            slug="test",
            condition_id="cond1",
            category="test",
            outcomes=["YES", "NO"],
            outcome_prices=[0.5, 0.5],
            volume=100,
            liquidity=50,
            active=True,
            closed=False,
            rewards_min_size=10.0
        )
        assert market.rewards_min_size == 10.0

    def test_market_has_rewards_max_spread(self):
        """Test Market has rewards_max_spread field."""
        market = Market(
            id="1",
            question="Test",
            slug="test",
            condition_id="cond1",
            category="test",
            outcomes=["YES", "NO"],
            outcome_prices=[0.5, 0.5],
            volume=100,
            liquidity=50,
            active=True,
            closed=False,
            rewards_max_spread=0.05
        )
        assert market.rewards_max_spread == 0.05

    def test_market_has_ticker(self):
        """Test Market has ticker field."""
        market = Market(
            id="1",
            question="Test",
            slug="test",
            condition_id="cond1",
            category="test",
            outcomes=["YES", "NO"],
            outcome_prices=[0.5, 0.5],
            volume=100,
            liquidity=50,
            active=True,
            closed=False,
            ticker="TEST"
        )
        assert market.ticker == "TEST"

    def test_market_has_new_flag(self):
        """Test Market has new flag."""
        market = Market(
            id="1",
            question="Test",
            slug="test",
            condition_id="cond1",
            category="test",
            outcomes=["YES", "NO"],
            outcome_prices=[0.5, 0.5],
            volume=100,
            liquidity=50,
            active=True,
            closed=False,
            new=True
        )
        assert market.new is True

    def test_market_has_featured_flag(self):
        """Test Market has featured flag."""
        market = Market(
            id="1",
            question="Test",
            slug="test",
            condition_id="cond1",
            category="test",
            outcomes=["YES", "NO"],
            outcome_prices=[0.5, 0.5],
            volume=100,
            liquidity=50,
            active=True,
            closed=False,
            featured=True
        )
        assert market.featured is True

    def test_market_has_restricted_flag(self):
        """Test Market has restricted flag."""
        market = Market(
            id="1",
            question="Test",
            slug="test",
            condition_id="cond1",
            category="test",
            outcomes=["YES", "NO"],
            outcome_prices=[0.5, 0.5],
            volume=100,
            liquidity=50,
            active=True,
            closed=False,
            restricted=True
        )
        assert market.restricted is True

    def test_market_has_archived_flag(self):
        """Test Market has archived flag."""
        market = Market(
            id="1",
            question="Test",
            slug="test",
            condition_id="cond1",
            category="test",
            outcomes=["YES", "NO"],
            outcome_prices=[0.5, 0.5],
            volume=100,
            liquidity=50,
            active=True,
            closed=False,
            archived=False
        )
        assert market.archived is False


class TestEventModelAdded:
    """Test that Event model was added successfully."""

    def test_event_model_exists(self):
        """Test Event model can be imported."""
        from shared.polymarket.models import Event
        assert Event is not None

    def test_event_creation(self):
        """Test Event object creation."""
        event = Event(
            id="1",
            slug="test-event",
            title="Test Event",
            description="Test description",
            ticker="TEST",
            active=True,
            closed=False,
            archived=False,
            new=True,
            featured=False,
            restricted=False,
            markets=["market1", "market2"],
            neg_risk=False
        )
        assert event.id == "1"
        assert event.title == "Test Event"
        assert len(event.markets) == 2

    def test_event_markets_parsing_from_comma_string(self):
        """Test Event parses comma-separated market string."""
        event = Event(
            id="1",
            slug="test",
            title="Test",
            active=True,
            closed=False,
            archived=False,
            markets="market1, market2, market3"
        )
        assert event.markets == ["market1", "market2", "market3"]

    def test_event_markets_parsing_from_list(self):
        """Test Event accepts list of markets."""
        event = Event(
            id="1",
            slug="test",
            title="Test",
            active=True,
            closed=False,
            archived=False,
            markets=["m1", "m2"]
        )
        assert event.markets == ["m1", "m2"]


class TestGammaAPIHelperMethods:
    """Test new helper methods in GammaAPI."""

    def test_gamma_api_has_get_all_current_markets(self):
        """Test get_all_current_markets method exists."""
        from shared.polymarket.api.gamma import GammaAPI
        assert hasattr(GammaAPI, 'get_all_current_markets')

    def test_gamma_api_has_get_clob_tradable_markets(self):
        """Test get_clob_tradable_markets method exists."""
        from shared.polymarket.api.gamma import GammaAPI
        assert hasattr(GammaAPI, 'get_clob_tradable_markets')

    def test_gamma_api_has_filter_events_for_trading(self):
        """Test filter_events_for_trading method exists."""
        from shared.polymarket.api.gamma import GammaAPI
        assert hasattr(GammaAPI, 'filter_events_for_trading')

    def test_gamma_api_has_get_all_tradeable_events(self):
        """Test get_all_tradeable_events method exists."""
        from shared.polymarket.api.gamma import GammaAPI
        assert hasattr(GammaAPI, 'get_all_tradeable_events')

    def test_filter_events_for_trading_logic(self):
        """Test filter_events_for_trading filters correctly."""
        from shared.polymarket.api.gamma import GammaAPI
        from shared.polymarket.config import get_settings

        gamma = GammaAPI(settings=get_settings())

        # Create test events
        events = [
            Event(id="1", slug="e1", title="Good Event",
                  active=True, closed=False, archived=False, restricted=False),
            Event(id="2", slug="e2", title="Restricted Event",
                  active=True, closed=False, archived=False, restricted=True),
            Event(id="3", slug="e3", title="Archived Event",
                  active=True, closed=False, archived=True, restricted=False),
            Event(id="4", slug="e4", title="Closed Event",
                  active=True, closed=True, archived=False, restricted=False),
            Event(id="5", slug="e5", title="Inactive Event",
                  active=False, closed=False, archived=False, restricted=False),
        ]

        tradeable = gamma.filter_events_for_trading(events)

        # Only the first event should pass all filters
        assert len(tradeable) == 1
        assert tradeable[0].id == "1"


class TestClientHelperMethods:
    """Test new helper methods exposed in PolymarketClient."""

    def test_client_has_get_all_current_markets(self):
        """Test client exposes get_all_current_markets."""
        from shared.polymarket.client import PolymarketClient
        assert hasattr(PolymarketClient, 'get_all_current_markets')

    def test_client_has_get_clob_tradable_markets(self):
        """Test client exposes get_clob_tradable_markets."""
        from shared.polymarket.client import PolymarketClient
        assert hasattr(PolymarketClient, 'get_clob_tradable_markets')

    def test_client_has_get_events(self):
        """Test client exposes get_events."""
        from shared.polymarket.client import PolymarketClient
        assert hasattr(PolymarketClient, 'get_events')

    def test_client_has_filter_events_for_trading(self):
        """Test client exposes filter_events_for_trading."""
        from shared.polymarket.client import PolymarketClient
        assert hasattr(PolymarketClient, 'filter_events_for_trading')

    def test_client_has_get_all_tradeable_events(self):
        """Test client exposes get_all_tradeable_events."""
        from shared.polymarket.client import PolymarketClient
        assert hasattr(PolymarketClient, 'get_all_tradeable_events')

    def test_client_imports_event_type(self):
        """Test client imports Event type."""
        from shared.polymarket.client import Event
        assert Event is not None


class TestArchivedParameterSupport:
    """Test that archived parameter is supported in get_markets and get_events."""

    def test_get_markets_accepts_archived_parameter(self):
        """Test get_markets method signature includes archived."""
        from shared.polymarket.api.gamma import GammaAPI
        import inspect

        sig = inspect.signature(GammaAPI.get_markets)
        assert 'archived' in sig.parameters, "get_markets missing archived parameter"

    def test_get_events_accepts_archived_parameter(self):
        """Test get_events method signature includes archived."""
        from shared.polymarket.api.gamma import GammaAPI
        import inspect

        sig = inspect.signature(GammaAPI.get_events)
        assert 'archived' in sig.parameters, "get_events missing archived parameter"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
