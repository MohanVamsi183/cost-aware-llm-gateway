"""
Generate realistic multi-tenant traffic through the gateway, so Revenium has
real data to show in the demo.

Each tenant behaves like a different kind of customer:
  acme-support     - a support chatbot: lots of short, cheap questions
  globex-research  - a research assistant: fewer, long analytical requests (expensive)
  initech-intern   - a sandbox with a tiny budget: hits the budget-downgrade, then gets blocked

Run (server must be running in the other window):
    python generate_traffic.py            # default: 120 requests
    python generate_traffic.py 40         # fewer requests
Estimated Bedrock cost for 120 requests: well under $0.50.
"""

import json
import random
import sys
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

URL = "http://127.0.0.1:8000/v1/chat/completions"
TOTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 120

SUPPORT = [
    "What are your support hours?",
    "How do I reset my password?",
    "Can I change my shipping address after ordering?",
    "Where is my order #48213?",
    "Do you ship to Canada?",
    "How do I cancel my subscription?",
    "Is there a student discount?",
    "My invoice shows the wrong company name. How do I fix it?",
    "Summarize this customer message in one line: " + "The app keeps logging me out and I lose my cart every time. " * 6,
    "Classify the sentiment (positive/negative/neutral): 'Delivery was late again, third time this month.'",
]

RESEARCH = [
    "Compare PostgreSQL and DynamoDB for a multi-tenant SaaS billing system and explain why a team might pick each.",
    "Analyze the trade-offs of usage-based pricing versus seat-based pricing for an AI product.",
    "Explain why LLM inference costs differ so much between small and large models, step by step.",
    "Design an architecture for tracking per-customer AI spend in real time.",
    "Compare three strategies for reducing LLM costs in production and reason about when each one fails.",
    "Summarize the main risks of letting customers run unlimited AI requests on a flat-rate plan.",
]

INTERN = [
    "Analyze our onboarding funnel and explain why users might drop off at each step.",
    "Design a test plan for a new checkout flow, step by step.",
    "Compare Python and Go for writing internal tools.",
]

# (api_key, prompts, share of traffic)
PROFILES = [
    ("tk_acme_demo_001", SUPPORT, 0.60),
    ("tk_globex_demo_002", RESEARCH, 0.25),
    ("tk_initech_demo_003", INTERN, 0.15),
]


TENANT_OF = {"tk_acme_demo_001": "acme-support", "tk_globex_demo_002": "globex-research",
             "tk_initech_demo_003": "initech-intern"}


def pick():
    r, total = random.random(), 0.0
    for key, prompts, share in PROFILES:
        total += share
        if r <= total:
            return key, random.choice(prompts)
    return PROFILES[0][0], random.choice(PROFILES[0][1])


def send(_):
    key, prompt = pick()
    body = json.dumps({"model": "auto", "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": 400}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
            g, u = data["gateway"], data["usage"]
            # What the SAME tokens would have cost on Nova Pro ($0.80 in / $3.20 out per 1M)
            all_pro = (u["prompt_tokens"] * 0.80 + u["completion_tokens"] * 3.20) / 1_000_000
            return g["tenant_id"], g["tier"], g["routing_reason"], g["cost_usd"], all_pro
    except urllib.error.HTTPError as e:
        return TENANT_OF[key], "BLOCKED" if e.code == 429 else f"HTTP {e.code}", "", 0.0, 0.0
    except Exception as e:
        return TENANT_OF[key], "ERROR", str(e)[:60], 0.0, 0.0


if __name__ == "__main__":
    random.seed(42)
    print(f"Sending {TOTAL} requests (4 at a time)...")
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, res in enumerate(pool.map(send, range(TOTAL)), 1):
            results.append(res)
            if i % 10 == 0:
                print(f"  {i}/{TOTAL} done")

    print("\nRequests by tenant and tier:")
    for (tenant, tier), n in sorted(Counter((r[0], r[1]) for r in results).items()):
        print(f"  {tenant:22} {tier:10} {n:4}")

    print("\nRouting reasons:")
    for reason, n in Counter(r[2] for r in results if r[2]).most_common():
        print(f"  {reason:22} {n:4}")

    print("\nSpend by tenant (gateway's own numbers, compare with Revenium):")
    spend = Counter()
    for tenant, _, _, cost, _ in results:
        spend[tenant] += cost
    for tenant, usd in spend.most_common():
        print(f"  {tenant:22} ${usd:.6f}")
    print(f"  {'TOTAL':22} ${sum(spend.values()):.6f}")

    actual = sum(r[3] for r in results)
    baseline = sum(r[4] for r in results)
    if baseline > 0:
        print("\nSavings from routing (same requests, same token counts):")
        print(f"  If every request went to Nova Pro:  ${baseline:.6f}")
        print(f"  Actual cost through the gateway:    ${actual:.6f}")
        print(f"  Saved:                              ${baseline - actual:.6f}  ({(1 - actual / baseline) * 100:.0f}% less)")
