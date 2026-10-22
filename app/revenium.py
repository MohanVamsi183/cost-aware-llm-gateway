"""
Send one usage record per request to Revenium's metering API.

Revenium docs: https://docs.revenium.io/get-started/quickstart/5-minute-quickstart
Endpoint:      POST https://api.revenium.ai/meter/v2/ai/completions
Auth header:   x-api-key: <your rev_mk_ metering key>

How our gateway data maps onto Revenium's fields:
  tenant (our customer)   -> organizationName + subscriber.id
  our gateway             -> productName
  tier the client asked   -> requestedServiceTier  ("auto", "pro", ...)
  tier we actually used   -> actualServiceTier     ("micro", "lite", "pro")
  why we routed there     -> taskType              ("short_prompt", "budget_downgrade", ...)

This runs AFTER the response is sent to the client (FastAPI background task),
so metering never slows down or breaks a customer's request.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger("revenium")

API_KEY = os.getenv("REVENIUM_METERING_API_KEY", "")
BASE_URL = os.getenv("REVENIUM_METERING_BASE_URL", "https://api.revenium.ai")
PRODUCT_NAME = os.getenv("REVENIUM_PRODUCT_NAME", "cost-aware-llm-gateway")

# If Revenium shows $0 cost for Nova models (because it doesn't have their price),
# set REVENIUM_SEND_GATEWAY_COST=true in .env and we'll send our own computed cost.
SEND_GATEWAY_COST = os.getenv("REVENIUM_SEND_GATEWAY_COST", "false").lower() == "true"

# Bedrock's stop reasons -> Revenium's allowed values
STOP_REASONS = {
    "end_turn": "END",
    "stop_sequence": "END_SEQUENCE",
    "max_tokens": "TOKEN_LIMIT",
    "content_filtered": "CONTENT_FILTER",
    "guardrail_intervened": "CONTENT_FILTER",
    "tool_use": "TOOL_CALL",
}


def iso(ts: float) -> str:
    """Unix timestamp -> '2026-10-21T17:04:05.123Z' (the UTC format Revenium wants)."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_record(*, request_id, tenant, model_id, requested, tier, reason,
                 in_tok, out_tok, cost, stop_reason, started_at, finished_at,
                 temperature, region):
    record = {
        "transactionId": request_id,
        "provider": "AWS Bedrock",
        # Remove the cross-region prefix: "us.amazon.nova-micro-v1:0" -> "amazon.nova-micro-v1:0"
        "model": model_id.split(".", 1)[1] if model_id.startswith(("us.", "eu.", "apac.")) else model_id,
        "operationType": "CHAT",
        "inputTokenCount": in_tok,
        "outputTokenCount": out_tok,
        "totalTokenCount": in_tok + out_tok,
        "stopReason": STOP_REASONS.get(stop_reason, "END"),
        "requestTime": iso(started_at),
        "completionStartTime": iso(started_at),  # not streaming, so first token time ~ start
        "responseTime": iso(finished_at),
        "requestDuration": round((finished_at - started_at) * 1000),
        "isStreamed": False,
        "temperature": temperature,
        "region": region,
        "environment": os.getenv("GATEWAY_ENV", "dev"),
        "modelSource": "COST_AWARE_GATEWAY",
        "organizationName": tenant["name"],
        "productName": PRODUCT_NAME,
        "subscriber": {"id": tenant["tenant_id"]},
        "agent": tenant["tenant_id"],
        "taskType": reason,
        "requestedServiceTier": requested,
        "actualServiceTier": tier,
    }
    if SEND_GATEWAY_COST:
        record["totalCost"] = round(cost, 10)
    return record


def send(record: dict) -> None:
    """POST the record to Revenium. Logs (never raises) so the gateway keeps working if Revenium is down."""
    if not API_KEY:
        log.warning("REVENIUM_METERING_API_KEY not set - skipping metering for %s", record["transactionId"])
        return
    req = urllib.request.Request(
        f"{BASE_URL}/meter/v2/ai/completions",
        data=json.dumps(record).encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": API_KEY,
            "Idempotency-Key": record["transactionId"],  # safe retries, no double counting
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            log.warning("Revenium accepted %s (HTTP %s)", record["transactionId"], r.status)
    except urllib.error.HTTPError as e:
        log.error("Revenium rejected %s: HTTP %s %s", record["transactionId"], e.code, e.read().decode()[:500])
    except Exception as e:
        log.error("Could not reach Revenium for %s: %s", record["transactionId"], e)
