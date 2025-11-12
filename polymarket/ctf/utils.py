"""
Utilities for neg-risk CTF operations.

Provides:
- Conversion calculations (NO→YES)
- Safety checks for augmented markets
- Position equivalence verification
"""

import logging
from typing import Dict, List, Optional
from ..models import Market, Position

logger = logging.getLogger(__name__)

# Solidity uint256 has 256 bits, so maximum outcome index is 255
MAX_OUTCOME_INDEX = 255
MAX_INDEX_SET = (2 ** 256) - 1


class ConversionCalculator:
    """
    Calculate NO→YES conversions for neg-risk markets.

    Formula: For a market with n outcomes, converting m NO tokens yields:
    - Collateral released: amount * (m - 1)
    - YES tokens: m - 1 (one for each complementary outcome)
    """

    @staticmethod
    def calculate_conversion(
        no_tokens: List[str],
        amount: float,
        total_outcomes: int
    ) -> Dict[str, float]:
        """
        Calculate conversion output for NO→YES transformation.

        Args:
            no_tokens: List of NO token IDs being converted
            amount: Amount to convert (in USDC)
            total_outcomes: Total number of outcomes in the market

        Returns:
            Dict with:
            - collateral: USDC released from conversion
            - yes_token_count: Number of YES tokens received
            - yes_outcomes: List of outcome indices for YES tokens

        Raises:
            ValueError: If validation fails

        Example:
            Election with 3 candidates (A, B, C):
            Convert 1 NO_A + 1 NO_B → 1 USDC + 1 YES_C
        """
        # Validate inputs
        if total_outcomes < 2:
            raise ValueError(
                f"total_outcomes must be at least 2 for conversion, got {total_outcomes}"
            )
        if total_outcomes > MAX_OUTCOME_INDEX + 1:
            raise ValueError(
                f"total_outcomes {total_outcomes} exceeds maximum {MAX_OUTCOME_INDEX + 1}"
            )

        m = len(no_tokens)

        if m < 1:
            return {
                "collateral": 0.0,
                "yes_token_count": 0,
                "yes_outcomes": []
            }

        # Validate: Cannot convert more tokens than total outcomes
        if m >= total_outcomes:
            raise ValueError(
                f"Cannot convert {m} NO tokens in market with {total_outcomes} outcomes. "
                f"Number of NO tokens must be less than total outcomes."
            )

        if amount < 0:
            raise ValueError(f"Amount must be non-negative, got {amount}")

        # Collateral released: amount * (m - 1)
        collateral = amount * (m - 1)

        # YES tokens: One for each complementary outcome
        yes_token_count = m - 1

        # Complementary outcomes (all outcomes NOT in no_tokens)
        all_outcomes = set(range(total_outcomes))
        no_outcome_indices = set()  # Would need to extract from token IDs
        yes_outcomes = list(all_outcomes - no_outcome_indices)

        return {
            "collateral": collateral,
            "yes_token_count": yes_token_count,
            "yes_outcomes": yes_outcomes[:yes_token_count]
        }

    @staticmethod
    def is_conversion_profitable(
        no_tokens: List[str],
        amount: float,
        conversion_fee_bps: int = 0
    ) -> bool:
        """
        Check if conversion is profitable after fees.

        Args:
            no_tokens: List of NO token IDs
            amount: Amount to convert
            conversion_fee_bps: Conversion fee in basis points (0-10000)

        Returns:
            True if conversion is profitable
        """
        m = len(no_tokens)

        if m < 2:
            # Need at least 2 NO tokens for conversion to make sense
            return False

        # Collateral released before fees
        collateral = amount * (m - 1)

        # Apply fee
        fee = collateral * (conversion_fee_bps / 10000)
        net_collateral = collateral - fee

        # Profitable if we get more collateral than we put in
        return net_collateral > amount


def is_safe_to_trade(market: Market) -> bool:
    """
    Check if market is safe for automated trading.

    Filters out problematic markets:
    - Augmented neg-risk markets (incomplete outcome universe)
    - Markets with unnamed outcomes (e.g., "Candidate_3")

    Args:
        market: Market object

    Returns:
        True if safe to trade
    """
    # Check if it's an augmented neg-risk market
    if market.neg_risk_augmented:
        logger.warning(
            f"Market {market.slug} is augmented neg-risk (unsafe for auto-trading)"
        )
        return False

    # Check for unnamed outcomes (placeholders)
    if market.outcomes:
        for outcome in market.outcomes:
            # Look for patterns like "Candidate_1", "Option_2", "Other"
            outcome_lower = outcome.lower()
            if any(pattern in outcome_lower for pattern in [
                "candidate_",
                "option_",
                "other",
                "unnamed",
                "tbd",
                "to be determined"
            ]):
                logger.warning(
                    f"Market {market.slug} has placeholder outcome: {outcome}"
                )
                return False

    return True


def filter_safe_neg_risk_markets(markets: List[Market]) -> List[Market]:
    """
    Filter list of markets to only safe neg-risk markets.

    Args:
        markets: List of Market objects

    Returns:
        Filtered list of safe neg-risk markets
    """
    safe_markets = []

    for market in markets:
        # Only include neg-risk markets
        if not (market.neg_risk or market.enable_neg_risk):
            continue

        # Check if safe
        if is_safe_to_trade(market):
            safe_markets.append(market)

    logger.info(
        f"Filtered {len(markets)} markets → {len(safe_markets)} safe neg-risk markets"
    )

    return safe_markets


def calculate_index_set(no_outcome_indices: List[int]) -> int:
    """
    Calculate index set bitmask for conversion.

    Index set is a bitmask where bit i is set if outcome i is a NO token.

    Args:
        no_outcome_indices: List of outcome indices for NO tokens (0-indexed)

    Returns:
        Index set as integer bitmask

    Raises:
        ValueError: If any outcome index exceeds MAX_OUTCOME_INDEX (255)

    Example:
        Outcomes [0, 2] → 0b101 → 5
        Outcomes [1, 3] → 0b1010 → 10
    """
    if not no_outcome_indices:
        return 0

    # Validate all indices are within bounds
    for idx in no_outcome_indices:
        if idx < 0:
            raise ValueError(f"Outcome index {idx} must be non-negative")
        if idx > MAX_OUTCOME_INDEX:
            raise ValueError(
                f"Outcome index {idx} exceeds maximum {MAX_OUTCOME_INDEX}. "
                f"Solidity uint256 supports max 256 outcomes (indices 0-255)."
            )

    # Calculate bitmask
    index_set = 0
    for idx in no_outcome_indices:
        index_set |= (1 << idx)

    return index_set


def parse_index_set(index_set: int, total_outcomes: int) -> List[int]:
    """
    Parse index set bitmask to outcome indices.

    Args:
        index_set: Bitmask representing NO tokens
        total_outcomes: Total number of outcomes

    Returns:
        List of outcome indices where bit is set

    Raises:
        ValueError: If index_set is invalid or total_outcomes exceeds limits

    Example:
        index_set=5 (0b101), total=3 → [0, 2]
    """
    # Validate inputs
    if index_set < 0:
        raise ValueError(f"index_set must be non-negative, got {index_set}")
    if index_set > MAX_INDEX_SET:
        raise ValueError(
            f"index_set {index_set} exceeds maximum {MAX_INDEX_SET} (uint256 max)"
        )
    if total_outcomes < 0:
        raise ValueError(f"total_outcomes must be non-negative, got {total_outcomes}")
    if total_outcomes > MAX_OUTCOME_INDEX + 1:
        raise ValueError(
            f"total_outcomes {total_outcomes} exceeds maximum {MAX_OUTCOME_INDEX + 1}. "
            f"Solidity uint256 supports max 256 outcomes."
        )

    # Parse bitmask
    indices = []
    for i in range(total_outcomes):
        if index_set & (1 << i):
            indices.append(i)
    return indices


def get_complementary_outcomes(
    no_outcome_indices: List[int],
    total_outcomes: int
) -> List[int]:
    """
    Get complementary YES outcomes for given NO outcomes.

    Args:
        no_outcome_indices: Indices of NO outcomes
        total_outcomes: Total outcomes in market

    Returns:
        List of complementary YES outcome indices

    Example:
        NO outcomes: [0, 1], total: 3 → YES outcomes: [2]
    """
    all_outcomes = set(range(total_outcomes))
    no_outcomes = set(no_outcome_indices)
    yes_outcomes = all_outcomes - no_outcomes
    return sorted(list(yes_outcomes))
