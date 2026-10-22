"""
Routing: pick the cheapest model tier that can reasonably handle a request.

These are simple, explainable rules on purpose. In the demo you can say:
"short prompts go to Micro, normal ones to Lite, long or analytical ones to Pro,
and a tenant close to its budget is never sent to Pro."
"""

from .config import DOWNGRADE_AT, TIER_ORDER

# Words that usually mean the task needs a stronger model.
HARD_TASK_WORDS = (
    "analyze", "analyse", "compare", "explain why", "step by step",
    "reason", "prove", "design", "architecture", "write code", "debug",
)


def choose_tier(messages: list[dict], requested: str, spent: float, budget: float) -> tuple[str, str]:
    """
    Returns (tier, reason). `reason` is sent back to the client and to Revenium,
    so you can see in the data *why* each request went where it did.
    """
    # 1. The client asked for a specific tier ("micro", "lite", "pro").
    if requested in TIER_ORDER:
        tier, reason = requested, "client_requested"
    else:
        # 2. "auto": decide from the prompt itself.
        text = " ".join(str(m.get("content", "")) for m in messages).lower()
        if len(text) > 2000 or any(w in text for w in HARD_TASK_WORDS):
            tier, reason = "pro", "complex_prompt"
        elif len(text) > 300:
            tier, reason = "lite", "medium_prompt"
        else:
            tier, reason = "micro", "short_prompt"

    # 3. Budget guardrail: near the limit, cap at "lite".
    if tier == "pro" and budget > 0 and spent / budget >= DOWNGRADE_AT:
        tier, reason = "lite", "budget_downgrade"

    return tier, reason
