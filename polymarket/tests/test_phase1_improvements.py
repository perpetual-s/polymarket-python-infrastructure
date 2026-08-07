"""
Tests for Phase 1 improvements from GitHub analysis.

Tests:
- New Market and Event fields
- Helper methods (get_all_current_markets, get_clob_tradable_markets, etc.)
- archived parameter support
- Contract address verification
"""

import inspect
from decimal import Decimal

import pytest

from polymarket.config import DEFAULT_POLYGON_RPC_URL, PolymarketSettings
from polymarket.ctf.adapter import NegRiskAdapter
from polymarket.models import Event, Market
from polymarket.utils.allowances import (
    AllowanceManager,
    COLLATERAL_ADDRESS,
    CTF_ADDRESS,
    EXCHANGE_CONTRACTS_V2,
)


class TestContractAddresses:
    """Approval targets match the CLOB V2 canon (docs.polymarket.com/resources/contracts)."""

    def test_exchange_contracts_count(self):
        """Verify we have all 3 V2 approval targets."""
        assert len(EXCHANGE_CONTRACTS_V2) == 3, "Should have 3 exchange contracts"

    def test_exchange_contracts_include_neg_risk_adapter_v2(self):
        """Verify the V2 NegRiskCtfCollateralAdapter is included."""
        NEG_RISK_ADAPTER_V2 = "0xadA2005600Dec949baf300f4C6120000bDB6eAab"
        assert NEG_RISK_ADAPTER_V2 in EXCHANGE_CONTRACTS_V2, "Missing Neg Risk Adapter V2"

    def test_ctf_exchange_v2_included(self):
        """Verify CTF Exchange V2 is included."""
        CTF_EXCHANGE_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
        assert CTF_EXCHANGE_V2 in EXCHANGE_CONTRACTS_V2, "Missing CTF Exchange V2"

    def test_neg_risk_ctf_exchange_v2_included(self):
        """Verify Neg Risk CTF Exchange V2 is included."""
        NEG_RISK_EXCHANGE_V2 = "0xe2222d279d744050d28e00520010520000310F59"
        assert NEG_RISK_EXCHANGE_V2 in EXCHANGE_CONTRACTS_V2, "Missing Neg Risk CTF Exchange V2"

    def test_collateral_is_pusd(self):
        """Verify the collateral token is pUSD (replaced USDC.e in CLOB V2)."""
        OFFICIAL_PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
        assert COLLATERAL_ADDRESS.lower() == OFFICIAL_PUSD.lower(), (
            f"collateral address mismatch: {COLLATERAL_ADDRESS}"
        )

    def test_ctf_address_matches_official(self):
        """Verify CTF address matches official (case-insensitive for EIP-55 checksum)."""
        OFFICIAL_CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        assert CTF_ADDRESS.lower() == OFFICIAL_CTF.lower(), f"CTF address mismatch: {CTF_ADDRESS}"

    def test_current_polygon_rpc_is_the_shared_default(self):
        """All on-chain helpers use Polygon's current documented public RPC."""
        assert DEFAULT_POLYGON_RPC_URL == "https://polygon.drpc.org"
        assert PolymarketSettings().rpc_url == DEFAULT_POLYGON_RPC_URL
        assert (
            inspect.signature(AllowanceManager.__init__)
            .parameters["web3_provider"]
            .default
            == DEFAULT_POLYGON_RPC_URL
        )
        assert (
            inspect.signature(NegRiskAdapter.__init__)
            .parameters["web3_provider"]
            .default
            == DEFAULT_POLYGON_RPC_URL
        )

    def test_allowance_checks_cover_all_v2_targets(self):
        """The read path must use each token standard's approval method."""

        class Call:
            def __init__(self, value):
                self._value = value

            def call(self):
                return self._value

        class Erc20Functions:
            def allowance(self, _wallet, _spender):
                return Call(123)

        class Erc1155Functions:
            def isApprovedForAll(self, _wallet, _operator):
                return Call(True)

        class Erc20Token:
            functions = Erc20Functions()

        class Erc1155Token:
            functions = Erc1155Functions()

        manager = object.__new__(AllowanceManager)
        manager.usdc = Erc20Token()
        manager.ctf = Erc1155Token()

        result = manager.check_allowances("0x1111111111111111111111111111111111111111")

        assert result["USDC"] == {target: 123 for target in EXCHANGE_CONTRACTS_V2}
        assert result["CTF"] == {target: True for target in EXCHANGE_CONTRACTS_V2}


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
        assert market.rewards_max_spread == Decimal("0.05")

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


def _sample_market(market_id: str) -> Market:
    """Minimal valid Market for Event membership assertions."""
    return Market(
        id=market_id,
        question=f"Question {market_id}",
        slug=f"market-{market_id}",
        condition_id=f"cond{market_id}",
        category="test",
        outcomes=["YES", "NO"],
        outcome_prices=[0.5, 0.5],
        volume=100,
        liquidity=50,
        active=True,
        closed=False,
    )


class TestEventModelAdded:
    """Test that Event model was added successfully."""

    def test_event_model_exists(self):
        """Test Event model can be imported."""
        from polymarket.models import Event
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
            markets=[_sample_market("1"), _sample_market("2")],
            neg_risk=False
        )
        assert event.id == "1"
        assert event.title == "Test Event"
        assert len(event.markets) == 2
        assert [m.id for m in event.markets] == ["1", "2"]

    def test_event_markets_default_to_empty(self):
        """Test Event without markets yields an empty list, not None."""
        event = Event(
            id="1",
            slug="test",
            title="Test",
            active=True,
            closed=False,
            archived=False,
        )
        assert event.markets == []

    def test_event_markets_reject_bare_ids(self):
        """Test Event.markets holds full Market objects, never bare IDs."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            Event(
                id="1",
                slug="test",
                title="Test",
                active=True,
                closed=False,
                archived=False,
                markets=["market1", "market2", "market3"],
            )


class TestGammaAPIHelperMethods:
    """Test new helper methods in GammaAPI."""

    def test_gamma_api_has_get_all_current_markets(self):
        """Test get_all_current_markets method exists."""
        from polymarket.api.gamma import GammaAPI
        assert hasattr(GammaAPI, 'get_all_current_markets')

    def test_gamma_api_has_get_clob_tradable_markets(self):
        """Test get_clob_tradable_markets method exists."""
        from polymarket.api.gamma import GammaAPI
        assert hasattr(GammaAPI, 'get_clob_tradable_markets')

    def test_gamma_api_has_filter_events_for_trading(self):
        """Test filter_events_for_trading method exists."""
        from polymarket.api.gamma import GammaAPI
        assert hasattr(GammaAPI, 'filter_events_for_trading')

    def test_gamma_api_has_get_all_tradeable_events(self):
        """Test get_all_tradeable_events method exists."""
        from polymarket.api.gamma import GammaAPI
        assert hasattr(GammaAPI, 'get_all_tradeable_events')

    async def test_filter_events_for_trading_logic(self):
        """Test filter_events_for_trading filters correctly."""
        from polymarket.api.gamma import GammaAPI
        from polymarket.config import get_settings

        # GammaAPI opens an aiohttp session on construction, so this test must
        # run inside a live event loop and must close the session afterwards.
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

        try:
            tradeable = gamma.filter_events_for_trading(events)
        finally:
            await gamma.close()

        # Only the first event should pass all filters
        assert len(tradeable) == 1
        assert tradeable[0].id == "1"


class TestClientHelperMethods:
    """Test new helper methods exposed in PolymarketClient."""

    def test_client_has_get_all_current_markets(self):
        """Test client exposes get_all_current_markets."""
        from polymarket.client import PolymarketClient
        assert hasattr(PolymarketClient, 'get_all_current_markets')

    def test_client_has_get_clob_tradable_markets(self):
        """Test client exposes get_clob_tradable_markets."""
        from polymarket.client import PolymarketClient
        assert hasattr(PolymarketClient, 'get_clob_tradable_markets')

    def test_client_has_get_events(self):
        """Test client exposes get_events."""
        from polymarket.client import PolymarketClient
        assert hasattr(PolymarketClient, 'get_events')

    def test_client_has_filter_events_for_trading(self):
        """Test client exposes filter_events_for_trading."""
        from polymarket.client import PolymarketClient
        assert hasattr(PolymarketClient, 'filter_events_for_trading')

    def test_client_has_get_all_tradeable_events(self):
        """Test client exposes get_all_tradeable_events."""
        from polymarket.client import PolymarketClient
        assert hasattr(PolymarketClient, 'get_all_tradeable_events')

    def test_client_imports_event_type(self):
        """Test client imports Event type."""
        from polymarket.client import Event
        assert Event is not None


class TestArchivedParameterSupport:
    """Test that archived parameter is supported in get_markets and get_events."""

    def test_get_markets_accepts_archived_parameter(self):
        """Test get_markets method signature includes archived."""
        from polymarket.api.gamma import GammaAPI
        import inspect

        sig = inspect.signature(GammaAPI.get_markets)
        assert 'archived' in sig.parameters, "get_markets missing archived parameter"

    def test_get_events_accepts_archived_parameter(self):
        """Test get_events method signature includes archived."""
        from polymarket.api.gamma import GammaAPI
        import inspect

        sig = inspect.signature(GammaAPI.get_events)
        assert 'archived' in sig.parameters, "get_events missing archived parameter"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
