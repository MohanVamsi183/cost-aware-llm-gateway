"""
Send a few test requests to your running gateway.

Start the gateway in one PowerShell window:   uvicorn app.main:app --reload
Then in a SECOND window (with .venv active):   python try_gateway.py
"""

import json
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8000/v1/chat/completions"

TESTS = [
    ("tk_acme_demo_001", "What are your support hours?"),
    ("tk_globex_demo_002", "Compare REST and GraphQL and explain why a team might pick each."),
    ("tk_acme_demo_001", "Summarize this in one line: " + "Customers want faster refunds. " * 15),
    ("tk_bad_key", "This should be rejected."),
]

for key, prompt in TESTS:
    body = json.dumps({"model": "auto", "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        URL, data=body, headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    )
    print("=" * 70)
    print(f"Tenant key: {key}\nPrompt:     {prompt[:70]}...")
    try:
        with urllib.request.urlopen(req) as r:
            data = json.load(r)
        g = data["gateway"]
        print(f"Tier:       {g['tier']}  ({g['routing_reason']})")
        print(f"Tokens:     {data['usage']['prompt_tokens']} in / {data['usage']['completion_tokens']} out")
        print(f"Cost:       ${g['cost_usd']:.8f}   tenant total ${g['tenant_spent_usd']:.8f} of ${g['tenant_budget_usd']}")
        print(f"Answer:     {data['choices'][0]['message']['content'][:150]}")
    except urllib.error.HTTPError as e:
        print(f"Rejected:   HTTP {e.code} - {e.read().decode()}")
