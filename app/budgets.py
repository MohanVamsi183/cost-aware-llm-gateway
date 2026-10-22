"""
Budget tracking per tenant, per month.

THE PROBLEM WE'RE SOLVING
  Old way: (1) read "spent", (2) call Bedrock, (3) add the cost.
  If 4 requests arrive at once, all 4 read the same "spent", all 4 pass the
  check, and the tenant overspends. And everything vanished on restart.

THE FIX: "reserve, then settle"  (like a hotel holding a deposit on your card)
  1. RESERVE the most this request could possibly cost, BEFORE calling Bedrock.
     DynamoDB does "add this amount ONLY IF the total stays under budget" as one
     atomic step (a conditional update). Two requests can't both squeeze in.
  2. Call Bedrock.
  3. SETTLE: swap the reservation for the real cost (usually much smaller).
     If Bedrock fails, settle with a real cost of $0, which releases the hold.

Budgets are stored per month ("2026-10"), so they reset automatically each month.

Two versions with the same methods:
  DynamoBudgets   - the real one (survives restarts, safe under concurrency)
  InMemoryBudgets - for running without AWS; resets on restart
Choose with BUDGET_BACKEND=dynamodb or memory in .env
"""

import threading
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError


def current_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def dec(x: float) -> Decimal:
    """DynamoDB stores numbers as Decimal, not float."""
    return Decimal(str(round(x, 10)))


class DynamoBudgets:
    def __init__(self, table_name: str, region: str):
        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def get_spent(self, tenant_id: str) -> float:
        item = self.table.get_item(
            Key={"tenant_id": tenant_id, "period": current_period()}
        ).get("Item")
        return float(item["spent_usd"]) if item else 0.0

    def reserve(self, tenant_id: str, amount: float, budget: float) -> bool:
        """Hold `amount` against the budget. Returns False if it would go over."""
        if amount > budget:  # one request alone could exceed the whole budget
            return False
        try:
            self.table.update_item(
                Key={"tenant_id": tenant_id, "period": current_period()},
                UpdateExpression="ADD spent_usd :amt",
                # "Only if nothing spent yet, OR spent is low enough that adding amt stays <= budget"
                ConditionExpression="attribute_not_exists(spent_usd) OR spent_usd <= :max_before",
                ExpressionAttributeValues={
                    ":amt": dec(amount),
                    ":max_before": dec(budget - amount),
                },
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def settle(self, tenant_id: str, reserved: float, actual: float) -> float:
        """Replace the reservation with the real cost. Returns the new monthly total."""
        resp = self.table.update_item(
            Key={"tenant_id": tenant_id, "period": current_period()},
            UpdateExpression="ADD spent_usd :diff",
            ExpressionAttributeValues={":diff": dec(actual - reserved)},
            ReturnValues="UPDATED_NEW",
        )
        return float(resp["Attributes"]["spent_usd"])


class InMemoryBudgets:
    def __init__(self):
        self._spent: dict[str, float] = {}
        self._lock = threading.Lock()

    def get_spent(self, tenant_id: str) -> float:
        with self._lock:
            return self._spent.get(tenant_id, 0.0)

    def reserve(self, tenant_id: str, amount: float, budget: float) -> bool:
        with self._lock:
            spent = self._spent.get(tenant_id, 0.0)
            if spent + amount > budget:
                return False
            self._spent[tenant_id] = spent + amount
            return True

    def settle(self, tenant_id: str, reserved: float, actual: float) -> float:
        with self._lock:
            self._spent[tenant_id] = self._spent.get(tenant_id, 0.0) + actual - reserved
            return self._spent[tenant_id]
