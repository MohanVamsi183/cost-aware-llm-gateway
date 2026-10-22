"""
Step 1: prove that your computer can call Amazon Bedrock.

Run:  python bedrock_test.py
Or try another model:  python bedrock_test.py us.amazon.nova-lite-v1:0

What this teaches you:
- boto3 is AWS's Python library. "bedrock-runtime" is the client that calls models.
- The Converse API works the same way for every Bedrock model (Amazon, Anthropic,
  Meta...), which is why the gateway will use it: one code path, many models.
- Every response includes token counts (inputTokens / outputTokens). Tokens are
  what you pay for, so these two numbers are the raw material for cost tracking,
  budgets, and everything you'll send to Revenium.
"""

import sys
import time

import boto3

REGION = "us-east-1"
# Cheapest Amazon model; the "us." prefix is a cross-region inference profile.
MODEL_ID = sys.argv[1] if len(sys.argv) > 1 else "us.amazon.nova-micro-v1:0"

client = boto3.client("bedrock-runtime", region_name=REGION)

start = time.perf_counter()
response = client.converse(
    modelId=MODEL_ID,
    messages=[
        {"role": "user", "content": [{"text": "In one sentence, what is an API gateway?"}]}
    ],
    inferenceConfig={"maxTokens": 200, "temperature": 0.2},
)
latency_ms = (time.perf_counter() - start) * 1000

text = response["output"]["message"]["content"][0]["text"]
usage = response["usage"]

print(f"Model:         {MODEL_ID}")
print(f"Answer:        {text}")
print(f"Input tokens:  {usage['inputTokens']}")
print(f"Output tokens: {usage['outputTokens']}")
print(f"Latency:       {latency_ms:.0f} ms")
