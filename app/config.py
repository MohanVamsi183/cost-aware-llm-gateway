"""
Settings for the gateway: which models exist, what they cost, and who the tenants are.

Keeping all of this in one file means you can explain the business rules
("Micro is our cheap tier, it costs $X per million tokens") by pointing at one place.
"""

import json
from pathlib import Path

REGION = "us-east-1"

# Three routing tiers, cheapest first.
# Prices are USD per 1,000,000 tokens (Bedrock on-demand, us-east-1).
# Double-check them on https://aws.amazon.com/bedrock/pricing/ before the demo.
MODEL_TIERS = {
    "micro": {
        "model_id": "us.amazon.nova-micro-v1:0",
        "input_per_million": 0.035,
        "output_per_million": 0.14,
    },
    "lite": {
        "model_id": "us.amazon.nova-lite-v1:0",
        "input_per_million": 0.06,
        "output_per_million": 0.24,
    },
    "pro": {
        "model_id": "us.amazon.nova-pro-v1:0",
        "input_per_million": 0.80,
        "output_per_million": 3.20,
    },
}

TIER_ORDER = ["micro", "lite", "pro"]

# When a tenant has spent this fraction of its budget, the gateway stops
# sending its requests to the expensive "pro" tier ("budget-aware downgrade").
DOWNGRADE_AT = 0.80


def load_tenants() -> dict:
    """Read tenants.json and index tenants by their API key."""
    path = Path(__file__).resolve().parent.parent / "tenants.json"
    tenants = json.loads(path.read_text())
    return {t["api_key"]: t for t in tenants}


def max_cost_usd(tier: str, prompt_chars: int, max_tokens: int) -> float:
    """
    The MOST a request could cost, used to reserve budget before calling Bedrock.
    Input: ~1 token per 3 characters (generous) + 100 for overhead.
    Output: the model can't write more than max_tokens.
    """
    est_input_tokens = prompt_chars // 3 + 100
    return cost_usd(tier, est_input_tokens, max_tokens)


def cost_usd(tier: str, input_tokens: int, output_tokens: int) -> float:
    """Tokens x price. This one function is the heart of cost tracking."""
    p = MODEL_TIERS[tier]
    return (
        input_tokens * p["input_per_million"] + output_tokens * p["output_per_million"]
    ) / 1_000_000
