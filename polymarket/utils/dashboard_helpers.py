"""
Dashboard helper functions for multi-wallet analytics.

Optimized for Strategy-3's 100+ wallet tracking needs.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from collections import defaultdict
import logging

from ..models import Position, Trade, Activity, Side

logger = logging.getLogger(__name__)


def calculate_wallet_pnl(positions: List[Position]) -> Dict[str, float]:
    """
    Calculate aggregated P&L metrics for a wallet.

    Args:
        positions: List of positions for the wallet

    Returns:
        Dict with total_pnl, unrealized_pnl, realized_pnl, total_value
    """
    if not positions:
        return {
            "total_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "realized_pnl": 0.0,
            "total_value": 0.0,
            "position_count": 0
        }

    total_unrealized = sum(p.cash_pnl for p in positions)
    total_realized = sum(p.realized_pnl for p in positions)
    total_value = sum(p.current_value for p in positions)

    return {
        "total_pnl": total_unrealized + total_realized,
        "unrealized_pnl": total_unrealized,
        "realized_pnl": total_realized,
        "total_value": total_value,
        "position_count": len(positions)
    }


def calculate_win_rate(trades: List[Trade], positions: List[Position]) -> Dict[str, float]:
    """
    Calculate win rate from closed positions.

    Args:
        trades: Trade history
        positions: Current positions

    Returns:
        Dict with win_rate, winning_trades, losing_trades, total_trades
    """
    if not positions:
        return {
            "win_rate": 0.0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_trades": 0
        }

    # Calculate from positions with realized P&L
    closed_positions = [p for p in positions if p.realized_pnl != 0]

    if not closed_positions:
        return {
            "win_rate": 0.0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_trades": 0
        }

    winning = len([p for p in closed_positions if p.realized_pnl > 0])
    losing = len([p for p in closed_positions if p.realized_pnl < 0])
    total = len(closed_positions)

    return {
        "win_rate": (winning / total * 100) if total > 0 else 0.0,
        "winning_trades": winning,
        "losing_trades": losing,
        "total_trades": total
    }


def group_positions_by_market(
    positions: List[Position]
) -> Dict[str, List[Position]]:
    """
    Group positions by market slug.

    Args:
        positions: List of positions

    Returns:
        Dict mapping market slug to positions
    """
    grouped: Dict[str, List[Position]] = defaultdict(list)

    for position in positions:
        grouped[position.slug].append(position)

    return dict(grouped)


def calculate_market_exposure(positions: List[Position]) -> Dict[str, Dict[str, float]]:
    """
    Calculate exposure per market.

    Args:
        positions: List of positions

    Returns:
        Dict mapping market slug to exposure metrics
    """
    grouped = group_positions_by_market(positions)
    exposure = {}

    for market_slug, market_positions in grouped.items():
        total_value = sum(p.current_value for p in market_positions)
        total_pnl = sum(p.cash_pnl for p in market_positions)

        exposure[market_slug] = {
            "total_value": total_value,
            "total_pnl": total_pnl,
            "position_count": len(market_positions),
            "title": market_positions[0].title if market_positions else ""
        }

    return exposure


def calculate_sharpe_ratio(
    trades: List[Trade],
    positions: List[Position],
    risk_free_rate: float = 0.05
) -> float:
    """
    Calculate Sharpe ratio from trading history using position P&L.

    NOTE: This implementation uses position-based P&L (cash_pnl) rather than
    reconstructing P&L from trades, which is more accurate as it accounts for
    proper position basis tracking and mark-to-market valuations.

    Args:
        trades: Trade history (used for grouping by date)
        positions: Current positions with P&L data
        risk_free_rate: Annual risk-free rate (default 5%)

    Returns:
        Sharpe ratio (annualized)
    """
    # Use position P&L which is more accurate than trade-based calculation
    # CRITICAL FIX: Previous implementation calculated cash flow, not P&L
    if not positions:
        return 0.0

    # Calculate daily P&L from position data
    daily_pnl: Dict[str, float] = defaultdict(float)

    # Use actual P&L from positions (includes both realized and unrealized)
    for position in positions:
        # Use cash_pnl which is the actual profit/loss
        if position.cash_pnl != 0.0:
            # Group by current date (could be enhanced to track historical P&L)
            daily_pnl[datetime.utcnow().date().isoformat()] += position.cash_pnl

    if len(daily_pnl) < 2:
        # Fallback: if we only have current day, can't calculate Sharpe
        return 0.0

    # Calculate daily returns
    daily_values = sorted(daily_pnl.items())
    returns = [pnl for _, pnl in daily_values]

    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
    std_dev = variance ** 0.5

    if std_dev == 0:
        return 0.0

    # Annualize (252 trading days)
    daily_risk_free = risk_free_rate / 252
    sharpe = ((mean_return - daily_risk_free) / std_dev) * (252 ** 0.5)

    return round(sharpe, 2)


def calculate_topic_performance(
    positions: List[Position],
    topic_classifier: Optional[Dict[str, List[str]]] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Calculate performance grouped by market topic.

    Args:
        positions: List of positions
        topic_classifier: Optional dict mapping topics to keywords

    Returns:
        Dict mapping topics to performance metrics
    """
    if topic_classifier is None:
        # Default topic classifier
        topic_classifier = {
            "politics": ["election", "trump", "biden", "vote", "senate", "congress"],
            "sports": ["nba", "nfl", "mlb", "soccer", "championship", "super bowl"],
            "crypto": ["bitcoin", "eth", "crypto", "blockchain", "defi"],
            "economics": ["fed", "inflation", "gdp", "recession", "jobs", "economy"],
            "tech": ["ai", "tech", "apple", "google", "microsoft", "openai"],
            "other": []  # Catch-all
        }

    topic_positions: Dict[str, List[Position]] = defaultdict(list)

    # Classify each position
    for position in positions:
        title_lower = position.title.lower()
        classified = False

        for topic, keywords in topic_classifier.items():
            if topic == "other":
                continue

            for keyword in keywords:
                if keyword in title_lower:
                    topic_positions[topic].append(position)
                    classified = True
                    break

            if classified:
                break

        if not classified:
            topic_positions["other"].append(position)

    # Calculate metrics per topic
    topic_performance = {}

    for topic, topic_pos in topic_positions.items():
        if not topic_pos:
            continue

        total_pnl = sum(p.cash_pnl for p in topic_pos)
        total_value = sum(p.current_value for p in topic_pos)
        winning = len([p for p in topic_pos if p.cash_pnl > 0])

        topic_performance[topic] = {
            "total_pnl": total_pnl,
            "total_value": total_value,
            "position_count": len(topic_pos),
            "win_rate": (winning / len(topic_pos) * 100) if topic_pos else 0.0
        }

    return topic_performance


def calculate_time_weighted_pnl(
    positions: List[Position],
    activities: List[Activity],
    days: int = 30
) -> Dict[str, float]:
    """
    Calculate P&L over specific time periods.

    Args:
        positions: Current positions
        activities: Activity history
        days: Number of days to look back (30, 90, etc.)

    Returns:
        Dict with pnl_Nd, roi_Nd (e.g., pnl_30d, roi_30d)
    """
    cutoff_timestamp = int((datetime.now() - timedelta(days=days)).timestamp())

    # Filter activities within time window
    recent_activities = [
        a for a in activities
        if a.timestamp >= cutoff_timestamp and a.type.value == "TRADE"
    ]

    # Calculate P&L from recent activities
    pnl = sum(a.usd_value for a in recent_activities)

    # Calculate initial value (approximation)
    initial_value = sum(p.initial_value for p in positions)
    roi = (pnl / initial_value * 100) if initial_value > 0 else 0.0

    return {
        f"pnl_{days}d": pnl,
        f"roi_{days}d": roi,
        f"trades_{days}d": len(recent_activities)
    }


def aggregate_multi_wallet_positions(
    wallet_positions: Dict[str, List[Position]]
) -> Dict[str, Any]:
    """
    Aggregate positions across multiple wallets.

    Optimized for Strategy-3's 100+ wallet tracking.

    Args:
        wallet_positions: Dict mapping wallet address to positions

    Returns:
        Aggregated metrics across all wallets
    """
    all_positions = []
    wallet_summaries = {}

    for wallet_addr, positions in wallet_positions.items():
        all_positions.extend(positions)

        # Calculate per-wallet summary
        wallet_pnl = calculate_wallet_pnl(positions)
        wallet_summaries[wallet_addr] = wallet_pnl

    # Overall aggregates
    total_positions = len(all_positions)
    total_pnl = sum(s["total_pnl"] for s in wallet_summaries.values())
    total_value = sum(s["total_value"] for s in wallet_summaries.values())

    # Top performers
    top_wallets = sorted(
        wallet_summaries.items(),
        key=lambda x: x[1]["total_pnl"],
        reverse=True
    )[:10]

    return {
        "total_wallets": len(wallet_positions),
        "total_positions": total_positions,
        "total_pnl": total_pnl,
        "total_value": total_value,
        "avg_pnl_per_wallet": total_pnl / len(wallet_positions) if wallet_positions else 0.0,
        "top_performers": [
            {
                "wallet": addr,
                "pnl": metrics["total_pnl"],
                "value": metrics["total_value"]
            }
            for addr, metrics in top_wallets
        ],
        "wallet_summaries": wallet_summaries
    }


def detect_consensus_signals(
    wallet_positions: Dict[str, List[Position]],
    min_wallets: int = 5,
    min_agreement: float = 0.6
) -> List[Dict[str, Any]]:
    """
    Detect consensus signals from multiple wallets.

    Strategy-3 specific: Find markets where N+ wallets agree.

    Args:
        wallet_positions: Dict mapping wallet to positions
        min_wallets: Minimum wallets for consensus
        min_agreement: Minimum agreement ratio (e.g., 60% on YES)

    Returns:
        List of consensus signals
    """
    # Group positions by market
    market_positions: Dict[str, List[tuple[str, Position]]] = defaultdict(list)

    for wallet_addr, positions in wallet_positions.items():
        for position in positions:
            market_positions[position.slug].append((wallet_addr, position))

    # Detect consensus
    signals = []

    for market_slug, positions_with_wallet in market_positions.items():
        if len(positions_with_wallet) < min_wallets:
            continue

        # Count YES vs NO positions
        outcome_counts: Dict[str, int] = defaultdict(int)
        total_value = 0.0

        for wallet_addr, position in positions_with_wallet:
            outcome_counts[position.outcome] += 1
            total_value += position.current_value

        total_wallets = len(positions_with_wallet)
        dominant_outcome = max(outcome_counts, key=outcome_counts.get)
        agreement_ratio = outcome_counts[dominant_outcome] / total_wallets

        if agreement_ratio >= min_agreement:
            signals.append({
                "market": market_slug,
                "title": positions_with_wallet[0][1].title,
                "outcome": dominant_outcome,
                "wallet_count": total_wallets,
                "agreement_ratio": agreement_ratio,
                "total_value": total_value,
                "wallets": [w for w, p in positions_with_wallet if p.outcome == dominant_outcome]
            })

    # Sort by strength (wallet count * agreement ratio)
    signals.sort(
        key=lambda s: s["wallet_count"] * s["agreement_ratio"],
        reverse=True
    )

    return signals


def format_dashboard_metrics(
    positions: List[Position],
    trades: List[Trade],
    activities: List[Activity]
) -> Dict[str, Any]:
    """
    Format all metrics for dashboard display.

    Complete metrics package for frontend.

    Args:
        positions: Current positions
        trades: Trade history
        activities: Activity log

    Returns:
        Comprehensive dashboard metrics
    """
    pnl_metrics = calculate_wallet_pnl(positions)
    win_rate_metrics = calculate_win_rate(trades, positions)
    market_exposure = calculate_market_exposure(positions)
    topic_performance = calculate_topic_performance(positions)
    time_30d = calculate_time_weighted_pnl(positions, activities, days=30)
    time_90d = calculate_time_weighted_pnl(positions, activities, days=90)

    return {
        "summary": {
            **pnl_metrics,
            **win_rate_metrics,
            **time_30d,
            **time_90d
        },
        "market_exposure": market_exposure,
        "topic_performance": topic_performance,
        "positions": {
            "total": len(positions),
            "profitable": len([p for p in positions if p.cash_pnl > 0]),
            "losing": len([p for p in positions if p.cash_pnl < 0]),
            "redeemable": len([p for p in positions if p.redeemable])
        },
        "activity": {
            "total_trades": len([a for a in activities if a.type.value == "TRADE"]),
            "total_redeems": len([a for a in activities if a.type.value == "REDEEM"]),
            "last_activity": max([a.timestamp for a in activities]) if activities else 0
        }
    }
