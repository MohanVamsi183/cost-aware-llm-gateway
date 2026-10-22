"""
The gateway itself: one web endpoint that looks like OpenAI's chat API.

Flow for every request:
  1. Identify the tenant from its API key             (who is calling?)
  2. Choose a model tier                                (cheapest that fits)
  3. Reserve the max possible cost in DynamoDB          (can they afford it? 429 if not)
  4. Call Bedrock                                       (do the work)
  5. Compute real cost, settle the reservation          (what did it cost?)
  6. Return the answer + a cost record                  (tell the client)
  7. Send a usage record to Revenium                    (in the background, after replying)

Run it:   uvicorn app.main:app --reload
Docs UI:  http://127.0.0.1:8000/docs
"""

import os
import time
import uuid

from dotenv import load_dotenv

load_dotenv()  # reads secrets from the .env file BEFORE the modules below use them

import boto3
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import revenium
from .budgets import DynamoBudgets, InMemoryBudgets
from .config import MODEL_TIERS, REGION, cost_usd, load_tenants, max_cost_usd
from .router import choose_tier

app = FastAPI(title="Cost-Aware LLM Gateway")
bedrock = boto3.client("bedrock-runtime", region_name=REGION)
tenants = load_tenants()

if os.getenv("BUDGET_BACKEND", "dynamodb") == "dynamodb":
    budgets = DynamoBudgets(os.getenv("BUDGET_TABLE", "mohan-gateway-budgets"), REGION)
else:
    budgets = InMemoryBudgets()


class Message(BaseModel):
    role: str          # "system", "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    model: str = "auto"  # "auto", "micro", "lite" or "pro"
    messages: list[Message]
    max_tokens: int = 512
    temperature: float = 0.3


def to_bedrock(messages: list[Message]):
    """OpenAI format -> Bedrock Converse format (system prompt is passed separately)."""
    system = [{"text": m.content} for m in messages if m.role == "system"]
    convo = [
        {"role": m.role, "content": [{"text": m.content}]}
        for m in messages
        if m.role in ("user", "assistant")
    ]
    return system, convo


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/chat/completions")
def chat(req: ChatRequest, background: BackgroundTasks, authorization: str = Header(default="")):
    # 1. Who is calling?  Header looks like: "Authorization: Bearer tk_acme_123"
    api_key = authorization.removeprefix("Bearer ").strip()
    tenant = tenants.get(api_key)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Unknown API key")
    tenant_id = tenant["tenant_id"]
    budget = tenant["monthly_budget_usd"]

    # 2. Pick a tier (uses current spend for the 80% downgrade rule).
    spent = budgets.get_spent(tenant_id)
    tier, reason = choose_tier([m.model_dump() for m in req.messages], req.model, spent, budget)
    model_id = MODEL_TIERS[tier]["model_id"]

    # 3. Reserve the most this request could cost. Atomic: refuse if it would break the budget.
    prompt_chars = sum(len(m.content) for m in req.messages)
    reserved = max_cost_usd(tier, prompt_chars, req.max_tokens)
    if not budgets.reserve(tenant_id, reserved, budget):
        raise HTTPException(
            status_code=429,
            detail=f"Budget limit for {tenant_id}: spent ${spent:.6f} of ${budget:.2f}; "
                   f"this request could cost up to ${reserved:.6f}",
        )

    # 4. Call Bedrock.
    system, convo = to_bedrock(req.messages)
    kwargs = {
        "modelId": model_id,
        "messages": convo,
        "inferenceConfig": {"maxTokens": req.max_tokens, "temperature": req.temperature},
    }
    if system:
        kwargs["system"] = system

    started_at = time.time()
    try:
        resp = bedrock.converse(**kwargs)
    except Exception as e:  # show Bedrock's error to the caller instead of a blank 500
        budgets.settle(tenant_id, reserved, 0.0)  # nothing was spent: release the hold
        raise HTTPException(status_code=502, detail=f"Bedrock error: {e}")
    finished_at = time.time()
    latency_ms = round((finished_at - started_at) * 1000)

    # 5. Real cost from token counts; swap the reservation for it.
    usage = resp["usage"]
    in_tok, out_tok = usage["inputTokens"], usage["outputTokens"]
    cost = cost_usd(tier, in_tok, out_tok)
    new_total = budgets.settle(tenant_id, reserved, cost)

    request_id = str(uuid.uuid4())

    # 7. Queue the Revenium record; FastAPI sends it right after replying to the client.
    background.add_task(revenium.send, revenium.build_record(
        request_id=request_id, tenant=tenant, model_id=model_id, requested=req.model,
        tier=tier, reason=reason, in_tok=in_tok, out_tok=out_tok, cost=cost,
        stop_reason=resp.get("stopReason", "end_turn"), started_at=started_at,
        finished_at=finished_at, temperature=req.temperature, region=REGION,
    ))

    # 6. Answer in OpenAI's shape, plus our own cost record.
    answer = resp["output"]["message"]["content"][0]["text"]
    return {
        "id": request_id,
        "object": "chat.completion",
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": resp.get("stopReason", "stop"),
            }
        ],
        "usage": {
            "prompt_tokens": in_tok,
            "completion_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
        },
        "gateway": {
            "tenant_id": tenant_id,
            "tier": tier,
            "routing_reason": reason,
            "cost_usd": round(cost, 8),
            "tenant_spent_usd": round(new_total, 8),
            "tenant_budget_usd": budget,
            "latency_ms": latency_ms,
        },
    }
