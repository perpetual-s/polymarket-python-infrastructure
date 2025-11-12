"""
ABIs for Polymarket CTF contracts.

Sources:
- https://github.com/Polymarket/neg-risk-ctf-adapter (MIT)
- https://github.com/Polymarket/ctf-exchange (MIT)

License: MIT
"""

# NegRiskAdapter ABI - Core functions for NO→YES conversions
NEG_RISK_ADAPTER_ABI = [
    # convertPositions - Core neg-risk functionality
    {
        "type": "function",
        "name": "convertPositions",
        "inputs": [
            {"name": "_marketId", "type": "bytes32"},
            {"name": "_indexSet", "type": "uint256"},
            {"name": "_amount", "type": "uint256"}
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    # splitPosition - Split USDC into YES/NO tokens (simplified signature)
    {
        "type": "function",
        "name": "splitPosition",
        "inputs": [
            {"name": "_conditionId", "type": "bytes32"},
            {"name": "_amount", "type": "uint256"}
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    # mergePositions - Merge YES/NO back to USDC (simplified signature)
    {
        "type": "function",
        "name": "mergePositions",
        "inputs": [
            {"name": "_conditionId", "type": "bytes32"},
            {"name": "_amount", "type": "uint256"}
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    # redeemPositions - Redeem after resolution
    {
        "type": "function",
        "name": "redeemPositions",
        "inputs": [
            {"name": "_conditionId", "type": "bytes32"},
            {"name": "_amounts", "type": "uint256[]"}
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    # View functions
    {
        "type": "function",
        "name": "getPositionId",
        "inputs": [
            {"name": "_collateralToken", "type": "address"},
            {"name": "_conditionId", "type": "bytes32"},
            {"name": "_index", "type": "uint256"}
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "pure"
    },
]

# WrappedCollateral ABI - ERC20 wrapper for USDC
WRAPPED_COLLATERAL_ABI = [
    {
        "type": "function",
        "name": "deposit",
        "inputs": [{"name": "_amount", "type": "uint256"}],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function",
        "name": "withdraw",
        "inputs": [{"name": "_amount", "type": "uint256"}],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function",
        "name": "balanceOf",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    },
]

# ERC1155 ABI - For CTF token operations
ERC1155_ABI = [
    {
        "type": "function",
        "name": "balanceOf",
        "inputs": [
            {"name": "account", "type": "address"},
            {"name": "id", "type": "uint256"}
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    },
    {
        "type": "function",
        "name": "balanceOfBatch",
        "inputs": [
            {"name": "accounts", "type": "address[]"},
            {"name": "ids", "type": "uint256[]"}
        ],
        "outputs": [{"name": "", "type": "uint256[]"}],
        "stateMutability": "view"
    },
    {
        "type": "function",
        "name": "setApprovalForAll",
        "inputs": [
            {"name": "operator", "type": "address"},
            {"name": "approved", "type": "bool"}
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function",
        "name": "isApprovedForAll",
        "inputs": [
            {"name": "account", "type": "address"},
            {"name": "operator", "type": "address"}
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view"
    },
]

# ERC20 ABI - For USDC operations
ERC20_ABI = [
    {
        "type": "function",
        "name": "balanceOf",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    },
    {
        "type": "function",
        "name": "allowance",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"}
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    },
    {
        "type": "function",
        "name": "approve",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"}
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable"
    },
]

# CTF Exchange ABI - Standard binary markets
CTF_EXCHANGE_ABI = [
    # Order filling
    {
        "type": "function",
        "name": "fillOrder",
        "inputs": [
            {
                "name": "order",
                "type": "tuple",
                "components": [
                    {"name": "salt", "type": "uint256"},
                    {"name": "maker", "type": "address"},
                    {"name": "signer", "type": "address"},
                    {"name": "taker", "type": "address"},
                    {"name": "tokenId", "type": "uint256"},
                    {"name": "makerAmount", "type": "uint256"},
                    {"name": "takerAmount", "type": "uint256"},
                    {"name": "expiration", "type": "uint256"},
                    {"name": "nonce", "type": "uint256"},
                    {"name": "feeRateBps", "type": "uint256"},
                    {"name": "side", "type": "uint8"},
                    {"name": "signatureType", "type": "uint8"},
                    {"name": "signature", "type": "bytes"}
                ]
            },
            {"name": "fillAmount", "type": "uint256"}
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    # Batch order filling
    {
        "type": "function",
        "name": "fillOrders",
        "inputs": [
            {"name": "orders", "type": "tuple[]"},
            {"name": "fillAmounts", "type": "uint256[]"}
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    # Order cancellation
    {
        "type": "function",
        "name": "cancelOrder",
        "inputs": [{"name": "order", "type": "tuple"}],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function",
        "name": "cancelOrders",
        "inputs": [{"name": "orders", "type": "tuple[]"}],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    # Order status queries
    {
        "type": "function",
        "name": "getOrderStatus",
        "inputs": [{"name": "orderHash", "type": "bytes32"}],
        "outputs": [
            {
                "name": "status",
                "type": "tuple",
                "components": [
                    {"name": "isFilledOrCancelled", "type": "bool"},
                    {"name": "remaining", "type": "uint256"}
                ]
            }
        ],
        "stateMutability": "view"
    },
    {
        "type": "function",
        "name": "isValidNonce",
        "inputs": [
            {"name": "maker", "type": "address"},
            {"name": "nonce", "type": "uint256"}
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view"
    },
    # Token registration
    {
        "type": "function",
        "name": "registerToken",
        "inputs": [
            {"name": "token", "type": "uint256"},
            {"name": "complement", "type": "uint256"},
            {"name": "conditionId", "type": "bytes32"}
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    # Admin functions
    {
        "type": "function",
        "name": "pauseTrading",
        "inputs": [],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function",
        "name": "unpauseTrading",
        "inputs": [],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function",
        "name": "isOperator",
        "inputs": [{"name": "operator", "type": "address"}],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view"
    },
    # Events
    {
        "type": "event",
        "name": "OrderFilled",
        "inputs": [
            {"name": "orderHash", "type": "bytes32", "indexed": True},
            {"name": "maker", "type": "address", "indexed": True},
            {"name": "taker", "type": "address", "indexed": True},
            {"name": "makerAssetId", "type": "uint256", "indexed": False},
            {"name": "takerAssetId", "type": "uint256", "indexed": False},
            {"name": "makerAmountFilled", "type": "uint256", "indexed": False},
            {"name": "takerAmountFilled", "type": "uint256", "indexed": False},
            {"name": "fee", "type": "uint256", "indexed": False}
        ]
    },
    {
        "type": "event",
        "name": "OrdersMatched",
        "inputs": [
            {"name": "takerOrderHash", "type": "bytes32", "indexed": True},
            {"name": "makerOrderHashes", "type": "bytes32[]", "indexed": False},
            {"name": "takerAssetId", "type": "uint256", "indexed": False},
            {"name": "makerAssetId", "type": "uint256", "indexed": False},
            {"name": "takerAmountFilled", "type": "uint256", "indexed": False},
            {"name": "makerAmountFilled", "type": "uint256", "indexed": False}
        ]
    },
    {
        "type": "event",
        "name": "OrderCancelled",
        "inputs": [
            {"name": "orderHash", "type": "bytes32", "indexed": True}
        ]
    },
]
