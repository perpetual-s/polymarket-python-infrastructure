"""Illustrate fee math and optional neg-risk CTF helpers.

Install ``.[onchain]`` before running this file. It performs no transaction,
adds no wallet, and uses an explicitly illustrative fee schedule. Query
``await client.get_fee_info(token_id)`` for a real token instead of copying the
example rate.
"""

from decimal import Decimal

from polymarket import (
    CTF_ADDRESS,
    NEG_RISK_ADAPTER,
    NEG_RISK_EXCHANGE,
    ConversionCalculator,
    Side,
    calculate_net_cost,
    calculate_order_fee,
)


def demonstrate_fee_math() -> None:
    price = Decimal("0.60")
    notional = Decimal("100")
    illustrative_rate_bps = 100
    illustrative_exponent = Decimal("2")

    fee = calculate_order_fee(
        Side.BUY,
        price,
        notional,
        illustrative_rate_bps,
        illustrative_exponent,
    )
    total, _ = calculate_net_cost(
        Side.BUY,
        price,
        notional,
        illustrative_rate_bps,
        illustrative_exponent,
    )
    print("Illustrative fee curve (not live market metadata)")
    print(
        {
            "price": str(price),
            "notional": str(notional),
            "rate_bps": illustrative_rate_bps,
            "exponent": str(illustrative_exponent),
            "fee": str(fee),
            "total_buy_cost": str(total),
        }
    )


def demonstrate_conversion_math() -> None:
    result = ConversionCalculator().calculate_conversion(
        no_tokens=["token_a_no", "token_b_no"],
        amount=1.0,
        total_outcomes=3,
    )
    print("Neg-risk conversion preview (no transaction)")
    print(result)


def main() -> None:
    print(
        {
            "ctf_address": CTF_ADDRESS,
            "neg_risk_adapter": NEG_RISK_ADAPTER,
            "neg_risk_exchange": NEG_RISK_EXCHANGE,
        }
    )
    demonstrate_fee_math()
    demonstrate_conversion_math()
    print(
        "On-chain adapter methods can move assets and require gas and approvals; "
        "review the adapter implementation before using them."
    )


if __name__ == "__main__":
    main()
