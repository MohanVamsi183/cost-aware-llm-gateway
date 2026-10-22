# Cost-Aware LLM Gateway

An OpenAI-compatible gateway in front of Amazon Bedrock that **routes each request to the cheapest model tier that fits**, **enforces per-tenant monthly budgets atomically in DynamoDB**, and **meters every request to [Revenium](https://www.revenium.ai)** for cost attribution by customer, model and routing decision.

```
Customer apps ──► FastAPI gateway ────────────────► Amazon Bedrock (Nova Micro / Lite / Pro)
 (tenant API key)   1. identify tenant      (401)
                    2. pick tier: micro / lite / pro
                    3. reserve max cost in DynamoDB  (429 if over budget)
                    4. call Bedrock
                    5. settle to the real cost
                    6. reply with answer + cost record
                    7. send usage record ──────────► Revenium (background)
```

## What it does

| Feature | How |
|---|---|
| OpenAI-compatible API | `POST /v1/chat/completions`; existing clients switch by changing the base URL |
| Multi-tenant | Each tenant has an API key and a monthly budget (`tenants.json`) |
| Cost-aware routing | Short prompts → Nova Micro, medium → Lite, complex → Pro; every decision records a reason |
| Budget guardrail | At 80% of budget, Pro requests are downgraded to Lite |
| Atomic budgets | **Reserve, then settle**: a DynamoDB conditional update holds the request's maximum possible cost before the model call, so concurrent requests can't overspend; the hold is swapped for the real cost afterwards (released if Bedrock fails) |
| Per-request cost | Tokens × model price, returned to the client and stored per tenant per month |
| Metering | Each request is sent to Revenium's metering API after the response (never blocks the client), with an idempotency key |

## Results from a test run

120 requests from three simulated customers:

| Customer | Share of requests | Share of spend |
|---|---|---|
| Globex (research assistant) | 29% | 84% |
| Acme (support bot) | 55% | 4% |
| Initech (sandbox, $0.006 budget) | 16% | 12% |

- Nova Pro served about 29% of requests but 94% of spend.
- Initech was downgraded to Lite at 80%, then blocked; with requests sent 4 at a time it stopped at **$0.005936 of $0.006, never over**.
- Revenium's independently computed costs matched the gateway's per request.

## Run it locally

Requires Python 3.11+, an AWS account with Bedrock access to Amazon Nova models, and a Revenium metering key.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env            # then add your REVENIUM_METERING_API_KEY

aws dynamodb create-table --table-name mohan-gateway-budgets \
  --attribute-definitions AttributeName=tenant_id,AttributeType=S AttributeName=period,AttributeType=S \
  --key-schema AttributeName=tenant_id,KeyType=HASH AttributeName=period,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST --region us-east-1

uvicorn app.main:app --reload    # http://127.0.0.1:8000/docs
python try_gateway.py            # 4 sample requests
python generate_traffic.py       # 120 requests from 3 tenant profiles
python reset_budgets.py          # show this month's spend (--reset to zero it)
```

`iam-dynamodb-policy.json` is a least-privilege policy limited to the budget table.

## Project layout

| File | Purpose |
|---|---|
| `app/main.py` | The endpoint and request flow |
| `app/router.py` | Tier selection and budget downgrade |
| `app/budgets.py` | DynamoDB reserve/settle (plus an in-memory version) |
| `app/config.py` | Model tiers, prices, cost functions |
| `app/revenium.py` | Builds and sends metering records |
| `tenants.json` | Demo tenants (fake keys) |

## Limitations and next steps

- Routing uses simple length and keyword rules; a classifier or per-tier quality evaluation would route more precisely.
- Requests blocked by budget never reach Bedrock, so they aren't metered.
- Runs locally today. Planned: AWS Lambda deployment, Kinesis + Athena for cost analytics, and a React dashboard.
